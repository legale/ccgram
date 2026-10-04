"""Bind Telegram topics to authoritative tmux sessions."""

from __future__ import annotations

from telegram import CallbackQuery

from ...config import config
from ...tmux_manager import TmuxWindow, tmux_manager
from ...thread_router import ThreadRouter, thread_router


def _session_name(window_id: str) -> str:
    if ":" in window_id and not window_id.startswith("@"):
        return window_id.rsplit(":", 1)[0]
    return tmux_manager.session_name


def _runtime_bind(
    user_id: int,
    chat_id: int,
    thread_id: int,
    session: TmuxWindow,
    *,
    router: ThreadRouter = thread_router,
) -> str:
    name = tmux_manager.topic_name_from_session_name(session.window_name)
    router.bind_thread(user_id, thread_id, session.window_id, window_name=name)
    router.set_group_chat_id(user_id, thread_id, chat_id)
    router.remember_forum_chat_id(user_id, chat_id)
    return session.window_id


async def ensure_topic_session(
    user_id: int,
    chat_id: int,
    thread_id: int,
    topic_name: str = "",
    *,
    router: ThreadRouter = thread_router,
) -> tuple[str | None, str | None]:
    """Resolve or create the authoritative tmux session for a Telegram topic."""
    sessions = [
        session
        for session in await tmux_manager.list_sessions()
        if session.window_name.startswith(config.tmux_session_prefix)
    ]
    topic_ref = (chat_id, thread_id)
    linked = [session for session in sessions if session.topic_ref == topic_ref]
    if len(linked) > 1:
        return None, f"Multiple tmux sessions are bound to Telegram topic {thread_id}."
    if linked:
        return _runtime_bind(
            user_id, chat_id, thread_id, linked[0], router=router
        ), None

    topic_name = topic_name.strip()
    if not topic_name:
        return None, "Cannot determine this topic name. Rename the topic and try again."

    target = tmux_manager.topic_session_name(topic_name)
    session = next((item for item in sessions if item.window_name == target), None)
    if session and session.topic_ref and session.topic_ref != topic_ref:
        return (
            None,
            f'Topic "{topic_name}" conflicts with existing topic. '
            f'tmux session "{target}" is already bound. '
            "Rename this Telegram topic and try again.",
        )

    created = False
    if session is None:
        ok, msg, _name, window_id = await tmux_manager.create_window(
            config.session_working_directory,
            session_name=target,
            window_name=topic_name,
            start_agent=False,
        )
        if not ok or not window_id:
            return None, f"Failed to create tmux session {target}: {msg}"
        session = TmuxWindow(
            window_id=window_id,
            window_name=target,
            cwd=config.session_working_directory,
        )
        created = True

    if not await tmux_manager.set_session_topic(target, chat_id, thread_id):
        if created:
            await tmux_manager.kill_session(target)
        return None, f"Failed to bind tmux session {target}."

    session.topic_ref = topic_ref
    return _runtime_bind(user_id, chat_id, thread_id, session, router=router), None


async def bind_topic_to_window(
    query: CallbackQuery,
    user_id: int,
    thread_id: int,
    window_id: str,
    window_name: str,
    *,
    router: ThreadRouter = thread_router,
) -> bool:
    """Stamp an already-managed session and update volatile routing state."""
    chat = query.message.chat if query.message else None
    if not chat or not window_id or not window_name:
        return False
    session_name = _session_name(window_id)
    if not session_name.startswith(config.tmux_session_prefix):
        return False
    if not await tmux_manager.set_session_topic(session_name, chat.id, thread_id):
        return False
    router.bind_thread(user_id, thread_id, window_id, window_name=window_name)
    if chat.type in ("group", "supergroup"):
        router.set_group_chat_id(user_id, thread_id, chat.id)
    router.remember_forum_chat_id(user_id, chat.id)
    return True


async def rename_bound_topic(
    _client: object,
    user_id: int,
    thread_id: int,
    window_id: str,
    window_name: str,
    approval_mode: str,
    *,
    router: ThreadRouter = thread_router,
) -> None:
    """Rename a selected tmux session into the managed namespace."""
    _ = user_id, thread_id, approval_mode, router
    if not window_id or not window_name:
        return

    window = await tmux_manager.find_window_by_id(window_id)
    if not window or ":" not in window.window_id:
        return

    await tmux_manager.rename_window(window.window_id, window_name)

    session_name = window.window_id.rsplit(":", 1)[0]
    if not session_name:
        return
    new_name = tmux_manager.topic_session_name(window_name)
    if not await tmux_manager.rename_session(session_name, new_name):
        return
    chat_id = router.resolve_chat_id(user_id, thread_id)
    await tmux_manager.set_session_topic(new_name, chat_id, thread_id)
