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
from src.kraken import execute_paper_trade, get_kraken_balance, get_portfolio_status, run_kraken_command
from src.market import get_market_data
from src.agent import get_claude_decision
from src.risk import check_risk
from src.store import save_log, save_status
from src import memory


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
        run_kraken_command(["paper", "balance"])
    except RuntimeError:
        logger.info("Paper account not initialized — running 'kraken paper init'...")
        subprocess.run(["kraken", "paper", "init"], check=False, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # already caught above

    logger.info("Preflight checks passed.")


def main() -> None:
    _preflight()

    # Start the web dashboard in a daemon thread
    try:
        from src import dashboard as _dashboard
        _t = threading.Thread(target=_dashboard.run_dashboard, daemon=True, name="aria-dashboard")
        _t.start()
        logger.info("Dashboard started on port %d", DASHBOARD_PORT)
    except Exception as exc:
        logger.warning("Could not start dashboard: %s", exc)

    portfolio_state: dict = {
        "balance_usd": 10000.0,
        "starting_balance": 10000.0,
        "positions": {},
        "trades_today": 0,
        "last_trade_date": "",
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
                    _process_asset(symbol, now, cycle, portfolio_state)
                except Exception:
                    logger.exception("[%s] Unhandled error — skipping.", symbol)

            save_log(portfolio_state["trade_log"])
            save_status({
                "total_value": ps.get("total_value", portfolio_state["balance_usd"]),
                "unrealized_pnl": ps.get("unrealized_pnl", 0.0),
                "pnl_percent": ps.get("pnl_percent", 0.0),
                "total_trades": ps.get("total_trades", len(portfolio_state["trade_log"])),
                "timestamp": now,
            })
            logger.info("Log saved → %s", LOG_PATH)
            logger.info("Sleeping %ds until next cycle...", LOOP_INTERVAL_SECONDS)

        except Exception:
            logger.exception("Unhandled error in cycle %d — continuing.", cycle)

        time.sleep(LOOP_INTERVAL_SECONDS)


def _process_asset(symbol: str, now: str, cycle: int, portfolio_state: dict) -> None:
    """Run the full decision → risk → execution pipeline for one asset."""
    logger.info("[%s] Processing...", symbol)

    market_data = get_market_data(symbol)
    if market_data.get("error"):
        logger.error("[%s] Market data error: %s", symbol, market_data["error"])
        return

    price = market_data["price"]
    change_pct = (market_data["change_24h"] or 0) * 100
    logger.info(
        "[%s] Price: $%s | 24h: %+.2f%% | Signal: %s (%s)",
        symbol, f"{price:,.2f}", change_pct,
        market_data["signal"], market_data["signal_strength"],
    )

    decision = get_claude_decision(market_data, portfolio_state)
    if decision.get("error"):
        logger.error("[%s] Decision error: %s", symbol, decision["error"])
        return

    action = decision["action"].upper()
    logger.info(
        "[%s] Decision: %s | Confidence: %d | Risk: %s",
        symbol, action, decision["confidence"], decision["risk_level"],
    )

    decision["symbol"] = symbol
    decision["current_price"] = price
    risk = check_risk(decision, portfolio_state)
    if "forced_action" in risk:
        action = risk["forced_action"]
        logger.info("[%s] Forced action override → %s", symbol, action)
    if risk["approved"]:
        logger.info("[%s] Risk check APPROVED: %s", symbol, risk["reason"])
    else:
        logger.info("[%s] Risk check REJECTED: %s", symbol, risk["reason"])

    if risk["approved"] and action in ("BUY", "SELL"):
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
    """Execute a paper trade and update portfolio state, trade log, and memory."""
    if action == "BUY":
        position_usd = risk["position_usd"]
        volume = position_usd / price
    else:
        held = portfolio_state["positions"].get(symbol, {})
        volume = held.get("volume", 0.0)
        position_usd = volume * price

    cli_output = execute_paper_trade(action, symbol, volume)
    logger.info("[%s] CLI: %s", symbol, cli_output)

    if action == "BUY":
        existing = portfolio_state["positions"].get(symbol)
        if existing:
            total_vol = existing["volume"] + volume
            avg_price = (
                (existing["avg_price"] * existing["volume"] + price * volume) / total_vol
            )
            portfolio_state["positions"][symbol] = {"volume": total_vol, "avg_price": avg_price}
        else:
            portfolio_state["positions"][symbol] = {"volume": volume, "avg_price": price}
        portfolio_state["balance_usd"] = round(portfolio_state["balance_usd"] - position_usd, 2)
        memory.record_entry(symbol, market_data, decision)
    else:
        portfolio_state["balance_usd"] = round(portfolio_state["balance_usd"] + position_usd, 2)
        portfolio_state["positions"].pop(symbol, None)
        memory.record_exit(symbol, price)

    portfolio_state["trades_today"] += 1
    portfolio_state["trade_log"].append({
        "timestamp": now,
        "cycle": cycle,
        "symbol": symbol,
        "action": action,
        "price": price,
        "volume": volume,
        "position_usd": position_usd,
        "confidence": decision.get("confidence"),
        "risk_level": decision.get("risk_level"),
        "reasoning": decision.get("reasoning"),
        "cli_output": cli_output,
        "balance_after": portfolio_state["balance_usd"],
    })


if __name__ == "__main__":
    import json
    if len(sys.argv) > 1 and sys.argv[1] == "test-balance":
        logger.info("Testing get_kraken_balance()...")
        bal = get_kraken_balance()
        print(json.dumps(bal, indent=2))
        sys.exit(0)
    main()

