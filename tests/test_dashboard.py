"""Tests for src.dashboard — Flask web dashboard."""
import json
import os
from unittest.mock import patch
import pytest

from src.dashboard import app


@pytest.fixture
def client(tmp_path):
    """Flask test client with temp files for trades/status."""
    trades_path = str(tmp_path / "trades.json")
    status_path = str(tmp_path / "status.json")

    with open(trades_path, "w") as f:
        json.dump([{"action": "LONG", "symbol": "BTC", "price": 70000}], f)
    with open(status_path, "w") as f:
        json.dump({"total_value": 10200, "unrealized_pnl": 200, "pnl_percent": 2.0}, f)

    with patch("src.dashboard.TRADES_PATH", trades_path), \
         patch("src.dashboard.STATUS_PATH", status_path):
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c


class TestHealthCheck:
    def test_healthz_no_auth(self, client):
        """Health check must work without any token."""
        with patch("src.dashboard.DASHBOARD_TOKEN", "secret"):
            resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"


class TestAuthRequired:
    def test_index_rejected_without_token(self, client):
        with patch("src.dashboard.DASHBOARD_TOKEN", "secret"):
            resp = client.get("/api/trades")
        assert resp.status_code == 401

    def test_accepted_with_query_token(self, client):
        with patch("src.dashboard.DASHBOARD_TOKEN", "secret"):
            resp = client.get("/api/trades?token=secret")
        assert resp.status_code == 200

    def test_accepted_with_header_token(self, client):
        with patch("src.dashboard.DASHBOARD_TOKEN", "secret"):
            resp = client.get("/api/trades", headers={"X-Aria-Token": "secret"})
        assert resp.status_code == 200

    def test_no_auth_when_no_token_set(self, client):
        with patch("src.dashboard.DASHBOARD_TOKEN", ""):
            resp = client.get("/api/trades")
        assert resp.status_code == 200


class TestAPIEndpoints:
    def test_api_trades_returns_json(self, client):
        with patch("src.dashboard.DASHBOARD_TOKEN", ""):
            resp = client.get("/api/trades")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert data[0]["symbol"] == "BTC"

    def test_api_status_returns_json(self, client):
        with patch("src.dashboard.DASHBOARD_TOKEN", ""):
            resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total_value"] == 10200
