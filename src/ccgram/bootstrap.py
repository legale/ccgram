"""Application bootstrap — wires post_init and post_shutdown lifecycle.

`bot.py` defines the PTB ``Application`` factory + lifecycle delegates;
the actual lifecycle (status polling) lives here as named functions so
each step is independently testable.

Module-level state (``_status_poll_task``) is
created in post_init and torn down in post_shutdown — kept here, not
in ``bot.py``, so the lifecycle delegates stay one-liners.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import structlog

from .handlers.polling.polling_coordinator import status_poll_loop
from .utils import task_done_callback

if TYPE_CHECKING:
    from telegram.ext import Application

logger = structlog.get_logger()

_status_poll_task: asyncio.Task[None] | None = None


def install_global_exception_handler() -> None:
    """Install the asyncio last-resort exception handler."""
    asyncio.get_running_loop().set_exception_handler(_global_exception_handler)


def _global_exception_handler(
    _loop: asyncio.AbstractEventLoop, ctx: dict[str, object]
) -> None:
    """Last-resort handler for uncaught exceptions in asyncio tasks."""
    exc = ctx.get("exception")
    msg = ctx.get("message", "Unhandled exception in event loop")
    if isinstance(exc, BaseException):
        logger.error(
            "asyncio exception handler: %s",
            msg,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
    else:
        logger.error("asyncio exception handler: %s", msg)


def start_status_polling(application: Application) -> asyncio.Task[None]:
    """Spawn the status-polling background task."""
    global _status_poll_task

    _status_poll_task = asyncio.create_task(status_poll_loop(application.bot))
    _status_poll_task.add_done_callback(task_done_callback)
    logger.info("Status polling task started")
    return _status_poll_task


async def bootstrap_application(application: Application) -> None:
    """Run the full post_init sequence in the prescribed order."""
    install_global_exception_handler()
    start_status_polling(application)


async def shutdown_runtime() -> None:
    """Run the post_shutdown teardown sequence."""
    global _status_poll_task

    if _status_poll_task is not None:
        _status_poll_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _status_poll_task
        _status_poll_task = None
        logger.info("Status polling stopped")


def reset_for_testing() -> None:
    """Clear bootstrap module state between test runs."""
    global _status_poll_task

    _status_poll_task = None
