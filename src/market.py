"""PRISM API market data fetcher.

Per-symbol: 5 calls  (price, technicals, trend, sentiment, support-resistance)
Per-cycle:  1 call   (fear-greed)

Includes a TTL cache so repeated calls within the TTL window (default 300s)
return the previous successful response — avoids hammering rate limits.
"""
import time

import requests

from .config import API_KEY, BASE_URL, logger

_HEADERS = {"X-API-Key": API_KEY}
_TIMEOUT = 10

# ── TTL cache (path → (timestamp, data)) ─────────────────────────────────────
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 300  # seconds — technicals rarely change faster than 5 min


def _get(path: str, params: dict | None = None, ttl: int | None = None) -> dict | None:
    """GET helper — returns parsed JSON or None on failure.

    Results are cached for *ttl* seconds (default _CACHE_TTL).
    On 429 / timeout / error, returns the cached value if available.
    """
    cache_key = path
    effective_ttl = ttl if ttl is not None else _CACHE_TTL

    # Serve from cache if fresh enough
    if cache_key in _cache:
        ts, data = _cache[cache_key]
        if time.monotonic() - ts < effective_ttl:
            return data

    url = f"{BASE_URL}{path}"
    try:
        r = requests.get(url, headers=_HEADERS, params=params, timeout=_TIMEOUT)
        if r.status_code == 429:
            logger.warning("PRISM %s rate-limited (429) — using cached data", path)
            return _cache.get(cache_key, (0, None))[1]
        if r.status_code != 200:
            logger.warning("PRISM %s returned %d: %s", path, r.status_code, r.text[:200])
            return _cache.get(cache_key, (0, None))[1]
        result = r.json()
        _cache[cache_key] = (time.monotonic(), result)
        return result
    except requests.exceptions.RequestException as exc:
        logger.warning("PRISM %s request failed: %s — using cached data", path, exc)
        return _cache.get(cache_key, (0, None))[1]


# ── Per-symbol (4 calls) ──────────────────────────────────────────────

def get_market_data(symbol: str) -> dict:
    """Return a flat dict of market data for *symbol*.

    Calls (in order):
      1. GET /crypto/price/{symbol}
      2. GET /technicals/{symbol}
      3. GET /technicals/{symbol}/trend
      4. GET /social/{symbol}/sentiment
      5. GET /technicals/{symbol}/support-resistance
    """
    symbol = symbol.upper()
    out: dict = {"symbol": symbol, "error": None}

    # 1 ── Price (hard-fail, short TTL) ──────────────────────────────
    p = _get(f"/crypto/price/{symbol}", ttl=30)
    if p is None:
        out["error"] = f"Price endpoint failed for {symbol}"
        return out
    out["price"] = p.get("price_usd")
    out["change_24h"] = p.get("change_24h_pct")

    # 2 ── Technicals ─────────────────────────────────────────────────
    t = _get(f"/technicals/{symbol}") or {}
    ind = t.get("indicators") or t
    out["rsi"] = ind.get("rsi")
    out["macd_histogram"] = ind.get("macd_histogram")
    out["adx"] = ind.get("adx")
    out["atr"] = ind.get("atr")
    out["bb_upper"] = ind.get("bb_upper") or ind.get("bollinger_upper")
    out["bb_lower"] = ind.get("bb_lower") or ind.get("bollinger_lower")
    out["bb_mid"] = ind.get("bb_middle") or ind.get("bollinger_middle") or ind.get("bb_mid")
    out["sma_50"] = ind.get("sma_50") or ind.get("sma50")
    out["sma_200"] = ind.get("sma_200") or ind.get("sma200")

    # 3 ── Trend ──────────────────────────────────────────────────────
    tr = _get(f"/technicals/{symbol}/trend") or {}
    if tr.get("golden_cross"):
        out["cross_signal"] = "golden"
    elif tr.get("death_cross"):
        out["cross_signal"] = "death"
    else:
        out["cross_signal"] = None
    out["adx_strength"] = tr.get("trend_strength")
    out["momentum_score"] = tr.get("momentum_score")
    # Backfill SMA from trend if technicals missed
    if out["sma_50"] is None:
        out["sma_50"] = tr.get("sma_50")
    if out["sma_200"] is None:
        out["sma_200"] = tr.get("sma_200")

    # 4 ── Social sentiment ───────────────────────────────────────────
    sent = _get(f"/social/{symbol}/sentiment") or {}
    out["sentiment_score"] = sent.get("score") or sent.get("sentiment")

    # 5 ── Support / Resistance ───────────────────────────────────────
    sr = _get(f"/technicals/{symbol}/support-resistance") or {}
    out["nearest_support"] = sr.get("nearest_support")
    out["nearest_resistance"] = sr.get("nearest_resistance")

    return out


# ── Once per cycle (1 call) ───────────────────────────────────────────

def get_fear_greed() -> dict:
    """GET /market/fear-greed → {"score": int, "label": str}."""
    fg = _get("/market/fear-greed") or {}
    return {"score": fg.get("value"), "label": fg.get("label")}
