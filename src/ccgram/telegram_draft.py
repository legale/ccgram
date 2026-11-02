"""Streaming-message helper for Telegram bot messages.

`DraftStream` provides incremental message delivery using standard
`send_message` + `edit_message_text`.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Final

import structlog
from telegram import Bot, InlineKeyboardMarkup
from telegram.error import BadRequest, NetworkError, RetryAfter, TelegramError, TimedOut

from .topic_tail import record_message

logger = structlog.get_logger()

DRAFT_STREAMING: Final[str] = "streaming"
DRAFT_LEGACY: Final[str] = "legacy"
DRAFT_UNSET: Final[str] = "unset"

_MAX_LEN: Final[int] = 4096


class _Sentinel:
    pass


_KEEP_MARKUP: Final[Any] = _Sentinel()


def is_draft_unavailable() -> bool:
    return True


def mark_draft_unavailable(reason: str = "") -> None:
    pass


def draft_unavailable_reason() -> str:
    return "legacy"


def reset_draft_state() -> None:
    pass


def is_peer_draft_unsupported(
    chat_id: int,  # noqa: ARG001
    thread_id: int | None,  # noqa: ARG001
) -> bool:
    return True


def mark_peer_draft_unsupported(
    chat_id: int,  # noqa: ARG001
    thread_id: int | None,  # noqa: ARG001
) -> None:
    pass


def _retry_after_seconds(exc: RetryAfter) -> float:
    return float(getattr(exc, "retry_after", 1.0) or 1.0)


def _truncate(text: str) -> str:
    if len(text) <= _MAX_LEN:
        return text
    head_len = _MAX_LEN - 32
    return f"{text[:head_len]}\n... [truncated {len(text) - head_len} chars]"


class DraftStream:
    """Streaming message that grows incrementally.

    Lifecycle: ``start`` → ``append``* → ``finalize`` (or ``abort``).
    """

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        *,
        message_thread_id: int | None = None,
        reply_to_message_id: int | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._thread_id = message_thread_id
        self._reply_to = reply_to_message_id
        self._reply_markup: InlineKeyboardMarkup | None = reply_markup
        self._message_id: int | None = None
        self._last_pushed_text: str = ""
        self._last_pushed_markup: Any = None
        self._buffer: str = ""
        self._mode: str = DRAFT_UNSET
        self._closed: bool = False

    @property
    def message_id(self) -> int | None:
        return self._message_id

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def text(self) -> str:
        return _truncate(self._buffer)

    async def start(self, initial_text: str) -> int | None:
        if self._mode != DRAFT_UNSET:
            raise RuntimeError("DraftStream.start called twice")
        if not initial_text:
            return None
        self._buffer = initial_text

        try:
            send_kwargs = self._send_kwargs()
            msg = await self._bot.send_message(
                chat_id=self._chat_id,
                text=self.text,
                **send_kwargs,
            )
            self._message_id = msg.message_id
            self._last_pushed_text = self.text
            self._last_pushed_markup = self._reply_markup or InlineKeyboardMarkup([])
            record_message(self._chat_id, self._thread_id, self._message_id)
            self._mode = DRAFT_LEGACY
        except (TimedOut, NetworkError) as exc:
            logger.warning("DraftStream.start transient failure: %s", exc)
            return None
        except RetryAfter as exc:
            logger.warning("DraftStream.start rate-limited: %s", exc)
            return None
        except TelegramError as exc:
            logger.warning("DraftStream.start telegram error: %s", exc)
            return None
        return self._message_id

    async def append(self, delta: str) -> None:
        self._ensure_open()
        self._buffer += delta
        await self._push_update()

    async def replace(
        self,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None | Any = _KEEP_MARKUP,
    ) -> None:
        self._ensure_open()
        self._buffer = text
        if reply_markup is not _KEEP_MARKUP:
            self._reply_markup = reply_markup
        await self._push_update()

    async def finalize(
        self,
        final_text: str | None = None,
        *,
        reply_markup: InlineKeyboardMarkup | None | Any = _KEEP_MARKUP,
    ) -> None:
        self._ensure_open()
        if final_text is not None:
            self._buffer = final_text
        if reply_markup is not _KEEP_MARKUP:
            self._reply_markup = reply_markup
        await self._push_update(final=True)
        self._closed = True

    async def abort(self) -> None:
        if self._closed or self._message_id is None:
            self._closed = True
            return
        try:
            await self._bot.delete_message(
                chat_id=self._chat_id, message_id=self._message_id
            )
        except TelegramError as exc:
            logger.warning("DraftStream.abort delete failed: %s", exc)
        self._closed = True

    def _ensure_open(self) -> None:
        if self._mode == DRAFT_UNSET:
            raise RuntimeError("DraftStream not started")
        if self._closed:
            raise RuntimeError("DraftStream already closed")

    def _send_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self._thread_id is not None:
            kwargs["message_thread_id"] = self._thread_id
        if self._reply_to is not None:
            kwargs["reply_to_message_id"] = self._reply_to
        if self._reply_markup is not None:
            kwargs["reply_markup"] = self._reply_markup
        return kwargs

    async def _push_update(self, *, final: bool = False) -> None:
        _ = final
        if self._message_id is None:
            return
        markup = self._reply_markup or InlineKeyboardMarkup([])
        if self._last_pushed_text == self.text and self._last_pushed_markup == markup:
            return
        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=self._message_id,
                text=self.text,
                reply_markup=markup,
            )
            self._last_pushed_text = self.text
            self._last_pushed_markup = markup
        except BadRequest as exc:
            if "not modified" in (exc.message or "").lower():
                return
            logger.warning("DraftStream edit failed: %s", exc)
        except RetryAfter as exc:
            await asyncio.sleep(_retry_after_seconds(exc) + 1)
            with contextlib.suppress(TelegramError):
                await self._bot.edit_message_text(
                    chat_id=self._chat_id,
                    message_id=self._message_id,
                    text=self.text,
                    reply_markup=markup,
                )
        except TelegramError as exc:
            logger.warning("DraftStream update failed: %s", exc)


__all__ = [
    "DRAFT_LEGACY",
    "DRAFT_STREAMING",
    "DRAFT_UNSET",
    "DraftStream",
    "is_draft_unavailable",
    "is_peer_draft_unsupported",
    "mark_draft_unavailable",
    "mark_peer_draft_unsupported",
    "reset_draft_state",
]
