"""Shared test helpers for ccgram unit tests."""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from telegram import (
    InputMedia,
    Message,
)

from ccgram.topic_tail import record_telegram_message


@dataclass
class _FakeCall:
    """Single recorded call on ``FakeTelegramClient``."""

    method: str
    kwargs: dict[str, Any]


_DEFAULT_RETURNS: dict[str, Any] = {
    "send_chat_action": True,
    "edit_forum_topic": True,
    "close_forum_topic": True,
    "reopen_forum_topic": True,
    "delete_forum_topic": True,
}


@dataclass
class FakeTelegramClient:
    """Recording fake for tests."""

    calls: list[_FakeCall] = field(default_factory=list)
    returns: dict[str, Any] = field(default_factory=dict)

    def _record(self, method: str, kwargs: dict[str, Any]) -> Any:
        self.calls.append(_FakeCall(method=method, kwargs=dict(kwargs)))
        if method in self.returns:
            spec = self.returns[method]
            if inspect.isfunction(spec) or inspect.ismethod(spec):
                return spec(**kwargs)
            return spec
        return _DEFAULT_RETURNS.get(method, True)

    def call_count(self, method: str) -> int:
        return sum(1 for c in self.calls if c.method == method)

    def last_call(self, method: str) -> _FakeCall | None:
        for call in reversed(self.calls):
            if call.method == method:
                return call
        return None

    def set_side_effect(self, method: str, effects: Sequence[Any]) -> None:
        iterator = iter(effects)

        def step(**_kwargs: Any) -> Any:
            value = next(iterator)
            if isinstance(value, BaseException):
                raise value
            return value

        self.returns[method] = step

    async def send_message(
        self, chat_id: int | str, text: str, **kwargs: Any
    ) -> Message:
        msg = self._record("send_message", {"chat_id": chat_id, "text": text, **kwargs})
        record_telegram_message(msg)
        return msg

    async def edit_message_text(
        self,
        text: str,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool:
        return self._record(
            "edit_message_text",
            {"text": text, "chat_id": chat_id, "message_id": message_id, **kwargs},
        )

    async def edit_message_reply_markup(
        self,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        inline_message_id: str | None = None,
        reply_markup: Any = None,
        **kwargs: Any,
    ) -> Message | bool:
        return self._record(
            "edit_message_reply_markup",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "inline_message_id": inline_message_id,
                "reply_markup": reply_markup,
                **kwargs,
            },
        )

    async def edit_message_media(
        self,
        media: InputMedia,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool:
        return self._record(
            "edit_message_media",
            {"media": media, "chat_id": chat_id, "message_id": message_id, **kwargs},
        )

    async def edit_message_caption(
        self,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool:
        return self._record(
            "edit_message_caption",
            {"chat_id": chat_id, "message_id": message_id, **kwargs},
        )

    async def send_photo(
        self, chat_id: int | str, photo: Any, **kwargs: Any
    ) -> Message:
        return self._record(
            "send_photo", {"chat_id": chat_id, "photo": photo, **kwargs}
        )

    async def send_document(
        self, chat_id: int | str, document: Any, **kwargs: Any
    ) -> Message:
        return self._record(
            "send_document", {"chat_id": chat_id, "document": document, **kwargs}
        )

    async def send_chat_action(
        self, chat_id: int | str, action: str, **kwargs: Any
    ) -> bool:
        return self._record(
            "send_chat_action", {"chat_id": chat_id, "action": action, **kwargs}
        )

    async def edit_forum_topic(
        self, chat_id: int | str, message_thread_id: int, **kwargs: Any
    ) -> bool:
        return self._record(
            "edit_forum_topic",
            {"chat_id": chat_id, "message_thread_id": message_thread_id, **kwargs},
        )

    async def close_forum_topic(
        self, chat_id: int | str, message_thread_id: int, **kwargs: Any
    ) -> bool:
        return self._record(
            "close_forum_topic",
            {"chat_id": chat_id, "message_thread_id": message_thread_id, **kwargs},
        )

    async def reopen_forum_topic(
        self, chat_id: int | str, message_thread_id: int, **kwargs: Any
    ) -> bool:
        return self._record(
            "reopen_forum_topic",
            {"chat_id": chat_id, "message_thread_id": message_thread_id, **kwargs},
        )

    async def delete_forum_topic(
        self, chat_id: int | str, message_thread_id: int, **kwargs: Any
    ) -> bool:
        return self._record(
            "delete_forum_topic",
            {"chat_id": chat_id, "message_thread_id": message_thread_id, **kwargs},
        )


__all__ = [
    "FakeTelegramClient",
    "_FakeCall",
]
