"""Click-based CLI for ccgram.

Defines the top-level command group and the ``run`` subcommand with
bot-configuration flags. Precedence: CLI flag > env var > .env > default.
``apply_args_to_env()`` sets os.environ for explicitly provided flags so
Config reads the overridden values.

Every subcommand body lazy-loads its workers (``run_bot``, ``status_main``).
``ccgram --help`` and ``ccgram --version`` stay snappy and avoid pulling PTB;
only the invoked subcommand pays its import cost.
"""

import os
from pathlib import Path

import click

_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class _DefaultToRun(click.Group):
    """Click group that runs the ``run`` command when invoked without a subcommand."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        # If the first arg is not a known command and not --help/--version,
        # prepend "run" so flags like -v go to the run command.
        if args and args[0] not in self.commands and not args[0].startswith("--"):
            args = ["run", *args]
        return super().parse_args(ctx, args)


@click.group(
    cls=_DefaultToRun,
    invoke_without_command=True,
    help="Manage tmux sessions from Telegram.",
)
@click.version_option(package_name="ccgram", prog_name="ccgram")
@click.pass_context
def cli(ctx: click.Context) -> None:
    if ctx.invoked_subcommand is None:
        ctx.invoke(run_cmd)


# --- run command -----------------------------------------------------------

# Mapping: click option name -> environment variable name
_FLAG_TO_ENV: list[tuple[str, str]] = [
    ("config_dir", "CCGRAM_DIR"),
    ("allowed_users", "ALLOWED_USERS"),
    ("tmux_session", "TMUX_SESSION_NAME"),
    ("group_id", "CCGRAM_GROUP_ID"),
]


def apply_args_to_env(**kwargs: object) -> None:
    """Set environment variables from explicitly provided CLI flags.

    Call BEFORE Config instantiation to ensure CLI flags take precedence.
    Only sets env vars for flags that were explicitly provided (not None).
    """
    verbose = kwargs.get("verbose", False)
    log_level = kwargs.get("log_level")

    if verbose:
        os.environ["CCGRAM_LOG_LEVEL"] = "DEBUG"
    elif log_level is not None:
        os.environ["CCGRAM_LOG_LEVEL"] = str(log_level).upper()

    for attr, env_var in _FLAG_TO_ENV:
        value = kwargs.get(attr)
        if value is None:
            continue
        if isinstance(value, Path):
            os.environ[env_var] = str(value.expanduser().resolve())
        else:
            os.environ[env_var] = str(value)


@cli.command("run")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
@click.option(
    "--log-level",
    type=click.Choice(_LOG_LEVELS, case_sensitive=False),
    default=None,
    help="Logging level.",
)
@click.option(
    "--config-dir",
    type=click.Path(path_type=Path),
    default=None,
    envvar="CCGRAM_DIR",
    help="Config directory (default: ~/.ccgram).",
)
@click.option(
    "--allowed-users",
    default=None,
    envvar="ALLOWED_USERS",
    help="Comma-separated Telegram user IDs.",
)
@click.option(
    "--tmux-session",
    default=None,
    envvar="TMUX_SESSION_NAME",
    help="Tmux session name (default: ccgram).",
)
@click.option(
    "--group-id",
    type=int,
    default=None,
    envvar="CCGRAM_GROUP_ID",
    help="Restrict to one Telegram group.",
)
@click.option(
    "--provider",
    default=None,
    hidden=True,
    help="Deprecated no-op flag kept for CLI compatibility.",
)
def run_cmd(**kwargs: object) -> None:
    """Start the bot with optional overrides."""
    apply_args_to_env(**kwargs)

    # Lazy: defer subcommand import until that command is invoked, keeping `ccgram --help` fast
    from .main import run_bot

    run_bot()


# --- status command --------------------------------------------------------


@cli.command("status")
def status_cmd() -> None:
    """Show running state."""
    # Lazy: defer subcommand import until that command is invoked, keeping `ccgram --help` fast
    from .status_cmd import status_main

    status_main()
