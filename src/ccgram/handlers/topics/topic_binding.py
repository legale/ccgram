"""Bind Telegram topics to authoritative tmux sessions."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from telegram import Update
from telegram.error import BadRequest, TelegramError

from ...config import config
from ...telegram_client import PTBTelegramClient

from ...session import session_manager
from ...thread_router import ThreadRouter, thread_router
from ...tmux_manager import TmuxWindow, tmux_manager
from ..callback_helpers import get_thread_id
from ..command_meta import command
from ..messaging_pipeline.message_sender import safe_reply

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = structlog.get_logger()


def _valid_name(name: str) -> bool:
    return bool(name) and len(name) <= 50 and "\n" not in name  # noqa: PLR2004


async def _managed_sessions() -> list[TmuxWindow]:
    return await tmux_manager.list_sessions()


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
    sessions = await _managed_sessions()
    topic_ref = (chat_id, thread_id)
    matches = [session for session in sessions if session.topic_ref == topic_ref]
    if len(matches) == 1:
        return matches[0]
    matches_thread = [
        session
        for session in sessions
        if session.topic_ref and session.topic_ref[1] == thread_id
    ]
    return matches_thread[0] if len(matches_thread) == 1 else None


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
        return None, "Topic is not bound. Use `//bind <key>`."
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


@command("bind", "Bind topic to session", params="<key>")
async def bind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``//bind key`` — attach to prefix_key session and bind the topic."""
    user = update.effective_user
    message = update.message
    chat = update.effective_chat
    if not user or not config.is_user_allowed(user.id) or not message or not chat:
        return

    thread_id = get_thread_id(update)
    if thread_id is None:
        await safe_reply(message, "Error: not inside a topic")
        return

    args = list(context.args or [])
    if len(args) != 1 or not _valid_name(args[0].strip()):  # noqa: PLR2004
        await safe_reply(message, "Usage: //bind <key>")
        return
    name = args[0].strip()
    target_session = tmux_manager.topic_session_name(name)

    # Verify session exists (tmux attach target)
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        "has-session",
        "-t",
        target_session,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode().strip() or f"can't find session: {target_session}"
        await safe_reply(message, err)
        return

    # Switch attached tmux client if any
    switch_proc = await asyncio.create_subprocess_exec(
        "tmux",
        "switch-client",
        "-t",
        target_session,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await switch_proc.communicate()

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
            logger.warning("Topic rename failed on bind: %s", exc)
    except TelegramError as exc:
        logger.warning("Topic rename failed on bind: %s", exc)

    await safe_reply(message, target_session)


@command("rename", "Rename topic and session", params="<name>")
async def rename_command(  # noqa: C901
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """``//rename <new_name>`` — rename both the Telegram topic and the bound tmux session."""
    user = update.effective_user
    message = update.message
    chat = update.effective_chat
    if not user or not config.is_user_allowed(user.id) or not message or not chat:
        return

    thread_id = get_thread_id(update)
    if thread_id is None:
        await safe_reply(message, "Error: not inside a topic")
        return

    args = list(context.args or [])
    if len(args) != 1:  # noqa: PLR2004
        await safe_reply(message, "Usage: //rename <new_name>")
        return

    # Lazy: avoid circular imports
    from ...utils import strip_emoji_prefix

    raw_name = strip_emoji_prefix(args[0].strip()).strip()
    name = raw_name.removeprefix(config.tmux_session_prefix).strip()
    if not _valid_name(name):
        await safe_reply(message, f"Invalid topic name: {name!r}")
        return

    # Find the bound session
    session = await find_topic_session(chat.id, thread_id)
    if session is None:
        window_id = thread_router.get_window_for_thread(user.id, thread_id)
        if not window_id:
            window_id = thread_router.get_window_for_chat_thread(chat.id, thread_id)
        if window_id:
            session_name = window_id.split(":", 1)[0]
            sessions = await tmux_manager.list_sessions()
            session = next((s for s in sessions if s.window_name == session_name), None)

    if session is None:
        await safe_reply(
            message, "Topic is not bound to any session. Use //bind <key>."
        )
        return

    target = tmux_manager.topic_session_name(name)
    old_name = session.window_name
    if session.window_name != target:
        if not await tmux_manager.rename_session(session.window_name, target):
            await safe_reply(
                message,
                f"Failed to rename tmux session {old_name} -> {target}",
            )
            return
        session.window_name = target
        if ":" in session.window_id:
            bare_id = session.window_id.rsplit(":", 1)[1]
            session.window_id = f"{target}:{bare_id}"
        await tmux_manager.set_session_topic(target, chat.id, thread_id)
        session.topic_ref = (chat.id, thread_id)
        bind_runtime(user.id, chat.id, thread_id, session)

    bind_runtime(user.id, chat.id, thread_id, session)

    # Lazy: clear dead notification and autoclose if a rename race occurred
    from .topic_lifecycle import clear_topic_lifecycle_state

    clear_topic_lifecycle_state(user.id, thread_id)

    # Rename Telegram topic if supported
    tg_renamed = False
    client = PTBTelegramClient(context.bot)
    try:
        res = await client.edit_forum_topic(chat.id, thread_id, name=name)
        tg_renamed = bool(res)
    except BadRequest as exc:
        if "topic_not_modified" in exc.message.lower():
            tg_renamed = True
        else:
            logger.warning("Topic rename failed on //rename: %s", exc)
    except TelegramError as exc:
        logger.warning("Topic rename failed on //rename: %s", exc)

    logger.info(
        "Renamed topic via //rename: %s -> %s (chat=%d, thread=%d, tg_ok=%s)",
        old_name,
        target,
        chat.id,
        thread_id,
        tg_renamed,
    )
    await safe_reply(message, f"{target} (топик: {name})")


COMMAND_HANDLERS = (bind_command, rename_command)
