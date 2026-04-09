"""
ARIA Memory System — logs/memory.json

Tracks full trade lifecycles (entry → exit) so Claude can learn from past
performance on each asset.
"""
import json
import os
import uuid

from config import LOG_DIR
MEMORY_PATH = os.path.join(LOG_DIR, "memory.json")


# ─── Persistence helpers ──────────────────────────────────────────────────────

def _load() -> list:
    try:
        with open(MEMORY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(entries: list) -> None:
    os.makedirs(_LOG_DIR, exist_ok=True)
    tmp = MEMORY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
    os.replace(tmp, MEMORY_PATH)


# ─── Public API ───────────────────────────────────────────────────────────────

def record_entry(asset: str, market_data: dict, decision: dict) -> str:
    """
    Called immediately after a BUY is executed.
    Creates a new open memory entry and returns its id.
    """
    entry = {
        "id": str(uuid.uuid4()),
        "asset": asset.upper(),
        "entry_price": market_data.get("price"),
        "entry_signal": market_data.get("signal"),
        "entry_rsi": market_data.get("rsi"),
        "entry_macd_histogram": market_data.get("macd_histogram"),
        "entry_reasoning": decision.get("reasoning"),
        "entry_confidence": decision.get("confidence"),
        "entry_risk_level": decision.get("risk_level"),
        "entry_time": market_data.get("timestamp") or _utcnow(),
        "exit_price": None,
        "exit_time": None,
        "outcome_pct": None,
        "outcome": None,
        "confidence_justified": None,
    }
    entries = _load()
    entries.append(entry)
    _save(entries)
    return entry["id"]


def record_exit(asset: str, exit_price: float) -> None:
    """
    Called immediately after a SELL is executed.
    Finds the most recent open entry for this asset, fills exit fields,
    and calculates outcome.
    """
    entries = _load()
    asset = asset.upper()

    # Find the latest open entry for this asset (exit_price is None)
    target = None
    for e in reversed(entries):
        if e.get("asset") == asset and e.get("exit_price") is None:
            target = e
            break

    if target is None:
        return  # No open entry to close — nothing to do

    entry_price = target.get("entry_price") or 0.0
    outcome_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price else 0.0

    if abs(outcome_pct) <= 0.5:
        outcome = "breakeven"
    elif outcome_pct > 0:
        outcome = "profit"
    else:
        outcome = "loss"

    confidence = target.get("entry_confidence") or 0
    confidence_justified = (
        (confidence > 70 and outcome == "profit")
        or (confidence < 60 and outcome == "loss")
    )

    target["exit_price"] = exit_price
    target["exit_time"] = _utcnow()
    target["outcome_pct"] = round(outcome_pct, 4)
    target["outcome"] = outcome
    target["confidence_justified"] = confidence_justified

    _save(entries)


def get_relevant_memories(asset: str, n: int = 5) -> str:
    """
    Returns the n most recent *closed* trades for this asset as a
    formatted multi-line string for inclusion in the Claude prompt.

    Returns an empty string if no closed memories exist.
    """
    asset = asset.upper()
    entries = _load()

    closed = [
        e for e in entries
        if e.get("asset") == asset and e.get("exit_price") is not None
    ]
    recent = closed[-n:]

    if not recent:
        return ""

    lines = []
    for e in reversed(recent):
        entry_p = e.get("entry_price") or 0.0
        exit_p = e.get("exit_price") or 0.0
        signal = e.get("entry_signal") or "unknown"
        rsi = e.get("entry_rsi")
        rsi_str = f", RSI {rsi:.1f}" if rsi is not None else ""
        outcome_pct = e.get("outcome_pct") or 0.0
        outcome = (e.get("outcome") or "unknown").upper()
        confidence = e.get("entry_confidence") or 0
        justified = e.get("confidence_justified")
        if justified is None:
            just_str = ""
        else:
            just_str = " (justified)" if justified else " (NOT justified)"
        pct_sign = "+" if outcome_pct >= 0 else ""
        lines.append(
            f"{asset} | Bought at ${entry_p:,.2f} ({signal} signal{rsi_str}) | "
            f"Sold at ${exit_p:,.2f} | {outcome} {pct_sign}{outcome_pct:.2f}% | "
            f"Confidence was {confidence}{just_str}"
        )
    return "\n".join(lines)


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _utcnow() -> str:
    import datetime
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
