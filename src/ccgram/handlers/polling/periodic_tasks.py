"""Periodic task orchestration for the polling subsystem."""

import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import structlog
from telegram.error import BadRequest, TelegramError

from ...config import config
from ...telegram_client import TelegramClient
from ...thread_router import thread_router
from ...tmux_manager import send_to_window, tmux_manager
from ...utils import log_throttle_sweep, log_throttled
from ..live.live_view import tick_live_views
from ..messaging.msg_broker import BROKER_CYCLE_INTERVAL, SWEEP_INTERVAL
from ..messaging_pipeline.message_sender import is_thread_gone
from ..status.topic_status_diff import prime_topic_status_diff
from ..topics.topic_lifecycle import check_autoclose_timers, prune_stale_state

if TYPE_CHECKING:
    from ...tmux_manager import TmuxWindow

logger = structlog.get_logger()

TOPIC_CHECK_INTERVAL = 60.0


def _topic_users(chat_id: int, thread_id: int) -> set[int]:
    users: set[int] = set()
    for user_id, bound_thread, _window_id in thread_router.iter_thread_bindings():
        if bound_thread != thread_id:
            continue
        if thread_router.resolve_chat_id(user_id, thread_id) == chat_id:
            users.add(user_id)
    return users


def _runtime_user(chat_id: int, thread_id: int) -> int | None:
    users = _topic_users(chat_id, thread_id)
    if users:
        return min(users)
    return min(config.allowed_users) if config.allowed_users else None


async def _clear_runtime_topic(
    client: TelegramClient,
    chat_id: int,
    thread_id: int,
    *,
    window_dead: bool,
) -> None:
    # Lazy: cleanup imports polling state and would create an import cycle here.
    from ..cleanup import clear_topic_state

    for user_id in list(_topic_users(chat_id, thread_id)):
        window_id = thread_router.get_window_for_thread(user_id, thread_id)
        await clear_topic_state(
            user_id,
            thread_id,
            client,
            window_id=window_id,
            window_dead=window_dead,
        )
        thread_router.unbind_thread(user_id, thread_id)


async def _bind_runtime(
    session: "TmuxWindow", user_id: int, chat_id: int, thread_id: int
) -> bool:
    """Bind volatile routing to one authoritative tmux session.

    Returns True when the route changed and the Screen baseline must be reset.
    """
    users = _topic_users(chat_id, thread_id)
    changed = not users or any(
        thread_router.get_window_for_thread(uid, thread_id) != session.window_id
        for uid in users
    )
    users.add(user_id)

    topic_name = tmux_manager.topic_name_from_session_name(session.window_name)
    for uid in users:
        if thread_router.get_window_for_thread(uid, thread_id) != session.window_id:
            thread_router.bind_thread(uid, thread_id, session.window_id, topic_name)
        thread_router.set_group_chat_id(uid, thread_id, chat_id)

    if changed:
        pane_text = await tmux_manager.capture_pane(session.window_id, with_ansi=True)
        if pane_text:
            prime_topic_status_diff(chat_id, thread_id, session.window_id, pane_text)
    return changed


async def _delete_runtime_topic(
    client: TelegramClient, chat_id: int, thread_id: int
) -> bool:
    """Delete a Telegram projection whose authoritative tmux session vanished."""
    try:
        await client.delete_forum_topic(chat_id, thread_id)
    except TelegramError as e:
        if not is_thread_gone(e):
            log_throttled(
                logger,
                f"topic-delete:{chat_id}:{thread_id}",
                "Topic delete error for %s:%s: %s",
                chat_id,
                thread_id,
                e,
            )
            return False

    await _clear_runtime_topic(client, chat_id, thread_id, window_dead=True)
    return True


async def _remove_missing_topic(
    client: TelegramClient,
    session: "TmuxWindow",
    chat_id: int,
    thread_id: int,
) -> None:
    """Remove a tmux session whose Telegram topic was deleted."""
    session_name = session.window_name
    if not await tmux_manager.kill_session(session_name):
        logger.warning("Failed to remove tmux session %s after topic deletion", session_name)
        return
    session.topic_ref = None
    await _clear_runtime_topic(client, chat_id, thread_id, window_dead=True)
    logger.info(
        "Removed tmux session %s after Telegram topic deletion",
        session_name,
    )


async def _sync_topic(
    client: TelegramClient,
    session: "TmuxWindow",
    chat_id: int,
    thread_id: int,
) -> None:
    """Force one Telegram topic to match its authoritative tmux session."""
    try:
        await client.reopen_forum_topic(chat_id, thread_id)
    except BadRequest as e:
        if is_thread_gone(e):
            await _remove_missing_topic(client, session, chat_id, thread_id)
            return
        if "topic_not_modified" not in e.message.lower():
            log_throttled(
                logger,
                f"topic-reopen:{session.window_name}",
                "Topic reopen error for %s: %s",
                session.window_name,
                e,
            )
            return
    except TelegramError as e:
        log_throttled(
            logger,
            f"topic-reopen:{session.window_name}",
            "Topic reopen error for %s: %s",
            session.window_name,
            e,
        )
        return

    name = tmux_manager.topic_name_from_session_name(session.window_name)
    try:
        await client.edit_forum_topic(chat_id, thread_id, name=name)
    except BadRequest as e:
        if "topic_not_modified" in e.message.lower():
            return
        if is_thread_gone(e):
            await _remove_missing_topic(client, session, chat_id, thread_id)
            return
        log_throttled(
            logger,
            f"topic-sync:{session.window_name}",
            "Topic sync error for %s: %s",
            session.window_name,
            e,
        )
    except TelegramError as e:
        log_throttled(
            logger,
            f"topic-sync:{session.window_name}",
            "Topic sync error for %s: %s",
            session.window_name,
            e,
        )


def _runtime_refs() -> set[tuple[int, int]]:
    refs: set[tuple[int, int]] = set()
    for user_id, thread_id, _window_id in thread_router.iter_thread_bindings():
        refs.add((thread_router.resolve_chat_id(user_id, thread_id), thread_id))
    return refs


async def _load_authoritative_sessions() -> list["TmuxWindow"]:
    return [
        s
        for s in await tmux_manager.list_sessions()
        if s.window_name.startswith(config.tmux_session_prefix)
    ]


def _prepare_bindings(
    sessions: list["TmuxWindow"],
) -> tuple[list[tuple["TmuxWindow", int, int, int]], set[tuple[int, int]]]:
    refs: dict[tuple[int, int], list[TmuxWindow]] = {}
    for s in sessions:
        if s.topic_ref is not None:
            refs.setdefault(s.topic_ref, []).append(s)

    dups = {ref for ref, items in refs.items() if len(items) > 1}
    for ref in dups:
        logger.error("Duplicate tmux topic binding %s", ref)

    bindings: list[tuple[TmuxWindow, int, int, int]] = []
    for s in sessions:
        if s.topic_ref is None or s.topic_ref in dups:
            continue
        chat_id, thread_id = s.topic_ref
        user_id = _runtime_user(chat_id, thread_id)
        if user_id is None:
            logger.warning("Cannot route %s: no authorized users", s.window_name)
            continue
        bindings.append((s, user_id, chat_id, thread_id))
    return bindings, dups


async def _sync_bindings(
    client: TelegramClient,
    bindings: list[tuple["TmuxWindow", int, int, int]],
    verify: bool,
) -> set[tuple[int, int]]:
    checked: set[tuple[int, int]] = set()
    for s, user_id, chat_id, thread_id in bindings:
        changed = await _bind_runtime(s, user_id, chat_id, thread_id)
        if changed or verify:
            await _sync_topic(client, s, chat_id, thread_id)
            checked.add((chat_id, thread_id))
    return checked


async def _clear_orphans(
    client: TelegramClient,
    sessions: list["TmuxWindow"],
    dups: set[tuple[int, int]],
) -> None:
    live = {
        s.topic_ref
        for s in sessions
        if s.topic_ref is not None and s.topic_ref not in dups
    }
    for chat_id, thread_id in _runtime_refs() - live - dups:
        await _clear_runtime_topic(client, chat_id, thread_id, window_dead=False)


async def _clear_dead_topics(
    client: TelegramClient,
    sessions: list["TmuxWindow"],
    checked: set[tuple[int, int]],
) -> None:
    """Kill tmux sessions whose Telegram thread IDs no longer exist."""
    # Lazy: topic name cache is only needed for the no-op Telegram check.
    from ..status.topic_emoji import get_stored_topic_name

    for session in sessions:
        if session.topic_ref is None or session.topic_ref in checked:
            continue
        chat_id, thread_id = session.topic_ref
        name = get_stored_topic_name(chat_id, thread_id)
        if not name:
            name = tmux_manager.topic_name_from_session_name(session.window_name)
        try:
            await client.edit_forum_topic(chat_id, thread_id, name=name)
        except BadRequest as exc:
            if is_thread_gone(exc) or "topic_not_modified" not in exc.message.lower():
                await _remove_missing_topic(client, session, chat_id, thread_id)
        except TelegramError as exc:
            log_throttled(
                logger,
                f"topic-check:{session.window_name}",
                "Topic check error for %s: %s",
                session.window_name,
                exc,
            )


async def reconcile(client: TelegramClient, *, verify: bool = False) -> None:
    """Project authoritative ``cc_*`` tmux sessions into Telegram."""
    # Получаем authoritative tmux-сессии.
    ss = await _load_authoritative_sessions()
    # Подготавливаем валидные привязки и конфликты.
    bs, dups = _prepare_bindings(ss)
    # Синхронизируем runtime и Telegram.
    checked = await _sync_bindings(client, bs, verify)
    # Проверяем живость thread_id, которые уже были синхронизированы ранее.
    await _clear_dead_topics(client, ss, checked)
    # Очищаем осиротевшие привязки.
    await _clear_orphans(client, ss, dups)


async def send_with_reconcile(
    client: TelegramClient,
    user_id: int,
    thread_id: int,
    window_id: str,
    text: str,
    *,
    raw: bool = False,
    send_fn: Callable[..., Awaitable[tuple[bool, str]]] | None = None,
) -> tuple[bool, str]:
    """Send once; reconcile and retry once only if the route changes."""
    sender = send_fn or send_to_window
    result = await sender(window_id, text, raw=raw)
    if result[0] or not result[1].startswith("Window not found"):
        return result

    await reconcile(client)
    retry_window_id = thread_router.get_window_for_thread(user_id, thread_id)
    if not retry_window_id or retry_window_id == window_id:
        return result
    return await sender(retry_window_id, text, raw=raw)


# ── Broker integration ────────────────────────────────────────────────────


async def run_broker_cycle(
    client: TelegramClient | None = None,
    idle_windows: frozenset[str] = frozenset(),
) -> None:
    """Run one broker delivery cycle (called from poll loop and hook_events)."""
    # Lazy: msg_broker is registered as a callback target via the broker
    # registry; importing it at top of periodic_tasks pulls the
    # messaging subpackage into the polling package's cold path.
    # Lazy: imports resolved per-tick so tests can swap singletons
    from ... import window_query

    # Lazy: imports resolved per-tick so tests can swap singletons
    from ...mailbox import Mailbox

    # Lazy: messaging ↔ polling cycle through msg_telegram
    from ..messaging.msg_broker import broker_delivery_cycle

    mailbox = Mailbox(config.mailbox_dir)
    await broker_delivery_cycle(
        mailbox=mailbox,
        tmux_mgr=tmux_manager,
        window_ids=window_query.iter_window_ids(),
        tmux_session=config.tmux_session_name,
        msg_rate_limit=config.msg_rate_limit,
        client=client,
        idle_windows=idle_windows,
    )
    if client is not None:
        await _run_spawn_cycle(client)


async def _run_spawn_cycle(client: TelegramClient) -> None:
    """Scan for file-based spawn requests and post approval keyboards or auto-approve."""
    # Lazy: msg_spawn pulls topic_orchestration which sits inside the
    # sync_command cycle; keep at call site.
    # Lazy: spawn pipeline reaches back into polling
    from ...spawn_request import pop_pending, scan_spawn_requests

    # Lazy: spawn pipeline reaches back into polling
    from ..messaging.msg_spawn import (
        handle_spawn_approval,
        post_spawn_approval_keyboard,
    )

    new_requests = scan_spawn_requests(spawn_timeout=config.msg_spawn_timeout)
    for req in new_requests:
        try:
            if req.auto or config.msg_auto_spawn:
                await handle_spawn_approval(
                    req.id, client, spawn_timeout=config.msg_spawn_timeout
                )
            else:
                posted = await post_spawn_approval_keyboard(
                    client, req.requester_window, req
                )
                if not posted:
                    pop_pending(req.id)
        except (OSError, TelegramError):
            pop_pending(req.id)
            logger.debug("Failed to process spawn request", request_id=req.id)


def _run_mailbox_sweep() -> None:
    """Run periodic mailbox sweep."""
    # Lazy: Mailbox is a leaf module; loading inside the sweep keeps
    # the polling subpackage's import surface narrow.
    # Lazy: imports resolved per-tick so tests can swap singletons
    from ...mailbox import Mailbox

    mailbox = Mailbox(config.mailbox_dir)
    removed = mailbox.sweep()
    if removed:
        logger.debug("Mailbox sweep removed %d messages", removed)


# ── Orchestration ──────────────────────────────────────────────────────────


async def run_periodic_tasks(
    client: TelegramClient,
    all_windows: list["TmuxWindow"],
    timers: dict[str, float],
) -> None:
    """Run time-gated periodic tasks (topic check, broker, sweep)."""
    now = time.monotonic()

    verify_topics = now - timers["topic_check"] >= TOPIC_CHECK_INTERVAL
    await reconcile(client, verify=verify_topics)

    if now - timers["live_view"] >= config.live_view_interval:
        timers["live_view"] = now
        await tick_live_views(client)

    if verify_topics:
        timers["topic_check"] = now
        await prune_stale_state(all_windows)
        log_throttle_sweep()

    if now - timers["broker"] >= BROKER_CYCLE_INTERVAL:
        timers["broker"] = now
        await run_broker_cycle(client)

    if now - timers["sweep"] >= SWEEP_INTERVAL:
        timers["sweep"] = now
        _run_mailbox_sweep()


async def run_lifecycle_tasks(
    client: TelegramClient,
    all_windows: list["TmuxWindow"],  # noqa: ARG001 - lifecycle API
) -> None:
    """Run per-tick topic lifecycle tasks."""
    await check_autoclose_timers(client)
