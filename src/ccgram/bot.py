"""Telegram bot factory + lifecycle delegates.

This is the top-level wiring point for the PTB ``Application``. The
actual handler bodies live in feature subpackages under ``handlers/``;
``register_all`` composes them into the application, and ``bootstrap``
runs the post-init / post-shutdown sequence.

Responsibilities kept here:
  - ``create_bot`` — PTB ``Application.builder()`` factory
  - lifecycle delegates: ``post_init``, ``post_shutdown``
  - ``_error_handler`` — top-level error funnel for the application
  - ``_group_filter`` / ``is_user_allowed`` — auth/filter helpers
"""

import os
import signal

import structlog
from telegram import Update
from telegram.error import BadRequest, Conflict, NetworkError
from telegram.ext import (
    Application,
    ContextTypes,
    TypeHandler,
    filters,
)

from . import bootstrap
from .config import config
from .handlers.registry import register_all
from .telegram_request import ResilientPollingHTTPXRequest

__all__ = [
    "create_bot",
    "is_user_allowed",
    "post_init",
    "post_shutdown",
]

logger = structlog.get_logger()


def is_user_allowed(user_id: int | None) -> bool:
    """Thin wrapper around ``config.is_user_allowed`` for None-safety."""
    return user_id is not None and config.is_user_allowed(user_id)


# Group filter: when CCGRAM_GROUP_ID is set, only process updates from that group.
# filters.ALL is a no-op — single-instance backward compat.
_group_filter: filters.BaseFilter = (
    filters.Chat(chat_id=config.group_id) if config.group_id else filters.ALL
)


# --- App lifecycle ---


async def post_init(application: Application) -> None:
    """Run the post_init wiring sequence — see ``bootstrap.bootstrap_application``."""
    await bootstrap.bootstrap_application(application)


async def post_shutdown(_application: Application) -> None:
    """Tear down runtime state — see ``bootstrap.shutdown_runtime``."""
    await bootstrap.shutdown_runtime()


async def _trace_update(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log every update before feature handlers process it."""
    message = update.effective_message
    logger.info(
        "telegram_update_received",
        update_id=update.update_id,
        message_id=message.message_id if message else None,
        chat_id=message.chat_id if message else None,
        thread_id=message.message_thread_id if message else None,
        text=message.text if message else None,
    )


async def _error_handler(_update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle bot-level errors from updater and handlers."""
    if isinstance(context.error, Conflict):
        logger.critical(
            "Another bot instance is polling with the same token. "
            "Shutting down to avoid conflicts."
        )
        os.kill(os.getpid(), signal.SIGINT)
        return
    if isinstance(context.error, BadRequest) and "too old" in str(context.error):
        logger.debug("Callback query expired (query too old)")
        return
    if isinstance(context.error, NetworkError) and not isinstance(
        context.error, BadRequest
    ):
        # PTB will retry automatically — not actionable; demoted from warning.
        logger.info("Transient network error (PTB will retry): %s", context.error)
        return
    logger.error("Unhandled bot error", exc_info=context.error)


def create_bot() -> Application:
    # Suppress PTBUserWarning about JobQueue (we intentionally don't use it for core tasks)
    # Lazy: only used inside the deprecation guard
    import warnings

    warnings.filterwarnings("ignore", message=".*JobQueue.*", category=UserWarning)
    application = (
        Application.builder()
        .token(config.telegram_bot_token)
        .request(ResilientPollingHTTPXRequest())
        .get_updates_request(ResilientPollingHTTPXRequest(connection_pool_size=1))
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_error_handler(_error_handler)
    application.add_handler(TypeHandler(Update, _trace_update), group=-1)
    register_all(application, _group_filter)

    return application
