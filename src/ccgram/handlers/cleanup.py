"""Unified cleanup API for topic state.

Orchestrates topic teardown: dispatches registered cleanups via
TopicStateRegistry, then handles infrastructure and bot-specific async
cleanup that cannot be registered (log throttle, mailbox I/O, status
messages and user_data).

Functions:
  - clear_topic_state: Clean up all memory state for a specific topic
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telegram.error import TelegramError

from ..telegram_client import PTBTelegramClient, TelegramClient

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

from ..config import config
from ..mailbox import Mailbox
from ..thread_router import thread_router
from ..topic_state_registry import topic_state
from ..tmux_manager import tmux_manager
from ..utils import handle_general_topic_message, is_general_topic, log_throttle_reset
from .callback_helpers import get_thread_id
from .messaging_pipeline.message_queue import enqueue_status_update
from .messaging_pipeline.message_sender import safe_reply
from .status.status_bubble import clear_status_msg_info


async def clear_topic_state(
    user_id: int,
    thread_id: int,
    client: TelegramClient | None = None,
    user_data: dict[str, Any] | None = None,  # noqa: ARG001 - callback API
    window_id: str | None = None,
    *,
    window_dead: bool = True,
) -> None:
    """Clear all memory state associated with a topic.

    Dispatches registered cleanups via TopicStateRegistry, then handles
    bot-specific async cleanup and infrastructure I/O that cannot be
    registered as simple callbacks.

    Args:
        window_dead: When False, skip mailbox/qualified-scope cleanup because
            the tmux window is still alive (e.g. topic close, //unbind).
            Window-scope callbacks (toolbar labels, screen buffer, etc.) always
            run.  Shell prompt orchestrator state is cleared separately, only
            when the window is truly dead, to preserve skip/offer state for
            live sessions.
    """
    chat_id = thread_router.resolve_chat_id(user_id, thread_id)

    qualified_id: str | None = None
    if window_id and window_dead:
        qualified_id = f"{config.tmux_session_name}:{window_id}"

    # Enqueue status-message delete BEFORE registry clears the message ID
    if client is not None:
        await enqueue_status_update(
            client,
            user_id,
            window_id or "",
            None,
            thread_id=thread_id,
        )
    else:
        clear_status_msg_info(user_id, thread_id)

    # Registry dispatch — all module-specific per-topic/window/chat state.
    # Always pass window_id so window-scope callbacks (toolbar, screen buffer,
    # monitor state, etc.) run even when the window is still alive.
    # Shell prompt orchestrator state is excluded from the registry and handled
    # below so it only clears on true window death.
    topic_state.clear_all(
        user_id,
        thread_id,
        window_id=window_id,
        qualified_id=qualified_id,
        chat_id=chat_id,
    )
    if window_id and window_dead:
        # Lazy: cleanup → shell.shell_prompt_orchestrator → shell/__init__ →
        # polling → window_tick → apply → cleanup forms a cycle. Keep lazy.
        from .shell.shell_prompt_orchestrator import clear_state as _clear_shell_prompt

        _clear_shell_prompt(window_id)

    # Infrastructure cleanup (formatted keys, file I/O — not registerable)
    log_throttle_reset(f"status-update:{user_id}:{thread_id}")
    if window_id:
        log_throttle_reset(f"topic-probe:{window_id}")
        mb = Mailbox(config.mailbox_dir)
        if qualified_id is not None:
            mb.clear_inbox(qualified_id)


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
    await enqueue_status_update(client, user.id, window_id, None, thread_id)
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
