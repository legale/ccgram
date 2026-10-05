"""Route Telegram topic text to its authoritative tmux session.

A topic is resolved only from tmux session metadata. Unknown topics require
explicit ``//bind <name>``.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import structlog
from telegram import Message, Update
from telegram.constants import ChatAction

from ...config import config
from ...telegram_client import PTBTelegramClient
from ...thread_router import thread_router
from ...tmux_manager import send_to_window
from ...utils import handle_general_topic_message, is_general_topic
from ..callback_helpers import get_thread_id as _get_thread_id
from ..live.pane_callbacks import apply_pane_rename
from ..messaging_pipeline.message_sender import ack_reaction, safe_reply
from ..sessions_dashboard import apply_session_rename

logger = structlog.get_logger()
if TYPE_CHECKING:
    from telegram import Bot, Chat
    from telegram.ext import ContextTypes

    from ...telegram_client import TelegramClient


async def _handle_unbound_topic(
    user_id: int,
    thread_id: int,
    message: Message,
) -> bool:
    """Resolve a topic from tmux metadata or its strict ``cc_<name>`` session."""
    if thread_router.get_window_for_thread(user_id, thread_id) is not None:
        return False

    # Lazy: topic binding imports the topic lifecycle graph.
    from ..topics.topic_binding import bind_runtime, find_topic_session

    chat = message.chat
    if chat is None:
        return True
    session = await find_topic_session(chat.id, thread_id)
    if session is None:
        await safe_reply(message, "Topic is not bound. Use `//bind <name>`.")
        return True
    bind_runtime(user_id, chat.id, thread_id, session)
    return False


async def _forward_message(
    window_id: str,
    user_id: int,
    thread_id: int,
    text: str,
    client: TelegramClient,
    message: Message,
) -> None:
    """Forward one text message to the bound tmux window."""
    logger.info(
        "topic_text_forward",
        user_id=user_id,
        thread_id=thread_id,
        window_id=window_id,
        message_id=message.message_id,
        text=text,
    )
    await message.chat.send_action(ChatAction.TYPING)  # type: ignore[union-attr]

    # Lazy: periodic tasks imports the polling and messaging orchestration.
    from ..polling.periodic_tasks import send_with_reconcile

    success, error = await send_with_reconcile(
        client,
        user_id,
        thread_id,
        window_id,
        text,
        raw=True,
        send_fn=send_to_window,
    )
    if not success:
        await safe_reply(message, f"❌ {error}")
        return

    await ack_reaction(client, message.chat.id, message.message_id)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a normal Telegram text message."""
    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        if update.message:
            await safe_reply(update.message, "You are not authorized to use this bot.")
        return
    if not update.message or not update.message.text:
        return

    await handle_text_message(update, context)


async def _handle_unnamed_topic(bot: Bot, chat: Chat | None, message: Message) -> None:
    if chat and is_general_topic(message):
        await handle_general_topic_message(bot, message, chat.id)
    else:
        await safe_reply(message, "Use a named topic.")


async def _handle_rename_captures(
    user_data: dict | None,
    thread_id: int | None,
    text: str,
    message: Message,
) -> bool:
    if await apply_pane_rename(user_data, thread_id, text, message):
        return True
    return await apply_session_rename(user_data, thread_id, text, message)


async def handle_text_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Resolve the current topic and forward its text to tmux."""
    user = update.effective_user
    message = update.message
    assert user is not None
    assert message is not None and message.text

    thread_id = _get_thread_id(update)
    client = PTBTelegramClient(context.bot)
    window_id = (
        thread_router.get_window_for_thread(user.id, thread_id)
        if thread_id is not None
        else None
    )
    logger.info(
        "topic_text_received",
        user_id=user.id,
        chat_id=message.chat.id if message.chat else None,
        thread_id=thread_id,
        window_id=window_id,
        message_id=message.message_id,
        text=message.text,
    )
    if window_id and message.chat is not None and thread_id is not None:
        # Lazy: status diff state is needed only for Telegram activity updates.
        from ..status.topic_status_diff import mark_topic_status_activity

        mark_topic_status_activity(
            message.chat.id, thread_id, window_id, message.message_id
        )

    chat = message.chat
    if chat and thread_id is not None:
        thread_router.set_group_chat_id(user.id, thread_id, chat.id)

    if await _handle_rename_captures(
        context.user_data, thread_id, message.text, message
    ):
        return

    if thread_id is None:
        await _handle_unnamed_topic(context.bot, update.effective_chat, message)
        return

    if await _handle_unbound_topic(user.id, thread_id, message):
        return

    window_id = thread_router.get_window_for_thread(user.id, thread_id)
    assert window_id is not None

    if message.text.startswith("!!"):
        command = message.text[2:].strip()
        if not command:
            await safe_reply(message, "Usage: `!!<command>`")
            return
        await _handle_sidecar_command(
            window_id=window_id,
            user_id=user.id,
            thread_id=thread_id,
            command=command,
            client=client,
            message=message,
        )
        return

    await _forward_message(
        window_id,
        user.id,
        thread_id,
        message.text,
        client,
        message,
    )


async def _handle_sidecar_command(
    window_id: str,
    user_id: int,
    thread_id: int,
    command: str,
    client: TelegramClient,
    message: Message,
) -> None:
    """Execute a shell command in the sidecar pane without interrupting main pane."""
    logger.info(
        "sidecar_command_received",
        user_id=user_id,
        thread_id=thread_id,
        window_id=window_id,
        command=command,
    )
    from ...tmux_manager import tmux_manager
    from ..status.topic_status_diff import (
        start_sidecar_diff,
        update_topic_status_diff,
    )

    sidecar_pane_id = await tmux_manager.ensure_sidecar_pane(window_id)
    if not sidecar_pane_id:
        await safe_reply(message, "❌ Failed to create or access sidecar pane.")
        return

    baseline = await tmux_manager.capture_pane_by_id(
        sidecar_pane_id, with_ansi=True, window_id=window_id
    )

    time_str = time.strftime("%H:%M:%S")
    title = f"⚡ Sidecar: {command} ({time_str})"
    assert message.chat is not None
    chat_id = message.chat.id
    start_sidecar_diff(
        chat_id=chat_id,
        thread_id=thread_id,
        window_id=window_id,
        title=title,
        baseline_text=baseline,
    )

    sent = await tmux_manager.send_keys_to_pane(
        sidecar_pane_id,
        command,
        enter=True,
        literal=True,
        window_id=window_id,
    )
    if not sent:
        await safe_reply(
            message, f"❌ Failed to send command to sidecar pane {sidecar_pane_id}."
        )
        return

    await ack_reaction(client, chat_id, message.message_id)

    async def _quick_sidecar_tick() -> None:
        await asyncio.sleep(0.3)
        text = await tmux_manager.capture_pane_by_id(
            sidecar_pane_id, with_ansi=True, window_id=window_id
        )
        if text:
            await update_topic_status_diff(
                client, chat_id, thread_id, window_id, text, target="sidecar"
            )

    asyncio.create_task(_quick_sidecar_tick())

