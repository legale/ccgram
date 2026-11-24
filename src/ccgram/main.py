"""Application entry point — Click CLI dispatcher and bot bootstrap.

The ``main()`` function invokes the Click command group defined in cli.py,
which dispatches to subcommands (run, status).
``run_bot()`` contains the actual bot startup logic, called by the ``run``
command after CLI flags have been applied to the environment.

Module-level imports stay minimal on purpose: ``run_bot`` lazy-loads
``config``, ``utils``, and ``bot.create_bot`` so that ``ccgram status``
does not pay PTB startup cost.
"""

import logging
import os
import signal
import sys
from types import FrameType

import structlog
from telegram import Update

# Tracks which signal triggered shutdown (0 = none/clean exit)
_shutdown_signal = 0


def _install_signal_handlers() -> None:
    """Install signal handlers that record the signal and trigger PTB shutdown.

    PTB's default signal handling catches SIGINT/SIGTERM/SIGABRT and exits
    with code 0 after graceful shutdown.  The restart.sh supervisor needs the
    real signal exit code (130 for SIGINT=restart, 131 for SIGQUIT=stop), so
    we install our own handlers and tell PTB not to override them via
    ``stop_signals=None``.
    """

    def _on_signal(signum: int, _frame: FrameType | None) -> None:
        global _shutdown_signal
        _shutdown_signal = signum
        sig_name = signal.Signals(signum).name
        sys.stderr.write(f"\n[ccgram] {sig_name} received (pid={os.getpid()})\n")
        sys.stderr.flush()
        raise SystemExit

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGQUIT, _on_signal)


def _reraise_shutdown_signal() -> None:
    """Re-raise the original signal with default disposition.

    This makes the process exit with the correct code (128 + signum) so the
    parent (restart.sh) can distinguish restart from stop.
    """
    if _shutdown_signal:
        signal.signal(_shutdown_signal, signal.SIG_DFL)
        os.kill(os.getpid(), _shutdown_signal)


def setup_logging(log_level: str) -> None:
    """Configure structured, colored logging for interactive CLI use."""
    numeric_level = getattr(logging, log_level, None)
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(
                colors=True,
                pad_event=40,
            ),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging for third-party libs
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.dev.ConsoleRenderer(colors=True),
            foreign_pre_chain=[
                structlog.stdlib.add_log_level,
                structlog.stdlib.add_logger_name,
                structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            ],
        )
    )
    root.addHandler(handler)
    root.setLevel(logging.WARNING)

    logging.getLogger("ccgram").setLevel(numeric_level)
    for name in ("httpx", "httpcore", "telegram.ext"):
        logging.getLogger(name).setLevel(logging.WARNING)


def run_bot() -> None:
    """Start the bot. Called by the ``run`` Click command after env is set."""
    log_level = (os.environ.get("CCGRAM_LOG_LEVEL") or "INFO").upper()
    setup_logging(log_level)

    try:
        # Lazy: config validates env at import time; deferring lets us catch
        # ValueError and emit a friendly error before the click subcommand layer.
        from .config import config
    except ValueError as e:
        # Lazy: ccgram_dir resolves the config dir without instantiating
        # `config` (which already failed); needed to print the .env path hint.
        from .utils import ccgram_dir

        config_dir = ccgram_dir()
        env_path = config_dir / ".env"
        print(f"Error: {e}\n")
        print(f"Create {env_path} with the following content:\n")
        print("  TELEGRAM_BOT_TOKEN=your_bot_token_here")
        print("  ALLOWED_USERS=your_telegram_user_id")
        print()
        print("Get your bot token from @BotFather on Telegram.")
        print("Get your user ID from @userinfobot on Telegram.")
        sys.exit(1)

    logger = structlog.get_logger()

    logger.info("Allowed users: %s", config.allowed_users)

    # Lazy: main runs `ccgram` startup; defer imports until the relevant subcommand executes
    from . import __version__

    dev = "+dev" if "+unknown" in __version__ or ".dev" in __version__ else ""
    logger.info("Starting ccgram %s%s", __version__, dev)
    # Lazy: main runs `ccgram` startup; defer imports until the relevant subcommand executes
    from .bot import create_bot

    application = create_bot()
    _install_signal_handlers()
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        stop_signals=None,
    )

    _reraise_shutdown_signal()


def main() -> None:
    """Main entry point — dispatches via Click CLI group."""
    # Lazy: main runs `ccgram` startup; defer imports until the relevant subcommand executes
    from .cli import cli

    cli()


if __name__ == "__main__":
    main()
