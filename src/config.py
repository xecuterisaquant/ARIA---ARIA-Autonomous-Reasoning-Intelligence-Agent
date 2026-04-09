"""Centralised configuration and logging for ARIA.

Import from this module in every other module — never call os.environ directly.
"""
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# ── API ───────────────────────────────────────────────────────────────────────
BASE_URL = "https://api.prismapi.ai"
API_KEY = os.environ.get("PRISM_API_KEY", "").strip()
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

# ── Agent behaviour ───────────────────────────────────────────────────────────
ASSETS = [
    s.strip().upper()
    for s in os.environ.get("ARIA_ASSETS", "BTC,ETH").split(",")
    if s.strip()
]
LOOP_INTERVAL_SECONDS = int(os.environ.get("ARIA_LOOP_INTERVAL", "300"))
DASHBOARD_PORT = int(os.environ.get("PORT", "8080"))

# ── Paths ─────────────────────────────────────────────────────────────────────
# src/config.py lives one level below the repo root — go up twice to get root.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(_ROOT, os.environ.get("ARIA_LOG_DIR", "logs"))
LOG_PATH = os.path.join(LOG_DIR, "trades.json")
ARIA_LOG_FILE = os.path.join(LOG_DIR, "aria.log")
STATUS_PATH = os.path.join(LOG_DIR, "status.json")

os.makedirs(LOG_DIR, exist_ok=True)

# ── Logging ───────────────────────────────────────────────────────────────────
_fmt = logging.Formatter(
    "[ARIA] %(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
_fh = logging.FileHandler(ARIA_LOG_FILE, encoding="utf-8")
_fh.setFormatter(_fmt)
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)

logger = logging.getLogger("aria")
logger.setLevel(logging.INFO)
logger.addHandler(_fh)
logger.addHandler(_sh)
