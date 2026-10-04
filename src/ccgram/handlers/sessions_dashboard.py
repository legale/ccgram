"""Sessions dashboard — /sessions command showing all bound sessions.

Displays a summary of all thread-bound sessions for the current user
with alive/dead status indicators, per-session action buttons (Esc,
Screenshot, Kill with two-step confirmation), cwd details, and
refresh/new-session actions.

Key functions:
  - sessions_command(): /sessions command handler
  - handle_sessions_refresh(): refresh button callback
  - handle_sessions_kill(): first Kill tap — show confirmation
  - handle_sessions_kill_confirm(): second tap — kill and unbind
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from pathlib import Path
import structlog

from telegram import (
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from ..config import config
from ..session import session_manager
from ..telegram_client import PTBTelegramClient, TelegramClient
from ..thread_router import thread_router
from ..tmux_manager import tmux_manager
from ..window_query import view_window  # noqa: F401 - legacy test patch seam
from .status.topic_emoji import get_stored_topic_name, update_stored_topic_name
from .callback_data import (
    CB_SESSIONS_KILL,
    CB_SESSIONS_KILL_CONFIRM,
    CB_SESSIONS_NEW,
    CB_SESSIONS_REFRESH,
    CB_SESSIONS_RENAME,
    CB_SESSIONS_SCREENSHOT,
    CB_STATUS_SCREENSHOT,
)
from .callback_helpers import user_owns_window  # noqa: F401 - legacy test patch seam
from .callback_registry import register
from .cleanup import clear_topic_state
from .messaging_pipeline.message_sender import safe_edit, safe_reply
from .polling.polling_state import lifecycle_strategy
from .topics.topic_binding import bind_topic_to_window
from .user_state import (
    SESSION_RENAME_CHAT_ID,
    SESSION_RENAME_THREAD_ID,
    SESSION_RENAME_WINDOW_ID,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = structlog.get_logger()

_REFRESH_BTN = InlineKeyboardButton("Refresh", callback_data=CB_SESSIONS_REFRESH)
_NEW_BTN = InlineKeyboardButton("New Session", callback_data=CB_SESSIONS_NEW)


async def _build_dashboard(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Build dashboard text and keyboard for a user's sessions."""
    bindings = thread_router.get_all_thread_windows(user_id)
    all_sessions = await tmux_manager.list_sessions()

    if not all_sessions:
        return (
            "No active sessions.\n\nCreate a new topic to start a session.",
            InlineKeyboardMarkup([]),
        )

    lines: list[str] = []
    action_rows: list[list[InlineKeyboardButton]] = []
    bound_window_ids = set(bindings.values())

    for session in all_sessions:
        window_id = next(
            (
                bound_id
                for bound_id in bound_window_ids
                if bound_id == session.window_id
                or bound_id.startswith(f"{session.window_name}:")
            ),
            session.window_id,
        )
        status = "+" if window_id in bound_window_ids else "o"
        display_name = session.window_name
        lines.append(f"{status} {display_name} {session.cwd}".rstrip())
        action_rows.extend(
            [
                [
                    InlineKeyboardButton(
                        f"{status} {display_name}",
                        callback_data=f"{CB_SESSIONS_RENAME}{window_id}"[:64],
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "scr",
                        callback_data=f"{CB_SESSIONS_SCREENSHOT}{window_id}"[:64],
                    ),
                    InlineKeyboardButton(
                        "kill",
                        callback_data=f"{CB_SESSIONS_KILL}{window_id}"[:64],
                    ),
                ],
            ]
        )

    content = "\n".join(lines)
    text = f"Sessions\n\n```\n{content}\n```"
    return text, InlineKeyboardMarkup(action_rows)


async def sessions_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sessions — show dashboard of all bound sessions."""
    user = update.effective_user
    if not user or not update.message:
        return

    if not config.is_user_allowed(user.id):
        await safe_reply(update.message, "You are not authorized to use this bot.")
        return

    text, keyboard = await _build_dashboard(user.id)
    await safe_reply(update.message, text, reply_markup=keyboard)


async def handle_sessions_refresh(query: CallbackQuery, user_id: int) -> None:
    """Handle refresh button — re-render the dashboard in-place."""
    text, keyboard = await _build_dashboard(user_id)
    await safe_edit(query, text, reply_markup=keyboard)


async def _create_session_for_topic(
    query: CallbackQuery, user_id: int, thread_id: int, _client: TelegramClient
) -> None:
    chat = query.message.chat if query.message else None
    if chat is None:
        await safe_edit(query, "Cannot determine topic")
        return

    topic_name = get_stored_topic_name(chat.id, thread_id)
    if not topic_name:
        topic_name = f"topic-{thread_id}"

    success, message, created_name, created_wid = await tmux_manager.create_window(
        str(Path.cwd()),
        session_name=tmux_manager.topic_session_name(topic_name),
        window_name=topic_name,
    )
    if not success:
        await safe_edit(query, f"Failed to create session: {message}")
        return

    display = created_name
    await bind_topic_to_window(
        query,
        user_id,
        thread_id,
        created_wid,
        display,
        router=thread_router,
    )
    await safe_edit(query, f"Created session `{display}`")


async def handle_sessions_kill(
    query: CallbackQuery, _user_id: int, window_id: str
) -> None:
    """First Kill tap — show confirmation prompt."""
    display = thread_router.get_display_name(window_id)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"Confirm kill {display}",
                    callback_data=f"{CB_SESSIONS_KILL_CONFIRM}{window_id}"[:64],
                ),
            ],
            [_REFRESH_BTN],
        ]
    )
    await safe_edit(
        query,
        f"Kill session '{display}'?",
        reply_markup=keyboard,
    )


async def handle_sessions_kill_confirm(
    query: CallbackQuery, user_id: int, window_id: str, client: TelegramClient
) -> bool:
    """Second tap — kill the tmux window, unbind all users, refresh dashboard."""
    display = thread_router.get_display_name(window_id)

    session_name = (
        window_id.rsplit(":", 1)[0]
        if ":" in window_id and not window_id.startswith("@")
        else tmux_manager.session_name
    )
    killed = await tmux_manager.kill_session(session_name)
    if not killed:
        await safe_edit(query, f"Session `{session_name}` was not killed.")
        return False

    # Clean up BEFORE unbind — resolve_chat_id needs group_chat_ids
    # which unbind_thread deletes
    for uid, tid, bound_wid in list(thread_router.iter_thread_bindings()):
        if bound_wid == window_id:
            await clear_topic_state(uid, tid, client, window_id=window_id)
            thread_router.unbind_thread(uid, tid)

    logger.info(
        "sessions_kill_confirm: killed session %s (%s), ok=%s, user=%d",
        session_name,
        display,
        killed,
        user_id,
    )

    # Re-render dashboard
    text, keyboard = await _build_dashboard(user_id)
    await safe_edit(query, f"Killed '{display}'\n\n{text}", reply_markup=keyboard)
    return True


async def handle_sessions_rename(
    query: CallbackQuery,
    user_id: int,
    window_id: str,
    context: ContextTypes.DEFAULT_TYPE,
    client: TelegramClient,
) -> None:
    """Prompt user to provide a new name for the session."""
    display = thread_router.get_display_name(window_id)
    thread_id = next(
        (
            tid
            for tid, bound_id in thread_router.get_all_thread_windows(user_id).items()
            if bound_id == window_id
        ),
        None,
    )
    chat = query.message.chat if query.message else None
    chat_id = chat.id if chat and thread_id is not None else None

    if context.user_data is not None:
        context.user_data[SESSION_RENAME_WINDOW_ID] = window_id
        context.user_data[SESSION_RENAME_THREAD_ID] = thread_id
        context.user_data[SESSION_RENAME_CHAT_ID] = chat_id

    prompt_text = f"Enter new name for session `{display}`:"
    try:
        if chat_id is not None:
            await client.send_message(
                chat_id=chat_id,
                text=prompt_text,
                message_thread_id=thread_id,
                reply_markup=ForceReply(selective=True),
            )
        else:
            await safe_edit(query, prompt_text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("session rename prompt failed: %s", exc)
        await query.answer("Failed to open rename prompt", show_alert=True)
        return

    await query.answer("Rename session")


async def apply_session_rename(
    user_data: dict | None,
    thread_id: int | None,
    text: str,
    message: Message,
    client: TelegramClient | None = None,
) -> bool:
    """Consume an in-flight session rename reply.

    Returns True when the message was handled (rename pending);
    caller must early-return. Returns False otherwise.
    """
    if not user_data or SESSION_RENAME_WINDOW_ID not in user_data:
        return False

    pending_thread = user_data.get(SESSION_RENAME_THREAD_ID)
    if pending_thread is not None and pending_thread != thread_id:
        return False

    window_id = user_data.pop(SESSION_RENAME_WINDOW_ID, None)
    user_data.pop(SESSION_RENAME_THREAD_ID, None)
    rename_chat_id = user_data.pop(SESSION_RENAME_CHAT_ID, None)

    if not window_id:
        return False

    name = tmux_manager.topic_name_from_session_name(text.strip())
    if name in ("-", "/cancel", "cancel"):
        await safe_reply(message, "Rename cancelled.")
        return True
    if not name or len(name) > 50 or "\n" in name:  # noqa: PLR2004
        await safe_reply(
            message,
            "Invalid session name. Must be 1-50 characters without newlines.",
        )
        return True

    for uid, tid, bound_wid in list(thread_router.iter_thread_bindings()):
        if bound_wid == window_id:
            lifecycle_strategy.mark_dead_notified(uid, tid, window_id)

    new_wid = await _execute_session_rename(window_id, name)

    chat_id = rename_chat_id
    if chat_id and thread_id is not None:
        update_stored_topic_name(chat_id, thread_id, name)
        await _rename_forum_topic(client, chat_id, thread_id, name)

    logger.info(
        "Session renamed: window %s -> %s (%r, thread=%s)",
        window_id,
        new_wid,
        name,
        thread_id,
    )
    await safe_reply(message, f"Renamed session to `{name}`")
    return True


async def _rename_forum_topic(
    client: TelegramClient | None,
    chat_id: int,
    thread_id: int,
    name: str,
) -> None:
    """Best-effort rename of the Telegram forum topic."""
    if client is None:
        return
    try:
        await client.edit_forum_topic(chat_id, thread_id, name=name)
    except Exception:  # noqa: BLE001
        logger.debug(
            "edit_forum_topic failed: chat=%d thread=%d name=%r",
            chat_id,
            thread_id,
            name,
        )


async def _execute_session_rename(window_id: str, name: str) -> str:
    new_wid = window_id
    w = await tmux_manager.find_window_by_id(window_id)
    if w:
        await tmux_manager.rename_window(w.window_id, name)
        if ":" in w.window_id:
            session_name, bare_id = w.window_id.rsplit(":", 1)
            topic_name = tmux_manager.topic_name_from_session_name(name)
            new_session_name = tmux_manager.topic_session_name(topic_name)
            if await tmux_manager.rename_session(session_name, new_session_name):
                new_wid = f"{new_session_name}:{bare_id}"

    if new_wid != window_id:
        for uid, tid, bound_wid in list(thread_router.iter_thread_bindings()):
            if bound_wid == window_id:
                thread_router.bind_thread(uid, tid, new_wid, window_name=name)
                lifecycle_strategy.clear_dead_notification(uid, tid)
                lifecycle_strategy.clear_autoclose_timer(uid, tid)
        session_manager.set_display_name(new_wid, name)
    else:
        for uid, tid, bound_wid in list(thread_router.iter_thread_bindings()):
            if bound_wid == window_id:
                lifecycle_strategy.clear_dead_notification(uid, tid)
        thread_router.set_display_name(window_id, name)
        session_manager.set_display_name(window_id, name)
    return new_wid


async def _dispatch_window_action(
    data: str,
    query: CallbackQuery,
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    client = PTBTelegramClient(context.bot)
    if data.startswith(CB_SESSIONS_KILL_CONFIRM):
        window_id = data[len(CB_SESSIONS_KILL_CONFIRM) :]
        killed = await handle_sessions_kill_confirm(query, user_id, window_id, client)
        await query.answer("Killed" if killed else "Session not found")
    elif data.startswith(CB_SESSIONS_KILL):
        window_id = data[len(CB_SESSIONS_KILL) :]
        await handle_sessions_kill(query, user_id, window_id)
        await query.answer()
    elif data.startswith(CB_SESSIONS_RENAME):
        window_id = data[len(CB_SESSIONS_RENAME) :]
        await handle_sessions_rename(query, user_id, window_id, context, client)


@register(
    CB_SESSIONS_REFRESH,
    CB_SESSIONS_NEW,
    CB_SESSIONS_KILL_CONFIRM,
    CB_SESSIONS_KILL,
    CB_SESSIONS_RENAME,
    CB_SESSIONS_SCREENSHOT,
)
async def _dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    user = update.effective_user
    if not user:
        return

    data = query.data

    if data == CB_SESSIONS_REFRESH:
        await handle_sessions_refresh(query, user.id)
        await query.answer("Refreshed")
    elif data == CB_SESSIONS_NEW:
        thread_id = getattr(query.message, "message_thread_id", None)
        if thread_id is None:
            await query.answer("Use in a topic", show_alert=True)
            return
        await _create_session_for_topic(
            query, user.id, thread_id, PTBTelegramClient(context.bot)
        )
        await query.answer("Created")
    else:
        if data.startswith(CB_SESSIONS_SCREENSHOT):
            from .live.screenshot_callbacks import handle_screenshot_callback

            window_id = data[len(CB_SESSIONS_SCREENSHOT) :]
            await handle_screenshot_callback(
                query,
                user.id,
                f"{CB_STATUS_SCREENSHOT}{window_id}",
                update,
                context,
                allow_unowned=True,
            )
        else:
            await _dispatch_window_action(data, query, user.id, context)
