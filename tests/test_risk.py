"""Tests for src.risk — risk management rules."""
import pytest
from src.risk import check_risk


# ── Helpers ────────────────────────────────────────────────────────────────────

def _decision(action="LONG", confidence=70, risk_level="LOW", symbol="BTC",
              current_price=50000, atr=None):
    d = {
        "action": action,
        "confidence": confidence,
        "risk_level": risk_level,
        "symbol": symbol,
        "current_price": current_price,
    }
    if atr is not None:
        d["atr"] = atr
    return d


def _portfolio(collateral=10000, starting_balance=10000, positions=None,
               trades_today=0):
    return {
        "collateral": collateral,
        "starting_balance": starting_balance,
        "positions": positions or {},
        "trades_today": trades_today,
    }


# ── Pre-rules: ATR-based stop-loss / take-profit ──────────────────────────────

class TestPreRules:
    def test_atr_stop_loss_long(self):
        """Long position loss exceeds 2×ATR → force CLOSE."""
        decision = _decision(action="HOLD", symbol="BTC", current_price=48000, atr=500)
        portfolio = _portfolio(positions={
            "BTC": {"side": "long", "size": 0.1, "entry_price": 50000, "unrealized_pnl": -200}
        })
        result = check_risk(decision, portfolio)
        assert result["approved"] is True
        assert result["forced_action"] == "CLOSE"
        assert "Force-close" in result["reason"]

    def test_atr_stop_loss_short(self):
        """Short position loss exceeds 2×ATR → force CLOSE."""
        decision = _decision(action="HOLD", symbol="BTC", current_price=52000, atr=500)
        portfolio = _portfolio(positions={
            "BTC": {"side": "short", "size": 0.1, "entry_price": 50000, "unrealized_pnl": -200}
        })
        result = check_risk(decision, portfolio)
        assert result["approved"] is True
        assert result["forced_action"] == "CLOSE"

    def test_atr_take_profit_long(self):
        """Long gains ≥ 3×ATR → take profit."""
        decision = _decision(action="HOLD", symbol="BTC", current_price=53500, atr=1000)
        portfolio = _portfolio(positions={
            "BTC": {"side": "long", "size": 0.1, "entry_price": 50000, "unrealized_pnl": 350}
        })
        result = check_risk(decision, portfolio)
        assert result["approved"] is True
        assert result["forced_action"] == "CLOSE"
        assert "Take-profit" in result["reason"]

    def test_atr_take_profit_short(self):
        """Short gains ≥ 3×ATR → take profit."""
        decision = _decision(action="HOLD", symbol="BTC", current_price=47000, atr=1000)
        portfolio = _portfolio(positions={
            "BTC": {"side": "short", "size": 0.1, "entry_price": 50000, "unrealized_pnl": 300}
        })
        result = check_risk(decision, portfolio)
        assert result["approved"] is True
        assert result["forced_action"] == "CLOSE"

    def test_fallback_stop_loss_no_atr(self):
        """Without ATR, fallback 15% stop-loss fires."""
        decision = _decision(action="HOLD", symbol="BTC", current_price=42000)
        portfolio = _portfolio(positions={
            "BTC": {"side": "long", "size": 0.1, "entry_price": 50000, "unrealized_pnl": -800}
        })
        result = check_risk(decision, portfolio)
        assert result["approved"] is True
        assert result["forced_action"] == "CLOSE"

    def test_no_stop_within_threshold(self):
        """Position within ATR threshold → no forced close."""
        decision = _decision(action="HOLD", symbol="BTC", current_price=49500, atr=500)
        portfolio = _portfolio(positions={
            "BTC": {"side": "long", "size": 0.1, "entry_price": 50000, "unrealized_pnl": -50}
        })
        result = check_risk(decision, portfolio)
        assert "forced_action" not in result


# ── Rule 1: Confidence threshold ──────────────────────────────────────────────

class TestConfidence:
    def test_below_45_rejected(self):
        result = check_risk(_decision(confidence=44), _portfolio())
        assert result["approved"] is False
        assert "45" in result["reason"]

    def test_exactly_45_passes(self):
        result = check_risk(_decision(confidence=45), _portfolio())
        assert result["approved"] is True

    def test_high_confidence_passes(self):
        result = check_risk(_decision(confidence=90), _portfolio())
        assert result["approved"] is True


# ── Rule 2: HIGH risk opening ─────────────────────────────────────────────────

class TestHighRisk:
    def test_high_risk_long_rejected(self):
        result = check_risk(_decision(risk_level="HIGH", action="LONG"), _portfolio())
        assert result["approved"] is False
        assert "HIGH risk" in result["reason"]

    def test_high_risk_short_rejected(self):
        result = check_risk(_decision(risk_level="HIGH", action="SHORT"), _portfolio())
        assert result["approved"] is False

    def test_high_risk_close_allowed(self):
        result = check_risk(_decision(risk_level="HIGH", action="CLOSE", symbol="BTC"), _portfolio())
        assert result["approved"] is True

    def test_high_risk_hold_allowed(self):
        result = check_risk(_decision(risk_level="HIGH", action="HOLD"), _portfolio())
        assert result["approved"] is True


# ── Rule 3: duplicate detection ───────────────────────────────────────────────

class TestDuplicate:
    def test_same_direction_long_rejected(self):
        portfolio = _portfolio(positions={
            "BTC": {"side": "long", "size": 0.1, "entry_price": 50000, "unrealized_pnl": 0}
        })
        result = check_risk(_decision(action="LONG", symbol="BTC"), portfolio)
        assert result["approved"] is False
        assert "already long" in result["reason"].lower()

    def test_opposite_direction_allowed(self):
        portfolio = _portfolio(positions={
            "BTC": {"side": "short", "size": 0.005, "entry_price": 50000, "unrealized_pnl": 0}
        })
        result = check_risk(_decision(action="LONG", symbol="BTC"), portfolio)
        assert result["approved"] is True

    def test_no_position_allowed(self):
        result = check_risk(_decision(action="LONG", symbol="BTC"), _portfolio())
        assert result["approved"] is True


# ── Rule 4: daily trade limit ─────────────────────────────────────────────────

class TestDailyLimit:
    def test_at_limit_rejected(self):
        result = check_risk(_decision(), _portfolio(trades_today=10))
        assert result["approved"] is False
        assert "10" in result["reason"]

    def test_below_limit_passes(self):
        result = check_risk(_decision(), _portfolio(trades_today=9))
        assert result["approved"] is True


# ── Rule 5: sizing ────────────────────────────────────────────────────────────

class TestSizing:
    def test_low_risk_10_pct(self):
        result = check_risk(
            _decision(risk_level="LOW"),
            _portfolio(collateral=10000, starting_balance=10000),
        )
        assert result["approved"] is True
        assert result["position_usd"] == 1000.0  # 10% of 10k

    def test_medium_risk_15_pct(self):
        result = check_risk(
            _decision(risk_level="MEDIUM"),
            _portfolio(collateral=10000, starting_balance=10000),
        )
        assert result["approved"] is True
        assert result["position_usd"] == 1500.0  # 15% of 10k

    def test_capped_at_20_pct_starting(self):
        result = check_risk(
            _decision(risk_level="MEDIUM"),
            _portfolio(collateral=20000, starting_balance=10000),
        )
        assert result["approved"] is True
        assert result["position_usd"] == 2000.0  # 20% of 10k starting


# ── Rule 6: total exposure cap ────────────────────────────────────────────────

class TestExposureCap:
    def test_under_cap_allowed(self):
        portfolio = _portfolio(
            starting_balance=10000,
            positions={"ETH": {"side": "long", "size": 0.5, "entry_price": 3000, "unrealized_pnl": 0}},
        )
        result = check_risk(_decision(symbol="BTC"), portfolio)
        assert result["approved"] is True

    def test_over_cap_rejected(self):
        """Existing exposure exceeds 30% cap → new trade rejected."""
        portfolio = _portfolio(
            starting_balance=10000,
            positions={
                "ETH": {"side": "long", "size": 1.1, "entry_price": 3000, "unrealized_pnl": 0},
            },
        )
        result = check_risk(_decision(symbol="BTC"), portfolio)
        assert result["approved"] is False
        assert "exposure cap" in result["reason"].lower()

    def test_partial_fill_when_room(self):
        """Some room under cap → position_usd reduced to fit."""
        portfolio = _portfolio(
            starting_balance=10000,
            positions={
                "ETH": {"side": "long", "size": 0.7, "entry_price": 3000, "unrealized_pnl": 0},
            },  # $2100 exposure, cap = $3000, room = $900
        )
        result = check_risk(_decision(symbol="BTC", risk_level="LOW"), portfolio)
        assert result["approved"] is True
        assert result["position_usd"] == 900.0


# ── HOLD / CLOSE paths ────────────────────────────────────────────────────────

class TestHoldClose:
    def test_hold_approved_zero_usd(self):
        result = check_risk(_decision(action="HOLD"), _portfolio())
        assert result["approved"] is True
        assert result["position_usd"] == 0.0

    def test_close_approved(self):
        result = check_risk(_decision(action="CLOSE", symbol="BTC"), _portfolio())
        assert result["approved"] is True
        assert result["position_usd"] == 0.0
