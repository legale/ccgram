"""Periodic task orchestration for the polling subsystem.

Orchestrates time-gated tasks within the poll loop: message broker delivery,
mailbox sweep, spawn request processing, topic lifecycle management, live view
ticking, and state pruning.

Key components:
  - run_periodic_tasks: time-gated broker, sweep, live view tick, and topic check
  - run_lifecycle_tasks: per-tick autoclose and unbound window management
  - run_broker_cycle: message broker delivery (also called from hook_events)
"""

import time
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import structlog
from telegram.error import BadRequest, TelegramError

from ... import window_query
from ...config import config
from ...telegram_client import TelegramClient
from ...tmux_manager import send_to_window, tmux_manager
from ...thread_router import thread_router
from ...utils import log_throttle_sweep, log_throttled
from ..live.live_view import tick_live_views
from ..messaging.msg_broker import BROKER_CYCLE_INTERVAL, SWEEP_INTERVAL
from ..topics.topic_lifecycle import check_autoclose_timers, prune_stale_state
from ..messaging_pipeline.message_sender import is_thread_gone
from ..status.topic_status_diff import prime_topic_status_diff

if TYPE_CHECKING:
    from ...tmux_manager import TmuxWindow

logger = structlog.get_logger()

# ── Timing constants ──────────────────────────────────────────────────────

TOPIC_CHECK_INTERVAL = 60.0  # seconds


@dataclass
class _SessionRuntime:
    """Volatile runtime entry for one prefixed tmux session."""

    window_id: str
    user_id: int
    thread_id: int
    chat_id: int


_session_runtime: dict[str, _SessionRuntime] = {}


def _session_name(window_id: str) -> str:
    if ":" in window_id and not window_id.startswith("@"):
        return window_id.rsplit(":", 1)[0]
    return tmux_manager.session_name


def _runtime_user(session_name: str) -> int | None:
    runtime = _session_runtime.get(session_name)
    if runtime is not None:
        return runtime.user_id
    if not config.allowed_users:
        return None
    return min(config.allowed_users)


async def _bind_runtime(
    session: "TmuxWindow", user_id: int, chat_id: int, thread_id: int
) -> None:
    session_name = session.window_name
    topic_name = tmux_manager.topic_name_from_session_name(session_name)
    old = _session_runtime.get(session_name)
    changed = (
        old is None
        or old.window_id != session.window_id
        or old.thread_id != thread_id
        or old.chat_id != chat_id
    )
    if old and changed:
        if thread_router.get_window_for_thread(old.user_id, old.thread_id) == old.window_id:
            thread_router.unbind_thread(old.user_id, old.thread_id)

    thread_router.bind_thread(user_id, thread_id, session.window_id, topic_name)
    thread_router.set_group_chat_id(user_id, thread_id, chat_id)
    thread_router.remember_forum_chat_id(user_id, chat_id)
    _session_runtime[session_name] = _SessionRuntime(
        window_id=session.window_id,
        user_id=user_id,
        thread_id=thread_id,
        chat_id=chat_id,
    )
    if not changed:
        return
    pane_text = await tmux_manager.capture_pane(session.window_id, with_ansi=True)
    if pane_text:
        prime_topic_status_diff(chat_id, thread_id, session.window_id, pane_text)


async def _replace_missing_topic(
    client: TelegramClient, session: "TmuxWindow", runtime: _SessionRuntime
) -> None:
    session_name = session.window_name
    topic_name = tmux_manager.topic_name_from_session_name(session_name)
    topic = await client.create_forum_topic(runtime.chat_id, name=topic_name)
    thread_id = topic.message_thread_id
    if not await tmux_manager.set_session_topic(
        session_name, runtime.chat_id, thread_id
    ):
        try:
            await client.delete_forum_topic(runtime.chat_id, thread_id)
        except TelegramError:
            pass
        logger.error("Failed to store replacement topic for %s", session_name)
        return

    from ..cleanup import clear_topic_state

    await clear_topic_state(
        runtime.user_id,
        runtime.thread_id,
        client,
        window_id=runtime.window_id,
        window_dead=False,
    )
    thread_router.unbind_thread(runtime.user_id, runtime.thread_id)
    session.topic_ref = (runtime.chat_id, thread_id)
    await _bind_runtime(session, runtime.user_id, runtime.chat_id, thread_id)
    logger.info(
        "Recreated Telegram topic %d for tmux session %s", thread_id, session_name
    )


async def _sync_topic(
    client: TelegramClient, session: "TmuxWindow", runtime: _SessionRuntime
) -> None:
    topic_name = tmux_manager.topic_name_from_session_name(session.window_name)
    try:
        await client.reopen_forum_topic(runtime.chat_id, runtime.thread_id)
    except BadRequest as e:
        if is_thread_gone(e):
            await _replace_missing_topic(client, session, runtime)
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

    try:
        await client.edit_forum_topic(
            runtime.chat_id, runtime.thread_id, name=topic_name
        )
    except BadRequest as e:
        if "topic_not_modified" in e.message.lower():
            return
        if not is_thread_gone(e):
            log_throttled(
                logger,
                f"topic-sync:{session.window_name}",
                "Topic sync error for %s: %s",
                session.window_name,
                e,
            )
            return
        await _replace_missing_topic(client, session, runtime)
    except TelegramError as e:
        log_throttled(
            logger,
            f"topic-sync:{session.window_name}",
            "Topic sync error for %s: %s",
            session.window_name,
            e,
        )


async def reconcile(
    client: TelegramClient,
    _all_windows: list["TmuxWindow"],
    *,
    verify: bool = False,
) -> None:
    """Project authoritative managed tmux sessions into Telegram."""
    sessions = [
        session
        for session in await tmux_manager.list_sessions()
        if session.window_name.startswith(config.tmux_session_prefix)
    ]

    refs: dict[tuple[int, int], list[TmuxWindow]] = {}
    for session in sessions:
        if session.topic_ref is not None:
            refs.setdefault(session.topic_ref, []).append(session)
    duplicate_refs = {ref for ref, items in refs.items() if len(items) > 1}
    for ref in duplicate_refs:
        logger.error("Duplicate tmux topic binding %s", ref)

    live_names = {session.window_name for session in sessions}
    bound_names: set[str] = set()
    for session in sessions:
        if session.topic_ref is None or session.topic_ref in duplicate_refs:
            continue
        bound_names.add(session.window_name)
        chat_id, thread_id = session.topic_ref
        user_id = _runtime_user(session.window_name)
        if user_id is None:
            logger.warning("Cannot route %s: no authorized users", session.window_name)
            continue
        first_seen = session.window_name not in _session_runtime
        await _bind_runtime(session, user_id, chat_id, thread_id)
        runtime = _session_runtime[session.window_name]
        if first_seen or verify:
            await _sync_topic(client, session, runtime)

    for session_name in list(_session_runtime):
        if session_name in bound_names:
            continue
        runtime = _session_runtime[session_name]
        owns_route = (
            thread_router.get_window_for_thread(runtime.user_id, runtime.thread_id)
            == runtime.window_id
        )
        if owns_route and session_name not in live_names:
            try:
                await client.delete_forum_topic(runtime.chat_id, runtime.thread_id)
            except TelegramError as e:
                if not is_thread_gone(e):
                    log_throttled(
                        logger,
                        f"topic-delete:{session_name}",
                        "Topic delete error for %s: %s",
                        session_name,
                        e,
                    )
                    continue
        _session_runtime.pop(session_name, None)
        if owns_route:
            thread_router.unbind_thread(runtime.user_id, runtime.thread_id)


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
    """Send once; refresh runtime routing after a missing tmux window."""
    sender = send_fn or send_to_window
    result = await sender(window_id, text, raw=raw)
    if result[0] or not result[1].startswith("Window not found"):
        return result
    await reconcile(client, await tmux_manager.list_windows())
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
    await reconcile(client, all_windows, verify=verify_topics)

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
    client: TelegramClient, all_windows: list["TmuxWindow"]
) -> None:
    """Run per-tick topic lifecycle tasks."""
    await check_autoclose_timers(client)
