"""Risk management — evaluates every decision before execution.

Pre-rules (forced exits, bypass confidence/risk checks):
  0a. Price ≤ avg_entry × 0.95  →  stop-loss SELL
  0b. Price ≥ avg_entry × 1.08  →  take-profit SELL

Ordered rules (any failure rejects the trade):
  1. Confidence < 60             →  REJECT
  2. HIGH risk + BUY             →  REJECT
  3. SELL with no open position  →  REJECT
  4. ≥ 10 trades today           →  REJECT
  5. Position sizing cap         →  10% (LOW) / 15% (MEDIUM) of balance,
                                    capped at 20% of starting balance
"""
from config import logger


def check_risk(decision: dict, portfolio_state: dict) -> dict:
    """Apply risk rules to a decision.

    Returns:
      {
        "approved":      bool,
        "reason":        str,
        "position_usd":  float | None,
        "forced_action": str  # only present when stop/take-profit fires
      }
    """
    action = (decision.get("action") or "").upper()
    confidence = decision.get("confidence", 0)
    risk_level = (decision.get("risk_level") or "").upper()
    symbol = decision.get("symbol")
    current_price = decision.get("current_price", 0.0)

    balance_usd = portfolio_state.get("balance_usd", 0.0)
    starting_balance = portfolio_state.get("starting_balance", 10000.0)
    positions = portfolio_state.get("positions", {})
    trades_today = portfolio_state.get("trades_today", 0)

    # --- Pre-rules: stop-loss / take-profit (forced exits) ---
    if symbol and current_price > 0:
        held = positions.get(symbol.upper(), {})
        avg_price = held.get("avg_price", 0.0) if held else 0.0
        if avg_price > 0:
            pct_change = (current_price - avg_price) / avg_price
            if pct_change <= -0.05:
                return {
                    "approved": True,
                    "reason": (
                        f"Stop-loss triggered: {symbol} is {pct_change * 100:.2f}% below avg entry "
                        f"(avg ${avg_price:,.2f} → current ${current_price:,.2f})."
                    ),
                    "position_usd": 0.0,
                    "forced_action": "SELL",
                }
            if pct_change >= 0.08:
                return {
                    "approved": True,
                    "reason": (
                        f"Take-profit triggered: {symbol} is +{pct_change * 100:.2f}% above avg entry "
                        f"(avg ${avg_price:,.2f} → current ${current_price:,.2f})."
                    ),
                    "position_usd": 0.0,
                    "forced_action": "SELL",
                }

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
            "reason": "HIGH risk BUY rejected — policy prohibits high-risk entries.",
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
            "reason": f"Daily trade limit reached ({trades_today}/10).",
            "position_usd": None,
        }

    # --- Rule 5: position sizing ---
    max_position = starting_balance * 0.20
    risk_pct = {"LOW": 0.10, "MEDIUM": 0.15}.get(risk_level, 0.10)
    position_usd = min(round(balance_usd * risk_pct, 2), round(max_position, 2))

    if action == "HOLD":
        return {"approved": True, "reason": "HOLD — no trade executed.", "position_usd": 0.0}

    if action == "SELL":
        return {
            "approved": True,
            "reason": f"SELL approved. Will liquidate {symbol} position.",
            "position_usd": 0.0,
        }

    # BUY
    return {
        "approved": True,
        "reason": (
            f"BUY approved. Allocating ${position_usd:,.2f} "
            f"({risk_pct * 100:.0f}% of balance, capped at 20% of starting balance)."
        ),
        "position_usd": position_usd,
    }
