"""Single-task polling coordinator for terminal status monitoring.

The runtime owns one background task. Each tick discovers the current tmux
windows, then polls all Telegram-bound windows sequentially in ``poll_sessions``.
The per-window function is a synchronous step in this task, not a worker.

Key components:
  - status_poll_loop: Background polling task (entry point for bot.py)
"""

import asyncio
from typing import TYPE_CHECKING

import structlog
from telegram.error import TelegramError

from ...thread_router import thread_router
from ...tmux_manager import tmux_manager
from ...utils import log_throttled
from . import window_tick

if TYPE_CHECKING:
    from telegram import Bot
    from ...tmux_manager import TmuxWindow

logger = structlog.get_logger()

# ── Timing constants ──────────────────────────────────────────────────────

_BACKOFF_MIN = 2.0
_BACKOFF_MAX = 30.0

_LoopError = (TelegramError, OSError, RuntimeError, ValueError)


# ── Main loop ─────────────────────────────────────────────────────────────


async def poll_sessions(
    bot: "Bot",
    bindings: list[tuple[int, int, str]],
    window_lookup: dict[str, "TmuxWindow"],
) -> None:
    """Poll bound tmux windows sequentially in the caller's task."""
    for user_id, thread_id, wid in bindings:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(window_id=wid)
        try:
            window = window_lookup.get(wid)
            await window_tick.tick_window(bot, user_id, thread_id, wid, window)
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
    # Lazy: status_poll_loop is launched once during bootstrap; keep the
    # config + telegram_client imports tied to the call site so the
    # polling package's cold path does not pull PTB.
    # Lazy: config singleton resolved at call time
    from ...config import config as _cfg

    # Lazy: PTBTelegramClient wraps the live PTB bot — resolved per-tick
    from ...telegram_client import PTBTelegramClient

    # Lazy: periodic_tasks transitively imports topics.topic_lifecycle,
    # which imports polling_state. Hoisting forms a cycle through
    # polling/__init__.py whenever a module reaches polling_state
    # before polling_coordinator finishes loading.
    # Lazy: periodic_tasks ↔ coordinator cycle
    from .periodic_tasks import run_lifecycle_tasks, run_periodic_tasks

    poll_interval = _cfg.status_poll_interval
    client = PTBTelegramClient(bot)
    logger.info("Status polling started (interval: %ss)", poll_interval)
    timers = {"topic_check": 0.0, "broker": 0.0, "sweep": 0.0, "live_view": 0.0}
    _error_streak = 0
    while True:
        try:
            all_windows = await tmux_manager.list_windows()
            window_lookup = {w.window_id: w for w in all_windows}

            await run_periodic_tasks(client, all_windows, timers)

            await poll_sessions(
                bot,
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
