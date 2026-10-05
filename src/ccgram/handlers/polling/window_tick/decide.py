"""Pure decision kernel for window_tick — no I/O, no side effects.

All inputs flow in via ``TickContext``; output is a ``TickDecision``.
"""

from __future__ import annotations

import time

from ..polling_types import (
    STARTUP_TIMEOUT,
    StatusUpdate,
    TickContext,
    TickDecision,
    is_shell_prompt,
)


def build_status_line(status: StatusUpdate | None) -> str | None:
    """Status lines are disabled in simplified polling model."""
    return None


def decide_tick(ctx: TickContext) -> TickDecision:
    """Pure status/idle transition decision — no I/O, no side effects."""
    if ctx.is_dead_window:
        return TickDecision(show_recovery=True)

    if ctx.is_recently_active:
        return TickDecision(transition="active")

    if ctx.is_shell_prompt:
        return TickDecision(transition="idle")

    if ctx.has_seen_status:
        return TickDecision(transition="idle")

    startup_expired = (
        ctx.startup_time is not None
        and (time.monotonic() - ctx.startup_time) >= STARTUP_TIMEOUT
    )
    if startup_expired:
        return TickDecision(transition="idle")

    return TickDecision(transition="starting")


__all__ = ["build_status_line", "decide_tick", "is_shell_prompt"]
