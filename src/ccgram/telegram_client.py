"""TelegramClient Protocol + adapters.

Protocol exposing exactly the bot API surface used across ``handlers/`` and
top-level modules. Lets handlers depend on a narrow seam instead of importing
``telegram.Bot`` directly, so:

  - tests can pass test doubles that record calls
  - ``PTBTelegramClient`` wraps a real PTB ``Bot`` for production

Method names match PTB's ``Bot`` method names verbatim so the adapter is a
straight delegation. The Protocol covers only methods grep'd from the
codebase — no aspirational additions. Add new methods here if and when a
handler needs them.

Public API:
  - ``TelegramClient``       — Protocol the handlers depend on
  - ``PTBTelegramClient``    — adapter wrapping ``telegram.Bot``
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from telegram import Bot, Message
from telegram._files.inputmedia import InputMedia

from .topic_tail import record_telegram_message


@runtime_checkable
class TelegramClient(Protocol):
    """Narrow seam over the PTB ``Bot`` methods used in this codebase."""

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        **kwargs: Any,
    ) -> Message: ...

    async def edit_message_text(
        self,
        text: str,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool: ...

    async def edit_message_media(
        self,
        media: InputMedia,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool: ...

    async def edit_message_caption(
        self,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool: ...

    async def edit_message_reply_markup(
        self,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool: ...

    async def send_photo(
        self,
        chat_id: int | str,
        photo: Any,
        **kwargs: Any,
    ) -> Message: ...

    async def send_document(
        self,
        chat_id: int | str,
        document: Any,
        **kwargs: Any,
    ) -> Message: ...

    async def send_chat_action(
        self,
        chat_id: int | str,
        action: str,
        **kwargs: Any,
    ) -> bool: ...

    async def edit_forum_topic(
        self,
        chat_id: int | str,
        message_thread_id: int,
        **kwargs: Any,
    ) -> bool: ...

    async def close_forum_topic(
        self,
        chat_id: int | str,
        message_thread_id: int,
        **kwargs: Any,
    ) -> bool: ...

    async def reopen_forum_topic(
        self,
        chat_id: int | str,
        message_thread_id: int,
        **kwargs: Any,
    ) -> bool: ...

    async def delete_forum_topic(
        self,
        chat_id: int | str,
        message_thread_id: int,
        **kwargs: Any,
    ) -> bool: ...


class PTBTelegramClient:
    """Adapter that delegates ``TelegramClient`` calls to a PTB ``Bot``."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_message(
        self, chat_id: int | str, text: str, **kwargs: Any
    ) -> Message:
        msg = await self._bot.send_message(chat_id=chat_id, text=text, **kwargs)
        record_telegram_message(msg)
        return msg

    async def edit_message_text(
        self,
        text: str,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool:
        return await self._bot.edit_message_text(
            text=text, chat_id=chat_id, message_id=message_id, **kwargs
        )

    async def edit_message_media(
        self,
        media: InputMedia,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool:
        return await self._bot.edit_message_media(
            media=media, chat_id=chat_id, message_id=message_id, **kwargs
        )

    async def edit_message_caption(
        self,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool:
        return await self._bot.edit_message_caption(
            chat_id=chat_id, message_id=message_id, **kwargs
        )

    async def edit_message_reply_markup(
        self,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool:
        return await self._bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=message_id, **kwargs
        )

    async def send_photo(
        self, chat_id: int | str, photo: Any, **kwargs: Any
    ) -> Message:
        return await self._bot.send_photo(chat_id=chat_id, photo=photo, **kwargs)

    async def send_document(
        self, chat_id: int | str, document: Any, **kwargs: Any
    ) -> Message:
        return await self._bot.send_document(
            chat_id=chat_id, document=document, **kwargs
        )

    async def send_chat_action(
        self, chat_id: int | str, action: str, **kwargs: Any
    ) -> bool:
        return await self._bot.send_chat_action(
            chat_id=chat_id, action=action, **kwargs
        )

    async def edit_forum_topic(
        self, chat_id: int | str, message_thread_id: int, **kwargs: Any
    ) -> bool:
        return await self._bot.edit_forum_topic(
            chat_id=chat_id, message_thread_id=message_thread_id, **kwargs
        )

    async def close_forum_topic(
        self, chat_id: int | str, message_thread_id: int, **kwargs: Any
    ) -> bool:
        return await self._bot.close_forum_topic(
            chat_id=chat_id, message_thread_id=message_thread_id, **kwargs
        )

    async def reopen_forum_topic(
        self, chat_id: int | str, message_thread_id: int, **kwargs: Any
    ) -> bool:
        return await self._bot.reopen_forum_topic(
            chat_id=chat_id, message_thread_id=message_thread_id, **kwargs
        )

    async def delete_forum_topic(
        self, chat_id: int | str, message_thread_id: int, **kwargs: Any
    ) -> bool:
        return await self._bot.delete_forum_topic(
            chat_id=chat_id, message_thread_id=message_thread_id, **kwargs
        )


__all__ = [
    "PTBTelegramClient",
    "TelegramClient",
]
