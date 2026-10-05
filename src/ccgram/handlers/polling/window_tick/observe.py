"""Input-gathering layer for window_tick — reads tmux and terminal state.

Gathers inputs into a pure ``TickContext`` that ``decide_tick`` can consume.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ....tmux_manager import has_insert_indicator, notify_vim_insert_seen
from ..polling_state import terminal_poll_state, terminal_screen_buffer
from ..polling_types import StatusUpdate, TickContext, is_shell_prompt
from .decide import build_status_line

if TYPE_CHECKING:
    from ....tmux_manager import TmuxWindow


def _parse_with_pyte(
    window_id: str,
    pane_text: str,
    columns: int = 0,
    rows: int = 0,
) -> StatusUpdate | None:
    return terminal_screen_buffer.parse_with_pyte(window_id, pane_text, columns, rows)


def _check_vim_insert(window_id: str, pane_text: str, w: "TmuxWindow") -> None:
    vim_text = terminal_screen_buffer.get_rendered_text(window_id, pane_text)
    if has_insert_indicator(vim_text):
        notify_vim_insert_seen(w.window_id)


def _get_last_activity_ts(window_id: str) -> float | None:
    _ = window_id
    return None


async def _resolve_status(
    window_id: str, pane_text: str, w: "TmuxWindow"
) -> StatusUpdate | None:
    """Terminal status line parsing is disabled."""
    return None


def build_context(
    window_id: str,
    w: "TmuxWindow",
    status: StatusUpdate | None,
    *,
    notification_mode: str,
) -> TickContext:
    last_activity_ts = _get_last_activity_ts(window_id)
    is_recently_active = terminal_poll_state.is_recently_active(
        window_id, last_activity_ts
    )
    resolved_status_text = build_status_line(status)
    ws = terminal_poll_state.peek_state(window_id)
    return TickContext(
        window_id=window_id,
        resolved_status_text=resolved_status_text,
        is_shell_prompt=is_shell_prompt(w.pane_current_command),
        has_seen_status=terminal_poll_state.check_seen_status(window_id),
        is_recently_active=is_recently_active,
        startup_time=ws.startup_time if ws else None,
        is_dead_window=False,
        supports_hook=False,
        notification_mode=notification_mode,
    )


__all__ = [
    "_check_vim_insert",
    "_get_last_activity_ts",
    "_parse_with_pyte",
    "_resolve_status",
    "build_context",
]
