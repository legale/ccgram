"""Tests for WindowStateStore — pane management, serialization, helpers."""

from __future__ import annotations

import pytest

from ccgram.session import SessionManager
from ccgram.window_state_store import (
    DEFAULT_PANE_STATE,
    DEFAULT_TOOL_CALL_VISIBILITY,
    TOOL_CALL_VISIBILITY_MODES,
    PaneInfo,
    WindowState,
    WindowStateStore,
    window_store,
)


@pytest.fixture
def store() -> WindowStateStore:
    save_calls: list[int] = []
    s = WindowStateStore(
        schedule_save=lambda: save_calls.append(1),
    )
    s._save_calls = save_calls  # type: ignore[attr-defined]
    return s


class TestPaneInfoSerialization:
    def test_round_trip_full(self) -> None:
        pane = PaneInfo(
            pane_id="%5",
            name="api-gateway",
            provider="claude",
            last_active_ts=1700000000.5,
            state="blocked",
            subscribed=True,
        )
        loaded = PaneInfo.from_dict(pane.to_dict())
        assert loaded == pane

    def test_round_trip_defaults_omits_optional_keys(self) -> None:
        pane = PaneInfo(pane_id="%6")
        d = pane.to_dict()
        assert d == {"pane_id": "%6"}
        loaded = PaneInfo.from_dict(d)
        assert loaded == pane

    def test_invalid_state_falls_back_to_default(self) -> None:
        pane = PaneInfo.from_dict({"pane_id": "%7", "state": "garbage"})
        assert pane.state == DEFAULT_PANE_STATE

    def test_pane_id_filled_from_dict_key_when_missing(self) -> None:
        pane = PaneInfo.from_dict({"name": "build"})
        assert pane.pane_id == ""

    def test_last_active_ts_coerces_to_float(self) -> None:
        pane = PaneInfo.from_dict({"pane_id": "%9", "last_active_ts": 0})
        assert pane.last_active_ts == 0.0


class TestStoreCRUD:
    def test_get_pane_returns_none_for_missing_window(
        self, store: WindowStateStore
    ) -> None:
        assert store.get_pane("@1", "%5") is None

    def test_get_pane_returns_none_for_missing_pane(
        self, store: WindowStateStore
    ) -> None:
        store.get_window_state("@1")
        assert store.get_pane("@1", "%5") is None

    def test_upsert_pane_creates_entry(self, store: WindowStateStore) -> None:
        pane = store.upsert_pane("@1", "%5", provider="claude", state="active")
        assert pane.pane_id == "%5"
        assert pane.provider == "claude"
        assert pane.state == "active"
        assert store.get_pane("@1", "%5") is pane

    def test_upsert_pane_updates_only_provided_fields(
        self, store: WindowStateStore
    ) -> None:
        store.upsert_pane(
            "@1",
            "%5",
            name="orig",
            provider="claude",
            last_active_ts=10.0,
            state="active",
            subscribed=True,
        )
        store.upsert_pane("@1", "%5", state="idle")
        pane = store.get_pane("@1", "%5")
        assert pane is not None
        assert pane.name == "orig"
        assert pane.provider == "claude"
        assert pane.last_active_ts == 10.0
        assert pane.state == "idle"
        assert pane.subscribed is True

    def test_upsert_pane_clears_name_when_explicitly_none(
        self, store: WindowStateStore
    ) -> None:
        store.upsert_pane("@1", "%5", name="api")
        store.upsert_pane("@1", "%5", name=None)
        pane = store.get_pane("@1", "%5")
        assert pane is not None and pane.name is None

    def test_upsert_pane_rejects_invalid_state(self, store: WindowStateStore) -> None:
        with pytest.raises(ValueError):
            store.upsert_pane("@1", "%5", state="garbage")  # type: ignore[arg-type]

    def test_remove_pane_removes_entry(self, store: WindowStateStore) -> None:
        store.upsert_pane("@1", "%5")
        assert store.remove_pane("@1", "%5") is True
        assert store.get_pane("@1", "%5") is None

    def test_remove_pane_returns_false_when_missing(
        self, store: WindowStateStore
    ) -> None:
        assert store.remove_pane("@1", "%5") is False
        store.upsert_pane("@1", "%5")
        assert store.remove_pane("@1", "%99") is False

    def test_legacy_state_without_panes_loads_cleanly(
        self, store: WindowStateStore
    ) -> None:
        legacy = {
            "@1": {
                "session_id": "s",
                "cwd": "/p",
                "window_name": "proj",
            }
        }
        store.from_dict(legacy)
        assert store.window_states["@1"].panes == {}


class TestNotificationMode:
    def test_default_is_all(self, store: WindowStateStore) -> None:
        assert store.get_notification_mode("@1") == "all"

    def test_set_and_get(self, store: WindowStateStore) -> None:
        store.set_notification_mode("@1", "errors_only")
        assert store.get_notification_mode("@1") == "errors_only"

    def test_invalid_mode_raises(self, store: WindowStateStore) -> None:
        with pytest.raises(ValueError):
            store.set_notification_mode("@1", "bad")

    def test_set_same_value_skips_save(self, store: WindowStateStore) -> None:
        store.set_notification_mode("@1", "muted")
        store._save_calls.clear()  # type: ignore[attr-defined]
        store.set_notification_mode("@1", "muted")
        assert store._save_calls == []  # type: ignore[attr-defined]

    def test_cycle_all_to_errors_only(self, store: WindowStateStore) -> None:
        assert store.cycle_notification_mode("@1") == "errors_only"

    def test_cycle_errors_only_to_muted(self, store: WindowStateStore) -> None:
        store.set_notification_mode("@1", "errors_only")
        assert store.cycle_notification_mode("@1") == "muted"

    def test_cycle_muted_to_all(self, store: WindowStateStore) -> None:
        store.set_notification_mode("@1", "muted")
        assert store.cycle_notification_mode("@1") == "all"


class TestApprovalMode:
    def test_default_is_normal(self, store: WindowStateStore) -> None:
        assert store.get_approval_mode("@1") == "normal"

    def test_unknown_window_returns_default(self, store: WindowStateStore) -> None:
        assert store.get_approval_mode("@missing") == "normal"

    def test_set_yolo(self, store: WindowStateStore) -> None:
        store.set_window_approval_mode("@1", "yolo")
        assert store.get_approval_mode("@1") == "yolo"

    def test_case_insensitive(self, store: WindowStateStore) -> None:
        store.set_window_approval_mode("@1", "YOLO")
        assert store.get_approval_mode("@1") == "yolo"

    def test_invalid_raises(self, store: WindowStateStore) -> None:
        with pytest.raises(ValueError):
            store.set_window_approval_mode("@1", "turbo")

    def test_corrupt_stored_value_falls_back_to_default(
        self, store: WindowStateStore
    ) -> None:
        store.get_window_state("@1").approval_mode = "garbage"
        assert store.get_approval_mode("@1") == "normal"


class TestBatchMode:
    def test_default_is_batched(self, store: WindowStateStore) -> None:
        assert store.get_batch_mode("@1") == "batched"

    def test_unknown_window_returns_default(self, store: WindowStateStore) -> None:
        assert store.get_batch_mode("@missing") == "batched"

    def test_set_verbose(self, store: WindowStateStore) -> None:
        store.set_batch_mode("@1", "verbose")
        assert store.get_batch_mode("@1") == "verbose"

    def test_invalid_raises(self, store: WindowStateStore) -> None:
        with pytest.raises(ValueError):
            store.set_batch_mode("@1", "stream")

    def test_set_same_value_skips_save(self, store: WindowStateStore) -> None:
        store.set_batch_mode("@1", "verbose")
        store._save_calls.clear()  # type: ignore[attr-defined]
        store.set_batch_mode("@1", "verbose")
        assert store._save_calls == []  # type: ignore[attr-defined]

    def test_cycle_batched_to_verbose(self, store: WindowStateStore) -> None:
        assert store.cycle_batch_mode("@1") == "verbose"

    def test_cycle_verbose_to_batched(self, store: WindowStateStore) -> None:
        store.set_batch_mode("@1", "verbose")
        assert store.cycle_batch_mode("@1") == "batched"

    def test_corrupt_stored_value_falls_back_to_default(
        self, store: WindowStateStore
    ) -> None:
        store.get_window_state("@1").batch_mode = "garbage"
        assert store.get_batch_mode("@1") == "batched"


