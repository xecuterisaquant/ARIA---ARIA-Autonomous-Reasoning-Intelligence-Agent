"""Tests for src.kraken — Kraken CLI wrapper."""
import json
import subprocess
from unittest.mock import patch, MagicMock
import pytest

from src.kraken import execute_futures_trade, get_kraken_balance, get_portfolio_status, run_kraken_command


# ── run_kraken_command ─────────────────────────────────────────────────────────

class TestRunKrakenCommand:
    @patch("src.kraken.subprocess.run")
    def test_parses_json_output(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"balance": 10000}).encode(),
            stderr=b"",
        )
        result = run_kraken_command(["futures", "paper", "balance"])
        assert result == {"balance": 10000}

    @patch("src.kraken.subprocess.run")
    def test_raises_on_nonzero(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout=b"", stderr=b"error"
        )
        with pytest.raises(RuntimeError, match="kraken exited 1"):
            run_kraken_command(["bad", "command"])


# ── execute_futures_trade ──────────────────────────────────────────────────────

class TestExecuteFuturesTrade:
    def test_size_zero_rejected(self):
        result = execute_futures_trade("buy", "PI_XBTUSD", 0)
        assert result["success"] is False
        assert "Invalid" in result["raw_output"]

    def test_negative_size_rejected(self):
        result = execute_futures_trade("sell", "PI_XBTUSD", -1)
        assert result["success"] is False

    @patch("src.kraken.subprocess.run")
    def test_successful_trade_parsed(self, mock_run):
        cli_output = (
            "╭──────────────────────────────────────╮\n"
            "│ Order ID ┆ abc123\n"
            "│ Status   ┆ filled\n"
            "│ Fill     ┆ 0.02077958 @ 72179.50 (fee: 0.7499)\n"
            "╰──────────────────────────────────────╯"
        )
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=cli_output.encode(),
            stderr=b"",
        )
        result = execute_futures_trade("buy", "PI_XBTUSD", 0.02077958)
        assert result["success"] is True
        assert result["fill_price"] == pytest.approx(72179.50)
        assert result["fill_size"] == pytest.approx(0.02077958)
        assert result["fee"] == pytest.approx(0.7499)

    @patch("src.kraken.subprocess.run")
    def test_cli_failure_returns_not_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout=b"connection error",
            stderr=b"",
        )
        result = execute_futures_trade("buy", "PI_XBTUSD", 0.01)
        assert result["success"] is False

    def test_cli_not_found_returns_mock_message(self):
        """When kraken binary is missing, returns a graceful failure."""
        with patch("src.kraken.subprocess.run", side_effect=FileNotFoundError):
            result = execute_futures_trade("buy", "PI_XBTUSD", 0.01)
        assert result["success"] is False
        assert "not found" in result["raw_output"].lower()

    @patch("src.kraken.subprocess.run", side_effect=subprocess.TimeoutExpired("kraken", 15))
    def test_timeout_returns_error(self, _):
        result = execute_futures_trade("sell", "PI_ETHUSD", 0.5)
        assert result["success"] is False
        assert "timed out" in result["raw_output"].lower()


# ── get_kraken_balance ─────────────────────────────────────────────────────────

class TestGetKrakenBalance:
    @patch("src.kraken.run_kraken_command")
    def test_parses_collateral_and_positions(self, mock_cmd):
        mock_cmd.side_effect = [
            {"collateral": 9500.0},
            {"positions": [
                {"symbol": "PI_XBTUSD", "side": "long", "size": 0.02,
                 "entry_price": 72000, "unrealized_pnl": 50},
            ]},
        ]
        result = get_kraken_balance()
        assert result["collateral"] == 9500.0
        assert "PI_XBTUSD" in result["positions"]
        assert result["positions"]["PI_XBTUSD"]["side"] == "long"

    @patch("src.kraken.run_kraken_command", side_effect=FileNotFoundError)
    def test_cli_missing_returns_error(self, _):
        result = get_kraken_balance()
        assert "error" in result


# ── get_portfolio_status ───────────────────────────────────────────────────────

class TestGetPortfolioStatus:
    @patch("src.kraken.run_kraken_command")
    def test_parses_status(self, mock_cmd):
        mock_cmd.return_value = {
            "total_value": 10200, "unrealized_pnl": 200,
            "pnl_percent": 2.0, "total_trades": 5,
        }
        result = get_portfolio_status()
        assert result["total_value"] == 10200.0
        assert result["error"] is None

    @patch("src.kraken.run_kraken_command", side_effect=RuntimeError("fail"))
    def test_error_returns_fallback(self, _):
        result = get_portfolio_status()
        assert result["error"] is not None
