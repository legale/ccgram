"""Topic lifecycle management for tmux-authoritative topics."""

from __future__ import annotations
import time
from typing import TYPE_CHECKING

import structlog
from telegram import Update
from telegram.error import BadRequest, TelegramError
from ...config import config
from ...session import session_manager
from ...telegram_client import PTBTelegramClient, TelegramClient
from ...thread_router import thread_router
from ...tmux_manager import tmux_manager
from ..cleanup import clear_topic_state
from ..messaging_pipeline.message_sender import is_thread_gone
from .topic_binding import ensure_topic_session, find_topic_session
from ..polling.polling_state import (
    lifecycle_strategy,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes
    from ...tmux_manager import TmuxWindow

logger = structlog.get_logger()


# ── Autoclose timer management ────────────────────────────────────────────


async def check_autoclose_timers(client: TelegramClient) -> None:
    """Close topics whose done/dead timers have expired."""
    all_topics = lifecycle_strategy.iter_topic_states()
    if not all_topics:
        return

    now = time.monotonic()
    expired: list[tuple[int, int]] = []
    for user_id, thread_id, ts in all_topics:
        if ts.autoclose is None:
            continue
        state, entered_at = ts.autoclose
        if state == "done":
            timeout = config.autoclose_done_minutes * 60
        elif state == "dead":
            timeout = config.autoclose_dead_minutes * 60
        else:
            continue
        if timeout > 0 and now - entered_at >= timeout:
            expired.append((user_id, thread_id))

    for user_id, thread_id in expired:
        await _close_expired_topic(client, user_id, thread_id)


async def _close_expired_topic(
    client: TelegramClient, user_id: int, thread_id: int
) -> None:
    """Attempt to close/delete an expired topic and clean up state."""
    chat_id = thread_router.resolve_chat_id(user_id, thread_id)
    window_id = thread_router.get_window_for_thread(user_id, thread_id)
    session = await find_topic_session(chat_id, thread_id)
    removed = False
    try:
        await client.delete_forum_topic(chat_id=chat_id, message_thread_id=thread_id)
        removed = True
    except TelegramError as e:
        if is_thread_gone(e):
            removed = True
        else:
            try:
                await client.close_forum_topic(
                    chat_id=chat_id, message_thread_id=thread_id
                )
                removed = True
            except TelegramError as close_err:
                if is_thread_gone(close_err):
                    removed = True
                else:
                    logger.debug(
                        "autoclose_failed", thread_id=thread_id, error=str(close_err)
                    )
    if not removed:
        return
    if session is not None and not await tmux_manager.kill_session(session.window_name):
        logger.warning("Autoclose: failed to kill %s", session.window_name)
        return

    lifecycle_strategy.clear_autoclose_timer(user_id, thread_id)
    logger.info(
        "auto_removed_topic", chat_id=chat_id, thread_id=thread_id, user_id=user_id
    )
    await clear_topic_state(
        user_id,
        thread_id,
        client=client,
        window_id=window_id,
        window_dead=True,
    )
    thread_router.unbind_thread(user_id, thread_id)


# ── Display name sync / state pruning ─────────────────────────────────────


async def prune_stale_state(live_windows: "list[TmuxWindow]") -> None:
    """Sync display names and prune orphaned state entries."""
    live_ids = {w.window_id for w in live_windows}
    live_pairs = [(w.window_id, w.window_name) for w in live_windows]
    session_manager.sync_display_names(live_pairs)
    session_manager.prune_stale_state(live_ids)


# ------------------------------------------------------------------
# Telegram topic event handlers (moved from bot.py)
# ------------------------------------------------------------------


async def topic_closed_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Close the tmux session bound to a manually closed Telegram topic."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not config.is_user_allowed(user.id) or not chat:
        return

    # Lazy: callback helpers are needed only while handling topic events.
    from ..callback_helpers import get_thread_id

    thread_id = get_thread_id(update)
    if thread_id is None:
        return
    session = await find_topic_session(chat.id, thread_id)
    if session is None:
        return
    if not await tmux_manager.kill_session(session.window_name):
        logger.warning("Failed to close tmux session %s", session.window_name)
        return
    lifecycle_strategy.clear_autoclose_timer(user.id, thread_id)
    await clear_topic_state(
        user.id,
        thread_id,
        client=PTBTelegramClient(context.bot),
        window_id=session.window_id,
        window_dead=True,
    )
    thread_router.unbind_thread(user.id, thread_id)


async def topic_created_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Claim or create the strictly name-matched managed tmux session."""
    user = update.effective_user
    message = update.message
    chat = update.effective_chat
    if not user or not config.is_user_allowed(user.id) or not message or not chat:
        return
    created = message.forum_topic_created
    if not created or not created.name:
        return

    # Lazy: callback helpers are needed only while handling topic events.
    from ..callback_helpers import get_thread_id

    # Lazy: topic name synchronization is needed only on topic creation.
    from ..status.topic_emoji import strip_emoji_prefix, sync_topic_name

    thread_id = get_thread_id(update)
    if thread_id is None:
        return

    client = PTBTelegramClient(context.bot)
    topic_name = strip_emoji_prefix(created.name)
    await sync_topic_name(client, chat.id, thread_id, topic_name)
    _window_id, error = await ensure_topic_session(
        user.id, chat.id, thread_id, topic_name
    )
    if error:
        await client.send_message(chat.id, error, message_thread_id=thread_id)
        return
    logger.info(
        "Bound Telegram topic %r (chat=%d, thread=%d)",
        topic_name,
        chat.id,
        thread_id,
    )


async def topic_edited_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Restore a managed Telegram topic name from its tmux session name."""
    user = update.effective_user
    message = update.message
    chat = update.effective_chat
    if not user or not config.is_user_allowed(user.id) or not message or not chat:
        return
    if not message.forum_topic_edited or not message.forum_topic_edited.name:
        return

    # Lazy: callback helpers are needed only while handling topic events.
    from ..callback_helpers import get_thread_id

    # Lazy: topic name cache is needed only on topic edits.
    from ..status.topic_emoji import update_stored_topic_name

    thread_id = get_thread_id(update)
    if thread_id is None:
        return
    session = await find_topic_session(chat.id, thread_id)
    if session is None:
        return

    name = tmux_manager.topic_name_from_session_name(session.window_name)
    update_stored_topic_name(chat.id, thread_id, name)
    try:
        await PTBTelegramClient(context.bot).edit_forum_topic(
            chat.id, thread_id, name=name
        )
    except BadRequest as e:
        if "topic_not_modified" not in e.message.lower():
            logger.warning("Failed to restore topic %d name: %s", thread_id, e)
