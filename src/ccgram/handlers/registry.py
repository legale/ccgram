"""Central handler registration for the Telegram bot Application.

Owns the command/message/callback/inline handler registration that used
to live inline in ``bot.py``. ``register_all()`` is the single entry
point; ``bot.py`` is a factory + lifecycle hooks only.

Every handler called below lives in a feature subpackage under
``handlers/`` — this module only assembles them in the order PTB
requires.
"""

from dataclasses import dataclass
from typing import TypeAlias

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    InlineQueryHandler,
    MessageHandler,
    PrefixHandler,
    filters,
)
from telegram.ext._utils.types import HandlerCallback

from ..topic_tail import record_telegram_message
from .callback_registry import dispatch as _dispatch_callback
from .callback_registry import load_handlers as _load_callback_handlers
from .cleanup import unbind_command
from .command_history import recall_command
from .commands import (
    commands_command,
    toolbar_command,
)
from .echo_command import echo_command
from .file_handler import handle_document_message, handle_photo_message
from .inline import inline_query_handler, unsupported_content_handler
from .live import live_command, panes_command, screenshot_command
from .messaging_pipeline import toolcalls_command, verbose_command
from .recovery.history import history_command
from .send import send_command
from .sessions_dashboard import sessions_command
from .sync_command import sync_command
from .text.text_handler import text_handler
from .topics import bind_command
from .topics.topic_lifecycle import topic_closed_handler, topic_edited_handler
from .upgrade import upgrade_command
from .voice import handle_voice_message

from .messaging_pipeline.message_sender import safe_reply

COMMAND_PREFIX: str = "//"

HandlerFn: TypeAlias = HandlerCallback


@dataclass(frozen=True)
class CommandSpec:
    """Specification for a bot command registration."""

    name: str | tuple[str, ...]
    handler: HandlerFn


async def _record_topic_tail(update, _context) -> None:
    message = getattr(update, "effective_message", None)
    record_telegram_message(message)


async def _unknown_double_slash_handler(update, _context) -> None:
    message = getattr(update, "effective_message", None)
    if message and getattr(message, "text", None):
        cmd = message.text.split()[0]
        await safe_reply(
            message,
            f"Unknown command `{cmd}`. Use `{COMMAND_PREFIX}commands` for the list of commands.",
        )


def register_all(
    application: Application,
    group_filter: filters.BaseFilter,
) -> None:
    """Register every command, callback, message and inline-query handler.

    Bot commands use the // prefix so shell paths and unix commands starting
    with / can pass through directly to tmux without being intercepted.
    """
    command_specs: list[CommandSpec] = [
        CommandSpec("history", history_command),
        CommandSpec(("commands", "help"), commands_command),
        CommandSpec(("sessions", "ses"), sessions_command),
        CommandSpec("unbind", unbind_command),
        CommandSpec("upgrade", upgrade_command),
        CommandSpec("recall", recall_command),
        CommandSpec(("screenshot", "screen"), screenshot_command),
        CommandSpec("live", live_command),
        CommandSpec("panes", panes_command),
        CommandSpec("sync", sync_command),
        CommandSpec("toolbar", toolbar_command),
        CommandSpec("send", send_command),
        CommandSpec("verbose", verbose_command),
        CommandSpec("toolcalls", toolcalls_command),
        CommandSpec("bind", bind_command),
        CommandSpec("echo", echo_command),
    ]

    for spec in command_specs:
        names = (spec.name,) if isinstance(spec.name, str) else spec.name
        for name in names:
            application.add_handler(
                PrefixHandler(COMMAND_PREFIX, name, spec.handler, filters=group_filter)
            )

    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"^//") & group_filter,
            _unknown_double_slash_handler,
        )
    )

    application.add_handler(
        MessageHandler(group_filter, _record_topic_tail),
        group=-1,
    )

    _load_callback_handlers()
    application.add_handler(CallbackQueryHandler(_dispatch_callback))

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
    application.add_handler(
        MessageHandler(filters.VOICE & group_filter, handle_voice_message)
    )
    application.add_handler(
        MessageHandler(
            ~filters.TEXT
            & ~filters.PHOTO
            & ~filters.Document.ALL
            & ~filters.VOICE
            & ~filters.StatusUpdate.ALL
            & group_filter,
            unsupported_content_handler,
        )
    )

    application.add_handler(InlineQueryHandler(inline_query_handler))


COMMAND_NAMES: tuple[str, ...] = (
    "history",
    "commands",
    "help",
    "sessions",
    "ses",
    "unbind",
    "upgrade",
    "recall",
    "screenshot",
    "screen",
    "live",
    "panes",
    "sync",
    "toolbar",
    "send",
    "verbose",
    "toolcalls",
    "bind",
    "echo",
)
"""Sentinel for tests: the exact command names register_all installs, in order."""
