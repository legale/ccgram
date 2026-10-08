"""Side-effecting transition functions for window_tick.

Applies transitions: topic emoji updates, typing indicators,
dead-window notifications, and screen diffing. Status bubble messages
and provider lookups have been removed.
"""

from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING

import structlog
from telegram.constants import ChatAction
from telegram.error import TelegramError

from .... import window_query
from ....config import config
from ....telegram_client import PTBTelegramClient
from ....thread_router import thread_router
from ....tmux_manager import tmux_manager
from ....window_state_store import window_store
from ...messaging_pipeline.message_queue import (
    clear_tool_msg_ids_for_topic,
)
from ...messaging_pipeline.message_sender import rate_limit_send_message
from ...status.topic_emoji import update_topic_emoji
from ...status.topic_status_diff import update_topic_status_diff
from ..polling_state import (
    lifecycle_strategy,
    terminal_poll_state,
)
from ..polling_types import TickDecision
from .decide import decide_tick
from .observe import _check_vim_insert, _resolve_status, build_context

if TYPE_CHECKING:
    from telegram import Bot
    from ....tmux_manager import TmuxWindow

logger = structlog.get_logger()


# ── Typing throttle ─────────────────────────────────────────────────────


async def _send_typing_throttled(
    bot: "Bot", user_id: int, thread_id: int | None
) -> None:
    if thread_id is None:
        return
    if lifecycle_strategy.is_typing_throttled(user_id, thread_id):
        return
    lifecycle_strategy.record_typing_sent(user_id, thread_id)
    chat_id = thread_router.resolve_chat_id(user_id, thread_id)
    client = PTBTelegramClient(bot)
    with contextlib.suppress(TelegramError):
        await client.send_chat_action(
            chat_id=chat_id,
            message_thread_id=thread_id,
            action=ChatAction.TYPING,
        )


# ── Idle / no-status transitions ────────────────────────────────────────


async def _transition_to_idle(
    bot: "Bot",
    user_id: int,
    window_id: str,
    thread_id: int,
    chat_id: int,
    display: str,
) -> None:
    terminal_poll_state.cancel_startup_timer(window_id)
    client = PTBTelegramClient(bot)
    await update_topic_emoji(client, chat_id, thread_id, "idle", display)
    lifecycle_strategy.clear_autoclose_timer(user_id, thread_id)
    lifecycle_strategy.clear_typing_state(user_id, thread_id)


# ── Dead window notification ─────────────────────────────────────────────


async def _handle_dead_window_notification(
    bot: "Bot", user_id: int, thread_id: int, wid: str
) -> None:
    if lifecycle_strategy.is_dead_notified(user_id, thread_id, wid):
        return
    terminal_poll_state.clear_seen_status(wid)

    clear_tool_msg_ids_for_topic(user_id, thread_id)
    chat_id = thread_router.resolve_chat_id(user_id, thread_id)
    display = thread_router.get_display_name(wid)
    client = PTBTelegramClient(bot)
    await update_topic_emoji(client, chat_id, thread_id, "dead", display)
    lifecycle_strategy.start_autoclose_timer(
        user_id, thread_id, "dead", time.monotonic()
    )

    text = f"Session `{display}` ended."
    await rate_limit_send_message(
        client,
        chat_id,
        text,
        message_thread_id=thread_id,
    )
    lifecycle_strategy.mark_dead_notified(user_id, thread_id, wid)


# ── Decision-application transitions ───────────────────────────────────


async def _apply_active_transition(
    bot: "Bot",
    user_id: int,
    window_id: str,
    thread_id: int | None,
    decision: TickDecision,
    notif_mode: str,
) -> None:
    client = PTBTelegramClient(bot)
    if thread_id is not None:
        chat_id = thread_router.resolve_chat_id(user_id, thread_id)
        display = thread_router.get_display_name(window_id)
        await update_topic_emoji(client, chat_id, thread_id, "active", display)
        lifecycle_strategy.clear_autoclose_timer(user_id, thread_id)


async def _apply_done_transition(
    bot: "Bot",
    user_id: int,
    window_id: str,
    thread_id: int | None,
) -> None:
    if thread_id is None:
        return
    chat_id = thread_router.resolve_chat_id(user_id, thread_id)
    display = thread_router.get_display_name(window_id)
    terminal_poll_state.cancel_startup_timer(window_id)
    client = PTBTelegramClient(bot)
    await update_topic_emoji(client, chat_id, thread_id, "done", display)
    lifecycle_strategy.start_autoclose_timer(
        user_id, thread_id, "done", time.monotonic()
    )
    lifecycle_strategy.clear_typing_state(user_id, thread_id)
    terminal_poll_state.mark_seen_status(window_id)


async def _apply_tick_decision(
    bot: "Bot",
    user_id: int,
    window_id: str,
    thread_id: int | None,
    decision: TickDecision,
    notif_mode: str,
) -> None:
    """Apply the effects dictated by a ``TickDecision``."""
    if decision.show_recovery or decision.transition is None:
        return

    if decision.transition == "active":
        await _apply_active_transition(
            bot, user_id, window_id, thread_id, decision, notif_mode
        )
    elif decision.transition == "idle" and thread_id is not None:
        await _transition_to_idle(
            bot,
            user_id,
            window_id,
            thread_id,
            thread_router.resolve_chat_id(user_id, thread_id),
            thread_router.get_display_name(window_id),
        )
    elif decision.transition == "done":
        await _apply_done_transition(bot, user_id, window_id, thread_id)


# ── Status-update orchestration ─────────────────────────────────────────


async def _update_status(
    bot: "Bot",
    user_id: int,
    window_id: str,
    thread_id: int | None = None,
    *,
    _window: "TmuxWindow | None" = None,
) -> None:
    w = _window or await tmux_manager.find_window_by_id(window_id)
    client = PTBTelegramClient(bot)
    if not w:
        return

    if w.cwd:
        from pathlib import Path
        from ....session import session_manager

        session_manager.set_window_cwd(
            window_id, str(Path(w.cwd).expanduser().resolve())
        )

    pane_text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
    if not pane_text:
        return

    _check_vim_insert(window_id, pane_text, w)
    status = await _resolve_status(window_id, pane_text, w)

    notification_mode = window_query.get_notification_mode(window_id)
    ctx = build_context(window_id, w, status, notification_mode=notification_mode)
    decision = decide_tick(ctx)

    if thread_id is not None and config.topic_status_diff_enabled:
        chat_id = thread_router.resolve_chat_id(user_id, thread_id)
        await update_topic_status_diff(client, chat_id, thread_id, window_id, pane_text)

        sidecar_pane_id = tmux_manager.get_sidecar_pane_id(window_id)
        if isinstance(sidecar_pane_id, str) and sidecar_pane_id:
            sidecar_text = await tmux_manager.capture_pane_by_id(
                sidecar_pane_id, with_ansi=True, window_id=window_id
            )
            if sidecar_text is not None:
                await update_topic_status_diff(
                    client,
                    chat_id,
                    thread_id,
                    window_id,
                    sidecar_text,
                    target="sidecar",
                )
            else:
                tmux_manager.forget_sidecar_pane(window_id)

    await _apply_tick_decision(
        bot,
        user_id,
        window_id,
        thread_id,
        decision,
        notif_mode=ctx.notification_mode,
    )


__all__ = [
    "_apply_active_transition",
    "_apply_done_transition",
    "_apply_tick_decision",
    "_handle_dead_window_notification",
    "_send_typing_throttled",
    "_transition_to_idle",
    "_update_status",
]
