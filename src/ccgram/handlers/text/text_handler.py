"""Text message handling — step functions for the text_handler orchestrator.

Routes incoming text messages through a bool early-return chain:
UI guards → unbound topic → dead window recovery → message forwarding.

Each step returns True if it handled the request (stop) or False to continue.
The orchestrator (handle_text_message) calls steps in sequence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from pathlib import Path
import re

import structlog
from telegram import Message, Update
from telegram.constants import ChatAction
from ...config import config
from ...telegram_client import PTBTelegramClient
from ..callback_helpers import get_thread_id as _get_thread_id
from ..topics.directory_browser import (
    BROWSE_DIRS_KEY,
    BROWSE_PAGE_KEY,
    BROWSE_PATH_KEY,
    STATE_BROWSING_DIRECTORY,
    STATE_KEY,
    STATE_SELECTING_WINDOW,
    UNBOUND_WINDOWS_KEY,
    build_directory_browser,
    build_window_picker,
    clear_browse_state,
    clear_window_picker_state,
)
from ..live.pane_callbacks import apply_pane_rename
from ..sessions_dashboard import apply_session_rename
from ..messaging_pipeline.message_sender import ack_reaction, safe_reply
from ..polling.polling_state import lifecycle_strategy
from ..user_state import PENDING_THREAD_ID, PENDING_THREAD_TEXT
from ..user_state import PENDING_TOPIC_NAME
from ... import window_query
from ...thread_router import thread_router
from ...session import session_manager
from ...user_preferences import user_preferences
from ...window_state_store import CCGRAM_CREATED_WINDOW_ORIGIN
from ...tmux_manager import send_to_window, tmux_manager
from ...utils import handle_general_topic_message, is_general_topic

if TYPE_CHECKING:
    from telegram import Bot, Chat
    from telegram.ext import ContextTypes
    from ...telegram_client import TelegramClient

logger = structlog.get_logger()

PENDING_DELIVERY_NOTICE = "\U0001f4ac Will deliver once the agent starts."

_DIR_INPUT_PREFIXES = ("cd ", "dir ", "path ")
_SESSION_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}\Z")


def _is_named_session_request(text: str) -> bool:
    """Return whether an unbound-topic message is a tmux session name."""
    return bool(_SESSION_NAME_RE.fullmatch(text.strip()))


def _extract_directory_input(text: str, allow_bare: bool = False) -> str:
    """Return explicit directory input from a Telegram message, or empty string."""
    raw = text.strip()
    if not raw:
        return ""

    lower = raw.lower()
    for prefix in _DIR_INPUT_PREFIXES:
        if lower.startswith(prefix):
            return raw[len(prefix) :].strip()

    if raw.startswith(("~", "/", "./", "../")) or raw == ".":
        return raw

    if allow_bare:
        return raw

    return ""


def _resolve_directory_input(
    text: str, base_path: str | None = None, allow_bare: bool = False
) -> str:
    """Resolve explicit directory input to an existing absolute directory path."""
    raw_path = _extract_directory_input(text, allow_bare=allow_bare)
    if not raw_path:
        return ""

    if raw_path == ".":
        path = (
            Path(base_path).expanduser().resolve()
            if base_path
            else Path.cwd().resolve()
        )
        return str(path) if path.is_dir() else ""

    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        base = Path(base_path).expanduser() if base_path else Path.cwd()
        path = base / path

    try:
        resolved = path.resolve()
    except OSError:
        return ""

    try:
        if not resolved.is_dir():
            return ""
    except OSError:
        return ""

    return str(resolved)


async def _create_shell_session_for_directory(
    message: Message,
    user_data: dict | None,
    user_id: int,
    thread_id: int,
    selected_path: str,
    topic_name: str = "",
    pending_text: str = "",
    bot: Bot | None = None,
) -> None:
    """Create a shell session directly in the selected directory and bind to topic."""
    topic_session_name = topic_name or Path(selected_path).name
    success, err_msg, created_wname, created_wid = await tmux_manager.create_window(
        selected_path,
        session_name=tmux_manager.topic_session_name(topic_session_name),
        window_name=topic_session_name,
        launch_command="",
    )
    if not success:
        await safe_reply(message, f"❌ Failed to create session: {err_msg}")
        if user_data is not None:
            clear_browse_state(user_data)
        return

    if user_data is not None:
        clear_browse_state(user_data)
        clear_window_picker_state(user_data)

    user_preferences.update_user_mru(user_id, selected_path)
    session_manager.set_window_origin(created_wid, CCGRAM_CREATED_WINDOW_ORIGIN)
    session_manager.set_window_cwd(created_wid, selected_path)
    session_manager.set_window_provider(created_wid, "shell")
    session_manager.set_window_approval_mode(created_wid, "normal")
    await tmux_manager.stamp_pane_title(created_wid, "shell")

    # Lazy: break text_handler <-> shell circular dependency at import time
    from ..shell.shell_prompt_orchestrator import ensure_setup

    # Lazy: break text_handler <-> directory_callbacks circular dependency at import time
    from ..topics.directory_callbacks import _wait_for_shell_ready

    await _wait_for_shell_ready(created_wid)
    await ensure_setup(created_wid, "auto")

    thread_router.bind_thread(
        user_id, thread_id, created_wid, window_name=created_wname
    )
    chat = message.chat
    if chat and chat.type in ("group", "supergroup"):
        thread_router.set_group_chat_id(user_id, thread_id, chat.id)

    await safe_reply(
        message,
        f"Session `{created_wname}` (tmux `{tmux_manager.topic_session_name(created_wname)}`) "
        f"created at `{selected_path}`.\nBound to this topic. Send commands here.",
    )
    if pending_text and bot is not None:
        await _forward_message(
            created_wid,
            user_id,
            thread_id,
            pending_text,
            PTBTelegramClient(bot),
            message,
        )


async def _handle_session_start_directory_input(
    thread_id: int | None,
    text: str,
    user_data: dict | None,
    message: Message,
) -> bool:
    """Handle explicit directory input while session-start UI is active."""
    if thread_id is None or not user_data:
        return False

    state = user_data.get(STATE_KEY)
    if state not in (STATE_SELECTING_WINDOW, STATE_BROWSING_DIRECTORY):
        return False

    pending_tid = user_data.get(PENDING_THREAD_ID)
    if pending_tid != thread_id:
        return False

    base_path = ""
    is_browsing = state == STATE_BROWSING_DIRECTORY
    if is_browsing:
        base_path = user_data.get(BROWSE_PATH_KEY, "")

    raw_path = _extract_directory_input(text, allow_bare=is_browsing)
    if not raw_path:
        if state != STATE_SELECTING_WINDOW:
            return False
        start_path = str(Path.cwd())
        msg_text, keyboard, subdirs = build_directory_browser(
            start_path, user_id=message.from_user.id if message.from_user else None
        )
        clear_window_picker_state(user_data)
        user_data[STATE_KEY] = STATE_BROWSING_DIRECTORY
        user_data[BROWSE_PATH_KEY] = start_path
        user_data[BROWSE_PAGE_KEY] = 0
        user_data[BROWSE_DIRS_KEY] = subdirs
        await safe_reply(message, msg_text, reply_markup=keyboard)
        return True

    selected_path = _resolve_directory_input(text, base_path, allow_bare=is_browsing)
    if not selected_path:
        await safe_reply(message, f"Directory not found: `{raw_path}`")
        return True

    await _create_shell_session_for_directory(
        message,
        user_data,
        message.from_user.id if message.from_user else 0,
        thread_id,
        selected_path,
        user_data.get(PENDING_TOPIC_NAME, ""),
        user_data.get(PENDING_THREAD_TEXT, "") if user_data else "",
    )
    return True


async def _check_ui_guards(
    user_data: dict | None, thread_id: int | None, message: Message
) -> bool:
    """Block text while a window picker or directory browser is active.

    Returns True if the message was handled (blocked), False to continue.
    """
    if not user_data:
        return False

    # Window picker guard
    if user_data.get(STATE_KEY) == STATE_SELECTING_WINDOW:
        pending_tid = user_data.get(PENDING_THREAD_ID)
        if pending_tid == thread_id:
            await safe_reply(
                message,
                "Please use the window picker above, or tap Cancel.",
            )
            return True
        # Stale picker state from a different thread — clear it
        clear_window_picker_state(user_data)
        user_data.pop(PENDING_THREAD_ID, None)
        user_data.pop(PENDING_THREAD_TEXT, None)

    # Directory browser guard
    if user_data.get(STATE_KEY) == STATE_BROWSING_DIRECTORY:
        pending_tid = user_data.get(PENDING_THREAD_ID)
        if pending_tid == thread_id:
            await safe_reply(
                message,
                "Please use the directory browser above, or tap Cancel.",
            )
            return True
        # Stale browsing state from a different thread — clear it
        clear_browse_state(user_data)
        user_data.pop(PENDING_THREAD_ID, None)
        user_data.pop(PENDING_THREAD_TEXT, None)

    return False


async def _handle_named_session_request(
    message: Message,
    user_data: dict | None,
    user_id: int,
    thread_id: int,
    requested_name: str,
    pending_text: str = "",
    client: TelegramClient | None = None,
) -> None:
    """Attach an existing named session or create it in the configured directory."""
    target_session = tmux_manager.topic_session_name(requested_name)
    sessions = await tmux_manager.list_sessions()
    existing = next(
        (session for session in sessions if session.window_name == target_session),
        None,
    )
    if existing is None:
        await _create_shell_session_for_directory(
            message,
            user_data,
            user_id,
            thread_id,
            config.session_working_directory,
            requested_name,
        )
        return

    if user_data is not None:
        clear_browse_state(user_data)
        clear_window_picker_state(user_data)
    thread_router.bind_thread(
        user_id,
        thread_id,
        existing.window_id,
        window_name=requested_name,
    )
    chat = message.chat
    if chat and chat.type in ("group", "supergroup"):
        thread_router.set_group_chat_id(user_id, thread_id, chat.id)
    await safe_reply(
        message,
        f"Attached session `{requested_name}` (tmux `{target_session}`) to this topic.",
    )
    if pending_text:
        if client is None:
            return
        await _forward_message(
            existing.window_id,
            user_id,
            thread_id,
            pending_text,
            client,
            message,
        )


async def _handle_stored_topic_session(
    message: Message,
    user_data: dict | None,
    user_id: int,
    thread_id: int,
    text: str,
    client: TelegramClient | None,
) -> bool:
    """Rebind an unbound topic to its strictly name-matched tmux session."""
    from ..status.topic_emoji import get_stored_topic_name

    topic_name = get_stored_topic_name(message.chat.id, thread_id)
    if not topic_name:
        return False
    await _handle_named_session_request(
        message,
        user_data,
        user_id,
        thread_id,
        topic_name,
        pending_text=text,
        client=client,
    )
    return True


async def _handle_named_session_text(
    message: Message,
    user_data: dict | None,
    user_id: int,
    thread_id: int,
    text: str,
) -> bool:
    """Create or attach a session named explicitly by an unbound topic message."""
    requested_name = text.strip()
    if not _is_named_session_request(requested_name):
        return False
    await _handle_named_session_request(
        message, user_data, user_id, thread_id, requested_name
    )
    return True


async def _handle_unbound_topic(  # noqa: C901
    user_id: int,
    thread_id: int,
    text: str,
    user_data: dict | None,
    message: Message,
    client: TelegramClient | None = None,
) -> bool:
    """Show window picker or directory browser for an unbound topic.

    Returns True if the topic is unbound (handled), False if already bound.
    """
    window_id = thread_router.get_window_for_thread(user_id, thread_id)
    if window_id is not None:
        return False

    topic_name = (
        (message.reply_to_message.forum_topic_created.name or "")
        if message.reply_to_message and message.reply_to_message.forum_topic_created
        else ""
    )
    if (
        not topic_name
        and message.reply_to_message
        and message.reply_to_message.forum_topic_edited
    ):
        topic_name = message.reply_to_message.forum_topic_edited.name or ""
    if not topic_name:
        topic_name = message.chat.title or message.chat.username or "topic"

    if await _handle_stored_topic_session(
        message, user_data, user_id, thread_id, text, client
    ):
        return True
    if await _handle_named_session_text(message, user_data, user_id, thread_id, text):
        return True

    selected_path = _resolve_directory_input(text)
    if selected_path:
        logger.info(
            "Unbound topic: path from message selected %s (user=%d, thread=%d)",
            selected_path,
            user_id,
            thread_id,
        )
        await _create_shell_session_for_directory(
            message,
            user_data,
            user_id,
            thread_id,
            selected_path,
            topic_name,
        )
        return True

    all_windows = await tmux_manager.list_windows()
    if config.tmux_external_patterns:
        external_windows = await tmux_manager.discover_external_sessions()
        all_windows.extend(external_windows)
    bound_ids = {bound_wid for _, _, bound_wid in thread_router.iter_thread_bindings()}
    unbound = [
        (w.window_id, w.window_name, w.cwd)
        for w in all_windows
        if w.window_id not in bound_ids and (not w.cwd or Path(w.cwd).exists())
    ]
    logger.debug(
        "Window picker check: all=%s, bound=%s, unbound=%s",
        [w.window_name for w in all_windows],
        bound_ids,
        [name for _, name, _ in unbound],
    )

    if unbound:
        logger.info(
            "Unbound topic: showing window picker (%d unbound windows, user=%d, thread=%d)",
            len(unbound),
            user_id,
            thread_id,
        )
        msg_text, keyboard, win_ids = build_window_picker(unbound)
        if user_data is not None:
            user_data[STATE_KEY] = STATE_SELECTING_WINDOW
            user_data[UNBOUND_WINDOWS_KEY] = win_ids
            user_data[PENDING_THREAD_ID] = thread_id
            user_data[PENDING_THREAD_TEXT] = text
            user_data[PENDING_TOPIC_NAME] = topic_name
        await safe_reply(message, msg_text, reply_markup=keyboard)
        await safe_reply(message, PENDING_DELIVERY_NOTICE)
        return True

    # No unbound windows — show directory browser to create a new session
    logger.info(
        "Unbound topic: showing directory browser (user=%d, thread=%d)",
        user_id,
        thread_id,
    )
    start_path = str(Path.cwd())
    msg_text, keyboard, subdirs = build_directory_browser(start_path, user_id=user_id)
    if user_data is not None:
        user_data[STATE_KEY] = STATE_BROWSING_DIRECTORY
        user_data[BROWSE_PATH_KEY] = start_path
        user_data[BROWSE_PAGE_KEY] = 0
        user_data[BROWSE_DIRS_KEY] = subdirs
        user_data[PENDING_THREAD_ID] = thread_id
        user_data[PENDING_THREAD_TEXT] = text
        user_data[PENDING_TOPIC_NAME] = topic_name
    await safe_reply(message, msg_text, reply_markup=keyboard)
    await safe_reply(message, PENDING_DELIVERY_NOTICE)
    return True


async def _handle_dead_window(
    window_id: str,
    user_id: int,
    thread_id: int,
    text: str,
    user_data: dict | None,
    message: Message,
) -> bool:
    """Show recovery UI or directory browser for a dead (killed) window.

    Returns True if the window is dead (handled), False if still alive.
    """
    w = await tmux_manager.find_window_by_id(window_id)
    if w:
        return False

    display = thread_router.get_display_name(window_id)
    view = window_query.view_window(window_id)
    cwd = view.cwd if view else ""
    start_path = cwd if (cwd and Path(cwd).is_dir()) else str(Path.cwd())
    logger.info(
        "Dead window %s (%s), falling back to directory browser at %s (user=%d, thread=%d)",
        window_id,
        display,
        start_path,
        user_id,
        thread_id,
    )
    thread_router.unbind_thread(user_id, thread_id)
    lifecycle_strategy.clear_dead_notification(user_id, thread_id)
    msg_text, keyboard, subdirs = build_directory_browser(start_path, user_id=user_id)
    if user_data is not None:
        user_data[STATE_KEY] = STATE_BROWSING_DIRECTORY
        user_data[BROWSE_PATH_KEY] = start_path
        user_data[BROWSE_PAGE_KEY] = 0
        user_data[BROWSE_DIRS_KEY] = subdirs
        user_data[PENDING_THREAD_ID] = thread_id
        user_data[PENDING_THREAD_TEXT] = text
    await safe_reply(
        message,
        f"Session `{display or window_id}` ended.\n\n{msg_text}",
        reply_markup=keyboard,
    )
    return True


async def _forward_message(
    window_id: str,
    _user_id: int,
    _thread_id: int,
    text: str,
    client: TelegramClient,
    message: Message,
) -> None:
    """Forward a text message to the bound tmux window."""
    await message.chat.send_action(ChatAction.TYPING)  # type: ignore[union-attr]
    success, err_message = await send_to_window(window_id, text, raw=False)
    if not success:
        await safe_reply(message, f"\u274c {err_message}")
        return

    await ack_reaction(client, message.chat.id, message.message_id)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Top-level ``MessageHandler(filters.TEXT & ~filters.COMMAND)`` callback.

    Performs auth, refreshes the user's scoped command menu for the
    current topic, and delegates to ``handle_text_message`` for the
    bool early-return routing chain.
    """
    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        if update.message:
            await safe_reply(update.message, "You are not authorized to use this bot.")
        return

    if not update.message or not update.message.text:
        return

    await handle_text_message(update, context)


async def _handle_unnamed_topic(bot: Bot, chat: Chat | None, message: Message) -> None:
    if chat and is_general_topic(message):
        await handle_general_topic_message(bot, message, chat.id)
    else:
        await safe_reply(message, "Use a named topic.")


async def _handle_rename_captures(
    user_data: dict | None,
    thread_id: int | None,
    text: str,
    message: Message,
    client: TelegramClient | None = None,
) -> bool:
    """Consume an in-flight pane or session rename reply."""
    if await apply_pane_rename(user_data, thread_id, text, message):
        return True
    return await apply_session_rename(user_data, thread_id, text, message, client)


async def handle_text_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Orchestrate text message handling via bool early-return chain.

    Called after auth validation in bot.py's text_handler.
    """
    user = update.effective_user
    message = update.message
    assert user is not None  # guaranteed by caller
    assert message is not None and message.text  # guaranteed by caller

    text = message.text
    thread_id = _get_thread_id(update)

    # Store group chat_id for forum topic message routing
    chat = message.chat
    if chat.type in ("group", "supergroup") and thread_id is not None:
        thread_router.set_group_chat_id(user.id, thread_id, chat.id)

    # Rename captures (pane or session)
    if await _handle_rename_captures(
        context.user_data, thread_id, text, message, PTBTelegramClient(context.bot)
    ):
        return

    if await _handle_session_start_directory_input(
        thread_id,
        text,
        context.user_data,
        message,
    ):
        return

    # UI guards (window picker / directory browser active)
    if await _check_ui_guards(context.user_data, thread_id, message):
        return

    # Must be in a named topic
    if thread_id is None:
        await _handle_unnamed_topic(context.bot, update.effective_chat, message)
        return

    # Unbound topic — show picker or browser
    if await _handle_unbound_topic(
        user.id,
        thread_id,
        text,
        context.user_data,
        message,
        PTBTelegramClient(context.bot),
    ):
        return

    # Bound topic — check if window is still alive
    window_id = thread_router.get_window_for_thread(user.id, thread_id)
    assert window_id is not None  # _handle_unbound_topic returned False

    if await _handle_dead_window(
        window_id, user.id, thread_id, text, context.user_data, message
    ):
        return

    await _forward_message(
        window_id,
        user.id,
        thread_id,
        text,
        PTBTelegramClient(context.bot),
        message,
    )
