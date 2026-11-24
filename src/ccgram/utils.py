"""Shared utility functions used across multiple CCGram modules.

Provides:
  - ccgram_dir(): resolve config directory from CCGRAM_DIR env var.
  - task_done_callback(): log unhandled exceptions from background asyncio tasks.
  - log_throttled() / log_throttle_reset() / log_throttle_sweep(): debug log throttling.
"""

import asyncio
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

# --- Log throttling -----------------------------------------------------------

_throttle_state: dict[str, tuple[float, str]] = {}


def log_throttled(
    log: Any,
    key: str,
    msg: str,
    *args: object,
    cooldown: float = 300.0,
    _clock: Callable[[], float] = time.monotonic,
) -> None:
    """Log at debug level, suppressing repeated identical messages per key.

    First occurrence always logs. Subsequent calls with the same *key* and
    identical formatted message are suppressed until *cooldown* seconds elapse.
    A changed message resets the timer and logs immediately.
    """
    formatted = msg % args if args else msg
    now = _clock()
    prev = _throttle_state.get(key)
    if prev and prev[1] == formatted and (now - prev[0]) < cooldown:
        return
    _throttle_state[key] = (now, formatted)
    log.debug(msg, *args)


def log_throttle_reset(prefix: str) -> None:
    """Clear throttle state for keys starting with *prefix*."""
    to_remove = [k for k in _throttle_state if k.startswith(prefix)]
    for k in to_remove:
        del _throttle_state[k]


def log_throttle_sweep(
    max_age: float = 600.0,
    _clock: Callable[[], float] = time.monotonic,
) -> int:
    """Remove throttle entries older than *max_age* seconds.

    Returns the number of entries removed.  Intended to be called
    periodically (e.g. every 60 s from the poll loop) to prevent
    unbounded growth of ``_throttle_state``.
    """
    now = _clock()
    stale = [k for k, (ts, _) in _throttle_state.items() if now - ts >= max_age]
    for k in stale:
        del _throttle_state[k]
    return len(stale)


CCGRAM_DIR_ENV = "CCGRAM_DIR"


def ccgram_dir() -> Path:
    """Resolve config directory from CCGRAM_DIR env var or default ~/.ccgram."""
    raw = os.environ.get(CCGRAM_DIR_ENV, "")
    return Path(raw) if raw else Path.home() / ".ccgram"


def task_done_callback(task: asyncio.Task[None]) -> None:
    """Log unhandled exceptions from background asyncio tasks.

    Attach to any fire-and-forget task via ``task.add_done_callback(task_done_callback)``.
    Suppresses CancelledError (normal shutdown).
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Background task %s failed", task.get_name(), exc_info=exc)
