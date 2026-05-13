"""Small helpers for binding a Telegram topic to a tmux window."""

from __future__ import annotations

import structlog
from telegram import CallbackQuery
from telegram.error import TelegramError

from ...telegram_client import TelegramClient
from ...thread_router import ThreadRouter, thread_router
from ..status.topic_emoji import format_topic_name_for_mode

logger = structlog.get_logger()


def bind_topic_to_window(
    query: CallbackQuery,
    user_id: int,
    thread_id: int,
    window_id: str,
    window_name: str,
    *,
    router: ThreadRouter = thread_router,
) -> None:
    router.bind_thread(user_id, thread_id, window_id, window_name=window_name)
    chat = query.message.chat if query.message else None
    if chat and chat.type in ("group", "supergroup"):
        router.set_group_chat_id(user_id, thread_id, chat.id)


async def rename_bound_topic(
    client: TelegramClient,
    user_id: int,
    thread_id: int,
    window_name: str,
    approval_mode: str,
    *,
    router: ThreadRouter = thread_router,
) -> None:
    try:
        await client.edit_forum_topic(
            chat_id=router.resolve_chat_id(user_id, thread_id),
            message_thread_id=thread_id,
            name=format_topic_name_for_mode(window_name, approval_mode),
        )
    except TelegramError as e:
        logger.debug("Failed to rename topic: %s", e)
