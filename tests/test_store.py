"""Tests for src.store — atomic persistence helpers."""
import json
import os
import pytest

from src.store import save_log, save_status


@pytest.fixture(autouse=True)
def _tmp_log_dir(tmp_path, monkeypatch):
    """Redirect LOG_DIR / LOG_PATH / STATUS_PATH to temp directory."""
    monkeypatch.setattr("src.store.LOG_DIR", str(tmp_path))
    monkeypatch.setattr("src.store.LOG_PATH", str(tmp_path / "trades.json"))
    monkeypatch.setattr("src.store.STATUS_PATH", str(tmp_path / "status.json"))
    yield tmp_path


class TestSaveLog:
    def test_writes_valid_json(self, _tmp_log_dir):
        trades = [{"action": "LONG", "price": 50000}]
        save_log(trades)
        with open(_tmp_log_dir / "trades.json", encoding="utf-8") as f:
            assert json.load(f) == trades

    def test_overwrites_existing(self, _tmp_log_dir):
        save_log([{"a": 1}])
        save_log([{"a": 1}, {"b": 2}])
        with open(_tmp_log_dir / "trades.json", encoding="utf-8") as f:
            assert len(json.load(f)) == 2

    def test_no_temp_file_left(self, _tmp_log_dir):
        save_log([])
        assert not os.path.exists(str(_tmp_log_dir / "trades.json.tmp"))


class TestSaveStatus:
    def test_writes_valid_json(self, _tmp_log_dir):
        status = {"total_value": 10000, "pnl_percent": 1.5}
        save_status(status)
        with open(_tmp_log_dir / "status.json", encoding="utf-8") as f:
            assert json.load(f) == status

    def test_no_temp_file_left(self, _tmp_log_dir):
        save_status({})
        assert not os.path.exists(str(_tmp_log_dir / "status.json.tmp"))
