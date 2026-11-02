"""Tests for //commands handler in handlers/commands/__init__.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccgram.handlers.commands import commands_command

_CO = "ccgram.handlers.commands"


def _make_update(
    *,
    user_id: int = 100,
    thread_id: int = 42,
) -> MagicMock:
    update = MagicMock()
    update.effective_user = MagicMock(id=user_id)
    msg = AsyncMock()
    msg.message_thread_id = thread_id
    msg.chat.type = "supergroup"
    msg.chat.id = -100999
    msg.chat.is_forum = True
    msg.is_topic_message = True
    update.message = msg
    return update


@pytest.fixture(autouse=True)
def _allow_user():
    with patch(f"{_CO}.config.is_user_allowed", return_value=True):
        yield


class TestCommandsCommand:
    async def test_unauthorized_user_returns_early(self) -> None:
        with (
            patch(f"{_CO}.config.is_user_allowed", return_value=False),
            patch(f"{_CO}.safe_reply", new_callable=AsyncMock) as mock_reply,
        ):
            await commands_command(_make_update(), MagicMock())
        mock_reply.assert_not_called()

    async def test_no_message_returns_early(self) -> None:
        update = _make_update()
        update.message = None
        with patch(f"{_CO}.safe_reply", new_callable=AsyncMock) as mock_reply:
            await commands_command(update, MagicMock())
        mock_reply.assert_not_called()

    async def test_replies_with_commands_help(self) -> None:
        update = _make_update()
        with patch(f"{_CO}.safe_reply", new_callable=AsyncMock) as mock_reply:
            await commands_command(update, MagicMock())

        mock_reply.assert_called_once()
        text = mock_reply.call_args.args[1]
        assert "Команды бота" in text
        assert "//commands" in text
        assert "//screenshot" in text
        assert "//live" in text
