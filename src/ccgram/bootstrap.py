"""Application bootstrap — wires post_init and post_shutdown lifecycle.

`bot.py` defines the PTB ``Application`` factory + lifecycle delegates;
the actual lifecycle (status polling and mini-app) lives here as named functions so
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

from telegram.error import TelegramError

from .handlers.messaging_pipeline.message_queue import shutdown_workers
from .handlers.polling.polling_coordinator import status_poll_loop
from .session import session_manager
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


async def sync_bot_commands(application: Application) -> None:
    """Sync the single-source-of-truth command list with Telegram menu across all scopes."""
    # Lazy: avoid circular import between bootstrap and telegram scopes
    from telegram import (
        BotCommandScopeAllGroupChats,
        BotCommandScopeAllPrivateChats,
        BotCommandScopeChat,
        BotCommandScopeDefault,
    )

    # Lazy: avoid circular import with config
    from .config import config

    # Lazy: avoid circular import between bootstrap and registry
    from .handlers.registry import get_telegram_menu_commands

    commands = get_telegram_menu_commands()
    bot = application.bot

    scopes = [
        BotCommandScopeDefault(),
        BotCommandScopeAllPrivateChats(),
        BotCommandScopeAllGroupChats(),
    ]
    for uid in config.allowed_users:
        scopes.append(BotCommandScopeChat(uid))
    if config.group_id:
        scopes.append(BotCommandScopeChat(config.group_id))

    for scope in scopes:
        try:
            await bot.set_my_commands(commands, scope=scope)
        except TelegramError as exc:
            logger.debug("Failed to set commands for scope %s: %s", scope, exc)

    logger.info(
        "Synced %d commands with Telegram menu across %d scopes",
        len(commands),
        len(scopes),
    )


async def bootstrap_application(application: Application) -> None:
    """Run the full post_init sequence in the prescribed order."""
    install_global_exception_handler()
    start_status_polling(application)
    await sync_bot_commands(application)

    # Lazy: main imports bot at top, bot imports bootstrap; hoisting forms
    # main → bot → bootstrap → main on cold import.
    # Lazy: bootstrap ↔ main cycle
    from .main import start_miniapp_if_enabled

    await start_miniapp_if_enabled()


async def shutdown_runtime() -> None:
    """Run the post_shutdown teardown sequence."""
    global _status_poll_task

    if _status_poll_task is not None:
        _status_poll_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _status_poll_task
        _status_poll_task = None
        logger.info("Status polling stopped")

    await shutdown_workers()

    # Lazy: main → bot → bootstrap cycle (same as start path).
    from .main import stop_miniapp_if_enabled

    await stop_miniapp_if_enabled()

    session_manager.flush_state()


def reset_for_testing() -> None:
    """Clear bootstrap module state and inner callback registrations.

    Each e2e/integration test that drives ``bootstrap_application`` must
    reset state between runs — F2.6 made the register_*_callbacks fail
    loud on double registration, and bootstrap caches its own
    ``_callbacks_wired`` flag too.
    """
    global _status_poll_task

    _status_poll_task = None
