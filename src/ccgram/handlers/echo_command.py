"""Debug /echo command.

Returns the Telegram Update payload seen by python-telegram-bot. This is
intentionally plain JSON so session/topic binding bugs can be inspected from
inside the affected Telegram topic.
"""

from __future__ import annotations

import json
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from ..telegram_sender import TELEGRAM_MAX_MESSAGE_LENGTH

_PREFIX = "Telegram update echo\n"
_CHUNK_MARGIN = 64


def _to_plain_dict(obj: Any) -> Any:
    if obj is None:
        return None
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return obj


def _echo_payload(update: Update) -> dict[str, Any]:
    return {
        "update": _to_plain_dict(update),
        "effective_chat": _to_plain_dict(update.effective_chat),
        "effective_message": _to_plain_dict(update.effective_message),
        "effective_user": _to_plain_dict(update.effective_user),
    }


def _json_chunks(payload: dict[str, Any]) -> list[str]:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    chunk_size = TELEGRAM_MAX_MESSAGE_LENGTH - len(_PREFIX) - _CHUNK_MARGIN
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)] or [""]


async def echo_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply with the raw Telegram update data visible to the bot."""
    message = update.effective_message
    if message is None:
        return

    chunks = _json_chunks(_echo_payload(update))
    total = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        header = _PREFIX if total == 1 else f"{_PREFIX}part {idx}/{total}\n"
        await message.reply_text(header + chunk)


__all__ = ["echo_command"]
