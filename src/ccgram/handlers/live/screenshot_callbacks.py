"""Screenshot callback handlers.

Handles inline keyboard callbacks for screenshot UI:
  - CB_SCREENSHOT_REFRESH: Refresh an existing screenshot
  - CB_LIVE_START: Start auto-refreshing live terminal view
  - CB_LIVE_STOP: Stop live view and revert to screenshot keyboard
  - CB_STATUS_SCREENSHOT: Take a screenshot from status message
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
import contextlib
import io
import time

import structlog

from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    InputMediaPhoto,
    Update,
)
from telegram.error import TelegramError
from ...screenshot import text_to_image
from ...telegram_client import PTBTelegramClient
from ...thread_router import thread_router
from ...tmux_manager import tmux_manager
from ..callback_data import (
    CB_KEYS_PREFIX,
    CB_LIVE_START,
    CB_LIVE_STOP,
    CB_SCREENSHOT_REFRESH,
    CB_STATUS_SCREENSHOT,
)
from ..callback_helpers import get_thread_id, user_owns_window
from ..callback_registry import register
from ..command_meta import command

if TYPE_CHECKING:
    from telegram import Message
    from telegram.ext import ContextTypes

    from ...tmux_manager import TmuxWindow

logger = structlog.get_logger()

# key_id -> (tmux_key, enter, literal)
KEYS_SEND_MAP: dict[str, tuple[str, bool, bool]] = {
    "up": ("Up", False, False),
    "dn": ("Down", False, False),
    "lt": ("Left", False, False),
    "rt": ("Right", False, False),
    "esc": ("Escape", False, False),
    "ent": ("Enter", False, False),
    "spc": ("Space", False, False),
    "tab": ("Tab", False, False),
    "cc": ("C-c", False, False),
}

# key_id -> display label (shown in callback answer toast)
KEY_LABELS: dict[str, str] = {
    "up": "\u2191",
    "dn": "\u2193",
    "lt": "\u2190",
    "rt": "\u2192",
    "esc": "\u238b Esc",
    "ent": "\u23ce Enter",
    "spc": "\u2423 Space",
    "tab": "\u21e5 Tab",
    "cc": "^C",
}


def build_screenshot_keyboard(window_id: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for screenshot: control keys + refresh."""

    def btn(label: str, key_id: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            label,
            callback_data=f"{CB_KEYS_PREFIX}{key_id}:{window_id}"[:64],
        )

    return InlineKeyboardMarkup(
        [
            [btn("\u2423 Space", "spc"), btn("\u2191", "up"), btn("\u21e5 Tab", "tab")],
            [btn("\u2190", "lt"), btn("\u2193", "dn"), btn("\u2192", "rt")],
            [btn("\u238b Esc", "esc"), btn("^C", "cc"), btn("\u23ce Enter", "ent")],
            [
                InlineKeyboardButton(
                    "Live",
                    callback_data=f"{CB_LIVE_START}{window_id}"[:64],
                ),
                InlineKeyboardButton(
                    "Refresh",
                    callback_data=f"{CB_SCREENSHOT_REFRESH}{window_id}"[:64],
                ),
            ],
        ]
    )


async def _handle_live_start(
    query: CallbackQuery, user_id: int, data: str, update: Update
) -> None:
    """Handle CB_LIVE_START: start auto-refreshing live view."""
    window_id = data[len(CB_LIVE_START) :]
    if not user_owns_window(user_id, window_id):
        await query.answer("Not your session", show_alert=True)
        return
    thread_id = get_thread_id(update)
    if thread_id is None:
        await query.answer("Use in a topic", show_alert=True)
        return

    # Lazy: config resolved at call time
    from ...config import config

    # Lazy: live_view ↔ screenshot_callbacks bidirectional cycle.
    from .live_view import (
        LiveViewState,
        build_live_keyboard,
        content_hash,
        is_live,
        start_live_view,
        tail_lines_ansi,
    )

    if is_live(user_id, thread_id):
        await query.answer("Already live")
        return

    w = await tmux_manager.find_window_by_id(window_id)
    if not w:
        await query.answer("Window not found", show_alert=True)
        return

    text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
    if not text:
        await query.answer("Failed to capture pane", show_alert=True)
        return

    text = tail_lines_ansi(text, config.live_view_limit)
    chat_id = thread_router.resolve_chat_id(user_id, thread_id)
    png_bytes = await text_to_image(text, with_ansi=True, live_mode=True)
    keyboard = build_live_keyboard(window_id)

    try:
        await query.edit_message_media(
            media=InputMediaPhoto(
                media=io.BytesIO(png_bytes),
                caption=f"Live \u00b7 {time.strftime('%H:%M:%S')}",
            ),
            reply_markup=keyboard,
        )
    except TelegramError as e:
        logger.error("Failed to start live view: %s", e)
        await query.answer("Failed to start live view", show_alert=True)
        return

    if query.message is None:
        await query.answer("Message lost")
        return
    start_live_view(
        LiveViewState(
            chat_id=chat_id,
            message_id=query.message.message_id,
            thread_id=thread_id,
            user_id=user_id,
            window_id=window_id,
            last_hash=content_hash(text),
        )
    )
    await query.answer("Live started")


async def _handle_live_stop(
    query: CallbackQuery, user_id: int, data: str, update: Update
) -> None:
    """Handle CB_LIVE_STOP: stop live view and revert to screenshot keyboard."""
    window_id = data[len(CB_LIVE_STOP) :]
    if not user_owns_window(user_id, window_id):
        await query.answer("Not your session", show_alert=True)
        return
    thread_id = get_thread_id(update)
    if thread_id is None:
        await query.answer("Use in a topic", show_alert=True)
        return

    # Lazy: live_view ↔ screenshot_callbacks bidirectional cycle.
    from .live_view import stop_live_view

    stop_live_view(user_id, thread_id)
    keyboard = build_screenshot_keyboard(window_id)
    with contextlib.suppress(TelegramError):
        await query.edit_message_caption(caption="Screenshot", reply_markup=keyboard)
    await query.answer("Stopped")


async def _handle_keys(query: CallbackQuery, user_id: int, data: str) -> None:
    """Send a control key from a screenshot or live-view keyboard."""
    rest = data[len(CB_KEYS_PREFIX) :]
    try:
        key_id, window_id = rest.split(":", 1)
    except ValueError:
        await query.answer("Invalid key", show_alert=True)
        return

    key_spec = KEYS_SEND_MAP.get(key_id)
    if key_spec is None:
        await query.answer("Unknown key", show_alert=True)
        return

    if not user_owns_window(user_id, window_id):
        await query.answer("Not your session", show_alert=True)
        return

    key, enter, literal = key_spec
    window = await tmux_manager.find_window_by_id(window_id)
    sent = bool(window) and await tmux_manager.send_keys(
        window.window_id,
        key,
        enter=enter,
        literal=literal,
    )

    if sent:
        await query.answer(KEY_LABELS[key_id])
    else:
        await query.answer("Failed to send key", show_alert=True)


async def _send_screenshot_media(
    client: PTBTelegramClient,
    chat_id: int,
    png_bytes: bytes,
    keyboard: InlineKeyboardMarkup,
    thread_id: int | None,
    filename: str = "screenshot.png",
) -> None:
    # Lazy: config resolved at call time for testability
    from ...config import config

    if config.screenshot_as_photo:
        await client.send_photo(
            chat_id=chat_id,
            photo=io.BytesIO(png_bytes),
            reply_markup=keyboard,
            message_thread_id=thread_id,
        )
    else:
        await client.send_document(
            chat_id=chat_id,
            document=io.BytesIO(png_bytes),
            filename=filename,
            reply_markup=keyboard,
            message_thread_id=thread_id,
        )


async def handle_screenshot_callback(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    _context: ContextTypes.DEFAULT_TYPE,
    allow_unowned: bool = False,
) -> None:
    """Handle screenshot-related callbacks."""
    with_update = {
        CB_KEYS_PREFIX: _handle_keys,
        CB_LIVE_START: _handle_live_start,
        CB_LIVE_STOP: _handle_live_stop,
        CB_STATUS_SCREENSHOT: _handle_status_screenshot,
    }
    for prefix, handler in with_update.items():
        if data.startswith(prefix):
            if prefix == CB_KEYS_PREFIX:
                await handler(query, user_id, data)
            elif prefix == CB_STATUS_SCREENSHOT:
                await handler(query, user_id, data, update, allow_unowned)
            else:
                await handler(query, user_id, data, update)
            return

    without_update = {
        CB_SCREENSHOT_REFRESH: _handle_refresh,
    }
    for prefix, handler in without_update.items():
        if data.startswith(prefix):
            await handler(query, user_id, data)
            return


async def _handle_refresh(query: CallbackQuery, user_id: int, data: str) -> None:
    """Handle CB_SCREENSHOT_REFRESH: refresh an existing screenshot."""
    window_id = data[len(CB_SCREENSHOT_REFRESH) :]
    if not user_owns_window(user_id, window_id):
        await query.answer("Not your session", show_alert=True)
        return
    w = await tmux_manager.find_window_by_id(window_id)
    if not w:
        await query.answer("Window no longer exists", show_alert=True)
        return

    text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
    if not text:
        await query.answer("Failed to capture pane", show_alert=True)
        return

    png_bytes = await text_to_image(text, with_ansi=True)
    keyboard = build_screenshot_keyboard(window_id)
    # Lazy: config resolved at call time for testability
    from ...config import config

    media = (
        InputMediaPhoto(media=io.BytesIO(png_bytes))
        if config.screenshot_as_photo
        else InputMediaDocument(media=io.BytesIO(png_bytes), filename="screenshot.png")
    )
    try:
        await query.edit_message_media(
            media=media,
            reply_markup=keyboard,
        )
        await query.answer("Refreshed")
    except TelegramError as e:
        logger.error("Failed to refresh screenshot: %s", e)
        await query.answer("Failed to refresh", show_alert=True)


async def _handle_status_screenshot(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    allow_unowned: bool = False,
) -> None:
    """Handle CB_STATUS_SCREENSHOT: take screenshot from status message."""
    window_id = data[len(CB_STATUS_SCREENSHOT) :]
    if not allow_unowned and not user_owns_window(user_id, window_id):
        await query.answer("Not your session", show_alert=True)
        return
    w = await tmux_manager.find_window_by_id(window_id)
    if not w:
        await query.answer("Window not found", show_alert=True)
        return
    text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
    if not text:
        await query.answer("Failed to capture", show_alert=True)
        return
    png_bytes = await text_to_image(text, with_ansi=True)
    keyboard = build_screenshot_keyboard(window_id)
    thread_id = get_thread_id(update)
    if thread_id is None:
        await query.answer("Use in a topic", show_alert=True)
        return
    chat_id = thread_router.resolve_chat_id(user_id, thread_id)
    if allow_unowned and chat_id is None and query.message and query.message.chat:
        chat_id = query.message.chat.id
    client = PTBTelegramClient(query.get_bot())
    try:
        await _send_screenshot_media(
            client=client,
            chat_id=chat_id,
            png_bytes=png_bytes,
            keyboard=keyboard,
            thread_id=thread_id,
        )
        await query.answer("Sent")
    except TelegramError as e:
        logger.error("Failed to send screenshot: %s", e)
        await query.answer("Failed to send screenshot", show_alert=True)


# ------------------------------------------------------------------
# Command handlers (moved from bot.py)
# ------------------------------------------------------------------


async def _command_thread_id(
    update: Update,
    safe_reply: Callable[..., Awaitable[object]],
    use_topic_text: str,
) -> int | None:
    message = update.message
    if message is None:
        return None

    thread_id = get_thread_id(update)
    if thread_id is not None:
        return thread_id

    await safe_reply(message, use_topic_text)
    return None


async def _resolve_bound_tmux_window(
    message: "Message",
    user_id: int,
    thread_id: int,
    safe_reply: Callable[..., Awaitable[object]],
    *,
    unbound_text: str,
    missing_text: str,
) -> tuple[str, "TmuxWindow"] | None:
    window_id = thread_router.get_window_for_thread(user_id, thread_id)
    if not window_id:
        # Lazy: avoid circular import with topic_binding
        from ..topics.topic_binding import resolve_topic_window

        chat_id = (
            message.chat.id
            if message.chat
            else thread_router.resolve_chat_id(user_id, thread_id)
        )
        window_id = await resolve_topic_window(user_id, chat_id, thread_id)
    if not window_id:
        await safe_reply(message, unbound_text)
        return None

    w = await tmux_manager.find_window_by_id(window_id)
    if not w:
        await safe_reply(message, missing_text)
        return None
    return window_id, w


@command("screenshot", "Capture terminal", aliases=("screen",))
async def screenshot_command(
    update: Update, _context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Capture and send a terminal screenshot for the current topic."""
    # Lazy: config singleton resolved at call time so tests can swap it
    from ...config import config

    # Lazy: messaging_pipeline <-> live cycle avoided at import time
    from ..messaging_pipeline.message_sender import safe_reply

    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = await _command_thread_id(
        update,
        safe_reply,
        "Use this command inside a topic.",
    )
    if thread_id is None:
        return

    resolved = await _resolve_bound_tmux_window(
        update.message,
        user.id,
        thread_id,
        safe_reply,
        unbound_text="This topic is not bound to any session.",
        missing_text="Window no longer exists.",
    )
    if resolved is None:
        return
    window_id, w = resolved

    pane_text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
    if not pane_text:
        await safe_reply(update.message, "Failed to capture terminal.")
        return

    png_bytes = await text_to_image(pane_text, with_ansi=True)
    keyboard = build_screenshot_keyboard(window_id)
    chat_id = thread_router.resolve_chat_id(user.id, thread_id)
    client = PTBTelegramClient(update.message.get_bot())
    try:
        await _send_screenshot_media(
            client=client,
            chat_id=chat_id,
            png_bytes=png_bytes,
            keyboard=keyboard,
            thread_id=thread_id,
        )
    except TelegramError as e:
        logger.error("Failed to send screenshot: %s", e)
        await safe_reply(update.message, "Failed to send screenshot.")


@command("live", "Live terminal view")
async def live_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open auto-refreshing live terminal view directly, skipping the screenshot step."""
    # Lazy: config singleton resolved at call time so tests can swap it
    from ...config import config

    # Lazy: messaging_pipeline <-> live cycle avoided at import time
    from ..messaging_pipeline.message_sender import safe_reply

    # Lazy: live_view registers callbacks against this module on import
    from .live_view import (
        LiveViewState,
        build_live_keyboard,
        content_hash,
        is_live,
        start_live_view,
        tail_lines_ansi,
    )

    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = await _command_thread_id(
        update,
        safe_reply,
        "Use this command inside a topic.",
    )
    if thread_id is None:
        return

    if is_live(user.id, thread_id):
        await safe_reply(update.message, "Live view already running.")
        return

    resolved = await _resolve_bound_tmux_window(
        update.message,
        user.id,
        thread_id,
        safe_reply,
        unbound_text="This topic is not bound to any session.",
        missing_text="Window no longer exists.",
    )
    if resolved is None:
        return
    window_id, w = resolved

    text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
    if not text:
        await safe_reply(update.message, "Failed to capture terminal.")
        return

    text = tail_lines_ansi(text, config.live_view_limit)
    chat_id = thread_router.resolve_chat_id(user.id, thread_id)
    png_bytes = await text_to_image(text, with_ansi=True, live_mode=True)
    keyboard = build_live_keyboard(window_id)
    client = PTBTelegramClient(update.message.get_bot())
    try:
        sent = await client.send_photo(
            chat_id=chat_id,
            photo=io.BytesIO(png_bytes),
            caption=f"Live · {time.strftime('%H:%M:%S')}",
            reply_markup=keyboard,
            message_thread_id=thread_id,
        )
    except TelegramError as e:
        logger.error("Failed to start live view: %s", e)
        await safe_reply(update.message, "Failed to start live view.")
        return

    start_live_view(
        LiveViewState(
            chat_id=chat_id,
            message_id=sent.message_id,
            thread_id=thread_id,
            user_id=user.id,
            window_id=window_id,
            last_hash=content_hash(text),
        )
    )


# --- Registry dispatch entry point ---


@register(
    CB_KEYS_PREFIX,
    CB_LIVE_START,
    CB_LIVE_STOP,
    CB_SCREENSHOT_REFRESH,
    CB_STATUS_SCREENSHOT,
)
async def _dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    assert query is not None and query.data is not None and user is not None
    await handle_screenshot_callback(query, user.id, query.data, update, context)


COMMAND_HANDLERS = (screenshot_command, live_command)
