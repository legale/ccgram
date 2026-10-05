"""Agent session management — the core state hub.

Manages the key mappings:
  Window→Session (window_states): which session_id a window holds (keyed by window_id).
  User→Thread→Window: delegated to ThreadRouter (see thread_router.py).

Responsibilities:
  - Persist/load state to ~/.ccgram/state.json.
  - Resolve window IDs to Session objects.
  - Delegate thread↔window routing to ThreadRouter.
  - Send keystrokes to tmux windows and retrieve message history.

Key class: SessionManager (singleton instantiated as `session_manager`).
Thread routing: delegated to ThreadRouter (see thread_router.py) — no pass-throughs.
"""

import structlog
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import config
from .mailbox import Mailbox
from .state_persistence import StatePersistence
from .tmux_manager import tmux_manager
from .thread_router import ThreadRouter, thread_router
from .user_preferences import (
    UserPreferences,
    install_user_preferences,
    user_preferences,
)
from .window_view import WindowView
from .window_state_store import (
    APPROVAL_MODES,
    BATCH_MODES,
    DEFAULT_APPROVAL_MODE,
    DEFAULT_BATCH_MODE,
    NOTIFICATION_MODES,
    WindowState,
    WindowStateStore,
    install_window_store,
    window_store,
)

logger = structlog.get_logger()


@dataclass
class SessionManager:
    """Manages session state for the agent.

    All internal keys use window_id (e.g. '@0', '@12') for uniqueness.
    Display names (window_name) are stored separately for UI presentation.

    Thread routing (thread_bindings, display names, group_chat_ids) is
    delegated to ThreadRouter — see thread_router.py.

    window_states: window_id -> WindowState (session_id, cwd, window_name)

    User preferences (starred dirs, MRU, read offsets) are delegated to
    UserPreferences — see user_preferences.py.
    """

    # Delegated persistence (not serialized)
    _persistence: StatePersistence = field(default=None, repr=False, init=False)  # type: ignore[assignment]

    @property
    def window_states(self) -> dict[str, WindowState]:
        return window_store.window_states

    # Backward-compat properties for routing data (owned by thread_router)
    @property
    def thread_bindings(self) -> dict[int, dict[int, str]]:
        return thread_router.thread_bindings

    @property
    def group_chat_ids(self) -> dict[str, int]:
        return thread_router.group_chat_ids

    @property
    def window_display_names(self) -> dict[str, str]:
        return thread_router.window_display_names

    def __post_init__(self) -> None:
        self._persistence = StatePersistence(config.state_file, self._serialize_state)
        self._window_store = WindowStateStore(
            schedule_save=self._save_state,
        )
        install_window_store(self._window_store)
        self._thread_router = thread_router
        self._user_preferences = UserPreferences(schedule_save=self._save_state)
        install_user_preferences(self._user_preferences)
        self._load_state()

    def _serialize_state(self) -> dict[str, Any]:
        """Serialize all state to a dict for persistence."""
        result = {}
        if ws_dict := window_store.to_dict():
            result["window_states"] = ws_dict
        result.update(user_preferences.to_dict())
        return result

    def _save_state(self) -> None:
        """Schedule debounced save (0.5s delay, resets on each call)."""
        self._persistence.schedule_save()

    def flush_state(self) -> None:
        """Force immediate save. Call on shutdown."""
        self._persistence.flush()

    def _load_state(self) -> None:
        """Load user preferences and per-window modes during initialization."""
        state = self._persistence.load()
        if not state:
            return

        window_store.from_dict(state.get("window_states", {}))
        user_preferences.from_dict(state)

    def set_display_name(self, window_id: str, window_name: str) -> None:
        """Update display name for a window_id."""
        thread_router.set_display_name(window_id, window_name)
        ws = self.window_states.get(window_id)
        if ws:
            ws.window_name = window_name

    # --- Window state management ---

    def view_window(self, window_id: str) -> WindowView | None:
        """Read-only snapshot of a window's state.

        Returns ``None`` when no state exists for the window. Prefer this
        over ``get_window_state`` for read-only callers — it documents the
        exact fields the caller depends on and insulates them from internal
        WindowState shape changes.
        """
        ws = window_store.window_states.get(window_id)
        if ws is None:
            return None
        return WindowView(
            window_id=window_id,
            cwd=ws.cwd or "",
            approval_mode=ws.approval_mode,
            notification_mode=ws.notification_mode,
            batch_mode=ws.batch_mode,
            tool_call_visibility=ws.tool_call_visibility,
            transcript_path=Path(ws.transcript_path) if ws.transcript_path else None,
            window_name=ws.window_name,
            session_id=ws.session_id,
        )

    @property
    def window_count(self) -> int:
        """Number of tracked windows — use instead of accessing window_states directly."""
        return len(window_store.window_states)

    def iter_window_ids(self) -> list[str]:
        """All tracked window IDs — use instead of accessing window_states.keys() directly."""
        return list(window_store.window_states.keys())

    def set_window_cwd(self, window_id: str, cwd: str) -> None:
        """Set the working directory for a window (runtime memory only)."""
        state = window_store.get_window_state(window_id)
        state.cwd = cwd

    def get_approval_mode(self, window_id: str) -> str:
        """Get approval mode for a window (default: 'normal')."""
        state = self.window_states.get(window_id)
        mode = state.approval_mode if state else DEFAULT_APPROVAL_MODE
        return mode if mode in APPROVAL_MODES else DEFAULT_APPROVAL_MODE

    def set_window_approval_mode(self, window_id: str, mode: str) -> None:
        """Set approval mode for a window."""
        normalized = mode.lower()
        if normalized not in APPROVAL_MODES:
            raise ValueError(f"Invalid approval mode: {mode!r}")
        state = window_store.get_window_state(window_id)
        state.approval_mode = normalized
        self._save_state()

    # --- Notification mode ---

    _NOTIFICATION_MODES = NOTIFICATION_MODES

    def get_notification_mode(self, window_id: str) -> str:
        """Get notification mode for a window (default: 'all')."""
        state = self.window_states.get(window_id)
        return state.notification_mode if state else "all"

    def set_notification_mode(self, window_id: str, mode: str) -> None:
        """Set notification mode for a window."""
        if mode not in self._NOTIFICATION_MODES:
            raise ValueError(f"Invalid notification mode: {mode!r}")
        state = window_store.get_window_state(window_id)
        if state.notification_mode != mode:
            state.notification_mode = mode
            self._save_state()

    def cycle_notification_mode(self, window_id: str) -> str:
        """Cycle notification mode: all → errors_only → muted → all. Returns new mode."""
        current = self.get_notification_mode(window_id)
        modes = self._NOTIFICATION_MODES
        idx = modes.index(current) if current in modes else 0
        new_mode = modes[(idx + 1) % len(modes)]
        self.set_notification_mode(window_id, new_mode)
        return new_mode

    # --- Batch mode ---

    def get_batch_mode(self, window_id: str) -> str:
        """Get batch mode for a window (default: 'batched')."""
        state = self.window_states.get(window_id)
        mode = state.batch_mode if state else DEFAULT_BATCH_MODE
        return mode if mode in BATCH_MODES else DEFAULT_BATCH_MODE

    def set_batch_mode(self, window_id: str, mode: str) -> None:
        """Set batch mode for a window."""
        if mode not in BATCH_MODES:
            raise ValueError(f"Invalid batch mode: {mode!r}")
        state = window_store.get_window_state(window_id)
        if state.batch_mode != mode:
            state.batch_mode = mode
            self._save_state()

    def cycle_batch_mode(self, window_id: str) -> str:
        """Toggle batch mode: batched ↔ verbose. Returns new mode."""
        current = self.get_batch_mode(window_id)
        new_mode = "verbose" if current == "batched" else "batched"
        self.set_batch_mode(window_id, new_mode)
        return new_mode

    # --- Tool-call visibility ---

    def get_tool_call_visibility(self, window_id: str) -> str:
        """Get tool-call visibility for a window (default: 'default')."""
        return window_store.get_tool_call_visibility(window_id)

    def set_tool_call_visibility(self, window_id: str, mode: str) -> None:
        """Set tool-call visibility for a window."""
        window_store.set_tool_call_visibility(window_id, mode)

    def cycle_tool_call_visibility(self, window_id: str) -> str:
        """Cycle tool-call visibility: default → shown → hidden → default. Returns new mode."""
        return window_store.cycle_tool_call_visibility(window_id)


session_manager = SessionManager()
