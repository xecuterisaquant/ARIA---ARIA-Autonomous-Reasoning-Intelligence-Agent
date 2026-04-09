import datetime
import json
import logging
import os
import subprocess
import sys
import time

import anthropic
import requests
from dotenv import load_dotenv

load_dotenv()

# ── Config from environment ─────────────────────────────────────────────
BASE_URL = "https://api.prismapi.ai"
API_KEY = os.environ.get("PRISM_API_KEY", "").strip()
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
ASSETS = [s.strip().upper() for s in os.environ.get("ARIA_ASSETS", "BTC,ETH").split(",") if s.strip()]
LOOP_INTERVAL_SECONDS = int(os.environ.get("ARIA_LOOP_INTERVAL", "300"))
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.environ.get("ARIA_LOG_DIR", "logs"))
LOG_PATH = os.path.join(LOG_DIR, "trades.json")
ARIA_LOG_FILE = os.path.join(LOG_DIR, "aria.log")
DASHBOARD_PORT = int(os.environ.get("PORT", "8080"))

# ── Logging ─────────────────────────────────────────────────────────
os.makedirs(LOG_DIR, exist_ok=True)
_fmt = logging.Formatter("[ARIA] %(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ")
_fh = logging.FileHandler(ARIA_LOG_FILE, encoding="utf-8")
_fh.setFormatter(_fmt)
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
logger = logging.getLogger("aria")
logger.setLevel(logging.INFO)
logger.addHandler(_fh)
logger.addHandler(_sh)


# ── Startup preflight ─────────────────────────────────────────────────
def _preflight() -> None:
    """Verify all required dependencies and config before starting the agent."""
    errors = []

    if not API_KEY:
        errors.append("PRISM_API_KEY is not set or empty.")
    if not ANTHROPIC_KEY:
        errors.append("ANTHROPIC_API_KEY is not set or empty.")

    try:
        proc = subprocess.run(
            ["kraken", "--version"], capture_output=True, text=True, timeout=5
        )
        if proc.returncode != 0:
            errors.append(f"kraken CLI returned non-zero exit code: {proc.returncode}")
    except FileNotFoundError:
        errors.append("kraken CLI not found on PATH. Install from https://github.com/nicholasgasior/kraken-cli")
    except subprocess.TimeoutExpired:
        errors.append("kraken CLI timed out during version check.")

    if errors:
        for e in errors:
            logger.error("Preflight failed: %s", e)
        sys.exit(1)

    try:
        _run_kraken_json(["paper", "balance"])
    except RuntimeError:
        logger.info("Paper account not initialized — running 'kraken paper init'...")
        subprocess.run(["kraken", "paper", "init"], check=False, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # already caught above

    logger.info("Preflight checks passed.")


def get_market_data(symbol: str) -> dict:
    """
    Fetch combined market data for a crypto symbol from the PRISM API.

    Hits:
      GET /crypto/{symbol}/price
      GET /signals/{symbol}

    Returns a dict with keys: symbol, price, change_24h, signal, signal_strength, error (if any).
    """
    symbol = symbol.upper()
    headers = {"X-API-Key": API_KEY}
    result = {
        "symbol": symbol,
        "price": None,
        "change_24h": None,
        "signal": None,
        "signal_strength": None,
        "rsi": None,
        "macd_histogram": None,
        "bb_upper": None,
        "bb_lower": None,
        "error": None,
    }

    # --- Price endpoint ---
    try:
        price_resp = requests.get(
            f"{BASE_URL}/crypto/price/{symbol}",
            headers=headers,
            timeout=10,
        )
        price_resp.raise_for_status()
        price_data = price_resp.json()
        result["price"] = price_data.get("price_usd")
        result["change_24h"] = price_data.get("change_24h_pct")
    except requests.exceptions.HTTPError as e:
        result["error"] = f"Price endpoint HTTP error: {e.response.status_code} {e.response.reason}"
        return result
    except requests.exceptions.RequestException as e:
        result["error"] = f"Price endpoint request failed: {e}"
        return result

    # --- Signals endpoint ---
    try:
        signal_resp = requests.get(
            f"{BASE_URL}/signals/summary",
            headers=headers,
            params={"symbols": symbol},
            timeout=10,
        )
        signal_resp.raise_for_status()
        signal_data = signal_resp.json()
        # /signals/summary returns {"data": [{...}, ...]} keyed by "data" list
        items = signal_data.get("data") or []
        sym_data = next((i for i in items if i.get("symbol", "").upper() == symbol), {})
        result["signal"] = sym_data.get("overall_signal") or sym_data.get("direction")
        result["signal_strength"] = sym_data.get("strength")
        indicators = sym_data.get("indicators") or {}
        result["rsi"] = indicators.get("rsi")
        result["macd_histogram"] = indicators.get("macd_histogram")
        result["bb_upper"] = indicators.get("bollinger_upper")
        result["bb_lower"] = indicators.get("bollinger_lower")
    except requests.exceptions.HTTPError as e:
        result["error"] = f"Signals endpoint HTTP error: {e.response.status_code} {e.response.reason}"
        return result
    except requests.exceptions.RequestException as e:
        result["error"] = f"Signals endpoint request failed: {e}"
        return result

    return result


def get_claude_decision(market_data: dict) -> dict:
    """
    Pass market data to Claude and get a structured trading decision.

    Returns a dict with keys: action, confidence, reasoning, risk_level, error.
    """
    if market_data.get("error"):
        return {"error": f"Skipping decision: upstream data error — {market_data['error']}"}

    change_pct = market_data.get("change_24h")
    change_str = f"{change_pct * 100:+.2f}%" if change_pct is not None else "N/A"
    price = market_data.get("price")
    price_str = f"${price:,.2f}" if price is not None else "N/A"

    user_message = (
        f"Asset: {market_data.get('symbol')} | "
        f"Price: {price_str} | "
        f"24h Change: {change_str} | "
        f"Signal: {market_data.get('signal')} ({market_data.get('signal_strength')}) | "
        f"RSI: {market_data.get('rsi')} | "
        f"MACD histogram: {market_data.get('macd_histogram')} | "
        f"Bollinger Upper: {market_data.get('bb_upper'):,.2f} | "
        f"Bollinger Lower: {market_data.get('bb_lower'):,.2f}"
    )

    system_prompt = (
        "You are ARIA, an autonomous crypto trading agent. "
        "You analyze market data and make disciplined trading decisions. "
        "You always explain your reasoning concisely before giving a decision."
    )

    response_schema = (
        '{"action": "BUY/SELL/HOLD", "confidence": 0-100, '
        '"reasoning": "2-3 sentence explanation", "risk_level": "LOW/MEDIUM/HIGH"}'
    )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=512,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"{user_message}\n\n"
                        f"Respond ONLY with valid JSON in this exact format:\n{response_schema}"
                    ),
                }
            ],
        )
        raw = message.content[0].text.strip()
        # Strip markdown code fences if Claude wraps the JSON
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        decision = json.loads(raw)
        decision["error"] = None
        return decision
    except json.JSONDecodeError as e:
        return {"error": f"Failed to parse Claude response as JSON: {e}", "raw": raw}
    except anthropic.APIError as e:
        return {"error": f"Anthropic API error: {e}"}


def check_risk(decision: dict, portfolio_state: dict) -> dict:
    """
    Apply risk management rules before executing a trade.

    Rules (in priority order):
      1. Reject if confidence < 60
      2. Reject if risk_level is HIGH and action is BUY
      3. Reject if action is SELL and symbol not held
      4. Reject if today's trade count >= 10
      5. Reject if position size would exceed 20% of starting_balance

    Position sizing (for approved BUYs):
      LOW risk  -> 10% of current balance_usd
      MEDIUM risk -> 15% of current balance_usd
      HIGH buys are rejected above, so never sized

    Returns:
      {"approved": True/False, "reason": str, "position_usd": float or None}
    """
    action = (decision.get("action") or "").upper()
    confidence = decision.get("confidence", 0)
    risk_level = (decision.get("risk_level") or "").upper()
    symbol = decision.get("symbol")  # may be absent; caller should inject it

    balance_usd = portfolio_state.get("balance_usd", 0.0)
    starting_balance = portfolio_state.get("starting_balance", 10000.0)
    positions = portfolio_state.get("positions", {})
    trades_today = portfolio_state.get("trades_today", 0)

    # --- Rule 1: low confidence ---
    if confidence < 60:
        return {
            "approved": False,
            "reason": f"Confidence {confidence} is below minimum threshold of 60.",
            "position_usd": None,
        }

    # --- Rule 2: HIGH-risk BUY ---
    if risk_level == "HIGH" and action == "BUY":
        return {
            "approved": False,
            "reason": "HIGH risk BUY rejected — policy prohibits buying into high-risk setups.",
            "position_usd": None,
        }

    # --- Rule 3: SELL without a position ---
    if action == "SELL" and symbol and symbol.upper() not in positions:
        return {
            "approved": False,
            "reason": f"SELL rejected — no open position in {symbol}.",
            "position_usd": None,
        }

    # --- Rule 4: daily trade limit ---
    if trades_today >= 10:
        return {
            "approved": False,
            "reason": f"Daily trade limit reached ({trades_today}/10). No further trades today.",
            "position_usd": None,
        }

    # --- Rule 5: position size cap (20% of starting balance) ---
    max_position = starting_balance * 0.20
    risk_pct = {"LOW": 0.10, "MEDIUM": 0.15}.get(risk_level, 0.10)
    position_usd = round(balance_usd * risk_pct, 2)

    if position_usd > max_position:
        position_usd = round(max_position, 2)

    # For HOLD or SELL, position_usd is informational only (size of held position)
    if action == "HOLD":
        return {
            "approved": True,
            "reason": "HOLD — no trade executed.",
            "position_usd": 0.0,
        }

    if action == "SELL":
        return {
            "approved": True,
            "reason": f"SELL approved. Will liquidate {symbol} position.",
            "position_usd": 0.0,  # full position exit; caller handles sizing
        }

    # BUY
    return {
        "approved": True,
        "reason": f"BUY approved. Allocating ${position_usd:,.2f} ({risk_pct*100:.0f}% of balance, capped at 20% of starting balance).",
        "position_usd": position_usd,
    }


def get_portfolio_status() -> dict:
    """
    Fetch current portfolio status from `kraken paper status`.

    Returns:
      {
        "total_value": float,      # total account value in USD
        "unrealized_pnl": float,   # unrealized PnL in USD
        "pnl_percent": float,      # PnL as a percentage
        "total_trades": int,       # lifetime trade count
        "error": str or None,
      }
    """
    try:
        status_raw = _run_kraken_json(["paper", "status"])
        total_value = (
            status_raw.get("total_value")
            or status_raw.get("portfolio_value")
            or status_raw.get("equity")
            or 0.0
        )
        unrealized_pnl = (
            status_raw.get("unrealized_pnl")
            or status_raw.get("open_pnl")
            or 0.0
        )
        pnl_percent = (
            status_raw.get("pnl_percent")
            or status_raw.get("return_pct")
            or (unrealized_pnl / total_value * 100 if total_value else 0.0)
        )
        total_trades = (
            status_raw.get("total_trades")
            or status_raw.get("trade_count")
            or 0
        )
        return {
            "total_value": float(total_value),
            "unrealized_pnl": float(unrealized_pnl),
            "pnl_percent": float(pnl_percent),
            "total_trades": int(total_trades),
            "error": None,
        }
    except FileNotFoundError:
        return {"error": "kraken CLI not found", "total_value": 0.0, "unrealized_pnl": 0.0, "pnl_percent": 0.0, "total_trades": 0}
    except subprocess.TimeoutExpired:
        return {"error": "kraken CLI timed out", "total_value": 0.0, "unrealized_pnl": 0.0, "pnl_percent": 0.0, "total_trades": 0}
    except (json.JSONDecodeError, KeyError, RuntimeError) as e:
        return {"error": f"get_portfolio_status failed: {e}", "total_value": 0.0, "unrealized_pnl": 0.0, "pnl_percent": 0.0, "total_trades": 0}


def _run_kraken_json(args: list) -> dict:
    """Helper: run a kraken CLI command with -o json and return parsed output."""
    result = subprocess.run(
        ["kraken"] + args + ["-o", "json"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"kraken exited {result.returncode}: {err}")
    return json.loads(result.stdout)


def get_kraken_balance() -> dict:
    """
    Combine `kraken paper balance` and `kraken paper history` to return:

      {
        "USD": 9999.17,
        "positions": {
          "BTC": {"volume": 0.001, "avg_price": 72400.2},
          ...
        }
      }

    Falls back gracefully if the CLI is unavailable.
    """
    try:
        # --- 1. Current balances (volumes) ---
        bal_raw = _run_kraken_json(["paper", "balance"])
        balances = {
            asset: info["total"]
            for asset, info in bal_raw.get("balances", {}).items()
            if isinstance(info, dict) and "total" in info
        }

        # --- 2. Trade history → reconstruct avg_price per held asset ---
        hist_raw = _run_kraken_json(["paper", "history"])
        trades = hist_raw.get("trades", [])

        # Replay fills in chronological order to compute running avg cost basis
        # pair format: "BTCUSD" → base = everything before "USD" (or "EUR" etc.)
        running: dict[str, dict] = {}  # {symbol: {volume, cost_basis}}
        for t in sorted(trades, key=lambda x: x.get("time", "")):
            if t.get("status") != "filled":
                continue
            pair: str = t.get("pair", "")
            # Strip quote currency (last 3 chars assumed USD/EUR/GBP)
            base = pair[:-3] if len(pair) > 3 else pair
            vol = float(t.get("volume", 0))
            price = float(t.get("price", 0))
            side = t.get("side", "").lower()

            entry = running.setdefault(base, {"volume": 0.0, "cost_basis": 0.0})
            if side == "buy":
                new_vol = entry["volume"] + vol
                entry["cost_basis"] = (
                    (entry["cost_basis"] * entry["volume"] + price * vol) / new_vol
                    if new_vol > 0 else 0.0
                )
                entry["volume"] = new_vol
            elif side == "sell":
                entry["volume"] = max(0.0, entry["volume"] - vol)
                if entry["volume"] == 0.0:
                    entry["cost_basis"] = 0.0

        # --- 3. Build positions from assets we actually still hold ---
        positions = {}
        for asset, total in balances.items():
            if asset == "USD":
                continue
            if total > 0:
                hist_entry = running.get(asset, {})
                positions[asset] = {
                    "volume": total,
                    "avg_price": round(hist_entry.get("cost_basis", 0.0), 8),
                }

        return {"USD": balances.get("USD", 0.0), "positions": positions}

    except FileNotFoundError:
        return {"error": "kraken CLI not found"}
    except subprocess.TimeoutExpired:
        return {"error": "kraken CLI timed out"}
    except (json.JSONDecodeError, KeyError, RuntimeError) as e:
        return {"error": f"get_kraken_balance failed: {e}"}


def _run_kraken_paper(action: str, symbol: str, volume: float) -> str:
    """
    Simulate a Kraken paper trade via subprocess.
    Falls back to a mock response if the kraken CLI is unavailable.

    Expected CLI: kraken paper buy BTC/USD 0.001
    """
    pair = f"{symbol}/USD"
    cmd = ["kraken", "paper", action.lower(), pair, f"{volume:.8f}"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return (result.stdout + result.stderr).strip() or f"[kraken exited {result.returncode}]"
    except FileNotFoundError:
        return f"[MOCK] kraken CLI not found — simulated {action} {volume:.8f} {pair}"
    except subprocess.TimeoutExpired:
        return "[ERROR] kraken CLI timed out"


def _save_log(trade_log: list) -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(trade_log, f, indent=2)


def main() -> None:
    _preflight()

    portfolio_state = {
        "balance_usd": 10000.0,
        "starting_balance": 10000.0,
        "positions": {},
        "trades_today": 0,
        "trade_log": [],
        "portfolio_status": {},
    }
    cycle = 0

    logger.info("ARIA trading agent started.")
    logger.info("Assets: %s | Interval: %ss | Log: %s", ASSETS, LOOP_INTERVAL_SECONDS, LOG_PATH)

    while True:
        try:
            cycle += 1
            now = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"

            # --- Portfolio status ---
            ps = get_portfolio_status()
            portfolio_state["portfolio_status"] = ps
            if ps.get("error"):
                logger.warning("Portfolio status unavailable: %s", ps["error"])
            else:
                pnl_sign = "+" if ps["unrealized_pnl"] >= 0 else ""
                logger.info(
                    "Portfolio: $%s | PnL: %s$%s (%s%s%%) | Trades: %s",
                    f"{ps['total_value']:,.2f}",
                    pnl_sign,
                    f"{abs(ps['unrealized_pnl']):,.2f}",
                    pnl_sign,
                    f"{abs(ps['pnl_percent']):.3f}",
                    ps["total_trades"],
                )

            # --- Sync portfolio state from Kraken paper account ---
            kb = get_kraken_balance()
            if "error" in kb:
                logger.warning("Could not sync Kraken balance: %s — using cached state", kb["error"])
            else:
                portfolio_state["balance_usd"] = kb.get("USD", portfolio_state["balance_usd"])
                portfolio_state["positions"] = kb.get("positions", {})

            logger.info("=== Cycle %d — %s ===", cycle, now)
            logger.info(
                "Balance: $%s | Trades today: %d/10",
                f"{portfolio_state['balance_usd']:,.2f}",
                portfolio_state["trades_today"],
            )

            for symbol in ASSETS:
                try:
                    logger.info("[%s] Processing...", symbol)

                    # --- 1. Market data ---
                    market_data = get_market_data(symbol)
                    if market_data.get("error"):
                        logger.error("[%s] Market data error: %s", symbol, market_data["error"])
                        continue

                    price = market_data["price"]
                    change_pct = (market_data["change_24h"] or 0) * 100
                    logger.info(
                        "[%s] Price: $%s | 24h: %+.2f%% | Signal: %s (%s)",
                        symbol, f"{price:,.2f}", change_pct,
                        market_data["signal"], market_data["signal_strength"],
                    )

                    # --- 2. Claude decision ---
                    decision = get_claude_decision(market_data)
                    if decision.get("error"):
                        logger.error("[%s] Decision error: %s", symbol, decision["error"])
                        continue

                    action = decision["action"].upper()
                    confidence = decision["confidence"]
                    risk_level = decision["risk_level"]
                    logger.info(
                        "[%s] Decision: %s | Confidence: %d | Risk: %s",
                        symbol, action, confidence, risk_level,
                    )

                    # --- 3. Risk check ---
                    decision["symbol"] = symbol
                    risk = check_risk(decision, portfolio_state)
                    approved = risk["approved"]
                    if approved:
                        logger.info("[%s] Risk check APPROVED: %s", symbol, risk["reason"])
                    else:
                        logger.info("[%s] Risk check REJECTED: %s", symbol, risk["reason"])

                    # --- 4. Execute if approved and actionable ---
                    if approved and action in ("BUY", "SELL"):
                        if action == "BUY":
                            position_usd = risk["position_usd"]
                            volume = position_usd / price
                        else:  # SELL — use full held volume
                            held = portfolio_state["positions"].get(symbol, {})
                            volume = held.get("volume", 0.0)
                            position_usd = volume * price

                        cli_output = _run_kraken_paper(action, symbol, volume)
                        logger.info("[%s] CLI: %s", symbol, cli_output)

                        # Update portfolio state
                        if action == "BUY":
                            existing = portfolio_state["positions"].get(symbol)
                            if existing:
                                total_vol = existing["volume"] + volume
                                avg_price = (
                                    (existing["avg_price"] * existing["volume"] + price * volume)
                                    / total_vol
                                )
                                portfolio_state["positions"][symbol] = {
                                    "volume": total_vol,
                                    "avg_price": avg_price,
                                }
                            else:
                                portfolio_state["positions"][symbol] = {
                                    "volume": volume,
                                    "avg_price": price,
                                }
                            portfolio_state["balance_usd"] = round(
                                portfolio_state["balance_usd"] - position_usd, 2
                            )
                        else:  # SELL
                            portfolio_state["balance_usd"] = round(
                                portfolio_state["balance_usd"] + position_usd, 2
                            )
                            portfolio_state["positions"].pop(symbol, None)

                        portfolio_state["trades_today"] += 1

                        # Append to trade log
                        portfolio_state["trade_log"].append({
                            "timestamp": now,
                            "cycle": cycle,
                            "symbol": symbol,
                            "action": action,
                            "price": price,
                            "volume": volume,
                            "position_usd": position_usd,
                            "confidence": confidence,
                            "risk_level": risk_level,
                            "reasoning": decision.get("reasoning"),
                            "cli_output": cli_output,
                            "balance_after": portfolio_state["balance_usd"],
                        })

                except Exception:
                    logger.exception("[%s] Unhandled error processing asset — skipping.", symbol)

            # --- Save log after every full cycle ---
            _save_log(portfolio_state["trade_log"])
            logger.info("Log saved → %s", LOG_PATH)
            logger.info("Sleeping %ds until next cycle...", LOOP_INTERVAL_SECONDS)

        except Exception:
            logger.exception("Unhandled error in cycle %d — continuing.", cycle)

        time.sleep(LOOP_INTERVAL_SECONDS)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test-balance":
        logger.info("Testing get_kraken_balance()...")
        bal = get_kraken_balance()
        print(json.dumps(bal, indent=2))
        sys.exit(0)
    main()

