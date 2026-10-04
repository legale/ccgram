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
from telegram.error import TelegramError

from ... import window_query
from ...config import config
from ...telegram_client import TelegramClient
from ...tmux_manager import send_to_window, tmux_manager
from ...thread_router import thread_router
from ...utils import log_throttle_sweep
from ..live.live_view import tick_live_views
from ..messaging.msg_broker import BROKER_CYCLE_INTERVAL, SWEEP_INTERVAL
from ..topics.topic_lifecycle import (
    check_autoclose_timers,
    probe_topic_existence,
    prune_stale_state,
)
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


def _topic_name(window_id: str) -> str:
    return thread_router.get_display_name(window_id).strip()


def _bound_sessions() -> dict[str, tuple[int, int, str]]:
    bound: dict[str, tuple[int, int, str]] = {}
    for user_id, thread_id, window_id in thread_router.iter_thread_bindings():
        actual_name = _session_name(window_id)
        topic_name = _topic_name(window_id)
        session_name = actual_name
        if (
            actual_name == tmux_manager.session_name
            and topic_name
            and not topic_name.startswith("@")
        ):
            session_name = tmux_manager.topic_session_name(topic_name)
        bound.setdefault(
            session_name, (user_id, thread_id, window_id)
        )
    return bound


async def _create_topic_for_session(
    client: TelegramClient, session_name: str, window_id: str
) -> tuple[int, int, str] | None:
    if not config.allowed_users:
        logger.warning(
            "Cannot reconcile tmux session %s: no authorized users are configured",
            session_name,
        )
        return None

    user_id = min(config.allowed_users)
    chat_id = config.group_id or thread_router.get_forum_chat_id(user_id)
    if chat_id is None:
        logger.info(
            "Deferring topic creation for tmux session %s until a forum update "
            "reveals the chat ID",
            session_name,
        )
        return None

    topic_name = tmux_manager.topic_name_from_session_name(session_name).strip()
    if not topic_name:
        return None
    topic = await client.create_forum_topic(chat_id, name=topic_name)
    thread_router.bind_thread(user_id, topic.message_thread_id, window_id, topic_name)
    thread_router.set_group_chat_id(user_id, topic.message_thread_id, chat_id)
    logger.info(
        "Reconciled tmux session %s to Telegram topic %d",
        session_name,
        topic.message_thread_id,
    )
    return user_id, topic.message_thread_id, window_id


async def _ensure_session_for_topic(
    user_id: int, thread_id: int, window_id: str
) -> tuple[str, str] | None:
    topic_name = _topic_name(window_id)
    if not topic_name or topic_name.startswith("@"):
        return None
    session_name = tmux_manager.topic_session_name(topic_name)
    view = window_query.view_window(window_id)
    work_dir = view.cwd if view and view.cwd else config.session_working_directory
    success, _message, _created_name, created_window_id = await tmux_manager.create_window(
        work_dir,
        session_name=session_name,
        window_name=topic_name,
        start_agent=False,
    )
    if not success or not created_window_id:
        logger.warning("Failed to ensure tmux session %s", session_name)
        return None
    thread_router.bind_thread(user_id, thread_id, created_window_id, topic_name)
    logger.info(
        "Reconciled Telegram topic %d to tmux session %s",
        thread_id,
        session_name,
    )
    return session_name, created_window_id


async def _prime_session_runtime(
    session_name: str,
    user_id: int,
    thread_id: int,
    window_id: str,
    chat_id: int,
) -> None:
    if session_name in _session_runtime:
        return
    _session_runtime[session_name] = _SessionRuntime(
        window_id=window_id,
        user_id=user_id,
        thread_id=thread_id,
        chat_id=chat_id,
    )
    pane_text = await tmux_manager.capture_pane(window_id, with_ansi=True)
    if pane_text:
        prime_topic_status_diff(chat_id, thread_id, window_id, pane_text)


async def reconcile(
    client: TelegramClient, all_windows: list["TmuxWindow"]
) -> None:
    """Reconcile prefixed tmux sessions, topics, and volatile runtime state."""
    session_records = await tmux_manager.list_sessions()
    windows_by_session: dict[str, TmuxWindow] = {}
    for window in [*all_windows, *session_records]:
        name = _session_name(window.window_id)
        if name.startswith(config.tmux_session_prefix):
            windows_by_session.setdefault(name, window)

    bound = _bound_sessions()
    for session_name, window in windows_by_session.items():
        binding = bound.get(session_name)
        if binding is None:
            binding = await _create_topic_for_session(
                client, session_name, window.window_id
            )
            if binding is None:
                continue
            bound[session_name] = binding
        user_id, thread_id, window_id = binding
        chat_id = thread_router.resolve_chat_id(user_id, thread_id)
        await _prime_session_runtime(
            session_name, user_id, thread_id, window_id, chat_id
        )

    for session_name, (user_id, thread_id, window_id) in list(bound.items()):
        if session_name in windows_by_session:
            continue
        ensured = await _ensure_session_for_topic(user_id, thread_id, window_id)
        if ensured is None:
            continue
        ensured_name, ensured_window_id = ensured
        bound[ensured_name] = (user_id, thread_id, ensured_window_id)
        await _prime_session_runtime(
            ensured_name,
            user_id,
            thread_id,
            ensured_window_id,
            thread_router.resolve_chat_id(user_id, thread_id),
        )

    live_names = set(windows_by_session) | set(bound)
    for name in list(_session_runtime):
        if name not in live_names:
            _session_runtime.pop(name, None)


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
    """Send once, reconcile a missing window, then retry exactly once."""
    sender = send_fn or send_to_window
    result = await sender(window_id, text, raw=raw)
    if result[0] or not result[1].startswith("Window not found"):
        return result
    await reconcile(client, await tmux_manager.list_windows())
    retry_window_id = thread_router.get_window_for_thread(user_id, thread_id)
    if not retry_window_id:
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
        except OSError, TelegramError:
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

    await reconcile(client, all_windows)

    if now - timers["live_view"] >= config.live_view_interval:
        timers["live_view"] = now
        await tick_live_views(client)

    if now - timers["topic_check"] >= TOPIC_CHECK_INTERVAL:
        timers["topic_check"] = now
        await prune_stale_state(all_windows)
        await probe_topic_existence(client)
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
