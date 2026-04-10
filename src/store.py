"""Persistence helpers — write trade log and portfolio status to disk.

Both functions operate on the paths defined in config. save_status uses an
atomic write (temp file + os.replace) so the dashboard never reads a partial file.
"""
import json
import os

from .config import LOG_DIR, LOG_PATH, STATUS_PATH


def save_log(trade_log: list) -> None:
    """Atomically overwrite logs/trades.json with the full in-memory trade log."""
    os.makedirs(LOG_DIR, exist_ok=True)
    tmp = LOG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(trade_log, f, indent=2)
    os.replace(tmp, LOG_PATH)


def save_status(status: dict) -> None:
    """Atomically write logs/status.json for the dashboard."""
    os.makedirs(LOG_DIR, exist_ok=True)
    tmp = STATUS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)
    os.replace(tmp, STATUS_PATH)
