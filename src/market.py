"""PRISM API market data fetcher.

Responsible for fetching spot price, 24h change, and technical signal data
for any supported crypto symbol.
"""
import requests

from .config import API_KEY, BASE_URL, logger


def get_market_data(symbol: str) -> dict:
    """Fetch combined market data for a symbol from the PRISM API.

    Calls:
      GET /crypto/price/{symbol}   — spot price, 24h change
      GET /signals/summary         — direction, strength, RSI, MACD histogram,
                                     Bollinger upper/lower

    Returns a dict with keys: symbol, price, change_24h, signal, signal_strength,
    rsi, macd_histogram, bb_upper, bb_lower, error (None on success).
    """
    symbol = symbol.upper()
    headers = {"X-API-Key": API_KEY}
    result: dict = {
        "symbol": symbol,
        "price": None,
        "change_24h": None,
        "signal": None,
        "signal_strength": None,
        "rsi": None,
        "macd_histogram": None,
        "bb_upper": None,
        "bb_lower": None,
        "error": None,
    }

    # --- Price ---
    try:
        resp = requests.get(
            f"{BASE_URL}/crypto/price/{symbol}", headers=headers, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        result["price"] = data.get("price_usd")
        result["change_24h"] = data.get("change_24h_pct")
    except requests.exceptions.HTTPError as exc:
        result["error"] = f"Price endpoint HTTP error: {exc.response.status_code} {exc.response.reason}"
        return result
    except requests.exceptions.RequestException as exc:
        result["error"] = f"Price endpoint request failed: {exc}"
        return result

    # --- Signals ---
    try:
        resp = requests.get(
            f"{BASE_URL}/signals/summary",
            headers=headers,
            params={"symbols": symbol},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("data") or []
        sym_data = next((i for i in items if i.get("symbol", "").upper() == symbol), {})
        result["signal"] = sym_data.get("overall_signal") or sym_data.get("direction")
        result["signal_strength"] = sym_data.get("strength")
        indicators = sym_data.get("indicators") or {}
        result["rsi"] = indicators.get("rsi")
        result["macd_histogram"] = indicators.get("macd_histogram")
        result["bb_upper"] = indicators.get("bollinger_upper")
        result["bb_lower"] = indicators.get("bollinger_lower")
    except requests.exceptions.HTTPError as exc:
        result["error"] = f"Signals endpoint HTTP error: {exc.response.status_code} {exc.response.reason}"
        return result
    except requests.exceptions.RequestException as exc:
        result["error"] = f"Signals endpoint request failed: {exc}"
        return result

    return result
