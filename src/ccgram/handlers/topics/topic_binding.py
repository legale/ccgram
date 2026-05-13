"""Small helpers for binding a Telegram topic to a tmux window."""

from __future__ import annotations

from telegram import CallbackQuery

from ...thread_router import ThreadRouter, thread_router


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
    _client: object,
    user_id: int,
    thread_id: int,
    window_name: str,
    approval_mode: str,
    *,
    router: ThreadRouter = thread_router,
) -> None:
    _ = user_id, thread_id, window_name, approval_mode, router
