"""Integration tests for SessionManager state persistence round-trips.

Tests save → reload → verify cycles using real file I/O,
ensuring state.json serialization is correct across restarts.
Only user preferences and window mode settings are persisted.
"""

from pathlib import Path

import pytest

from ccgram.session import SessionManager
from ccgram.user_preferences import user_preferences
from ccgram.window_state_store import window_store

pytestmark = pytest.mark.integration


@pytest.fixture
def make_session_manager(tmp_path, monkeypatch):
    """Factory: create a SessionManager with isolated state files."""

    def _make(state_file: Path | None = None) -> SessionManager:
        sf = state_file or (tmp_path / "state.json")
        monkeypatch.setattr("ccgram.config.config.state_file", sf)
        return SessionManager()

    return _make


@pytest.mark.parametrize(
    "setup_fn, check_fn",
    [
        pytest.param(
            lambda sm: user_preferences.update_user_window_offset(
                user_id=1, window_id="@0", offset=12345
            ),
            lambda sm: user_preferences.get_user_window_offset(1, "@0") == 12345,
            id="user-offsets",
        ),
        pytest.param(
            lambda sm: (
                user_preferences.toggle_user_star(user_id=1, path="/tmp/starred-proj"),
                user_preferences.update_user_mru(user_id=1, path="/tmp/recent-proj"),
            ),
            lambda sm: (
                any("starred-proj" in s for s in user_preferences.get_user_starred(1))
                and any("recent-proj" in s for s in user_preferences.get_user_mru(1))
            ),
            id="directory-favorites",
        ),
        pytest.param(
            lambda sm: sm.set_notification_mode("@0", "muted"),
            lambda sm: sm.get_notification_mode("@0") == "muted",
            id="notification-mode",
        ),
        pytest.param(
            lambda sm: sm.set_window_approval_mode("@0", "yolo"),
            lambda sm: sm.get_approval_mode("@0") == "yolo",
            id="approval-mode",
        ),
    ],
)
async def test_persist_reload(make_session_manager, setup_fn, check_fn) -> None:
    sm1 = make_session_manager()
    setup_fn(sm1)
    sm1.flush_state()

    sm2 = make_session_manager()
    assert check_fn(sm2)


async def test_window_state_survives_reload(make_session_manager) -> None:
    sm1 = make_session_manager()
    state = window_store.get_window_state("@5")
    state.session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    state.cwd = "/tmp/myproject"
    sm1.set_notification_mode("@5", "errors_only")
    sm1.set_window_approval_mode("@5", "yolo")
    sm1.flush_state()

    _sm2 = make_session_manager()  # reload triggers __post_init__ -> _load_state
    reloaded = window_store.get_window_state("@5")
    # Lifecycle and runtime attributes are NOT persisted
    assert reloaded.session_id == ""
    assert reloaded.cwd == ""
    # User mode preferences ARE persisted
    assert reloaded.notification_mode == "errors_only"
    assert reloaded.approval_mode == "yolo"
