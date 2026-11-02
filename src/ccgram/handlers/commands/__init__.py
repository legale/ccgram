"""Commands subpackage — bot commands and toolbar entry points."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from telegram import Update

from ...config import config
from ..command_meta import command
from ..messaging_pipeline.message_sender import safe_reply

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = structlog.get_logger()


@command("commands", "List commands", aliases=("help",))
async def commands_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """``//commands`` — list bot commands."""

    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        return
    if not update.message:
        return

    # Lazy: avoid circular import with handlers.registry
    from ..registry import format_commands_help

    await safe_reply(update.message, format_commands_help())


async def _send_special_key(  # noqa: C901
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    key: str,
    key_name: str,
    *,
    repeat: int = 1,
) -> None:
    """Helper to send a special key to the bound tmux session."""
    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        return
    if not update.message:
        return

    # Lazy: avoid circular imports at module load
    from ...telegram_client import PTBTelegramClient

    # Lazy: avoid circular imports at module load
    from ...thread_router import thread_router

    # Lazy: avoid circular imports at module load
    from ...tmux_manager import tmux_manager

    # Lazy: avoid circular imports at module load
    from ...utils import handle_general_topic_message, is_general_topic

    # Lazy: avoid circular imports at module load
    from ..callback_helpers import get_thread_id

    # Lazy: avoid circular imports at module load
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
        # Lazy: avoid circular import with topic_binding
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
        for _ in range(repeat):
            await tmux_manager.send_keys_to_pane(
                sidecar_pane_id, key, enter=False, literal=False, window_id=window_id
            )

    sent = True
    for _ in range(repeat):
        sent = await tmux_manager.send_keys(window_id, key, enter=False, literal=False)
        if not sent:
            break

    if not sent:
        await safe_reply(update.message, f"❌ Failed to send `{key_name}` to tmux.")
        return

    await ack_reaction(client, chat_id, update.message.message_id)
    if not config.ack_reaction:
        await safe_reply(update.message, f"Sent `{key_name}`")


@command("ctrl-c", "Send Ctrl+C", aliases=("ctrlc", "ctrl_c"), menu_name="ctrl_c")
async def ctrl_c_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``//ctrl-c`` — send Ctrl+C (SIGINT) to the bound tmux session."""
    await _send_special_key(update, context, "C-c", "Ctrl+C")


@command(
    "ctrl-cc",
    "Send Ctrl+C twice",
    aliases=("ctrlcc", "ctrl_cc"),
    menu_name="ctrl_cc",
)
async def ctrl_cc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``//ctrl-cc`` — send double Ctrl+C (SIGINT x2) to the bound tmux session."""
    await _send_special_key(update, context, "C-c", "Ctrl+C x2", repeat=2)


@command("enter", "Send Enter")
async def enter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``//enter`` — send Enter to the bound tmux session."""
    await _send_special_key(update, context, "Enter", "Enter")


@command("esc", "Send Escape", aliases=("escape",))
async def esc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``//esc`` — send Escape to the bound tmux session."""
    await _send_special_key(update, context, "Escape", "Escape")


@command("tab", "Send Tab")
async def tab_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``//tab`` — send Tab to the bound tmux session."""
    await _send_special_key(update, context, "Tab", "Tab")


__all__ = [
    "commands_command",
    "ctrl_c_command",
    "ctrl_cc_command",
    "enter_command",
    "esc_command",
    "tab_command",
]

HELP_COMMAND_HANDLERS = (commands_command,)
KEY_COMMAND_HANDLERS = (
    ctrl_c_command,
    ctrl_cc_command,
    enter_command,
    esc_command,
    tab_command,
)
