"""ARIA Memory System — logs/memory.json

Tracks full trade lifecycles (entry → exit) so Claude can learn from past
performance on each asset.
"""
import datetime
import json
import os
import uuid

from .config import LOG_DIR

MEMORY_PATH = os.path.join(LOG_DIR, "memory.json")
ARCHIVE_PATH = os.path.join(LOG_DIR, "memory_archive.json")
_MAX_ENTRIES = 100


# ── Persistence helpers ───────────────────────────────────────────────────────

def _load() -> list:
    try:
        with open(MEMORY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(entries: list) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    # Archive old entries if over retention limit
    if len(entries) > _MAX_ENTRIES:
        overflow = entries[:-_MAX_ENTRIES]
        entries = entries[-_MAX_ENTRIES:]
        _archive(overflow)
    tmp = MEMORY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
    os.replace(tmp, MEMORY_PATH)


def _archive(overflow: list) -> None:
    """Append overflow entries to the archive file."""
    existing = []
    try:
        with open(ARCHIVE_PATH, encoding="utf-8") as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    existing.extend(overflow)
    tmp = ARCHIVE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)
    os.replace(tmp, ARCHIVE_PATH)


# ── Public API ────────────────────────────────────────────────────────────────

def record_entry(asset: str, market_data: dict, decision: dict) -> str:
    """Called immediately after a LONG or SHORT is executed.

    Creates a new open memory entry and returns its id.
    """
    action = (decision.get("action") or "").upper()
    position_side = "long" if action == "LONG" else "short"
    entry = {
        "id": str(uuid.uuid4()),
        "asset": asset.upper(),
        "position_side": position_side,
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
    """Called immediately after a CLOSE is executed.

    Finds the most recent open entry for this asset, fills exit fields,
    and calculates outcome (inverted for shorts).
    """
    entries = _load()
    asset = asset.upper()

    target = None
    for e in reversed(entries):
        if e.get("asset") == asset and e.get("exit_price") is None:
            target = e
            break

    if target is None:
        return

    entry_price = target.get("entry_price") or 0.0
    side = target.get("position_side", "long")

    if entry_price:
        if side == "short":
            outcome_pct = (entry_price - exit_price) / entry_price * 100
        else:
            outcome_pct = (exit_price - entry_price) / entry_price * 100
    else:
        outcome_pct = 0.0

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
    """Return the n most recent closed trades for this asset as a formatted string.

    Returns an empty string if no closed memories exist.
    """
    asset = asset.upper()
    closed = [
        e for e in _load()
        if e.get("asset") == asset and e.get("exit_price") is not None
    ]

    if not closed:
        return ""

    lines = []
    for e in reversed(closed[-n:]):
        entry_p = e.get("entry_price") or 0.0
        exit_p = e.get("exit_price") or 0.0
        side = (e.get("position_side") or "long").upper()
        signal = e.get("entry_signal") or "unknown"
        rsi = e.get("entry_rsi")
        rsi_str = f", RSI {rsi:.1f}" if rsi is not None else ""
        outcome_pct = e.get("outcome_pct") or 0.0
        outcome = (e.get("outcome") or "unknown").upper()
        confidence = e.get("entry_confidence") or 0
        justified = e.get("confidence_justified")
        just_str = "" if justified is None else (" (justified)" if justified else " (NOT justified)")
        pct_sign = "+" if outcome_pct >= 0 else ""
        lines.append(
            f"{asset} | {side} at ${entry_p:,.2f} ({signal} signal{rsi_str}) | "
            f"Closed at ${exit_p:,.2f} | {outcome} {pct_sign}{outcome_pct:.2f}% | "
            f"Confidence was {confidence}{just_str}"
        )
    return "\n".join(lines)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
