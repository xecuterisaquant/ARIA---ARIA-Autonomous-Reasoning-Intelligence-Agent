"""Claude-powered trading decision engine.

Builds a rich prompt from live market data, portfolio context, and trade
memory, then calls the Anthropic API for a structured BUY/SELL/HOLD decision.
"""
import json

import anthropic

import memory
from config import ANTHROPIC_KEY, logger


def get_claude_decision(
    market_data: dict,
    portfolio_state: dict | None = None,
) -> dict:
    """Query Claude for a trading decision.

    Combines market data, portfolio context, and past trade memory into a single
    prompt. Returns {"action", "confidence", "reasoning", "risk_level", "error"}.
    """
    if market_data.get("error"):
        return {"error": f"Skipping decision: upstream data error — {market_data['error']}"}

    price = market_data.get("price")
    change_pct = market_data.get("change_24h")
    symbol = market_data.get("symbol", "")

    prompt = (
        f"Asset: {symbol} | "
        f"Price: {'$' + f'{price:,.2f}' if price is not None else 'N/A'} | "
        f"24h Change: {f'{change_pct * 100:+.2f}%' if change_pct is not None else 'N/A'} | "
        f"Signal: {market_data.get('signal')} ({market_data.get('signal_strength')}) | "
        f"RSI: {market_data.get('rsi')} | "
        f"MACD histogram: {market_data.get('macd_histogram')} | "
        f"Bollinger Upper: {market_data.get('bb_upper'):,.2f} | "
        f"Bollinger Lower: {market_data.get('bb_lower'):,.2f}"
    )

    if portfolio_state:
        prompt += _build_portfolio_context(symbol, price, portfolio_state)

    past = memory.get_relevant_memories(symbol)
    if past:
        prompt += f"\n\n--- PAST TRADE MEMORY ---\n{past}"

    response_schema = (
        '{"action": "BUY/SELL/HOLD", "confidence": 0-100, '
        '"reasoning": "2-3 sentence explanation", "risk_level": "LOW/MEDIUM/HIGH"}'
    )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        msg = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=512,
            system=(
                "You are ARIA, an autonomous crypto trading agent. "
                "You analyze market data and make disciplined trading decisions. "
                "You always explain your reasoning concisely before giving a decision."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"{prompt}\n\n"
                    f"Respond ONLY with valid JSON in this exact format:\n{response_schema}"
                ),
            }],
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
    balance_usd = portfolio_state.get("balance_usd", 0.0)
    positions = portfolio_state.get("positions", {})
    ps = portfolio_state.get("portfolio_status", {})
    trade_log = portfolio_state.get("trade_log", [])
    trades_today = portfolio_state.get("trades_today", 0)

    lines = [
        "\n\n--- PORTFOLIO CONTEXT ---",
        f"USD Balance: ${balance_usd:,.2f}",
        "Open positions:",
    ]

    if positions:
        for sym, pos in positions.items():
            avg = pos.get("avg_price", 0.0)
            vol = pos.get("volume", 0.0)
            if avg > 0 and price and sym == symbol:
                upnl_pct = (price - avg) / avg * 100
                lines.append(
                    f"  {sym}: {vol:.6f} units @ avg ${avg:,.2f} | "
                    f"Current: ${price:,.2f} | Unrealized PnL: {upnl_pct:+.2f}%"
                )
            else:
                lines.append(f"  {sym}: {vol:.6f} units @ avg ${avg:,.2f}")
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
