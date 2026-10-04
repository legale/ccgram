"""Bind Telegram topics to authoritative tmux sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import Update
from telegram.error import BadRequest, TelegramError

from ...config import config
from ...telegram_client import PTBTelegramClient
from pathlib import Path

from ...session import session_manager
from ...thread_router import ThreadRouter, thread_router
from ...tmux_manager import TmuxWindow, tmux_manager
from ..callback_helpers import get_thread_id
from ..messaging_pipeline.message_sender import safe_reply

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

    from ...telegram_client import TelegramClient


def _valid_name(name: str) -> bool:
    return bool(name) and len(name) <= 50 and "\n" not in name  # noqa: PLR2004


async def _managed_sessions() -> list[TmuxWindow]:
    return [
        session
        for session in await tmux_manager.list_sessions()
        if session.window_name.startswith(config.tmux_session_prefix)
    ]


def bind_runtime(
    user_id: int,
    chat_id: int,
    thread_id: int,
    session: TmuxWindow,
    *,
    router: ThreadRouter = thread_router,
) -> str:
    """Bind volatile routing from one Telegram topic to one tmux session."""
    name = tmux_manager.topic_name_from_session_name(session.window_name)
    router.bind_thread(user_id, thread_id, session.window_id, window_name=name)
    router.set_group_chat_id(user_id, thread_id, chat_id)
    if session.cwd:
        session_manager.set_window_cwd(
            session.window_id, str(Path(session.cwd).expanduser().resolve())
        )
    return session.window_id


def _drop_runtime_ref(
    chat_id: int,
    thread_id: int,
    *,
    router: ThreadRouter = thread_router,
) -> None:
    for user_id, bound_thread, _window_id in list(router.iter_thread_bindings()):
        if bound_thread != thread_id:
            continue
        if router.resolve_chat_id(user_id, thread_id) == chat_id:
            router.unbind_thread(user_id, thread_id)


async def _create_session(name: str) -> tuple[TmuxWindow | None, str | None]:
    target = tmux_manager.topic_session_name(name)
    ok, msg, _name, window_id = await tmux_manager.create_window(
        config.session_working_directory,
        session_name=target,
        window_name=name,
        start_agent=False,
    )
    if not ok or not window_id:
        return None, f"Failed to create tmux session {target}: {msg}"
    return (
        TmuxWindow(
            window_id=window_id,
            window_name=target,
            cwd=str(Path(config.session_working_directory).expanduser().resolve()),
        ),
        None,
    )


async def find_topic_session(chat_id: int, thread_id: int) -> TmuxWindow | None:
    """Return the single managed session bound to a Telegram topic."""
    topic_ref = (chat_id, thread_id)
    matches = [
        session
        for session in await _managed_sessions()
        if session.topic_ref == topic_ref
    ]
    return matches[0] if len(matches) == 1 else None


async def ensure_topic_session(  # noqa: C901, PLR0911, PLR0912
    user_id: int,
    chat_id: int,
    thread_id: int,
    topic_name: str = "",
    *,
    rebind: bool = False,
    router: ThreadRouter = thread_router,
) -> tuple[str | None, str | None]:
    """Resolve/create a session and store the Telegram identity in tmux."""
    sessions = await _managed_sessions()
    topic_ref = (chat_id, thread_id)
    linked = [session for session in sessions if session.topic_ref == topic_ref]
    if len(linked) > 1:
        return None, f"Multiple tmux sessions are bound to Telegram topic {thread_id}."

    topic_name = topic_name.strip()
    if not topic_name:
        if linked:
            return bind_runtime(
                user_id, chat_id, thread_id, linked[0], router=router
            ), None
        return None, "Topic is not bound. Use `//bind <name>`."
    if not _valid_name(topic_name):
        return None, "Invalid session name."

    target = tmux_manager.topic_session_name(topic_name)
    target_session = next(
        (session for session in sessions if session.window_name == target), None
    )

    if linked and linked[0].window_name == target:
        return bind_runtime(user_id, chat_id, thread_id, linked[0], router=router), None

    if not rebind:
        if linked:
            return bind_runtime(
                user_id, chat_id, thread_id, linked[0], router=router
            ), None
        if target_session and target_session.topic_ref:
            return None, f"Session {target} is already bound to another Telegram topic."

    created = False
    if target_session is None:
        target_session, error = await _create_session(topic_name)
        if error or target_session is None:
            return None, error or f"Failed to create tmux session {target}."
        created = True

    old_linked = linked[0] if linked else None
    if old_linked and old_linked.window_name != target:  # noqa: SIM102
        if not await tmux_manager.clear_session_topic(old_linked.window_name):
            if created:
                await tmux_manager.kill_session(target)
            return None, f"Failed to unbind tmux session {old_linked.window_name}."

    old_target_ref = target_session.topic_ref
    if not await tmux_manager.set_session_topic(target, chat_id, thread_id):
        if old_linked and old_linked.window_name != target:
            await tmux_manager.set_session_topic(
                old_linked.window_name, chat_id, thread_id
            )
        if created:
            await tmux_manager.kill_session(target)
        return None, f"Failed to bind tmux session {target}."

    if old_linked and old_linked.window_name != target:
        _drop_runtime_ref(chat_id, thread_id, router=router)
        old_linked.topic_ref = None
    if old_target_ref and old_target_ref != topic_ref:
        _drop_runtime_ref(*old_target_ref, router=router)

    target_session.topic_ref = topic_ref
    return bind_runtime(
        user_id, chat_id, thread_id, target_session, router=router
    ), None


async def bind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``//bind foo`` — bind the current topic to ``cc_foo``."""
    user = update.effective_user
    message = update.message
    chat = update.effective_chat
    if not user or not config.is_user_allowed(user.id) or not message or not chat:
        return

    thread_id = get_thread_id(update)
    if thread_id is None:
        await safe_reply(message, "Use `//bind <name>` inside a Telegram topic.")
        return

    parts = (message.text or "").strip().split(maxsplit=1)
    if len(parts) != 2 or not _valid_name(parts[1].strip()):  # noqa: PLR2004
        await safe_reply(message, "Usage: `//bind <name>`")
        return
    name = parts[1].strip()

    window_id, error = await ensure_topic_session(
        user.id,
        chat.id,
        thread_id,
        name,
        rebind=True,
    )
    if error or not window_id:
        await safe_reply(message, error or "Failed to bind session.")
        return

    client = PTBTelegramClient(context.bot)
    try:
        await client.edit_forum_topic(chat.id, thread_id, name=name)
    except BadRequest as exc:
        if "topic_not_modified" not in exc.message.lower():
            await safe_reply(
                message,
                f"Bound to `{tmux_manager.topic_session_name(name)}`, but topic rename failed: {exc}",
            )
            return
    except TelegramError as exc:
        await safe_reply(
            message,
            f"Bound to `{tmux_manager.topic_session_name(name)}`, but topic rename failed: {exc}",
        )
        return

    await safe_reply(message, f"Bound to `{tmux_manager.topic_session_name(name)}`.")


async def create_from_all(
    user_id: int,
    chat_id: int,
    name: str,
    client: TelegramClient,
    *,
    router: ThreadRouter = thread_router,
) -> tuple[str | None, str | None]:
    """Create a new ``cc_<name>`` session and its Telegram topic."""
    name = name.strip()
    if not _valid_name(name):
        return None, "Invalid session name."

    target = tmux_manager.topic_session_name(name)
    if any(session.window_name == target for session in await _managed_sessions()):
        return None, f"Session {target} already exists."

    session, error = await _create_session(name)
    if error or session is None:
        return None, error or f"Failed to create tmux session {target}."

    try:
        topic = await client.create_forum_topic(chat_id, name=name)
    except TelegramError as exc:
        await tmux_manager.kill_session(target)
        return None, f"Failed to create Telegram topic {name}: {exc}"

    thread_id = topic.message_thread_id
    if not await tmux_manager.set_session_topic(target, chat_id, thread_id):
        try:  # noqa: SIM105 - topic deletion is best effort
            await client.delete_forum_topic(chat_id, thread_id)
        except TelegramError:
            pass
        await tmux_manager.kill_session(target)
        return None, f"Failed to bind tmux session {target}."

    session.topic_ref = (chat_id, thread_id)
    return bind_runtime(user_id, chat_id, thread_id, session, router=router), None
