from pathlib import Path

import pytest
import structlog

from ccgram.utils import (
    _throttle_state,
    ccgram_dir,
    log_throttle_reset,
    log_throttle_sweep,
    log_throttled,
)


class TestCcgramDir:
    def test_returns_env_var_path(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CCGRAM_DIR", "/custom/config")
        assert ccgram_dir() == Path("/custom/config")

    def test_returns_default_without_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.delenv("CCGRAM_DIR", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert ccgram_dir() == tmp_path / ".ccgram"


class TestLogThrottled:
    @pytest.fixture(autouse=True)
    def _clean_throttle_state(self):
        _throttle_state.clear()
        yield
        _throttle_state.clear()

    def test_first_call_logs(self):
        log = structlog.get_logger()
        log_throttled(log, "k1", "hello %s", "world", cooldown=60.0)
        assert "k1" in _throttle_state
        assert _throttle_state["k1"][1] == "hello world"

    def test_duplicate_within_cooldown_suppressed(self):
        t = 100.0
        log = structlog.get_logger()
        log_throttled(log, "k1", "msg", _clock=lambda: t, cooldown=60.0)
        _throttle_state["k1"] = (t, "msg")
        calls_before = dict(_throttle_state)
        log_throttled(log, "k1", "msg", _clock=lambda: t + 30, cooldown=60.0)
        assert _throttle_state["k1"] == calls_before["k1"]

    def test_logs_again_after_cooldown(self):
        t = 100.0
        log = structlog.get_logger()
        log_throttled(log, "k1", "msg", _clock=lambda: t, cooldown=60.0)
        old_ts = _throttle_state["k1"][0]
        log_throttled(log, "k1", "msg", _clock=lambda: t + 61, cooldown=60.0)
        assert _throttle_state["k1"][0] != old_ts

    def test_changed_message_logs_immediately(self):
        t = 100.0
        log = structlog.get_logger()
        log_throttled(log, "k1", "msg-a", _clock=lambda: t, cooldown=300.0)
        assert _throttle_state["k1"][1] == "msg-a"
        log_throttled(log, "k1", "msg-b", _clock=lambda: t + 1, cooldown=300.0)
        assert _throttle_state["k1"][1] == "msg-b"

    def test_reset_clears_matching_keys(self):
        _throttle_state["topic-probe:@1"] = (0.0, "err")
        _throttle_state["topic-probe:@2"] = (0.0, "err")
        _throttle_state["status-update:1:2"] = (0.0, "err")
        log_throttle_reset("topic-probe:")
        assert "topic-probe:@1" not in _throttle_state
        assert "topic-probe:@2" not in _throttle_state
        assert "status-update:1:2" in _throttle_state


class TestLogThrottleSweep:
    @pytest.fixture(autouse=True)
    def _clean_throttle_state(self):
        _throttle_state.clear()
        yield
        _throttle_state.clear()

    def test_removes_stale_entries(self):
        _throttle_state["old"] = (100.0, "msg")
        _throttle_state["fresh"] = (800.0, "msg")
        removed = log_throttle_sweep(max_age=600.0, _clock=lambda: 800.0)
        assert removed == 1
        assert "old" not in _throttle_state
        assert "fresh" in _throttle_state

    def test_empty_state_returns_zero(self):
        assert log_throttle_sweep() == 0

    def test_all_fresh_removes_nothing(self):
        _throttle_state["a"] = (100.0, "msg")
        _throttle_state["b"] = (200.0, "msg")
        removed = log_throttle_sweep(max_age=600.0, _clock=lambda: 300.0)
        assert removed == 0
        assert len(_throttle_state) == 2

    def test_all_stale_clears_everything(self):
        _throttle_state["a"] = (0.0, "msg")
        _throttle_state["b"] = (1.0, "msg")
        removed = log_throttle_sweep(max_age=10.0, _clock=lambda: 1000.0)
        assert removed == 2
        assert len(_throttle_state) == 0
