"""Commands subpackage — slash-command forward, menu sync, probes, snapshots.

Round-5 split of the former ``handlers/command_orchestration.py`` (775
LOC) into four cohesive modules:

  - ``forward``: ``forward_command_handler`` — the main pipeline that
    routes /-commands to the topic's provider session via tmux.
  - ``menu_sync``: per-user/per-chat/global Telegram command menu cache
    + the periodic refresh job. Owns provider command metadata.
  - ``failure_probe``: post-send transcript + pane delta probes for
    "unknown command" failure surfacing, plus the cross-provider
    awareness check.
  - ``status_snapshot``: /status and /stats fallback that synthesises a
    one-shot snapshot from the transcript when the provider doesn't
    reply natively.

Hosts the ``/commands`` and ``/toolbar`` Telegram entry points here
because they are light orchestration that mixes provider command
discovery with toolbar rendering — they don't fit any single submodule.

Public surface (re-exported here) is what ``bot.py``, ``bootstrap.py``,
``handlers/registry.py`` and ``handlers/text/text_handler.py`` import.
"""

from __future__ import annotations


from typing import TYPE_CHECKING

import structlog
from telegram import Update

from ...config import config
from ... import window_query
from ...thread_router import thread_router
from ...utils import handle_general_topic_message, is_general_topic
from ..callback_helpers import get_thread_id as _get_thread_id
from ..messaging_pipeline.message_sender import safe_reply
from ..toolbar import build_toolbar_keyboard, seed_button_states
from .forward import forward_command_handler
from .menu_sync import (
    get_global_provider_menu,
    set_global_provider_menu,
    setup_menu_refresh_job,
    sync_scoped_menu_for_text_context,
    sync_scoped_provider_menu,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = structlog.get_logger()


async def commands_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """``//commands`` — list bot commands."""

    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        return
    if not update.message:
        return

    lines = [
        "*Команды бота (префикс `//`)*:",
        "",
        "• `//commands` (или `//help`) — список команд",
        "• `//screenshot` (или `//screen`) — скриншот терминала",
        "• `//live` — автообновляемый просмотр терминала",
        "• `//panes` — управление панелями окна",
        "• `//toolbar` — показать панель кнопок",
        "• `//sessions` — дашборд сессий",
        "• `//sync` — синхронизация и аудит состояния",
        "• `//bind` — привязать окно к топику",
        "• `//unbind` — отвязать топик",
        "• `//recall` — повтор недавних команд",
        "• `//verbose` — переключить детальность сообщений",
        "• `//upgrade` — обновление ccgram и перезапуск",
        "• `//echo` — эхо-тест",
        "",
        "_Команды и пути, начинающиеся с `/` (например `/bin/ls`), отправляются напрямую в tmux._",
    ]
    await safe_reply(update.message, "\n".join(lines))


async def toolbar_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """``/toolbar`` — show the persistent action toolbar for the topic."""

    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = _get_thread_id(update)
    if thread_id is None:
        if (
            update.message
            and update.effective_chat
            and is_general_topic(update.message)
        ):
            await handle_general_topic_message(
                update.get_bot(), update.message, update.effective_chat.id
            )
        else:
            await safe_reply(update.message, "Use this command inside a topic.")
        return

    window_id = thread_router.get_window_for_thread(user.id, thread_id)
    if not window_id:
        await safe_reply(update.message, "This topic is not bound to any session.")
        return

    provider_name = window_query.get_window_provider(window_id) or "claude"
    # Seed toggle-button labels with the actual current state so the
    # initial render shows "Edit"/"Plan"/"YOLO"/"Def" instead of "Mode".
    await seed_button_states(window_id)
    keyboard = build_toolbar_keyboard(window_id, provider_name)
    display = thread_router.get_display_name(window_id)
    await safe_reply(
        update.message,
        f"\U0001f39b `{display}` toolbar",
        reply_markup=keyboard,
    )


__all__ = [
    "commands_command",
    "forward_command_handler",
    "get_global_provider_menu",
    "set_global_provider_menu",
    "setup_menu_refresh_job",
    "sync_scoped_menu_for_text_context",
    "sync_scoped_provider_menu",
    "toolbar_command",
]
