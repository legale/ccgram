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
        "• `//bind <name>` — привязать текущий топик к `cc_<name>`",
        "• `//unbind` — закрыть топик, сохранив tmux-сессию",
        "• `//killme` — завершить сессию и закрыть топик",
        "• `//ctrl-c` — отправить Ctrl+C в сессию",
        "• `//send` — отправить файл в tmux",
        "",
        "_Команды и пути, начинающиеся с `/` (например `/bin/ls`), отправляются напрямую в tmux._",
    ]
    await safe_reply(update.message, "\n".join(lines))


async def ctrl_c_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``//ctrl-c`` — send Ctrl+C (SIGINT) to the bound tmux session."""
    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        return
    if not update.message:
        return

    # Lazy: avoid circular imports at module load
    from ...telegram_client import PTBTelegramClient
    from ...thread_router import thread_router
    from ...tmux_manager import tmux_manager
    from ...utils import handle_general_topic_message, is_general_topic
    from ..callback_helpers import get_thread_id
    from ..messaging_pipeline.message_sender import ack_reaction

    thread_id = get_thread_id(update)
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

    chat_id = thread_router.resolve_chat_id(user.id, thread_id)
    window_id = thread_router.get_window_for_thread(user.id, thread_id)
    if not window_id:
        from ..topics.topic_binding import find_topic_session

        session = await find_topic_session(chat_id, thread_id)
        if session:
            window_id = session.window_id
        else:
            await safe_reply(
                update.message, "This topic is not bound to a managed session."
            )
            return

    client = PTBTelegramClient(context.bot)
    sidecar_pane_id = tmux_manager.get_sidecar_pane_id(window_id)
    if sidecar_pane_id:
        await tmux_manager.send_keys_to_pane(
            sidecar_pane_id, "C-c", enter=False, literal=False, window_id=window_id
        )

    sent = await tmux_manager.send_keys(window_id, "C-c", enter=False, literal=False)
    if not sent:
        await safe_reply(update.message, "❌ Failed to send `Ctrl+C` to tmux.")
        return

    await ack_reaction(client, chat_id, update.message.message_id)
    if not config.ack_reaction:
        await safe_reply(update.message, "Sent `Ctrl+C`")


__all__ = [
    "commands_command",
    "ctrl_c_command",
]
