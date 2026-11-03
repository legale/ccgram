"""Single-task polling coordinator for terminal status monitoring.

The runtime owns one background task. Each tick discovers the current tmux
windows, then polls all Telegram-bound windows sequentially in ``poll_sessions``.
The per-window function is a synchronous step in this task, not a worker.

Key components:
  - status_poll_loop: Background polling task (entry point for bot.py)
"""

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from telegram.error import TelegramError

from ...thread_router import thread_router
from ...tmux_manager import extract_sidecar_output, tmux_manager
from ...utils import log_throttled

if TYPE_CHECKING:
    from telegram import Bot
    from ...telegram_client import TelegramClient
    from ...tmux_manager import TmuxWindow

logger = structlog.get_logger()

# ── Timing constants ──────────────────────────────────────────────────────

_BACKOFF_MIN = 2.0
_BACKOFF_MAX = 30.0

_LoopError = (TelegramError, OSError, RuntimeError, ValueError)


def _as_client(bot_or_client: Any) -> "TelegramClient":
    # Lazy: avoid circular/early PTB load
    from ...telegram_client import PTBTelegramClient, TelegramClient

    if isinstance(bot_or_client, TelegramClient):
        return bot_or_client
    return PTBTelegramClient(bot_or_client)


# ── Main loop ─────────────────────────────────────────────────────────────


async def _handle_dead_session(
    client: "TelegramClient", user_id: int, thread_id: int, wid: str
) -> None:
    # Lazy: topic lifecycle state resolved at tick time
    from ..topics.topic_lifecycle import (
        is_dead_notified,
        mark_dead_notified,
        start_autoclose_timer,
    )

    # Lazy: message_sender helpers resolved at tick time
    from ..messaging_pipeline.message_sender import rate_limit_send_message

    session_name = wid.split(":", 1)[0]
    if await tmux_manager.session_exists(session_name):
        return
    if is_dead_notified(user_id, thread_id, wid):
        return
    chat_id = thread_router.resolve_chat_id(user_id, thread_id)
    display = thread_router.get_display_name(wid)
    start_autoclose_timer(user_id, thread_id, "dead", time.monotonic())
    await rate_limit_send_message(
        client,
        chat_id,
        f"Session `{display}` ended.",
        message_thread_id=thread_id,
    )
    mark_dead_notified(user_id, thread_id, wid)


async def _poll_sidecar(
    client: "TelegramClient", chat_id: int, thread_id: int, wid: str
) -> None:
    sidecar_pane_id = tmux_manager.get_sidecar_pane_id(wid)
    if not isinstance(sidecar_pane_id, str) or not sidecar_pane_id:
        return

    sidecar_text = await tmux_manager.capture_pane_by_id(
        sidecar_pane_id,
        with_ansi=False,
        include_history=True,
        window_id=wid,
    )
    if sidecar_text is None:
        tmux_manager.forget_sidecar_pane(wid)
        return

    marker = tmux_manager.get_sidecar_completion_marker(wid)
    if not marker:
        tmux_manager.forget_sidecar_pane(wid)
        return

    sidecar_text, completed = extract_sidecar_output(sidecar_text, marker)
    if sidecar_text is None:
        return

    # Lazy: config and diff updater are resolved at tick time.
    from ...config import config
    from ..status.topic_status_diff import update_topic_status_diff

    if config.topic_status_diff_enabled:
        await update_topic_status_diff(
            client,
            chat_id,
            thread_id,
            wid,
            sidecar_text or "(no output)",
            target="sidecar",
            force=completed,
        )
    if completed:
        killed = await tmux_manager.kill_pane(sidecar_pane_id, window_id=wid)
        if killed:
            tmux_manager.forget_sidecar_pane(wid)


async def _poll_window(
    client: "TelegramClient",
    user_id: int,
    thread_id: int,
    wid: str,
    window: "TmuxWindow | None",
) -> None:
    if window is None:
        await _handle_dead_session(client, user_id, thread_id, wid)
        return

    if window.cwd:
        # Lazy: session_manager resolved at tick time
        from ...session import session_manager

        session_manager.set_window_cwd(
            wid, str(Path(window.cwd).expanduser().resolve())
        )

    pane_text = await tmux_manager.capture_pane(
        window.window_id, with_ansi=True, include_history=True
    )
    # Lazy: config resolved at tick time
    from ...config import config

    # Lazy: diff updater resolved at tick time
    from ..status.topic_status_diff import update_topic_status_diff

    chat_id = thread_router.resolve_chat_id(user_id, thread_id)
    if config.topic_status_diff_enabled and pane_text:
        await update_topic_status_diff(client, chat_id, thread_id, wid, pane_text)

    await _poll_sidecar(client, chat_id, thread_id, wid)


async def poll_sessions(
    bot_or_client: Any,
    bindings: list[tuple[int, int, str]],
    window_lookup: dict[str, "TmuxWindow"],
) -> None:
    """Poll bound tmux windows sequentially in the caller's task."""
    client = _as_client(bot_or_client)
    for user_id, thread_id, wid in bindings:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(window_id=wid)
        try:
            window = window_lookup.get(wid)
            await _poll_window(client, user_id, thread_id, wid, window)
        except (TelegramError, OSError) as e:
            log_throttled(
                logger,
                f"status-update:{user_id}:{thread_id}",
                "Status update error for user %s thread %s: %s",
                user_id,
                thread_id,
                e,
            )


async def status_poll_loop(bot: "Bot") -> None:
    """Background task to poll terminal status for all thread-bound windows."""
    # Lazy: config resolved at runtime
    from ...config import config as _cfg

    # Lazy: PTBTelegramClient resolved at runtime
    from ...telegram_client import PTBTelegramClient

    # Lazy: periodic_tasks cycle avoidance
    from .periodic_tasks import run_lifecycle_tasks, run_periodic_tasks

    poll_interval = _cfg.status_poll_interval
    client = PTBTelegramClient(bot)
    logger.info("Status polling started (interval: %ss)", poll_interval)
    timers = {"topic_check": 0.0, "broker": 0.0, "sweep": 0.0, "live_view": 0.0}
    _error_streak = 0
    while True:
        try:
            all_windows = await tmux_manager.list_windows()
            await run_periodic_tasks(client, all_windows, timers)

            all_windows = await tmux_manager.list_windows()
            window_lookup = {w.window_id: w for w in all_windows}

            await poll_sessions(
                client,
                list(thread_router.iter_thread_bindings()),
                window_lookup,
            )

            await run_lifecycle_tasks(client, all_windows)

        except _LoopError:
            logger.exception("Status poll loop error")
            backoff_delay = min(_BACKOFF_MAX, _BACKOFF_MIN * (2**_error_streak))
            _error_streak += 1
            await asyncio.sleep(backoff_delay)
            continue
        except Exception:
            logger.exception("Unexpected error in status poll loop")
            backoff_delay = min(_BACKOFF_MAX, _BACKOFF_MIN * (2**_error_streak))
            _error_streak += 1
            await asyncio.sleep(backoff_delay)
            continue

        _error_streak = 0
        await asyncio.sleep(poll_interval)
