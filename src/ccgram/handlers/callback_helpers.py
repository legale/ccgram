"""Shared helpers for callback handler modules.

Provides utility functions used by multiple callback handler modules:
  - user_owns_window: Check if a user has any thread binding to a window
  - get_bound_window: Resolve a user's topic binding
  - get_thread_id: Extract thread_id from a Telegram update
"""

from telegram import Update

from ..thread_router import thread_router


def user_owns_window(user_id: int, window_id: str) -> bool:
    """Check if a user has any thread binding to the given window."""
    return window_id in thread_router.get_all_thread_windows(user_id).values()


def get_bound_window(user_id: int, thread_id: int) -> str | None:
    """Return the window bound to a user's topic, if any."""
    return thread_router.get_window_for_thread(user_id, thread_id)


def get_window_display_name(window_id: str) -> str:
    """Return the display name for a bound window."""
    return thread_router.get_display_name(window_id)


def parse_target(target: str) -> tuple[str, str | None]:
    """Parse window_id and optional pane_id from callback target string.

    Target format: ``@0`` (window only) or ``@0:%3`` (window + pane).
    """
    if ":%" in target:
        idx = target.index(":%")
        return target[:idx], target[idx + 1 :]
    return target, None


def get_thread_id(update: Update) -> int | None:
    """Extract thread_id from an update, returning None if not in a named topic."""
    msg = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if msg is None:
        return None
    tid = getattr(msg, "message_thread_id", None)
    if tid is None or tid == 1:
        return None
    return tid
