"""Contract test for minimal Telegram to tmux bridge commands."""

from ccgram.handlers.registry import COMMAND_HANDLERS
from ccgram.handlers.command_meta import get_command_info

MINIMAL_CONTRACT_COMMANDS = frozenset({"commands", "bind", "unbind", "sessions", "ses"})


def test_minimal_contract_commands_are_registered() -> None:
    """Verify that all four minimal contract commands are registered."""
    names = {
        name
        for handler in COMMAND_HANDLERS
        for name in (
            get_command_info(handler).name,
            *get_command_info(handler).aliases,
        )
    }
    assert MINIMAL_CONTRACT_COMMANDS.issubset(names)
