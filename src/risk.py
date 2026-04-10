"""Risk management — evaluates every decision before execution.

Pre-rules (forced exits, bypass confidence/risk checks):
  0a. Unrealized loss ≥ 2× ATR  →  force CLOSE  (fallback: 15% if ATR unavailable)
  0b. Unrealized gain ≥ 3× ATR  →  take-profit CLOSE (fallback: 8%)

Ordered rules (any failure rejects the trade):
  1. Confidence < 45               →  REJECT
  2. HIGH risk + opening position  →  REJECT
  3. Same-direction duplicate      →  REJECT (already LONG and LONG again, etc.)
  4. ≥ 10 trades today             →  REJECT
  5. Collateral sizing cap         →  10% (LOW) / 15% (MEDIUM) of collateral,
                                      capped at 20% of starting balance
  6. Total exposure cap            →  sum of all open ≤ 30% of starting balance
"""
from .config import logger


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

    collateral = portfolio_state.get("collateral", portfolio_state.get("balance_usd", 0.0))
    starting_balance = portfolio_state.get("starting_balance", 10000.0)
    positions = portfolio_state.get("positions", {})
    trades_today = portfolio_state.get("trades_today", 0)

    # --- Pre-rules: forced close on unrealized loss / take-profit ---
    if symbol and current_price > 0:
        held = positions.get(symbol, {})
        entry_price = held.get("entry_price", 0.0) if held else 0.0
        side = (held.get("side") or "").lower() if held else ""
        atr = decision.get("atr")

        if entry_price > 0 and side:
            if side == "long":
                pct_change = (current_price - entry_price) / entry_price
            else:  # short
                pct_change = (entry_price - current_price) / entry_price

            # Dynamic thresholds based on ATR (fallback to fixed %)
            if atr and entry_price > 0:
                stop_pct = -(2 * atr / entry_price)   # 2× ATR stop-loss
                tp_pct = 3 * atr / entry_price          # 3× ATR take-profit
            else:
                stop_pct = -0.15  # fallback: 15% stop
                tp_pct = 0.08    # fallback: 8% take-profit

            # Stop-loss
            if pct_change <= stop_pct:
                return {
                    "approved": True,
                    "reason": (
                        f"Force-close triggered: {symbol} {side} is {pct_change * 100:.2f}% "
                        f"(stop at {stop_pct * 100:.1f}%, entry ${entry_price:,.2f} → current ${current_price:,.2f})."
                    ),
                    "position_usd": 0.0,
                    "forced_action": "CLOSE",
                }
            # Take-profit
            if pct_change >= tp_pct:
                return {
                    "approved": True,
                    "reason": (
                        f"Take-profit triggered: {symbol} {side} is +{pct_change * 100:.2f}% "
                        f"(TP at +{tp_pct * 100:.1f}%, entry ${entry_price:,.2f} → current ${current_price:,.2f})."
                    ),
                    "position_usd": 0.0,
                    "forced_action": "CLOSE",
                }

    # --- Rule 1: low confidence ---
    if confidence < 45:
        return {
            "approved": False,
            "reason": f"Confidence {confidence} is below minimum threshold of 45.",
            "position_usd": None,
        }

    # --- Rule 2: HIGH-risk opening position ---
    if risk_level == "HIGH" and action in ("LONG", "SHORT"):
        return {
            "approved": False,
            "reason": f"HIGH risk {action} rejected — policy prohibits high-risk entries.",
            "position_usd": None,
        }

    # --- Rule 3: same-direction duplicate ---
    if symbol and action in ("LONG", "SHORT"):
        held = positions.get(symbol, {})
        held_side = (held.get("side") or "").lower() if held else ""
        if held_side:
            action_side = "long" if action == "LONG" else "short"
            if held_side == action_side:
                return {
                    "approved": False,
                    "reason": f"{action} rejected — already {held_side} on {symbol}.",
                    "position_usd": None,
                }

    # --- Rule 4: daily trade limit ---
    if trades_today >= 10:
        return {
            "approved": False,
            "reason": f"Daily trade limit reached ({trades_today}/10).",
            "position_usd": None,
        }

    # --- Rule 5: collateral sizing + total exposure cap ---
    max_position = starting_balance * 0.20
    risk_pct = {"LOW": 0.10, "MEDIUM": 0.15}.get(risk_level, 0.10)
    position_usd = min(round(collateral * risk_pct, 2), round(max_position, 2))

    if action in ("LONG", "SHORT"):
        # Calculate total existing exposure
        total_exposure = sum(
            pos.get("size", 0.0) * pos.get("entry_price", 0.0)
            for pos in positions.values()
        )
        max_total_exposure = starting_balance * 0.30
        if total_exposure + position_usd > max_total_exposure:
            allowed = max(0, round(max_total_exposure - total_exposure, 2))
            if allowed < 100:  # minimum viable trade size
                return {
                    "approved": False,
                    "reason": (
                        f"Total exposure cap reached: ${total_exposure:,.2f} open "
                        f"+ ${position_usd:,.2f} new = ${total_exposure + position_usd:,.2f} "
                        f"> 30% cap (${max_total_exposure:,.2f})."
                    ),
                    "position_usd": None,
                }
            position_usd = allowed

    if action == "HOLD":
        return {"approved": True, "reason": "HOLD — no trade executed.", "position_usd": 0.0}

    if action == "CLOSE":
        return {
            "approved": True,
            "reason": f"CLOSE approved. Will close {symbol} position.",
            "position_usd": 0.0,
        }

    # LONG / SHORT
    return {
        "approved": True,
        "reason": (
            f"{action} approved. Allocating ${position_usd:,.2f} collateral "
            f"({risk_pct * 100:.0f}% of collateral, capped at 20% of starting balance)."
        ),
        "position_usd": position_usd,
    }
