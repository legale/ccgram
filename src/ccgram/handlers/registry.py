"""Central handler registration for the Telegram bot Application.

Owns the command/message/callback/inline handler registration that used
to live inline in ``bot.py``. ``register_all()`` is the single entry
point; ``bot.py`` is a factory + lifecycle hooks only.

Every handler called below lives in a feature subpackage under
``handlers/`` — this module only assembles them in the order PTB
requires.

Command dispatch uses iproute2-style prefix matching via
``handlers.arg_parser.matches()``.  A single MessageHandler on the
``^//`` regex replaces the old per-name PrefixHandler soup:

  ``//sc``   → screenshot_command   (prefix match of "screenshot")
  ``//unb``  → unbind_command        (prefix match of "unbind")
  ``//ses``  → sessions_command      (prefix match of "sessions")
  ``//bind`` → bind_command          (bind current topic to cc_<name>)

Full names are always valid; the shortest unambiguous prefix works too.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from telegram.ext._utils.types import HandlerCallback

from .arg_parser import ArgIter, matches
from .callback_registry import dispatch as _dispatch_callback
from .callback_registry import load_handlers as _load_callback_handlers
from .cleanup import killme_command, unbind_command
from .commands import commands_command, ctrl_c_command
from .file_handler import handle_document_message, handle_photo_message
from .live import live_command, screenshot_command
from .send import send_command
from .sessions_dashboard import sessions_command
from .text.text_handler import text_handler
from .topics.topic_binding import bind_command
from .topics.topic_lifecycle import (
    topic_closed_handler,
    topic_created_handler,
    topic_edited_handler,
)

from .messaging_pipeline.message_sender import safe_reply

COMMAND_PREFIX: str = "//"

HandlerFn: TypeAlias = HandlerCallback


@dataclass(frozen=True)
class CommandSpec:
    """Specification for a bot command registration."""

    name: str | tuple[str, ...]
    handler: HandlerFn


# Ordered list: first match wins.  Each entry is (canonical_name, handler).
# Multiple names for one handler are listed as separate rows so that
# matches() can distinguish them from each other without ambiguity checks.
_DISPATCH_TABLE: list[tuple[str, HandlerFn]] = [
    ("commands", commands_command),
    ("help", commands_command),
    ("sessions", sessions_command),
    ("ses", sessions_command),
    ("bind", bind_command),
    ("unbind", unbind_command),
    ("killme", killme_command),
    ("ctrl-c", ctrl_c_command),
    ("ctrlc", ctrl_c_command),
    ("screenshot", screenshot_command),
    ("screen", screenshot_command),
    ("live", live_command),
    ("send", send_command),
]


def _find_handler(token: str) -> HandlerFn | None:
    """Return the handler for *token*, or None if unknown/ambiguous.

    Resolution order:
    1. Exact match against any entry in _DISPATCH_TABLE — returned immediately.
    2. Prefix match (matches(token, canonical)) — returns the handler only when
       exactly one canonical name matches; returns None if ambiguous.

    Exact-match priority lets aliases like ``screen``, ``ses``, ``help``
    work even though they are also prefixes of longer canonical names.
    """
    # 1. exact match — fast path
    for canonical, handler in _DISPATCH_TABLE:
        if token == canonical:
            return handler

    # 2. prefix match with ambiguity check
    found: HandlerFn | None = None
    for canonical, handler in _DISPATCH_TABLE:
        if matches(token, canonical):
            if found is not None:
                return None  # ambiguous
            found = handler
    return found


async def _dispatch_double_slash(update: Update, context) -> None:  # type: ignore[type-arg]
    """Single entry-point for all ``//``-prefixed bot commands."""
    message = update.effective_message
    if not message or not message.text:
        return

    text = message.text.strip()
    # Strip the "//" prefix and split into tokens
    body = text[len(COMMAND_PREFIX) :]
    tokens = body.split()
    if not tokens:
        await safe_reply(
            message, f"Empty command. Use `{COMMAND_PREFIX}commands` for help."
        )
        return

    it = ArgIter(tokens)
    cmd_token = it.next_arg()  # consume the command name

    handler = _find_handler(cmd_token)
    if handler is None:
        await safe_reply(
            message,
            f"Unknown command `{COMMAND_PREFIX}{cmd_token}`. "
            f"Use `{COMMAND_PREFIX}commands` for the list of commands.",
        )
        return

    # Re-inject remaining args into the update so individual handlers can
    # read them from update.message.text as usual (send_command does
    # `text.split(maxsplit=1)` — that still works with the original text).
    await handler(update, context)


def register_all(
    application: Application,
    group_filter: filters.BaseFilter,
) -> None:
    """Register every command, callback, message and inline-query handler.

    Bot commands use the // prefix so shell paths and unix commands starting
    with / can pass through directly to tmux without being intercepted.

    A single MessageHandler on ``^//`` dispatches all bot commands via
    iproute2-style prefix matching instead of per-name PrefixHandlers.
    """
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"^//") & group_filter,
            _dispatch_double_slash,
        )
    )

    _load_callback_handlers()
    application.add_handler(CallbackQueryHandler(_dispatch_callback))

    application.add_handler(
        MessageHandler(
            filters.StatusUpdate.FORUM_TOPIC_CREATED & group_filter,
            topic_created_handler,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.StatusUpdate.FORUM_TOPIC_CLOSED & group_filter,
            topic_closed_handler,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.StatusUpdate.FORUM_TOPIC_EDITED & group_filter,
            topic_edited_handler,
        )
    )
    application.add_handler(MessageHandler(filters.TEXT & group_filter, text_handler))
    application.add_handler(
        MessageHandler(filters.PHOTO & group_filter, handle_photo_message)
    )
    application.add_handler(
        MessageHandler(filters.Document.ALL & group_filter, handle_document_message)
    )


COMMAND_NAMES: tuple[str, ...] = tuple(name for name, _ in _DISPATCH_TABLE)
"""Sentinel for tests: the exact command names in _DISPATCH_TABLE, in order."""
