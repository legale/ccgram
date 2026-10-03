"""Tests for SessionMonitor."""

from unittest.mock import AsyncMock, patch

import pytest

from ccgram.monitor_state import TrackedSession
from ccgram.session_monitor import NewWindowEvent, SessionMonitor


@pytest.fixture
def monitor(tmp_path) -> SessionMonitor:
    return SessionMonitor(
        projects_path=tmp_path / "projects",
        state_file=tmp_path / "monitor_state.json",
    )


class TestSessionMonitorInit:
    def test_init_defaults(self, monitor: SessionMonitor) -> None:
        assert monitor._running is False
        assert monitor._task is None
        assert monitor._file_mtimes == {}
        assert monitor._pending_tools == {}

    async def test_check_for_updates_returns_empty(
        self, monitor: SessionMonitor
    ) -> None:
        res = await monitor.check_for_updates({})
        assert res == []


class TestNewWindowDetection:
    async def test_callback_fires_for_new_window(self, monitor: SessionMonitor) -> None:
        cb = AsyncMock(spec=lambda event: None)
        monitor.set_new_window_callback(cb)
        monitor._last_session_map = {}

        new_map = {"@5": {"session_id": "s1", "cwd": "/proj", "window_name": "proj"}}
        with patch.object(
            monitor,
            "_load_current_session_map",
            spec=True,
            new_callable=AsyncMock,
            return_value=new_map,
        ):
            await monitor._detect_and_cleanup_changes()

        cb.assert_called_once()
        event = cb.call_args[0][0]
        assert isinstance(event, NewWindowEvent)
        assert event.window_id == "@5"
        assert event.session_id == "s1"
        assert event.window_name == "proj"

    async def test_startup_does_not_trigger_callback(
        self, monitor: SessionMonitor
    ) -> None:
        cb = AsyncMock(spec=lambda event: None)
        monitor.set_new_window_callback(cb)

        initial_map = {"@0": {"session_id": "s0", "cwd": "/a", "window_name": "a"}}
        monitor._last_session_map = initial_map

        with patch.object(
            monitor,
            "_load_current_session_map",
            spec=True,
            new_callable=AsyncMock,
            return_value=initial_map,
        ):
            await monitor._detect_and_cleanup_changes()

        cb.assert_not_called()

    async def test_callback_error_does_not_crash(self, monitor: SessionMonitor) -> None:
        cb = AsyncMock(side_effect=RuntimeError("boom"))
        monitor.set_new_window_callback(cb)
        monitor._last_session_map = {}

        new_map = {"@1": {"session_id": "s1", "cwd": "/x", "window_name": "x"}}
        with patch.object(
            monitor,
            "_load_current_session_map",
            spec=True,
            new_callable=AsyncMock,
            return_value=new_map,
        ):
            await monitor._detect_and_cleanup_changes()

        cb.assert_called_once()


class TestActivityTracking:
    def test_get_last_activity_returns_none_for_unknown(
        self, monitor: SessionMonitor
    ) -> None:
        assert monitor.get_last_activity("unknown-session") is None

    def test_record_hook_activity(self, monitor: SessionMonitor) -> None:
        with patch(
            "ccgram.session_lifecycle.session_lifecycle.resolve_session_id",
            return_value="sess-1",
        ):
            monitor.record_hook_activity("@0")
            assert monitor.get_last_activity("sess-1") is not None


class TestStaleSessionCleanup:
    async def test_cleanup_stale_sessions_removes_from_state(
        self, monitor: SessionMonitor
    ) -> None:
        monitor.state.update_session(
            TrackedSession(session_id="stale-sid", file_path="/tmp/f.jsonl")
        )
        assert monitor.state.get_session("stale-sid") is not None

        with patch.object(
            monitor,
            "_load_current_session_map",
            spec=True,
            new_callable=AsyncMock,
            return_value={},
        ):
            await monitor._cleanup_all_stale_sessions()

        assert monitor.state.get_session("stale-sid") is None
