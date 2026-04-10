"""Claude-powered trading decision engine.

Builds a rich prompt from live market data, portfolio context, and trade
memory, then calls the Anthropic API for a structured LONG/SHORT/CLOSE/HOLD decision.
"""
import json

import anthropic

from . import memory
from .config import ANTHROPIC_KEY, logger


def get_claude_decision(
    market_data: dict,
    portfolio_state: dict | None = None,
    fear_greed: dict | None = None,
) -> dict:
    """Query Claude for a trading decision.

    Combines market data, portfolio context, fear/greed, and past trade
    memory into a single prompt.
    Returns {"action", "confidence", "reasoning", "risk_level", "error"}.
    """
    if market_data.get("error"):
        return {"error": f"Skipping decision: upstream data error — {market_data['error']}"}

    price = market_data.get("price")
    change_pct = market_data.get("change_24h")
    symbol = market_data.get("symbol", "")
    fg = fear_greed or {}

    def _f(v, fmt=",.2f"):
        return f"{v:{fmt}}" if v is not None else "N/A"

    change_str = _f(change_pct, "+.2f")

    past = memory.get_relevant_memories(symbol)

    user_msg = (
        f"Asset: {symbol} | Price: ${_f(price)} | 24h: {change_str}%\n"
        f"TECHNICALS: RSI {_f(market_data.get('rsi'), '.1f')} | "
        f"MACD hist {_f(market_data.get('macd_histogram'), '.4f')} | "
        f"ADX {_f(market_data.get('adx'), '.1f')} | "
        f"ATR {_f(market_data.get('atr'), '.2f')}\n"
        f"BOLLINGER: Upper {_f(market_data.get('bb_upper'))} | "
        f"Mid {_f(market_data.get('bb_mid'))} | "
        f"Lower {_f(market_data.get('bb_lower'))}\n"
        f"TREND: {market_data.get('cross_signal') or 'none'} | "
        f"Momentum score: {_f(market_data.get('momentum_score'), '.1f')}\n"
        f"STRUCTURE: Support ${_f(market_data.get('nearest_support'))} | "
        f"Resistance ${_f(market_data.get('nearest_resistance'))}\n"
        f"SENTIMENT: Social {_f(market_data.get('sentiment_score'), '.0f')}/100 | "
        f"Fear & Greed {fg.get('score', 'N/A')} ({fg.get('label', 'N/A')})\n"
        f"MEMORY: {past or 'No prior trades for this asset.'}"
    )

    if portfolio_state:
        user_msg += _build_portfolio_context(symbol, price, portfolio_state)

    system_prompt = (
        "You are ARIA, an autonomous crypto futures trading agent. "
        "You trade perpetual futures (can go LONG or SHORT). "
        "You make disciplined trading decisions using a strict 4-step framework. "
        "You only output valid JSON, no markdown, no explanation outside the JSON.\n\n"
        "AVAILABLE DATA:\n"
        "- price, 24h change\n"
        "- RSI, MACD histogram, ADX (trend strength 0-100), ATR (volatility)\n"
        "- Bollinger upper/mid/lower, SMA50, SMA200\n"
        "- Golden/death cross signal, momentum score\n"
        "- Nearest support and resistance levels\n"
        "- Social sentiment score (-100 to +100)\n"
        "- Fear & Greed index (0-100, shared across all assets)\n"
        "- Past trade memory for this asset\n\n"
        "DECISION FRAMEWORK — follow these steps in order:\n\n"
        "STEP 1 — REGIME\n"
        "Classify the current market regime using these rules:\n"
        "- mean_reversion: RSI < 30 or > 70 AND price near a Bollinger Band AND MACD histogram changing direction\n"
        "- momentum: ADX > 25 AND RSI between 45-65 AND price on correct side of SMA50\n"
        "- breakout: momentum_score high AND golden/death cross just fired\n"
        "- risk_off: Fear & Greed < 25 AND social sentiment < -50\n"
        "- unclear: signals conflict or are insufficient — HOLD is the only valid action\n\n"
        "STEP 2 — CONFLUENCE\n"
        "List every signal pointing in the same direction. "
        "If fewer than 1 strong signal or 2 weak signals agree, downgrade confidence and lean toward HOLD.\n"
        "CRITICAL RULE: When price breaks above a resistance level that was previously acting as a ceiling, "
        "this is a HIGH CONVICTION breakout signal on its own. ADX > 40 confirming trend strength makes this a near-certain LONG.\n\n"
        "STEP 3 — STRUCTURE\n"
        "Is price closer to support or resistance? "
        "Never LONG near resistance. Never SHORT near support. "
        "If price is between both and signals are mixed, HOLD.\n\n"
        "STEP 4 — REGIME-ACTION MAPPING\n"
        "Use these rules to select an action:\n"
        "- mean_reversion bullish (RSI < 30, near support) → LONG\n"
        "- mean_reversion bearish (RSI > 70, near resistance) → SHORT\n"
        "- momentum bullish (price above SMA50, ADX rising) → LONG\n"
        "- momentum bearish (price below SMA50, ADX rising) → SHORT\n"
        "- breakout bullish (golden cross) → LONG\n"
        "- breakout bearish (death cross) → SHORT\n"
        "- risk_off → SHORT or CLOSE, never LONG\n"
        "- unclear → HOLD, or CLOSE if holding a losing position\n"
        "- CLOSE: use when holding a position that conflicts with current regime\n\n"
        "STEP 5 — OUTPUT\n"
        "Respond with this exact JSON and nothing else:\n"
        "{\n"
        '  "regime": "mean_reversion|momentum|breakout|risk_off|unclear",\n'
        '  "confluent_signals": ["signal1", "signal2", "signal3"],\n'
        '  "confluent_count": 0,\n'
        '  "structure_context": "near support at $X" or "near resistance at $X" or "mid-range",\n'
        '  "bull_case": "one sentence",\n'
        '  "bear_case": "one sentence",\n'
        '  "action": "LONG|SHORT|CLOSE|HOLD",\n'
        '  "confidence": 0,\n'
        '  "reasoning": "2-3 sentences with specific indicator values",\n'
        '  "risk_level": "LOW|MEDIUM|HIGH",\n'
        '  "hold_period": "short|medium|long"\n'
        "}"
    )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        decision = json.loads(raw)
        decision["error"] = None
        return decision
    except json.JSONDecodeError as exc:
        return {"error": f"Failed to parse Claude response as JSON: {exc}", "raw": raw}
    except anthropic.APIError as exc:
        return {"error": f"Anthropic API error: {exc}"}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _build_portfolio_context(
    symbol: str,
    price: float | None,
    portfolio_state: dict,
) -> str:
    """Build the portfolio context section appended to the Claude prompt."""
    collateral = portfolio_state.get("collateral", 0.0)
    positions = portfolio_state.get("positions", {})
    ps = portfolio_state.get("portfolio_status", {})
    trade_log = portfolio_state.get("trade_log", [])
    trades_today = portfolio_state.get("trades_today", 0)

    lines = [
        "\n\n--- PORTFOLIO CONTEXT ---",
        f"Collateral: ${collateral:,.2f}",
        "Open positions:",
    ]

    if positions:
        for sym, pos in positions.items():
            side = pos.get("side", "?")
            entry_p = pos.get("entry_price", 0.0)
            size = pos.get("size", pos.get("volume", 0.0))
            upnl = pos.get("unrealized_pnl", 0.0)
            if entry_p > 0 and price and sym == symbol:
                if side == "long":
                    upnl_pct = (price - entry_p) / entry_p * 100
                elif side == "short":
                    upnl_pct = (entry_p - price) / entry_p * 100
                else:
                    upnl_pct = 0.0
                lines.append(
                    f"  {sym}: {side.upper()} {size:.6f} @ entry ${entry_p:,.2f} | "
                    f"Current: ${price:,.2f} | Unrealized PnL: {upnl_pct:+.2f}%"
                )
            else:
                lines.append(f"  {sym}: {side.upper()} {size:.6f} @ entry ${entry_p:,.2f}")
    else:
        lines.append("  No open positions.")

    unrealized_pnl = ps.get("unrealized_pnl", 0.0)
    pnl_pct = ps.get("pnl_percent", 0.0)
    sign = "+" if unrealized_pnl >= 0 else ""
    lines.append(
        f"Portfolio PnL (unrealized): {sign}${abs(unrealized_pnl):,.2f} "
        f"({sign}{abs(pnl_pct):.3f}%)"
    )
    lines.append(f"Trades today: {trades_today}/10")

    recent = [t for t in trade_log if t.get("symbol") == symbol][-3:]
    if recent:
        lines.append(f"\n--- RECENT {symbol} DECISIONS ---")
        for t in reversed(recent):
            lines.append(
                f"  [{t.get('timestamp', '?')}] {t.get('action')} "
                f"(confidence: {t.get('confidence')}) — \"{t.get('reasoning', '')}\""
            )

    return "\n".join(lines)
