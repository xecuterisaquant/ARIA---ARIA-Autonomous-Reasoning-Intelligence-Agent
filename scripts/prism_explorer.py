"""PRISM API Explorer — systematically probes every endpoint for BTC and ETH.

Prints the HTTP status and full JSON response (truncated to 500 chars) for
each endpoint. Designed for discovery only — no trading logic here.

Usage (from repo root):
    python scripts/prism_explorer.py

Free-tier rate limit: 10 requests/minute.
This script spaces requests 7 seconds apart (~8.6 req/min) and prints an ETA
upfront so you know not to interrupt it.
"""
import json
import pathlib
import sys
import time

# Ensure the repo root is on sys.path so `src` is importable.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import requests
from src.config import API_KEY, BASE_URL

SYMBOLS = ["BTC", "ETH"]
TRUNCATE = 500
REQUEST_DELAY_SECONDS = 7  # keeps us at ~8.6 req/min, safely under the 10/min cap

# ── Endpoint definitions ──────────────────────────────────────────────────────
# Each entry: (label, url_template, optional query params dict)
ENDPOINTS: list[tuple[str, str, dict]] = [
    ("Price",               "{base}/crypto/price/{symbol}",               {}),
    ("OHLC",                "{base}/crypto/{symbol}/ohlc",                {}),
    ("Volume",              "{base}/crypto/{symbol}/volume",              {}),
    ("Signals Summary",     "{base}/signals/summary",                     {"symbols": "{symbol}"}),
    ("Signals Momentum",    "{base}/signals/{symbol}/momentum",           {}),
    ("Signals Breakout",    "{base}/signals/{symbol}/breakout",           {}),
    ("Signals Divergence",  "{base}/signals/{symbol}/divergence",         {}),
    ("Risk",                "{base}/risk/{symbol}",                       {}),
    ("Orderbook Imbalance", "{base}/crypto/{symbol}/orderbook/imbalance", {}),
    ("Resolve",             "{base}/resolve/{symbol}",                    {}),
]

TOTAL_REQUESTS = len(ENDPOINTS) * len(SYMBOLS)


def _probe(label: str, url_template: str, params: dict, symbol: str) -> None:
    url = url_template.format(base=BASE_URL, symbol=symbol)
    resolved_params = {k: v.format(symbol=symbol) for k, v in params.items()}

    try:
        resp = requests.get(
            url,
            headers={"X-API-Key": API_KEY},
            params=resolved_params or None,
            timeout=10,
        )
        status = resp.status_code
        if status == 404:
            body = "NOT AVAILABLE (404)"
        else:
            try:
                raw = json.dumps(resp.json(), separators=(",", ":"))
            except ValueError:
                raw = resp.text.strip() or "(empty body)"
            body = raw[:TRUNCATE] + ("…" if len(raw) > TRUNCATE else "")
    except requests.exceptions.Timeout:
        status = "TIMEOUT"
        body = "NOT AVAILABLE (request timed out)"
    except requests.exceptions.RequestException as exc:
        status = "ERROR"
        body = f"NOT AVAILABLE ({exc})"

    print(f"  [{status}] {label}")
    print(f"         {body}")
    print()


def main() -> None:
    if not API_KEY:
        print("ERROR: PRISM_API_KEY is not set. Check your .env file.")
        sys.exit(1)

    eta = TOTAL_REQUESTS * REQUEST_DELAY_SECONDS
    print(f"Probing {TOTAL_REQUESTS} endpoints ({len(SYMBOLS)} symbols × {len(ENDPOINTS)} endpoints)")
    print(f"Delay: {REQUEST_DELAY_SECONDS}s between requests  |  ETA: ~{eta}s")
    print()

    req_num = 0
    for symbol in SYMBOLS:
        print("=" * 72)
        print(f"  SYMBOL: {symbol}")
        print("=" * 72)
        for label, url_template, params in ENDPOINTS:
            req_num += 1
            print(f"[{req_num}/{TOTAL_REQUESTS}] ", end="", flush=True)
            _probe(label, url_template, params, symbol)
            if req_num < TOTAL_REQUESTS:
                time.sleep(REQUEST_DELAY_SECONDS)

    print("=" * 72)
    print("Done.")


if __name__ == "__main__":
    main()
