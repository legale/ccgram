"""Track the latest known Telegram message in each forum topic."""

from __future__ import annotations

from typing import Any

from .topic_state_registry import topic_state

_last_message_ids: dict[tuple[int, int], int] = {}


def record_message(chat_id: int, thread_id: int | None, message_id: int | None) -> None:
    if thread_id is None or message_id is None:
        return
    key = (int(chat_id), int(thread_id))
    prev = _last_message_ids.get(key, 0)
    if int(message_id) > prev:
        _last_message_ids[key] = int(message_id)


def record_telegram_message(message: Any) -> None:
    if message is None:
        return
    message_id = getattr(message, "message_id", None)
    thread_id = getattr(message, "message_thread_id", None)
    chat_id = getattr(message, "chat_id", None)
    if chat_id is None:
        chat = getattr(message, "chat", None)
        chat_id = getattr(chat, "id", None)
    if chat_id is None:
        return
    record_message(int(chat_id), thread_id, message_id)


def is_last(chat_id: int, thread_id: int, message_id: int) -> bool:
    latest = _last_message_ids.get((int(chat_id), int(thread_id)))
    return latest is None or int(message_id) >= latest


@topic_state.register("chat")
def clear_topic_tail_state(chat_id: int, thread_id: int) -> None:
    _last_message_ids.pop((chat_id, thread_id), None)


def reset_topic_tail_state() -> None:
    _last_message_ids.clear()
