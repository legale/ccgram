"""Bind Telegram topics to authoritative tmux sessions."""

from __future__ import annotations

from ...config import config
from ...tmux_manager import TmuxWindow, tmux_manager
from ...thread_router import ThreadRouter, thread_router


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
    return session.window_id


async def find_topic_session(chat_id: int, thread_id: int) -> TmuxWindow | None:
    """Return the single managed session bound to a Telegram topic."""
    topic_ref = (chat_id, thread_id)
    matches = [
        session
        for session in await tmux_manager.list_sessions()
        if session.window_name.startswith(config.tmux_session_prefix)
        and session.topic_ref == topic_ref
    ]
    return matches[0] if len(matches) == 1 else None


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
