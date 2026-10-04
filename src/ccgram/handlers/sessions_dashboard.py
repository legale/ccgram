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
import structlog

from telegram import (
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.error import TelegramError
from ..config import config
from ..telegram_client import PTBTelegramClient, TelegramClient
from ..thread_router import thread_router
from ..tmux_manager import tmux_manager
from ..window_query import view_window  # noqa: F401 - legacy test patch seam
from .callback_data import (
    CB_SESSIONS_KILL,
    CB_SESSIONS_KILL_CONFIRM,
    CB_SESSIONS_REFRESH,
    CB_SESSIONS_RENAME,
    CB_SESSIONS_SCREENSHOT,
    CB_STATUS_SCREENSHOT,
)
from .callback_helpers import user_owns_window  # noqa: F401 - legacy test patch seam
from .callback_registry import register
from .cleanup import clear_topic_state
from .messaging_pipeline.message_sender import safe_edit, safe_reply
from .messaging_pipeline.message_sender import is_thread_gone
from .user_state import (
    SESSION_RENAME_THREAD_ID,
    SESSION_RENAME_WINDOW_ID,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = structlog.get_logger()

_REFRESH_BTN = InlineKeyboardButton("Refresh", callback_data=CB_SESSIONS_REFRESH)


async def _build_dashboard(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Build dashboard text and keyboard for a user's sessions."""
    bindings = thread_router.get_all_thread_windows(user_id)
    all_sessions = [
        session
        for session in await tmux_manager.list_sessions()
        if session.window_name.startswith(config.tmux_session_prefix)
    ]

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
        display_name = tmux_manager.topic_name_from_session_name(session.window_name)
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
    session = next(
        (
            item
            for item in await tmux_manager.list_sessions()
            if item.window_name == session_name
        ),
        None,
    )
    if session and session.topic_ref:
        chat_id, thread_id = session.topic_ref
        try:
            await client.delete_forum_topic(chat_id, thread_id)
        except TelegramError as e:
            if not is_thread_gone(e):
                await safe_edit(query, f"Telegram topic was not deleted: {e}")
                return False

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

    new_wid = await _execute_session_rename(window_id, name)
    if new_wid is None:
        await safe_reply(message, f"Cannot rename session to `{name}`.")
        return True

    logger.info(
        "Session renamed: window %s -> %s (%r, thread=%s)",
        window_id,
        new_wid,
        name,
        thread_id,
    )
    await safe_reply(message, f"Renamed session to `{name}`")
    return True


async def _execute_session_rename(window_id: str, name: str) -> str | None:
    if ":" not in window_id or window_id.startswith("@"):
        return None
    session_name, bare_id = window_id.rsplit(":", 1)
    if not session_name.startswith(config.tmux_session_prefix):
        return None

    new_session_name = name
    if not await tmux_manager.rename_session(session_name, new_session_name):
        return None

    return f"{new_session_name}:{bare_id}"


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
    else:
        if data.startswith(CB_SESSIONS_SCREENSHOT):
            # Lazy: screenshot handling is needed only for screenshot callbacks.
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
