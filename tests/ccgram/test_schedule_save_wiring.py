"""Tests for required _schedule_save callbacks on persistence singletons.

The state singletons — ``WindowStateStore`` and ``UserPreferences`` — are
constructor-injected. Their ``schedule_save`` callbacks are required arguments,
so a singleton cannot be built without explicit wiring.
"""

from __future__ import annotations

import pytest

from ccgram.user_preferences import UserPreferences
from ccgram.window_state_store import WindowStateStore


class TestWindowStateStoreRequiresCallbacks:
    def test_constructor_requires_schedule_save(self) -> None:
        with pytest.raises(TypeError, match="schedule_save"):
            WindowStateStore()  # type: ignore[call-arg]

    def test_constructor_wires_schedule_save(self) -> None:
        calls: list[int] = []
        store = WindowStateStore(
            schedule_save=lambda: calls.append(1),
        )
        store.set_notification_mode("@1", "muted")
        assert calls == [1]

    def test_constructor_wires_schedule_save_for_mru(self) -> None:
        calls: list[int] = []
        prefs = UserPreferences(schedule_save=lambda: calls.append(1))
        prefs.update_user_mru(100, "/tmp/proj")
        assert calls == [1]

    def test_constructor_wires_schedule_save_for_star(self) -> None:
        calls: list[int] = []
        prefs = UserPreferences(schedule_save=lambda: calls.append(1))
        prefs.toggle_user_star(100, "/tmp/proj")
        assert calls == [1]

    def test_constructor_wires_schedule_save_for_offset(self) -> None:
        calls: list[int] = []
        prefs = UserPreferences(schedule_save=lambda: calls.append(1))
        prefs.update_user_window_offset(100, "@1", 42)
        assert calls == [1]

    def test_from_dict_does_not_trigger_save(self) -> None:
        calls: list[int] = []
        prefs = UserPreferences(schedule_save=lambda: calls.append(1))
        prefs.from_dict(
            {"user_window_offsets": {"100": {"@1": 42}}, "user_dir_favorites": {}}
        )
        assert calls == []


class TestSessionManagerWiresAllSingletons:
    def test_post_init_wires_all_schedule_save_callbacks(self) -> None:
        from ccgram.session import SessionManager
        from ccgram.user_preferences import user_preferences
        from ccgram.window_state_store import get_window_store

        sm = SessionManager()
        for singleton in (
            get_window_store(),
            user_preferences,
        ):
            assert singleton._schedule_save is not None
            singleton._schedule_save()

        del sm

    def test_user_preferences_star_triggers_save(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ccgram.session import SessionManager

        sm = SessionManager()
        saves: list[None] = []
        monkeypatch.setattr(
            sm._persistence, "schedule_save", lambda: saves.append(None)
        )
        sm._user_preferences.toggle_user_star(100, "/tmp/proj")
        assert saves, "UserPreferences.toggle_user_star must trigger a save"
        del sm


class TestGetWindowStore:
    def test_returns_installed_store(self) -> None:
        from ccgram.session import SessionManager
        from ccgram.window_state_store import get_window_store

        sm = SessionManager()
        store = get_window_store()
        assert store is sm._window_store
        del sm


class TestGetThreadRouter:
    def test_returns_installed_router(self) -> None:
        from ccgram.thread_router import get_thread_router, thread_router

        router = get_thread_router()
        assert router is thread_router


class TestGetUserPreferences:
    def test_returns_installed_prefs(self) -> None:
        from ccgram.session import SessionManager
        from ccgram.user_preferences import get_user_preferences

        sm = SessionManager()
        prefs = get_user_preferences()
        assert prefs is sm._user_preferences
        del sm
