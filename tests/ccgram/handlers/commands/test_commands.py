"""Tests for bot commands (//commands, //ctrl-c)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Message, Update, User

from ccgram.handlers.commands import commands_command, ctrl_c_command


@pytest.fixture
def update():
    u = MagicMock(spec=Update)
    u.effective_user = User(id=100, is_bot=False, first_name="User")
    msg = AsyncMock(spec=Message)
    msg.message_id = 42
    msg.message_thread_id = 77
    msg.text = "//ctrl-c"
    u.message = msg
    u.effective_message = msg
    return u


@pytest.fixture
def context():
    ctx = MagicMock()
    ctx.bot = AsyncMock()
    return ctx


class TestCommandsCommand:
    async def test_displays_commands_list(self, update, context):
        with (
            patch("ccgram.config.config.is_user_allowed", return_value=True),
            patch("ccgram.handlers.commands.safe_reply", new_callable=AsyncMock) as mock_reply,
        ):
            await commands_command(update, context)

        mock_reply.assert_awaited_once()
        text = mock_reply.call_args[0][1]
        assert "//killme" in text
        assert "//ctrl-c" in text
        assert "//unbind" in text


class TestCtrlCCommand:
    async def test_unauthorized_user_ignored(self, update, context):
        with (
            patch("ccgram.config.config.is_user_allowed", return_value=False),
            patch("ccgram.tmux_manager.tmux_manager.send_keys", new_callable=AsyncMock) as mock_send,
        ):
            await ctrl_c_command(update, context)

        mock_send.assert_not_called()

    async def test_outside_topic_replies_error(self, update, context):
        update.message.message_thread_id = None
        with (
            patch("ccgram.config.config.is_user_allowed", return_value=True),
            patch("ccgram.utils.is_general_topic", return_value=False),
            patch("ccgram.handlers.commands.safe_reply", new_callable=AsyncMock) as mock_reply,
        ):
            await ctrl_c_command(update, context)

        mock_reply.assert_awaited_once()
        assert "inside a topic" in mock_reply.call_args[0][1]

    async def test_sends_ctrl_c_to_tmux_and_sidecar(self, update, context):
        with (
            patch("ccgram.config.config.is_user_allowed", return_value=True),
            patch("ccgram.thread_router.thread_router.get_window_for_thread", return_value="@1"),
            patch("ccgram.thread_router.thread_router.resolve_chat_id", return_value=-100),
            patch("ccgram.tmux_manager.tmux_manager.get_sidecar_pane_id", return_value="%5"),
            patch("ccgram.tmux_manager.tmux_manager.send_keys_to_pane", new_callable=AsyncMock) as mock_sidecar_send,
            patch("ccgram.tmux_manager.tmux_manager.send_keys", new_callable=AsyncMock, return_value=True) as mock_send,
            patch("ccgram.handlers.messaging_pipeline.message_sender.ack_reaction", new_callable=AsyncMock) as mock_ack,
        ):
            await ctrl_c_command(update, context)

        mock_sidecar_send.assert_awaited_once_with("%5", "C-c", enter=False, literal=False, window_id="@1")
        mock_send.assert_awaited_once_with("@1", "C-c", enter=False, literal=False)
        mock_ack.assert_awaited_once()
