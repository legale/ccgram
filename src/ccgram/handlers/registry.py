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
import re
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

from .arg_parser import ArgIter, matches
from .callback_registry import dispatch as _dispatch_callback
from .callback_registry import load_handlers as _load_callback_handlers
from .cleanup import killme_command, unbind_command
from .commands import (
    commands_command,
    ctrl_c_command,
    ctrl_cc_command,
    enter_command,
    esc_command,
    tab_command,
)
from .file_handler import handle_document_message, handle_photo_message
from .live import live_command, screenshot_command
from .send import send_command
from .sessions_dashboard import sessions_command
from .text.text_handler import text_handler
from .topics.topic_binding import bind_command, rename_command
from .topics.topic_lifecycle import (
    topic_closed_handler,
    topic_created_handler,
    topic_edited_handler,
)

from .messaging_pipeline.message_sender import safe_reply

logger = structlog.get_logger()

COMMAND_PREFIX: str = "//"

HandlerFn: TypeAlias = HandlerCallback


@dataclass(frozen=True)
class CommandEntry:
    """Single definition for a bot command, serving as single source of truth."""

    name: str
    handler: HandlerFn
    description: str = ""
    aliases: tuple[str, ...] = ()
    params: str = ""
    menu_name: str | None = None


# Backward-compatible alias for tests/callers
CommandSpec = CommandEntry


COMMAND_CATALOG: tuple[CommandEntry, ...] = (
    CommandEntry(
        name="commands",
        handler=commands_command,
        description="список команд",
        aliases=("help",),
    ),
    CommandEntry(
        name="screenshot",
        handler=screenshot_command,
        description="скриншот терминала",
        aliases=("screen",),
    ),
    CommandEntry(
        name="live",
        handler=live_command,
        description="автообновляемый просмотр терминала",
    ),
    CommandEntry(
        name="sessions",
        handler=sessions_command,
        description="дашборд сессий",
        aliases=("ses",),
    ),
    CommandEntry(
        name="bind",
        handler=bind_command,
        description="привязать топик к cc_<key>",
        params="<key>",
    ),
    CommandEntry(
        name="rename",
        handler=rename_command,
        description="переименовать топик и сессию tmux",
        params="<name>",
    ),
    CommandEntry(
        name="unbind",
        handler=unbind_command,
        description="закрыть топик, сохранив tmux-сессию",
    ),
    CommandEntry(
        name="killme",
        handler=killme_command,
        description="завершить сессию и закрыть топик",
    ),
    CommandEntry(
        name="ctrl-c",
        handler=ctrl_c_command,
        description="отправить Ctrl+C в сессию",
        aliases=("ctrlc", "ctrl_c"),
        menu_name="ctrl_c",
    ),
    CommandEntry(
        name="ctrl-cc",
        handler=ctrl_cc_command,
        description="отправить двойной Ctrl+C в сессию",
        aliases=("ctrlcc", "ctrl_cc"),
        menu_name="ctrl_cc",
    ),
    CommandEntry(
        name="enter",
        handler=enter_command,
        description="отправить Enter в сессию",
    ),
    CommandEntry(
        name="esc",
        handler=esc_command,
        description="отправить Escape в сессию",
        aliases=("escape",),
    ),
    CommandEntry(
        name="tab",
        handler=tab_command,
        description="отправить Tab в сессию",
    ),
    CommandEntry(
        name="send",
        handler=send_command,
        description="отправить файл в tmux",
        params="<file>",
    ),
)


# Ordered list: first match wins.  Each entry is (canonical_name, handler).
_DISPATCH_TABLE: list[tuple[str, HandlerFn]] = [
    (name, entry.handler)
    for entry in COMMAND_CATALOG
    for name in (entry.name, *entry.aliases)
]


def format_commands_help() -> str:
    """Format user-facing help text for //commands and //help."""
    lines = [
        f"*Команды бота (префикс `{COMMAND_PREFIX}`)*:",
        "",
    ]
    for entry in COMMAND_CATALOG:
        alias_str = (
            f" (или `{COMMAND_PREFIX}{entry.aliases[0]}`)" if entry.aliases else ""
        )
        params_str = f" {entry.params}" if entry.params else ""
        lines.append(
            f"• `{COMMAND_PREFIX}{entry.name}{params_str}`{alias_str} — {entry.description}"
        )
    lines.extend(
        [
            "",
            "_Команды и пути, начинающиеся с `/` (например `/bin/ls`), отправляются напрямую в tmux._",
        ]
    )
    return "\n".join(lines)


def get_telegram_menu_commands() -> list[BotCommand]:
    """Return command list for Telegram Bot API set_my_commands."""
    menu: list[BotCommand] = []
    seen: set[str] = set()
    for entry in COMMAND_CATALOG:
        cmd = entry.menu_name or entry.name.replace("-", "_")
        if cmd in seen:
            continue
        seen.add(cmd)
        desc = (
            f"{entry.params} — {entry.description}"
            if entry.params
            else entry.description
        )
        menu.append(BotCommand(cmd, desc))
    return menu


def find_command_handler(token: str) -> tuple[HandlerFn | None, list[str]]:
    """Return the handler for *token*, or (None, candidates) if unknown/ambiguous.

    Resolution order:
    1. Exact match against any entry in _DISPATCH_TABLE — returned immediately.
    2. Prefix match (matches(token, canonical)) — returns the handler only when
       exactly one distinct handler matches; returns (None, candidates) if ambiguous;
       returns (None, []) if unknown.

    Exact-match priority lets aliases like ``screen``, ``ses``, ``help``
    work even though they are also prefixes of longer canonical names.
    """
    # 1. exact match — fast path
    for canonical, handler in _DISPATCH_TABLE:
        if token == canonical:
            return handler, []

    # 2. prefix match with ambiguity check
    matched = [
        (canonical, handler)
        for canonical, handler in _DISPATCH_TABLE
        if matches(token, canonical)
    ]
    if not matched:
        return None, []

    # If all matched entries point to the same handler, it is unambiguous
    distinct_handlers = {h for _, h in matched}
    if len(distinct_handlers) == 1:
        return matched[0][1], []

    # Ambiguous: list distinct command names
    candidates = list(dict.fromkeys(canonical for canonical, _ in matched))
    return None, candidates


def _find_handler(token: str) -> HandlerFn | None:
    """Return the handler for *token*, or None if unknown/ambiguous."""
    handler, _ = find_command_handler(token)
    return handler


async def _dispatch_double_slash(update: Update, context) -> None:  # type: ignore[type-arg]
    """Single entry-point for all ``//``-prefixed bot commands."""
    message = update.effective_message
    if not message or not message.text:
        return

    # Lazy: record_telegram_message at dispatch
    from ..topic_tail import record_telegram_message

    record_telegram_message(message)

    text = message.text.strip()
    if text.startswith(COMMAND_PREFIX):
        body = text[len(COMMAND_PREFIX) :]
    elif text.startswith("/"):
        body = text[1:]
    else:
        return

    tokens = body.split()
    if not tokens:
        await safe_reply(
            message, f"Empty command. Use `{COMMAND_PREFIX}commands` for help."
        )
        return

    it = ArgIter(tokens)
    cmd_token = it.next_arg()  # consume the command name

    handler, candidates = find_command_handler(cmd_token)
    if handler is None:
        if candidates:
            opts = ", ".join(f"`{COMMAND_PREFIX}{c}`" for c in candidates)
            await safe_reply(
                message,
                f"Ambiguous command `{COMMAND_PREFIX}{cmd_token}`. Possible matches: {opts}",
            )
        else:
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

    A single MessageHandler on ``^//`` or registered commands dispatches all bot commands via
    iproute2-style prefix matching instead of per-name PrefixHandlers.
    """
    cmd_names = [re.escape(name) for name, _ in _DISPATCH_TABLE]
    command_pattern = rf"^//|^/(?:{'|'.join(cmd_names)})(?:\s|$)"
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(command_pattern) & group_filter,
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
            filters.StatusUpdate.FORUM_TOPIC_EDITED,
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
