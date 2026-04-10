"""ARIA — Autonomous Reasoning & Intelligence Agent.

Thin orchestrator: wires config, market data, Claude decisions, risk checks,
Kraken execution, memory, and the web dashboard into a single trading loop."""
import datetime
import subprocess
import sys
import threading
import time

from src.config import (
    API_KEY,
    ANTHROPIC_KEY,
    ASSETS,
    DASHBOARD_PORT,
    LOG_PATH,
    LOOP_INTERVAL_SECONDS,
    logger,
)
from src.kraken import execute_futures_trade, get_kraken_balance, get_portfolio_status, run_kraken_command
from src.market import get_market_data, get_fear_greed
from src.agent import get_claude_decision
from src.risk import check_risk
from src.store import save_log, save_status
from src import memory


# ── Futures symbol mapping ─────────────────────────────────────────────
# PRISM API uses BTC/ETH; Kraken futures CLI uses PI_XBTUSD/PI_ETHUSD
FUTURES_SYMBOLS = {
    "BTC": "PI_XBTUSD",
    "ETH": "PI_ETHUSD",
}


# ── Startup preflight ─────────────────────────────────────────────────
def _preflight() -> bool:
    """Verify all required dependencies and config before starting the agent.

    Returns True if all checks pass, False otherwise.
    Never calls sys.exit — the dashboard must stay alive for healthchecks.
    """
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
        errors.append("kraken CLI not found on PATH. Install from https://github.com/krakenfx/kraken-cli")
    except subprocess.TimeoutExpired:
        errors.append("kraken CLI timed out during version check.")

    if errors:
        for e in errors:
            logger.error("Preflight failed: %s", e)
        return False

    try:
        run_kraken_command(["futures", "paper", "balance"])
    except RuntimeError:
        logger.info("Futures paper account not initialized — running 'kraken futures paper init'...")
        subprocess.run(["kraken", "futures", "paper", "init"], check=False, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # already caught above

    # Set 1x leverage for all futures symbols
    for sym in FUTURES_SYMBOLS.values():
        try:
            subprocess.run(
                ["kraken", "futures", "paper", "set-leverage", sym, "1"],
                capture_output=True, timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    logger.info("Preflight checks passed.")
    return True


def main() -> None:
    # Start the web dashboard FIRST so healthchecks pass during preflight
    try:
        from src import dashboard as _dashboard
        _t = threading.Thread(target=_dashboard.run_dashboard, daemon=True, name="aria-dashboard")
        _t.start()
        logger.info("Dashboard started on port %d", DASHBOARD_PORT)
    except Exception as exc:
        logger.warning("Could not start dashboard: %s", exc)

    if not _preflight():
        logger.error("Preflight failed — waiting 60s and retrying...")
        time.sleep(60)
        if not _preflight():
            logger.critical("Preflight failed twice — trading loop will not start. Dashboard stays alive.")
            # Block forever so dashboard keeps serving healthchecks
            while True:
                time.sleep(3600)

    portfolio_state: dict = {
        "collateral": 10000.0,
        "starting_balance": 10000.0,
        "positions": {},
        "trades_today": 0,
        "last_trade_date": "",
        "trade_log": [],
        "portfolio_status": {},
    }
    latest_decisions: dict = {}
    cycle = 0

    logger.info("ARIA trading agent started.")
    logger.info("Assets: %s | Interval: %ss | Log: %s", ASSETS, LOOP_INTERVAL_SECONDS, LOG_PATH)

    while True:
        try:
            cycle += 1
            now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
            today = now[:10]

            # Daily reset
            if portfolio_state["last_trade_date"] and today != portfolio_state["last_trade_date"]:
                logger.info("New UTC day (%s) — resetting trades_today counter.", today)
                portfolio_state["trades_today"] = 0
            portfolio_state["last_trade_date"] = today

            # Portfolio snapshot
            ps = get_portfolio_status()
            portfolio_state["portfolio_status"] = ps
            if ps.get("error"):
                logger.warning("Portfolio status unavailable: %s", ps["error"])
            else:
                pnl_sign = "+" if ps["unrealized_pnl"] >= 0 else ""
                logger.info(
                    "Portfolio: $%s | PnL: %s$%s (%s%s%%) | Trades: %s",
                    f"{ps['total_value']:,.2f}", pnl_sign,
                    f"{abs(ps['unrealized_pnl']):,.2f}", pnl_sign,
                    f"{abs(ps['pnl_percent']):.3f}", ps["total_trades"],
                )

            # Sync balances from Kraken
            kb = get_kraken_balance()
            if "error" in kb:
                logger.warning("Could not sync Kraken balance: %s — using cached state", kb["error"])
            else:
                portfolio_state["collateral"] = kb.get("collateral", portfolio_state["collateral"])
                portfolio_state["positions"] = kb.get("positions", {})

            logger.info("=== Cycle %d — %s ===", cycle, now)

            # Fetch fear/greed once per cycle
            fear_greed = get_fear_greed()
            if fear_greed.get("score") is not None:
                logger.info("Fear & Greed: %s (%s)", fear_greed["score"], fear_greed.get("label"))

            logger.info(
                "Collateral: $%s | Trades today: %d/10",
                f"{portfolio_state['collateral']:,.2f}",
                portfolio_state["trades_today"],
            )

            for i, symbol in enumerate(ASSETS):
                if i > 0:
                    time.sleep(5)  # pace API calls to stay under 10 req/min
                try:
                    _process_asset(symbol, now, cycle, portfolio_state, fear_greed, latest_decisions)
                except Exception:
                    logger.exception("[%s] Unhandled error — skipping.", symbol)

            save_log(portfolio_state["trade_log"])
            save_status({
                "total_value": ps.get("total_value", portfolio_state["collateral"]),
                "unrealized_pnl": ps.get("unrealized_pnl", 0.0),
                "pnl_percent": ps.get("pnl_percent", 0.0),
                "total_trades": ps.get("total_trades", len(portfolio_state["trade_log"])),
                "timestamp": now,
                "fear_greed": fear_greed,
                "latest_decisions": latest_decisions,
            })
            logger.info("Log saved → %s", LOG_PATH)
            logger.info("Sleeping %ds until next cycle...", LOOP_INTERVAL_SECONDS)

        except Exception:
            logger.exception("Unhandled error in cycle %d — continuing.", cycle)

        time.sleep(LOOP_INTERVAL_SECONDS)


def _process_asset(symbol: str, now: str, cycle: int, portfolio_state: dict, fear_greed: dict, latest_decisions: dict) -> None:
    """Run the full decision → risk → execution pipeline for one asset."""
    logger.info("[%s] Processing...", symbol)

    market_data = get_market_data(symbol)
    if market_data.get("error"):
        logger.error("[%s] Market data error: %s", symbol, market_data["error"])
        return

    price = market_data["price"]
    change_pct = market_data["change_24h"] or 0
    logger.info(
        "[%s] $%s (%+.2f%%) | RSI: %s | ADX: %s | Momentum: %s",
        symbol, f"{price:,.2f}", change_pct,
        market_data.get("rsi"), market_data.get("adx"),
        market_data.get("momentum_score"),
    )

    decision = get_claude_decision(market_data, portfolio_state, fear_greed)
    if decision.get("error"):
        logger.error("[%s] Decision error: %s", symbol, decision["error"])
        return

    action = decision["action"].upper()
    logger.info(
        "[%s] %s | Regime: %s | Confidence: %d | Risk: %s",
        symbol, action, decision.get("regime", "?"),
        decision["confidence"], decision["risk_level"],
    )

    # Snapshot latest decision for dashboard (includes HOLDs)
    latest_decisions[symbol] = {
        "symbol": symbol,
        "price": price,
        "change_24h": market_data.get("change_24h"),
        "timestamp": now,
        "action": action,
        "confidence": decision.get("confidence"),
        "risk_level": decision.get("risk_level"),
        "reasoning": decision.get("reasoning"),
        "regime": decision.get("regime"),
        "confluent_signals": decision.get("confluent_signals", []),
        "confluent_count": decision.get("confluent_count", 0),
        "structure_context": decision.get("structure_context"),
        "bull_case": decision.get("bull_case"),
        "bear_case": decision.get("bear_case"),
        "hold_period": decision.get("hold_period"),
        "nearest_support": market_data.get("nearest_support"),
        "nearest_resistance": market_data.get("nearest_resistance"),
    }

    decision["symbol"] = symbol
    decision["current_price"] = price
    decision["atr"] = market_data.get("atr")
    risk = check_risk(decision, portfolio_state)
    if "forced_action" in risk:
        action = risk["forced_action"]
        logger.info("[%s] Forced action override → %s", symbol, action)
    if risk["approved"]:
        logger.info("[%s] Risk check APPROVED: %s", symbol, risk["reason"])
    else:
        logger.info("[%s] Risk check REJECTED: %s", symbol, risk["reason"])

    if risk["approved"] and action in ("LONG", "SHORT", "CLOSE"):
        _execute_trade(symbol, action, price, risk, now, cycle, decision, market_data, portfolio_state)


def _execute_trade(
    symbol: str,
    action: str,
    price: float,
    risk: dict,
    now: str,
    cycle: int,
    decision: dict,
    market_data: dict,
    portfolio_state: dict,
) -> None:
    """Execute a futures paper trade and update portfolio state, trade log, and memory."""
    futures_sym = FUTURES_SYMBOLS.get(symbol, symbol)

    if action in ("LONG", "SHORT"):
        position_usd = risk["position_usd"]
        size = position_usd / price

        # LONG = futures buy, SHORT = futures sell
        cli_action = "buy" if action == "LONG" else "sell"
        trade_result = execute_futures_trade(cli_action, futures_sym, size)
        cli_output = trade_result["raw_output"]
        logger.info("[%s] CLI: %s", symbol, cli_output)

        if not trade_result["success"]:
            logger.error("[%s] Trade execution FAILED — aborting position update.", symbol)
            return

        # Use actual fill price/size if available
        if trade_result.get("fill_price"):
            price = trade_result["fill_price"]
        if trade_result.get("fill_size"):
            size = trade_result["fill_size"]

        # Close existing opposite position first (if any)
        held = portfolio_state["positions"].get(symbol, {})
        held_side = (held.get("side") or "").lower()
        if held_side and held_side != action.lower():
            memory.record_exit(symbol, price)

        portfolio_state["positions"][symbol] = {
            "side": action.lower(),
            "size": size,
            "entry_price": price,
            "unrealized_pnl": 0.0,
        }
        portfolio_state["collateral"] = round(portfolio_state["collateral"] - position_usd, 2)
        memory.record_entry(symbol, market_data, decision)

    elif action == "CLOSE":
        held = portfolio_state["positions"].get(symbol, {})
        held_side = (held.get("side") or "").lower()
        size = held.get("size", 0.0)
        position_usd = size * price

        # CLOSE long = sell, CLOSE short = buy
        cli_action = "sell" if held_side == "long" else "buy"
        trade_result = execute_futures_trade(cli_action, futures_sym, size)
        cli_output = trade_result["raw_output"]
        logger.info("[%s] CLI: %s", symbol, cli_output)

        if not trade_result["success"]:
            logger.error("[%s] Close execution FAILED — position may still be open.", symbol)
            return

        if trade_result.get("fill_price"):
            price = trade_result["fill_price"]
            position_usd = size * price

        portfolio_state["collateral"] = round(portfolio_state["collateral"] + position_usd, 2)
        portfolio_state["positions"].pop(symbol, None)
        memory.record_exit(symbol, price)

    else:
        return

    portfolio_state["trades_today"] += 1
    portfolio_state["trade_log"].append({
        "timestamp": now,
        "cycle": cycle,
        "symbol": symbol,
        "action": action,
        "price": price,
        "volume": size,
        "position_usd": position_usd,
        "confidence": decision.get("confidence"),
        "risk_level": decision.get("risk_level"),
        "reasoning": decision.get("reasoning"),
        "regime": decision.get("regime"),
        "cli_output": cli_output,
        "balance_after": portfolio_state["collateral"],
    })


if __name__ == "__main__":
    import json
    if len(sys.argv) > 1 and sys.argv[1] == "test-balance":
        logger.info("Testing get_kraken_balance()...")
        bal = get_kraken_balance()
        print(json.dumps(bal, indent=2))
        sys.exit(0)
    main()

