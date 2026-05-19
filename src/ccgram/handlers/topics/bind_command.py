"""/bind command for unbound topic attachment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import Update

from ...config import config
from ..callback_helpers import get_bound_window, get_window_display_name
from ..messaging_pipeline.message_sender import safe_reply

if TYPE_CHECKING:
    from telegram.ext import ContextTypes


async def bind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if not user or not config.is_user_allowed(user.id) or message is None:
        if message:
            await safe_reply(message, "You are not authorized to use this bot.")
        return

    thread_id = message.message_thread_id
    if thread_id is None:
        await safe_reply(message, "Use /bind inside a topic.")
        return

    window_id = get_bound_window(user.id, thread_id)
    if window_id is not None:
        display = get_window_display_name(window_id)
        await safe_reply(message, f"Already bound to session `{display}`.")
        return

    # Lazy: text_handler imports topic handlers during normal message dispatch.
    from ..text.text_handler import _handle_unbound_topic

    await _handle_unbound_topic(user.id, thread_id, "", context.user_data, message)
