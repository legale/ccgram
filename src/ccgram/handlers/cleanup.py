"""Unified cleanup API for topic state.

Orchestrates topic teardown: dispatches registered cleanups via
TopicStateRegistry, then handles infrastructure and bot-specific async
cleanup that cannot be registered (log throttle, mailbox I/O, status
messages and user_data).

Functions:
  - clear_topic_state: Clean up all memory state for a specific topic
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import structlog
from telegram.error import TelegramError

from ..telegram_client import PTBTelegramClient, TelegramClient

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

from ..config import config
from ..thread_router import thread_router
from ..tmux_manager import clear_vim_state, tmux_manager
from ..topic_tail import clear_topic_tail_state
from ..utils import handle_general_topic_message, is_general_topic, log_throttle_reset
from .callback_helpers import get_thread_id
from .command_history import clear_history
from .command_meta import command
from .live.live_view import clear_live_view
from .messaging_pipeline.message_sender import safe_reply
from .status.topic_status_diff import clear_topic_status_diff_state

logger = structlog.get_logger()


def _safe_call(fn: Callable[..., Any], *args: Any) -> None:
    try:
        fn(*args)
    except (
        OSError,
        ValueError,
        LookupError,
        TypeError,
        RuntimeError,
        AttributeError,
        ImportError,
    ):
        name = getattr(fn, "__qualname__", repr(fn))
        logger.warning("cleanup_function_failed", fn=name, exc_info=True)


def _clear_all_topic_state(
    user_id: int,
    thread_id: int,
    *,
    window_id: str | None = None,
    qualified_id: str | None = None,  # noqa: ARG001
    chat_id: int | None = None,
) -> None:
    """Explicitly clean up all per-topic, per-chat, per-window, and per-qualified state."""
    # Topic scope (user_id, thread_id)
    _safe_call(clear_history, user_id, thread_id)
    _safe_call(clear_live_view, user_id, thread_id)
    _safe_call(clear_topic_status_diff_state, user_id, thread_id)
    # Lazy: avoid circular import with topic_lifecycle
    from .topics.topic_lifecycle import clear_topic_lifecycle_state

    _safe_call(clear_topic_lifecycle_state, user_id, thread_id)

    # Chat scope (chat_id, thread_id)
    if chat_id is not None:
        _safe_call(clear_topic_tail_state, chat_id, thread_id)

    # Window scope (window_id)
    if window_id:
        _safe_call(clear_vim_state, window_id)
        _safe_call(tmux_manager.forget_sidecar_pane, window_id)


async def clear_topic_state(
    user_id: int,
    thread_id: int,
    client: TelegramClient | None = None,  # noqa: ARG001 - callback API
    user_data: dict[str, Any] | None = None,  # noqa: ARG001 - callback API
    window_id: str | None = None,
    *,
    window_dead: bool = True,
) -> None:
    """Clear all memory state associated with a topic.

    Explicitly calls all state cleanup functions, then handles
    bot-specific async cleanup and infrastructure I/O.

    Args:
        window_dead: When False, skip qualified-scope cleanup because
            the tmux window is still alive (e.g. topic close, //unbind).
            Window-scope callbacks (toolbar labels, monitor state, etc.) always
            run.  Shell prompt orchestrator state is cleared separately, only
            when the window is truly dead, to preserve skip/offer state for
            live sessions.
    """
    chat_id = thread_router.resolve_chat_id(user_id, thread_id)

    qualified_id: str | None = None
    if window_id and window_dead:
        qualified_id = f"{config.tmux_session_name}:{window_id}"

    # Direct explicit dispatch — all module-specific per-topic/window/chat state.
    # Always pass window_id so window-scope state (toolbar, monitor state, etc.) clears even when the window is still alive.
    # Shell prompt orchestrator state is handled below so it only clears on true window death.
    _clear_all_topic_state(
        user_id,
        thread_id,
        window_id=window_id,
        qualified_id=qualified_id,
        chat_id=chat_id,
    )

    # Infrastructure cleanup (formatted keys, file I/O — not registerable)
    log_throttle_reset(f"status-update:{user_id}:{thread_id}")
    if window_id:
        log_throttle_reset(f"topic-probe:{window_id}")


@command("unbind", "Close topic, keep session")
async def unbind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Close a topic without killing its tmux session."""
    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        return
    if not update.message:
        return

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

    client = PTBTelegramClient(context.bot)
    chat_id = thread_router.resolve_chat_id(user.id, thread_id)

    # Lazy: topic binding imports the topic lifecycle graph.
    from .topics.topic_binding import find_topic_session

    session = await find_topic_session(chat_id, thread_id)
    if session is None:
        await safe_reply(
            update.message, "This topic is not bound to a managed session."
        )
        return

    window_id = session.window_id
    session_name = session.window_name
    if not await tmux_manager.clear_session_topic(session_name):
        await safe_reply(update.message, "Cannot clear tmux topic metadata.")
        return

    session.topic_ref = None
    await clear_topic_state(
        user.id,
        thread_id,
        client,
        context.user_data,
        window_id=window_id,
        window_dead=False,
    )
    thread_router.unbind_thread(user.id, thread_id)
    try:
        await client.close_forum_topic(chat_id=chat_id, message_thread_id=thread_id)
    except TelegramError as e:
        await tmux_manager.set_session_topic(session_name, chat_id, thread_id)
        session.topic_ref = (chat_id, thread_id)
        # Lazy: topic binding imports the topic lifecycle graph.
        from .topics.topic_binding import bind_runtime

        bind_runtime(user.id, chat_id, thread_id, session)
        await safe_reply(update.message, f"Cannot close topic: {e}")
        return
    await safe_reply(
        update.message, f"Unbound topic. Session `{session_name}` is still running."
    )


@command("killme", "Kill session and close topic")
async def killme_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kill the managed tmux session and close the Telegram forum topic."""
    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        return
    if not update.message:
        return

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

    window_id = thread_router.get_window_for_thread(user.id, thread_id)
    if not window_id:
        chat_id = thread_router.resolve_chat_id(user.id, thread_id)
        # Lazy: topic binding imports the topic lifecycle graph.
        from .topics.topic_binding import find_topic_session

        session = await find_topic_session(chat_id, thread_id)
        if session:
            window_id = session.window_id
        else:
            await safe_reply(
                update.message, "This topic is not bound to a managed session."
            )
            return

    client = PTBTelegramClient(context.bot)
    # Lazy: avoid circular import between cleanup and sessions_dashboard
    from .sessions_dashboard import kill_session_and_unbind

    success, msg = await kill_session_and_unbind(window_id, client)
    if not success:
        await safe_reply(update.message, msg)


COMMAND_HANDLERS = (unbind_command, killme_command)
