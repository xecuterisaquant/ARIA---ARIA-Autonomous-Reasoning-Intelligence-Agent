"""Tests for src.agent — Claude decision engine."""
import json
from unittest.mock import patch, MagicMock
import pytest

from src.agent import get_claude_decision, _build_portfolio_context


# ── _build_portfolio_context ──────────────────────────────────────────────────

class TestBuildPortfolioContext:
    def test_uses_collateral_key(self):
        portfolio = {
            "collateral": 9500.0,
            "starting_balance": 10000.0,
            "positions": {},
            "trades_today": 2,
        }
        ctx = _build_portfolio_context("BTC", 70000.0, portfolio)
        assert "9,500" in ctx or "9500" in ctx
        assert "trades today" in ctx.lower() or "2" in ctx

    def test_shows_positions(self):
        portfolio = {
            "collateral": 9000.0,
            "starting_balance": 10000.0,
            "positions": {
                "BTC": {"side": "long", "size": 0.02, "entry_price": 72000, "unrealized_pnl": 100},
            },
            "trades_today": 1,
        }
        ctx = _build_portfolio_context("BTC", 72500.0, portfolio)
        assert "BTC" in ctx
        assert "long" in ctx.lower() or "LONG" in ctx

    def test_empty_positions(self):
        portfolio = {
            "collateral": 10000.0,
            "starting_balance": 10000.0,
            "positions": {},
            "trades_today": 0,
        }
        ctx = _build_portfolio_context("BTC", 70000.0, portfolio)
        assert "no open" in ctx.lower() or "none" in ctx.lower() or "0" in ctx


# ── get_claude_decision ──────────────────────────────────────────────────────

class TestGetClaudeDecision:
    @patch("src.agent.memory")
    @patch("src.agent.anthropic.Anthropic")
    def test_parses_valid_json_response(self, MockClient, mock_memory):
        mock_memory.get_relevant_memories.return_value = ""
        decision_json = json.dumps({
            "action": "LONG", "confidence": 75, "risk_level": "LOW",
            "reasoning": "Strong bullish setup", "regime": "TRENDING_UP",
            "confluent_signals": ["RSI", "MACD"], "confluent_count": 2,
            "structure_context": "Above support", "bull_case": "Momentum",
            "bear_case": "Overextended", "hold_period": "4-8 hours",
        })
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=decision_json)]
        MockClient.return_value.messages.create.return_value = mock_response

        market_data = {"price": 70000, "change_24h": 2.0, "rsi": 55,
                       "timestamp": "2025-01-01T00:00:00Z"}
        portfolio = {"collateral": 10000, "starting_balance": 10000,
                     "positions": {}, "trades_today": 0}
        fear_greed = {"score": 65, "label": "Greed"}

        result = get_claude_decision(market_data, portfolio, fear_greed)
        assert result["action"] == "LONG"
        assert result["confidence"] == 75

    @patch("src.agent.memory")
    @patch("src.agent.anthropic.Anthropic")
    def test_handles_markdown_fenced_json(self, MockClient, mock_memory):
        """Claude sometimes wraps JSON in ```json ... ``` fences."""
        mock_memory.get_relevant_memories.return_value = ""
        fenced = '```json\n{"action": "HOLD", "confidence": 50, "risk_level": "LOW", "reasoning": "wait"}\n```'
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=fenced)]
        MockClient.return_value.messages.create.return_value = mock_response

        result = get_claude_decision(
            {"price": 70000, "timestamp": "now"}, {"collateral": 10000, "positions": {}, "trades_today": 0}, {}
        )
        assert result["action"] == "HOLD"

    @patch("src.agent.memory")
    @patch("src.agent.anthropic.Anthropic")
    def test_returns_error_on_api_error(self, MockClient, mock_memory):
        import anthropic as _anthropic
        mock_memory.get_relevant_memories.return_value = ""
        MockClient.return_value.messages.create.side_effect = _anthropic.APIError(
            message="API down", request=MagicMock(), body=None,
        )

        result = get_claude_decision(
            {"price": 70000, "timestamp": "now"}, {"collateral": 10000, "positions": {}, "trades_today": 0}, {}
        )
        assert "error" in result
