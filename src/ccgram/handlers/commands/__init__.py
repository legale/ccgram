"""Commands subpackage — bot commands and toolbar entry points."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from telegram import Update

from ...config import config
from ..messaging_pipeline.message_sender import safe_reply

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
        "• `//sessions` — дашборд сессий",
        "• `//unbind` — отвязать топик",
        "• `//send` — отправить файл в tmux",
        "",
        "_Команды и пути, начинающиеся с `/` (например `/bin/ls`), отправляются напрямую в tmux._",
    ]
    await safe_reply(update.message, "\n".join(lines))


__all__ = [
    "commands_command",
]
