"""Central handler registration for the Telegram bot Application.

Owns the command/message/callback/inline handler registration that used
to live inline in ``bot.py``. ``register_all()`` is the single entry
point; ``bot.py`` is a factory + lifecycle hooks only.

Every handler called below lives in a feature subpackage under
``handlers/`` — this module only assembles them in the order PTB
requires.

Command dispatch uses iproute2-style prefix matching via
``handlers.arg_parser.match_command()``.  A single MessageHandler on the
``^//`` regex replaces the old per-name PrefixHandler soup:

  ``//sc``   → screenshot_command   (prefix match of "screenshot")
  ``//unb``  → unbind_command        (prefix match of "unbind")
  ``//ses``  → sessions_command      (prefix match of "sessions")
  ``//bind`` → bind_command          (bind current topic to cc_<name>)

Full names are always valid; the shortest unambiguous prefix works too.
"""

from __future__ import annotations

from typing import TypeAlias

import structlog
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from telegram.ext._utils.types import HandlerCallback

from .arg_parser import match_command
from .callback_registry import dispatch as _dispatch_callback
from .callback_registry import load_handlers as _load_callback_handlers
from .cleanup import COMMAND_HANDLERS as CLEANUP_COMMANDS
from .command_meta import get_command_info
from .commands import (
    HELP_COMMAND_HANDLERS,
    KEY_COMMAND_HANDLERS,
)
from .file_handler import handle_document_message, handle_photo_message
from .live import COMMAND_HANDLERS as LIVE_COMMANDS
from .send import COMMAND_HANDLERS as SEND_COMMANDS
from .sessions_dashboard import COMMAND_HANDLERS as SESSION_COMMANDS
from .text.text_handler import text_handler
from .topics.topic_binding import COMMAND_HANDLERS as TOPIC_COMMANDS
from .topics.topic_lifecycle import (
    topic_closed_handler,
    topic_created_handler,
    topic_edited_handler,
)

from .messaging_pipeline.message_sender import safe_reply

logger = structlog.get_logger()

COMMAND_PREFIX: str = "//"

HandlerFn: TypeAlias = HandlerCallback

COMMAND_HANDLERS: tuple[HandlerFn, ...] = (
    *HELP_COMMAND_HANDLERS,
    *LIVE_COMMANDS,
    *SESSION_COMMANDS,
    *TOPIC_COMMANDS,
    *CLEANUP_COMMANDS,
    *KEY_COMMAND_HANDLERS,
    *SEND_COMMANDS,
)


def format_commands_help() -> str:
    """Format user-facing help text for //commands and //help."""
    lines = [
        f"*Bot commands (prefix `{COMMAND_PREFIX}`)*:",
        "",
    ]
    for handler in COMMAND_HANDLERS:
        info = get_command_info(handler)
        alias_str = (
            f" (or `{COMMAND_PREFIX}{info.aliases[0]}`)" if info.aliases else ""
        )
        params_str = f" {info.params}" if info.params else ""
        lines.append(
            f"• `{COMMAND_PREFIX}{info.name}{params_str}`{alias_str} — {info.description}"
        )
    lines.extend(
        [
            "",
            "_Commands and paths starting with `/` (for example `/bin/ls`) are sent directly to tmux._",
        ]
    )
    return "\n".join(lines)


def get_telegram_menu_commands() -> list[BotCommand]:
    """Return command list for Telegram Bot API set_my_commands."""
    menu: list[BotCommand] = []
    seen: set[str] = set()
    for handler in COMMAND_HANDLERS:
        info = get_command_info(handler)
        cmd = info.menu_name or info.name.replace("-", "_")
        if cmd in seen:
            continue
        seen.add(cmd)
        desc = f"{info.params} — {info.description}" if info.params else info.description
        menu.append(BotCommand(cmd, desc))
    return menu


async def _dispatch_double_slash(update: Update, context) -> None:  # type: ignore[type-arg]
    """Single entry-point for all ``//``-prefixed bot commands."""
    message = update.effective_message
    if not message or not message.text:
        return

    logger.info(
        "telegram_double_slash_dispatch",
        update_id=update.update_id,
        message_id=message.message_id,
        chat_id=message.chat_id,
        thread_id=message.message_thread_id,
        text=message.text,
    )

    # Lazy: record_telegram_message at dispatch
    from ..topic_tail import record_telegram_message

    record_telegram_message(message)

    text = message.text.strip()
    if not text.startswith(COMMAND_PREFIX):
        return
    body = text[len(COMMAND_PREFIX) :]

    tokens = body.split()
    if not tokens:
        await safe_reply(
            message, f"Empty command. Use `{COMMAND_PREFIX}commands` for help."
        )
        return

    handler, args, candidates = match_command(tokens, COMMAND_HANDLERS)
    if handler is None:
        prefix = COMMAND_PREFIX if text.startswith(COMMAND_PREFIX) else "/"
        if candidates:
            opts = ", ".join(f"`{prefix}{c}`" for c in candidates)
            await safe_reply(
                message,
                f"Ambiguous command `{prefix}{tokens[0]}`. Possible matches: {opts}",
            )
        else:
            await safe_reply(
                message,
                f"Unknown command `{prefix}{tokens[0]}`. "
                f"Use `{prefix}commands` for the list of commands.",
            )
        return

    context.args = args
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
