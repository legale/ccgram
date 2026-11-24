"""Safe message sending helpers with entity-based formatting.

Provides utility functions for sending Telegram messages with automatic
conversion to entity-based formatting (no parse errors possible) and
fallback to plain text on failure.

Functions:
  - rate_limit_send: Rate limiter to avoid Telegram flood control
  - rate_limit_send_message: Combined rate limiting + send with fallback
  - safe_reply: Reply with entities, fallback to plain text
  - safe_edit: Edit message with entities, fallback to plain text
  - safe_send: Send message with entities, fallback to plain text
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from telegram import CallbackQuery, LinkPreviewOptions, Message
from telegram.error import BadRequest, RetryAfter, TelegramError

from ...entity_formatting import convert_to_entities
from ...telegram_client import TelegramClient
from ...telegram_sender import TELEGRAM_MAX_MESSAGE_LENGTH, split_message
from ...topic_tail import record_telegram_message

__all__ = [
    "edit_with_fallback",
    "is_thread_gone",
    "rate_limit_send",
    "rate_limit_send_message",
    "safe_edit",
    "safe_reply",
]

logger = structlog.get_logger()

_MAX_RETRY_AFTER_S = 10.0


def is_thread_gone(exc: TelegramError) -> bool:
    """Check if error indicates the Telegram topic/thread no longer exists."""
    if isinstance(exc, BadRequest):
        msg = exc.message.lower()
        return "thread not found" in msg or "topic_id_invalid" in msg
    return False


# Disable link previews in all messages to reduce visual noise
NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)


class _MessageGoneError(Exception):
    """Raised when the target message no longer exists (deleted topic)."""


def _retry_after_seconds(exc: RetryAfter) -> int:
    """Extract retry delay from RetryAfter, handling both int and timedelta."""
    ra = exc.retry_after
    return ra if isinstance(ra, int) else int(ra.total_seconds())


# Rate limiting: last send time per chat to avoid Telegram flood control
_last_send_time: dict[int, float] = {}
_rate_limit_locks: dict[int, asyncio.Lock] = {}
MESSAGE_SEND_INTERVAL = 0.5  # seconds between messages to same chat


async def rate_limit_send(chat_id: int) -> None:
    """Wait if necessary to avoid Telegram flood control (max 1 msg/sec per chat).

    Uses a per-chat lock to serialize concurrent senders, preventing two
    coroutines from computing the same wake-up time and sending simultaneously.
    """
    lock = _rate_limit_locks.setdefault(chat_id, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        if chat_id in _last_send_time:
            target = _last_send_time[chat_id] + MESSAGE_SEND_INTERVAL
            if target > now:
                await asyncio.sleep(target - now)
                _last_send_time[chat_id] = time.monotonic()
                return
        _last_send_time[chat_id] = time.monotonic()


def _cap_to_telegram_limit(
    plain_text: str, entities: list[Any]
) -> tuple[str, list[Any]]:
    """Truncate plain_text + drop overflow entities so a single send fits Telegram's limit."""
    if len(plain_text) <= TELEGRAM_MAX_MESSAGE_LENGTH:
        return plain_text, entities
    ellipsis = "…"
    cap = TELEGRAM_MAX_MESSAGE_LENGTH - len(ellipsis)
    truncated = plain_text[:cap] + ellipsis
    kept = [e for e in entities if e.offset + e.length <= cap]
    return truncated, kept


async def _send_phase(
    send_fn: Callable[..., Awaitable[Any]],
    plain_text: str,
    context_label: str,
    send_kwargs: dict[str, Any],
) -> tuple[Message | None, TelegramError | None, bool]:
    """Attempt one send phase, handling RetryAfter once. Returns (msg, error, abort)."""
    try:
        return await send_fn(plain_text, **send_kwargs), None, False
    except RetryAfter as e:
        retry_after_s = _retry_after_seconds(e)
        logger.warning(
            "telegram_send_rate_limited",
            context=context_label,
            retry_after_s=retry_after_s,
        )
        if retry_after_s > _MAX_RETRY_AFTER_S:
            return None, None, True
        await asyncio.sleep(retry_after_s + 1)
        try:
            return await send_fn(plain_text, **send_kwargs), None, False
        except TelegramError as e2:
            return None, e2, is_thread_gone(e2)
    except TelegramError as e:
        return None, e, is_thread_gone(e)


async def _with_entity_fallback(
    send_fn: Callable[..., Awaitable[Any]],
    text: str,
    context_label: str,
    **kwargs: Any,
) -> Message | None:
    """Convert to entities, send, fall back to plain text on error."""
    plain_text, entities = convert_to_entities(text)

    if not plain_text.strip():
        return None

    plain_text, entities = _cap_to_telegram_limit(plain_text, entities)

    # Phase 1: with entities; Phase 2: plain text fallback.
    # Thread-gone errors (deleted topic) short-circuit both phases.
    last_error: TelegramError | None = None
    for phase_entities in (entities, None):
        send_kwargs = {**kwargs}
        if phase_entities is not None:
            send_kwargs["entities"] = phase_entities
        msg, err, abort = await _send_phase(
            send_fn, plain_text, context_label, send_kwargs
        )
        if msg is not None:
            return msg
        if abort:
            return None
        if err is not None:
            last_error = err

    if last_error is not None:
        logger.warning("Failed to %s: %s", context_label, last_error)
    return None


async def _send_text_parts(
    send_fn: Callable[..., Awaitable[Message]],
    text: str,
    label: str,
    *,
    chat_id: int | None = None,
    track: bool = False,
    split_max_length: int = 3000,
    **kwargs: Any,
) -> Message | None:
    """Send all parts instead of truncating at Telegram's message limit."""
    sent = None
    for i, part in enumerate(split_message(text, max_length=split_max_length)):
        if i:
            kwargs.pop("reply_markup", None)
            kwargs.pop("reply_to_message_id", None)
            if chat_id is not None:
                await rate_limit_send(chat_id)
        msg = await _with_entity_fallback(send_fn, part, label, **kwargs)
        if msg is None:
            return None
        if track:
            record_telegram_message(msg)
        sent = msg
    return sent


async def _send_with_fallback(
    client: TelegramClient,
    chat_id: int,
    text: str,
    split_max_length: int = 3000,
    **kwargs: Any,
) -> Message | None:
    """Send message with entity formatting, falling back to plain text on failure.

    Returns the sent Message on success, None on failure.
    """
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)

    async def _send(text: str, **kw: Any) -> Message:
        return await client.send_message(chat_id=chat_id, text=text, **kw)

    return await _send_text_parts(
        _send,
        text,
        f"send message to {chat_id}",
        chat_id=chat_id,
        split_max_length=split_max_length,
        **kwargs,
    )


async def rate_limit_send_message(
    client: TelegramClient,
    chat_id: int,
    text: str,
    **kwargs: Any,
) -> Message | None:
    """Rate-limited send with entity formatting fallback.

    Combines rate_limit_send() + _send_with_fallback() for convenience.
    Returns the sent Message on success, None on failure.
    """
    await rate_limit_send(chat_id)
    return await _send_with_fallback(client, chat_id, text, **kwargs)


async def safe_reply(message: Message, text: str, **kwargs: Any) -> Message | None:
    """Reply with entity formatting, falling back to plain text on failure.

    Returns None if the original message no longer exists (e.g. deleted topic).
    """
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    logger.info(
        "telegram_reply_started",
        chat_id=message.chat_id,
        message_id=message.message_id,
        text=text,
    )

    async def _reply(text: str, **kw: Any) -> Message:
        try:
            return await message.reply_text(text, **kw)
        except BadRequest as exc:
            if "not found" in str(exc).lower():
                logger.warning("Cannot reply: original message gone (%s)", exc)
                raise _MessageGoneError from exc
            raise

    try:
        result = await _send_text_parts(_reply, text, "reply", track=True, **kwargs)
        logger.info(
            "telegram_reply_finished",
            chat_id=message.chat_id,
            message_id=message.message_id,
            sent_message_id=result.message_id if result else None,
        )
        return result
    except _MessageGoneError:
        return None


async def safe_edit(target: Message | CallbackQuery, text: str, **kwargs: Any) -> None:
    """Edit message with entity formatting, falling back to plain text on failure."""
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    # Message.edit_text vs CallbackQuery.edit_message_text
    raw_edit_fn = (
        target.edit_text if isinstance(target, Message) else target.edit_message_text
    )

    async def _edit(text: str, **kw: Any) -> Any:
        return await raw_edit_fn(text, **kw)

    await _with_entity_fallback(_edit, text, "edit message", **kwargs)


async def edit_with_fallback(
    client: TelegramClient,
    chat_id: int,
    message_id: int,
    text: str,
    **kwargs: Any,
) -> bool:
    """Edit a message with entity formatting, falling back to plain text.

    Returns True on success, False on failure.
    """
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    plain_text, entities = convert_to_entities(text)

    try:
        await client.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=plain_text,
            entities=entities,
            **kwargs,
        )
        return True
    except RetryAfter:
        raise
    except TelegramError:
        try:
            fallback = plain_text
            await client.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=fallback,
                **kwargs,
            )
            return True
        except RetryAfter:
            raise
        except TelegramError:
            return False
