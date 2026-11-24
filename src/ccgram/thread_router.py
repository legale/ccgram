"""Thread routing — Telegram topic to tmux session binding.

Maps Telegram topics (user_id + thread_id) to tmux sessions (window_id)
in runtime memory. Manages group chat IDs for multi-group forum topic
routing and display names for windows.

Key class: ThreadRouter.

Key data:
  - thread_bindings  (user_id -> {thread_id -> window_id})
  - group_chat_ids   (composite key -> chat_id)
  - window_display_names (window_id -> display name)
"""

from __future__ import annotations

from collections.abc import Iterator

import structlog

logger = structlog.get_logger()


class ThreadRouter:
    """Runtime mapping between Telegram topics and tmux sessions.

    Owns thread_bindings, group_chat_ids, and window_display_names.
    """

    def __init__(self) -> None:
        self.thread_bindings: dict[int, dict[int, str]] = {}
        # "user_id:thread_id" -> chat_id (supports multiple groups per user)
        self.group_chat_ids: dict[str, int] = {}
        # window_id -> display name (window_name)
        self.window_display_names: dict[str, str] = {}

    def reset(self) -> None:
        """Clear all state. Used for test isolation."""
        self.thread_bindings.clear()
        self.group_chat_ids.clear()
        self.window_display_names.clear()

    # ------------------------------------------------------------------
    # Thread binding operations
    # ------------------------------------------------------------------

    def bind_thread(
        self, user_id: int, thread_id: int, window_id: str, window_name: str = ""
    ) -> None:
        """Bind a Telegram topic thread to a tmux session.

        Enforces 1 topic = 1 tmux session: if another thread is already bound to
        the same window_id, that stale binding is removed first.
        """
        if user_id not in self.thread_bindings:
            self.thread_bindings[user_id] = {}

        # Enforce 1:1 — unbind any OTHER thread pointing to this window
        stale = [
            tid
            for tid, wid in self.thread_bindings[user_id].items()
            if wid == window_id and tid != thread_id
        ]
        for tid in stale:
            del self.thread_bindings[user_id][tid]
            logger.info(
                "Evicted stale tg_topic binding: thread %d -> tmux_session %s "
                "(replaced by thread %d)",
                tid,
                window_id,
                thread_id,
            )

        self.thread_bindings[user_id][thread_id] = window_id
        if window_name:
            self.window_display_names[window_id] = window_name
        display = window_name or self.get_display_name(window_id)
        logger.info(
            "Bound tg_topic %d -> tmux_session %s (%s) for user %d",
            thread_id,
            window_id,
            display,
            user_id,
        )

    def unbind_thread(self, user_id: int, thread_id: int) -> str | None:
        """Remove a thread binding. Returns the previously bound tmux_session."""
        bindings = self.thread_bindings.get(user_id)
        if not bindings or thread_id not in bindings:
            return None
        window_id = bindings.pop(thread_id)
        if not bindings:
            del self.thread_bindings[user_id]
        logger.info(
            "Unbound tg_topic %d (was tmux_session %s) for user %d",
            thread_id,
            window_id,
            user_id,
        )

        # Clean up group_chat_id for the unbound thread
        chat_key = f"{user_id}:{thread_id}"
        self.group_chat_ids.pop(chat_key, None)

        # Clean up display name if nothing references this window
        still_bound = any(
            wid == window_id
            for ub in self.thread_bindings.values()
            for wid in ub.values()
        )
        if not still_bound:
            self.window_display_names.pop(window_id, None)

        return window_id

    def get_window_for_thread(self, user_id: int, thread_id: int) -> str | None:
        """Look up the tmux_session bound to a thread."""
        bindings = self.thread_bindings.get(user_id)
        if not bindings:
            return None
        return bindings.get(thread_id)

    def get_all_thread_windows(self, user_id: int) -> dict[int, str]:
        """Get all thread bindings for a user."""
        return dict(self.thread_bindings.get(user_id, {}))

    def resolve_window_for_thread(
        self,
        user_id: int,
        thread_id: int | None,
    ) -> str | None:
        """Resolve the tmux window_id for a user's thread.

        Returns None if thread_id is None or the thread is not bound.
        """
        if thread_id is None:
            return None
        return self.get_window_for_thread(user_id, thread_id)

    def iter_thread_bindings(self) -> Iterator[tuple[int, int, str]]:
        """Iterate all thread bindings as (user_id, thread_id, window_id)."""
        for user_id, bindings in self.thread_bindings.items():
            for thread_id, window_id in bindings.items():
                yield user_id, thread_id, window_id

    # ------------------------------------------------------------------
    # Group chat ID management
    # ------------------------------------------------------------------

    def set_group_chat_id(self, user_id: int, thread_id: int, chat_id: int) -> None:
        """Store the group chat ID for a user's thread.

        Uses composite key ``user_id:thread_id`` to support multiple
        groups per user.
        """
        key = f"{user_id}:{thread_id}"
        if self.group_chat_ids.get(key) != chat_id:
            self.group_chat_ids[key] = chat_id
            logger.info(
                "Stored group chat_id %d for user %d, thread %d",
                chat_id,
                user_id,
                thread_id,
            )

    def resolve_chat_id(self, user_id: int, thread_id: int | None = None) -> int:
        """Resolve the chat_id for sending messages.

        In forum topics (thread_id is set), returns the stored group chat_id
        for that specific thread (user_id:thread_id).
        Falls back to user_id for direct messages or if no group_id stored.
        """
        if thread_id is not None:
            key = f"{user_id}:{thread_id}"
            group_id = self.group_chat_ids.get(key)
            if group_id is not None:
                return group_id
        return user_id

    def get_window_for_chat_thread(self, chat_id: int, thread_id: int) -> str | None:
        """Resolve window_id for a specific Telegram chat/thread pair."""
        for user_id, bindings in self.thread_bindings.items():
            window_id = bindings.get(thread_id)
            if not window_id:
                continue
            key = f"{user_id}:{thread_id}"
            resolved_chat = self.group_chat_ids.get(key, user_id)
            if resolved_chat == chat_id:
                return window_id
        return None

    # ------------------------------------------------------------------
    # Display name management
    # ------------------------------------------------------------------

    def get_display_name(self, window_id: str) -> str:
        """Get display name for a window_id, fallback to window_id itself."""
        return self.window_display_names.get(window_id, window_id)


thread_router: ThreadRouter = ThreadRouter()
