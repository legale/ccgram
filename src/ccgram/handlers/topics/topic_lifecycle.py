"""Topic lifecycle management for tmux-authoritative topics."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog
from telegram import Update
from telegram.error import BadRequest, TelegramError

from ...config import config
from ...telegram_client import PTBTelegramClient
from ...thread_router import thread_router
from ...tmux_manager import tmux_manager
from ..cleanup import clear_topic_state
from .topic_binding import (
    _valid_name,
    bind_runtime,
    ensure_topic_session,
    find_topic_session,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = structlog.get_logger()


async def topic_closed_handler(
    update: Update, _context: ContextTypes.DEFAULT_TYPE
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
    await clear_topic_state(user.id, thread_id, window_id=session.window_id)
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

    thread_id = get_thread_id(update)
    if thread_id is None:
        return

    client = PTBTelegramClient(context.bot)
    topic_name = created.name.strip()

    target = tmux_manager.topic_session_name(topic_name)
    _window_id, error = await ensure_topic_session(
        user.id, chat.id, thread_id, topic_name, rebind=True
    )
    if error:
        await client.send_message(chat.id, error, message_thread_id=thread_id)
        return

    switch_proc = await asyncio.create_subprocess_exec(
        "tmux",
        "switch-client",
        "-t",
        target,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await switch_proc.communicate()

    logger.info(
        "Bound Telegram topic %r (chat=%d, thread=%d)",
        topic_name,
        chat.id,
        thread_id,
    )


async def topic_edited_handler(  # noqa: C901, PLR0912
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Rename the bound tmux session after a Telegram topic rename."""
    user = update.effective_user
    message = update.effective_message or update.message
    chat = update.effective_chat
    logger.info(
        "topic_edited_handler_invoked",
        user_id=user.id if user else None,
        chat_id=chat.id if chat else None,
        has_message=bool(message),
        forum_topic_edited=bool(message and message.forum_topic_edited),
        edited_name=message.forum_topic_edited.name
        if message and message.forum_topic_edited
        else None,
    )
    allowed = (user is not None and config.is_user_allowed(user.id)) or (
        chat is not None and config.is_user_allowed(chat.id)
    )
    if not allowed or not message or not chat:
        logger.warning(
            "topic_edited_handler_rejected_user_or_chat",
            user_id=user.id if user else None,
            chat_id=chat.id if chat else None,
            allowed=allowed,
        )
        return
    if not message.forum_topic_edited or not message.forum_topic_edited.name:
        logger.warning(
            "topic_edited_handler_rejected_no_edited_name",
            forum_topic_edited=message.forum_topic_edited.to_dict()
            if message and message.forum_topic_edited
            else None,
        )
        return

    # Lazy: callback helpers are needed only while handling topic events.
    from ..callback_helpers import get_thread_id

    thread_id = get_thread_id(update)
    if thread_id is None:
        thread_id = getattr(message, "message_thread_id", None) or getattr(
            message, "message_id", None
        )
    if thread_id is None:
        logger.warning(
            "topic_edited_handler_rejected_no_thread_id",
            message_dict=message.to_dict() if message else None,
        )
        return

    user_id = user.id if user else chat.id
    session = await find_topic_session(chat.id, thread_id)
    if session is None:
        window_id = thread_router.get_window_for_thread(user_id, thread_id)
        if not window_id:
            window_id = thread_router.get_window_for_chat_thread(chat.id, thread_id)
        if window_id:
            session_name = window_id.split(":", 1)[0]
            sessions = await tmux_manager.list_sessions()
            session = next((s for s in sessions if s.window_name == session_name), None)
    if session is None:
        logger.warning(
            "Topic edited %d name=%r: no bound tmux session found",
            thread_id,
            message.forum_topic_edited.name,
        )
        return

    raw_name = message.forum_topic_edited.name.strip()
    name = raw_name.removeprefix(config.tmux_session_prefix).strip()
    if not _valid_name(name):
        logger.warning("Ignoring invalid renamed topic %d name=%r", thread_id, name)
        return

    target = tmux_manager.topic_session_name(name)
    old_name = session.window_name
    if session.window_name != target:
        if not await tmux_manager.rename_session(session.window_name, target):
            logger.warning(
                "Failed to rename tmux session %s to %s for topic %d",
                session.window_name,
                target,
                thread_id,
            )
            return
        session.window_name = target
        if ":" in session.window_id:
            bare_id = session.window_id.rsplit(":", 1)[1]
            session.window_id = f"{target}:{bare_id}"
        await tmux_manager.set_session_topic(target, chat.id, thread_id)
        session.topic_ref = (chat.id, thread_id)
        bind_runtime(user_id, chat.id, thread_id, session)

    if raw_name != name:
        try:
            await PTBTelegramClient(context.bot).edit_forum_topic(
                chat.id, thread_id, name=name
            )
        except BadRequest as exc:
            if "topic_not_modified" not in exc.message.lower():
                logger.warning(
                    "Failed to remove tmux prefix from topic %d: %s", thread_id, exc
                )
        except TelegramError as exc:
            logger.warning(
                "Failed to remove tmux prefix from topic %d: %s", thread_id, exc
            )
    logger.info(
        "Renamed bound topic session %s -> %s (chat=%d, thread=%d)",
        old_name,
        target,
        chat.id,
        thread_id,
    )
