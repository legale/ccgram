"""Contract test for minimal Telegram to tmux bridge commands."""

from ccgram.handlers.registry import COMMAND_NAMES

MINIMAL_CONTRACT_COMMANDS = frozenset({"commands", "bind", "detach", "sessions", "ses"})


def test_minimal_contract_commands_are_registered() -> None:
    """Verify that all four minimal contract commands are registered."""
    assert MINIMAL_CONTRACT_COMMANDS.issubset(set(COMMAND_NAMES))
