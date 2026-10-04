"""Shared fixtures for integration tests.

Provides reusable fixtures for state directories, config patching,
and state-file management.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _default_replace_prompt_mode():
    """Default to replace mode so existing tests using ccgram:N❯ markers pass."""
    from ccgram.config import config

    original = config.prompt_mode
    config.prompt_mode = "replace"
    yield
    config.prompt_mode = original


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """Temp directory with empty state files and config patched to use it."""
    (tmp_path / "state.json").write_text("{}")
    monkeypatch.setattr(
        "ccgram.config.config.tmux_session_name",
        "ccgram",
    )

    return tmp_path


@pytest.fixture
def mock_bot():
    """A mock Telegram Bot with common methods stubbed."""
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot.edit_message_text = AsyncMock()
    bot.create_forum_topic = AsyncMock()
    bot.defaults = None
    bot.local_mode = False
    bot.base_url = "https://api.telegram.org/bot"
    bot.base_file_url = "https://api.telegram.org/file/bot"
    return bot
