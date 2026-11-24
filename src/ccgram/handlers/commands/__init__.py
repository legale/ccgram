"""Commands subpackage — help and special-key bot commands."""

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


async def _resolve_target_window(
    user_id: int, chat_id: int, thread_id: int
) -> str | None:
    # Lazy: avoid circular imports at module load
    from ...thread_router import thread_router

    window_id = thread_router.get_window_for_thread(user_id, thread_id)
    if window_id:
        return window_id

    # Lazy: avoid circular import with topic_binding
    from ..topics.topic_binding import resolve_topic_window

    return await resolve_topic_window(user_id, chat_id, thread_id)


async def _send_special_key(
    update: Update,
    _context: ContextTypes.DEFAULT_TYPE,
    key: str,
    key_name: str,
    *,
    repeat: int = 1,
) -> None:
    """Helper to send a special key to the bound tmux session."""
    user = update.effective_user
    if not user or not config.is_user_allowed(user.id) or not update.message:
        return

    # Lazy: avoid circular imports at module load
    from ...thread_router import thread_router

    # Lazy: avoid circular imports at module load
    from ...tmux_manager import tmux_manager

    # Lazy: avoid circular imports at module load
    from ..callback_helpers import get_thread_id

    thread_id = get_thread_id(update)
    if thread_id is None:
        await safe_reply(update.message, "Use this command inside a topic.")
        return

    chat_id = thread_router.resolve_chat_id(user.id, thread_id)
    window_id = await _resolve_target_window(user.id, chat_id, thread_id)
    if not window_id:
        await safe_reply(
            update.message, "This topic is not bound to a managed session."
        )
        return

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
        await safe_reply(update.message, f"Failed to send `{key_name}` to tmux.")
        return

    await safe_reply(update.message, f"Sent `{key_name}`")


@command("ctrl-c", "Send Ctrl+C", aliases=("ctrlc", "ctrl_c"))
async def ctrl_c_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``//ctrl-c`` — send Ctrl+C (SIGINT) to the bound tmux session."""
    await _send_special_key(update, context, "C-c", "Ctrl+C")


@command(
    "ctrl-cc",
    "Send Ctrl+C twice",
    aliases=("ctrlcc", "ctrl_cc"),
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


@command("arrows", "Toggle screen delta arrows")
async def arrows_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``//arrows`` — toggle the arrow row on the current screen-delta message."""
    user = update.effective_user
    if not user or not config.is_user_allowed(user.id) or not update.message:
        return

    # Lazy: avoid circular imports at module load
    from telegram.error import TelegramError

    # Lazy: avoid circular imports at module load
    from ...telegram_client import PTBTelegramClient

    # Lazy: avoid circular imports at module load
    from ...thread_router import thread_router

    # Lazy: avoid circular imports at module load
    from ..callback_helpers import get_thread_id

    # Lazy: avoid circular imports at module load
    from ..status.topic_status_diff import toggle_screen_diff_arrows

    thread_id = get_thread_id(update)
    if thread_id is None:
        await safe_reply(update.message, "Use this command inside a topic.")
        return

    chat_id = thread_router.resolve_chat_id(user.id, thread_id)

    try:
        enabled = await toggle_screen_diff_arrows(
            PTBTelegramClient(context.bot), chat_id, thread_id
        )
    except TelegramError:
        await safe_reply(update.message, "Failed to update screen delta buttons.")
        return

    if enabled is None:
        await safe_reply(update.message, "No screen delta message in this topic.")
        return
    await safe_reply(update.message, f"arrows {'on' if enabled else 'off'}")


__all__ = [
    "commands_command",
    "ctrl_c_command",
    "ctrl_cc_command",
    "enter_command",
    "esc_command",
    "tab_command",
    "arrows_command",
]

HELP_COMMAND_HANDLERS = (commands_command,)
KEY_COMMAND_HANDLERS = (
    ctrl_c_command,
    ctrl_cc_command,
    enter_command,
    esc_command,
    tab_command,
    arrows_command,
)
