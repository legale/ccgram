"""Commands subpackage — bot commands and toolbar entry points."""

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

    provider_name = window_query.get_window_provider(window_id) or "shell"
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
    "toolbar_command",
]
