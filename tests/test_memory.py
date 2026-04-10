"""Tests for src.memory — trade lifecycle memory system."""
import json
import os
import tempfile
from unittest.mock import patch
import pytest

from src import memory


@pytest.fixture(autouse=True)
def _tmp_memory_files(tmp_path):
    """Redirect memory files to a temp directory for each test."""
    mem_path = str(tmp_path / "memory.json")
    archive_path = str(tmp_path / "memory_archive.json")
    with patch.object(memory, "MEMORY_PATH", mem_path), \
         patch.object(memory, "ARCHIVE_PATH", archive_path), \
         patch.object(memory, "LOG_DIR", str(tmp_path)):
        yield tmp_path


class TestRecordEntry:
    def test_creates_entry_with_fields(self):
        mid = memory.record_entry("BTC", {
            "price": 70000, "signal": "bullish", "rsi": 55,
            "macd_histogram": 500, "timestamp": "2025-01-01T00:00:00Z",
        }, {"action": "LONG", "reasoning": "test", "confidence": 75, "risk_level": "LOW"})

        assert isinstance(mid, str)
        entries = memory._load()
        assert len(entries) == 1
        e = entries[0]
        assert e["asset"] == "BTC"
        assert e["position_side"] == "long"
        assert e["entry_price"] == 70000
        assert e["exit_price"] is None

    def test_short_entry_side(self):
        memory.record_entry("ETH", {"price": 3000}, {"action": "SHORT"})
        entries = memory._load()
        assert entries[0]["position_side"] == "short"


class TestRecordExit:
    def test_fills_exit_fields_long(self):
        memory.record_entry("BTC", {"price": 50000}, {"action": "LONG", "confidence": 80})
        memory.record_exit("BTC", 55000)

        entries = memory._load()
        e = entries[0]
        assert e["exit_price"] == 55000
        assert e["outcome"] == "profit"
        assert e["outcome_pct"] == pytest.approx(10.0, rel=0.01)

    def test_fills_exit_fields_short(self):
        """Short: profit when exit < entry."""
        memory.record_entry("BTC", {"price": 50000}, {"action": "SHORT", "confidence": 60})
        memory.record_exit("BTC", 45000)

        entries = memory._load()
        e = entries[0]
        assert e["outcome"] == "profit"
        assert e["outcome_pct"] == pytest.approx(10.0, rel=0.01)

    def test_short_loss(self):
        """Short: loss when exit > entry."""
        memory.record_entry("ETH", {"price": 3000}, {"action": "SHORT", "confidence": 50})
        memory.record_exit("ETH", 3300)

        entries = memory._load()
        assert entries[0]["outcome"] == "loss"
        assert entries[0]["outcome_pct"] < 0

    def test_breakeven_threshold(self):
        memory.record_entry("BTC", {"price": 50000}, {"action": "LONG", "confidence": 50})
        memory.record_exit("BTC", 50200)  # 0.4% — within ±0.5%

        entries = memory._load()
        assert entries[0]["outcome"] == "breakeven"

    def test_no_open_entry_noop(self):
        """record_exit with no open entry does nothing."""
        memory.record_exit("BTC", 50000)
        assert memory._load() == []

    def test_confidence_justified_true(self):
        """High confidence + profit → justified."""
        memory.record_entry("BTC", {"price": 50000}, {"action": "LONG", "confidence": 80})
        memory.record_exit("BTC", 55000)
        assert memory._load()[0]["confidence_justified"] is True

    def test_confidence_justified_false(self):
        """High confidence + loss → NOT justified."""
        memory.record_entry("BTC", {"price": 50000}, {"action": "LONG", "confidence": 80})
        memory.record_exit("BTC", 40000)
        assert memory._load()[0]["confidence_justified"] is False


class TestArchive:
    def test_overflow_triggers_archive(self, _tmp_memory_files):
        """Adding >100 entries archives the overflow."""
        for i in range(105):
            memory.record_entry("BTC", {"price": 50000 + i}, {"action": "LONG", "confidence": 60})

        entries = memory._load()
        assert len(entries) == 100

        archive_path = str(_tmp_memory_files / "memory_archive.json")
        with open(archive_path, encoding="utf-8") as f:
            archived = json.load(f)
        assert len(archived) == 5


class TestGetRelevantMemories:
    def test_returns_formatted_string(self):
        memory.record_entry("BTC", {"price": 50000, "signal": "bullish", "rsi": 55},
                            {"action": "LONG", "confidence": 70})
        memory.record_exit("BTC", 55000)

        result = memory.get_relevant_memories("BTC", n=5)
        assert "BTC" in result
        assert "LONG" in result
        assert "profit" in result.lower()

    def test_empty_when_no_closed(self):
        memory.record_entry("ETH", {"price": 3000}, {"action": "LONG"})
        assert memory.get_relevant_memories("ETH") == ""

    def test_filters_by_asset(self):
        memory.record_entry("BTC", {"price": 50000}, {"action": "LONG", "confidence": 60})
        memory.record_exit("BTC", 55000)
        memory.record_entry("ETH", {"price": 3000}, {"action": "SHORT", "confidence": 70})
        memory.record_exit("ETH", 2800)

        btc_mem = memory.get_relevant_memories("BTC")
        assert "BTC" in btc_mem
        assert "ETH" not in btc_mem
