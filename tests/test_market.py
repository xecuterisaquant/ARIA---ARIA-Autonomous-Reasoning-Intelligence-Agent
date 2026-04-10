"""Tests for src.market — PRISM API wrapper with TTL cache."""
import time
from unittest.mock import patch, MagicMock
import pytest

from src import market


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset the module-level cache before each test."""
    market._cache.clear()
    yield
    market._cache.clear()


def _mock_response(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


class TestCacheGet:
    @patch("src.market.requests.get")
    def test_caches_within_ttl(self, mock_get):
        mock_get.return_value = _mock_response({"value": 42})
        result1 = market._get("/test", ttl=60)
        result2 = market._get("/test", ttl=60)
        assert result1 == {"value": 42}
        assert result2 == {"value": 42}
        assert mock_get.call_count == 1  # only one HTTP call

    @patch("src.market.requests.get")
    def test_cache_expires(self, mock_get):
        mock_get.return_value = _mock_response({"value": 42})
        market._get("/expire-test", ttl=0)  # ttl=0 → always expired
        time.sleep(0.01)
        market._get("/expire-test", ttl=0)
        assert mock_get.call_count == 2

    @patch("src.market.requests.get")
    def test_429_returns_cached_data(self, mock_get):
        """On 429, return the previously cached value."""
        mock_get.return_value = _mock_response({"value": 42})
        market._get("/rate-limited", ttl=300)

        # Force cache expiry by manipulating timestamp
        key = list(market._cache.keys())[0]
        market._cache[key] = (0, {"value": 42})  # timestamp=0 → expired

        mock_get.return_value = _mock_response({}, status_code=429)
        result = market._get("/rate-limited", ttl=300)
        assert result == {"value": 42}

    @patch("src.market.requests.get")
    def test_timeout_returns_cached_data(self, mock_get):
        """On timeout, return the previously cached value."""
        mock_get.return_value = _mock_response({"price": 100})
        market._get("/timeout-test", ttl=300)

        key = list(market._cache.keys())[0]
        market._cache[key] = (0, {"price": 100})

        import requests as req
        mock_get.side_effect = req.exceptions.Timeout("timeout")
        result = market._get("/timeout-test", ttl=300)
        assert result == {"price": 100}


class TestGetMarketData:
    @patch("src.market._get")
    def test_returns_expected_fields(self, mock_inner_get):
        """get_market_data should return a flat dict with all required keys."""
        mock_inner_get.side_effect = [
            {"price_usd": 70000, "change_24h_pct": 2.5},
            {"indicators": {"rsi": 55, "macd_histogram": 500, "adx": 30,
             "atr": 1200, "bb_upper": 72000, "bb_lower": 68000}},
            {"momentum_score": 65, "trend_strength": 70},
            {"score": 60},
            {"nearest_support": 68000, "nearest_resistance": 72000},
        ]
        data = market.get_market_data("BTC")
        assert data["price"] == 70000
        assert data["rsi"] == 55
        assert data["atr"] == 1200
        assert data["nearest_support"] == 68000
        assert data["nearest_resistance"] == 72000
        assert data.get("error") is None

    @patch("src.market._get")
    def test_handles_partial_failures(self, mock_inner_get):
        """If some endpoints fail, fields should be None, not crash."""
        mock_inner_get.side_effect = [
            {"price_usd": 70000, "change_24h_pct": 2.5},
            {},  # empty technical
            {},  # empty trend
            {},  # empty sentiment
            {},  # empty support/resistance
        ]
        data = market.get_market_data("BTC")
        assert data["price"] == 70000
        assert data["rsi"] is None


class TestGetFearGreed:
    @patch("src.market._get")
    def test_returns_score_and_label(self, mock_inner_get):
        mock_inner_get.return_value = {"value": 72, "label": "Greed"}
        result = market.get_fear_greed()
        assert result["score"] == 72
        assert result["label"] == "Greed"

    @patch("src.market._get")
    def test_returns_empty_on_error(self, mock_inner_get):
        mock_inner_get.return_value = {}
        result = market.get_fear_greed()
        assert result.get("score") is None
