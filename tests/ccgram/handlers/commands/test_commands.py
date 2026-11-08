"""Tests for bot commands (//commands, //ctrl-c)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Message, Update, User

from ccgram.handlers.commands import (
    arrows_command,
    commands_command,
    ctrl_c_command,
    ctrl_cc_command,
)


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
            patch(
                "ccgram.handlers.commands.safe_reply", new_callable=AsyncMock
            ) as mock_reply,
        ):
            await commands_command(update, context)

        mock_reply.assert_awaited_once()
        text = mock_reply.call_args[0][1]
        assert "//killme" in text
        assert "//ctrl-c" in text
        assert "//ctrl-cc" in text
        assert "//unbind" in text


class TestArrowsCommand:
    async def test_toggles_screen_delta_arrows(self, update, context):
        with (
            patch("ccgram.config.config.is_user_allowed", return_value=True),
            patch(
                "ccgram.thread_router.thread_router.resolve_chat_id",
                return_value=-100,
            ),
            patch(
                "ccgram.handlers.status.topic_status_diff.toggle_screen_diff_arrows",
                new_callable=AsyncMock,
                return_value=True,
            ) as toggle,
            patch(
                "ccgram.handlers.commands.safe_reply", new_callable=AsyncMock
            ) as reply,
        ):
            await arrows_command(update, context)

        toggle.assert_awaited_once()
        assert "on" in reply.call_args.args[1]


class TestCtrlCCommand:
    async def test_unauthorized_user_ignored(self, update, context):
        with (
            patch("ccgram.config.config.is_user_allowed", return_value=False),
            patch(
                "ccgram.tmux_manager.tmux_manager.send_keys", new_callable=AsyncMock
            ) as mock_send,
        ):
            await ctrl_c_command(update, context)

        mock_send.assert_not_called()

    async def test_outside_topic_replies_error(self, update, context):
        update.message.message_thread_id = None
        with (
            patch("ccgram.config.config.is_user_allowed", return_value=True),
            patch("ccgram.utils.is_general_topic", return_value=False),
            patch(
                "ccgram.handlers.commands.safe_reply", new_callable=AsyncMock
            ) as mock_reply,
        ):
            await ctrl_c_command(update, context)

        mock_reply.assert_awaited_once()
        assert "inside a topic" in mock_reply.call_args[0][1]

    async def test_sends_ctrl_c_to_tmux_and_sidecar(self, update, context):
        with (
            patch("ccgram.config.config.is_user_allowed", return_value=True),
            patch(
                "ccgram.thread_router.thread_router.get_window_for_thread",
                return_value="@1",
            ),
            patch(
                "ccgram.thread_router.thread_router.resolve_chat_id", return_value=-100
            ),
            patch(
                "ccgram.tmux_manager.tmux_manager.get_sidecar_pane_id",
                return_value="%5",
            ),
            patch(
                "ccgram.tmux_manager.tmux_manager.send_keys_to_pane",
                new_callable=AsyncMock,
            ) as mock_sidecar_send,
            patch(
                "ccgram.tmux_manager.tmux_manager.send_keys",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_send,
            patch(
                "ccgram.handlers.messaging_pipeline.message_sender.ack_reaction",
                new_callable=AsyncMock,
            ) as mock_ack,
        ):
            await ctrl_c_command(update, context)

        mock_sidecar_send.assert_awaited_once_with(
            "%5", "C-c", enter=False, literal=False, window_id="@1"
        )
        mock_send.assert_awaited_once_with("@1", "C-c", enter=False, literal=False)
        mock_ack.assert_awaited_once()


class TestCtrlCCCommand:
    async def test_unauthorized_user_ignored(self, update, context):
        with (
            patch("ccgram.config.config.is_user_allowed", return_value=False),
            patch(
                "ccgram.tmux_manager.tmux_manager.send_keys", new_callable=AsyncMock
            ) as mock_send,
        ):
            await ctrl_cc_command(update, context)

        mock_send.assert_not_called()

    async def test_outside_topic_replies_error(self, update, context):
        update.message.message_thread_id = None
        with (
            patch("ccgram.config.config.is_user_allowed", return_value=True),
            patch("ccgram.utils.is_general_topic", return_value=False),
            patch(
                "ccgram.handlers.commands.safe_reply", new_callable=AsyncMock
            ) as mock_reply,
        ):
            await ctrl_cc_command(update, context)

        mock_reply.assert_awaited_once()
        assert "inside a topic" in mock_reply.call_args[0][1]

    async def test_sends_two_ctrl_c_to_tmux_and_sidecar(self, update, context):
        from unittest.mock import call

        with (
            patch("ccgram.config.config.is_user_allowed", return_value=True),
            patch(
                "ccgram.thread_router.thread_router.get_window_for_thread",
                return_value="@1",
            ),
            patch(
                "ccgram.thread_router.thread_router.resolve_chat_id", return_value=-100
            ),
            patch(
                "ccgram.tmux_manager.tmux_manager.get_sidecar_pane_id",
                return_value="%5",
            ),
            patch(
                "ccgram.tmux_manager.tmux_manager.send_keys_to_pane",
                new_callable=AsyncMock,
            ) as mock_sidecar_send,
            patch(
                "ccgram.tmux_manager.tmux_manager.send_keys",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_send,
            patch(
                "ccgram.handlers.messaging_pipeline.message_sender.ack_reaction",
                new_callable=AsyncMock,
            ) as mock_ack,
        ):
            await ctrl_cc_command(update, context)

        assert mock_sidecar_send.call_count == 2
        mock_sidecar_send.assert_has_calls(
            [
                call("%5", "C-c", enter=False, literal=False, window_id="@1"),
                call("%5", "C-c", enter=False, literal=False, window_id="@1"),
            ]
        )
        assert mock_send.call_count == 2
        mock_send.assert_has_calls(
            [
                call("@1", "C-c", enter=False, literal=False),
                call("@1", "C-c", enter=False, literal=False),
            ]
        )
        mock_ack.assert_awaited_once()


class TestSpecialKeyCommands:
    async def test_enter_command_sends_enter(self, update, context):
        from ccgram.handlers.commands import enter_command

        with (
            patch("ccgram.config.config.is_user_allowed", return_value=True),
            patch(
                "ccgram.thread_router.thread_router.get_window_for_thread",
                return_value="@1",
            ),
            patch(
                "ccgram.thread_router.thread_router.resolve_chat_id", return_value=-100
            ),
            patch(
                "ccgram.tmux_manager.tmux_manager.get_sidecar_pane_id",
                return_value=None,
            ),
            patch(
                "ccgram.tmux_manager.tmux_manager.send_keys",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_send,
            patch(
                "ccgram.handlers.messaging_pipeline.message_sender.ack_reaction",
                new_callable=AsyncMock,
            ) as mock_ack,
        ):
            await enter_command(update, context)

        mock_send.assert_awaited_once_with("@1", "Enter", enter=False, literal=False)
        mock_ack.assert_awaited_once()

    async def test_esc_command_sends_escape(self, update, context):
        from ccgram.handlers.commands import esc_command

        with (
            patch("ccgram.config.config.is_user_allowed", return_value=True),
            patch(
                "ccgram.thread_router.thread_router.get_window_for_thread",
                return_value="@1",
            ),
            patch(
                "ccgram.thread_router.thread_router.resolve_chat_id", return_value=-100
            ),
            patch(
                "ccgram.tmux_manager.tmux_manager.get_sidecar_pane_id",
                return_value=None,
            ),
            patch(
                "ccgram.tmux_manager.tmux_manager.send_keys",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_send,
            patch(
                "ccgram.handlers.messaging_pipeline.message_sender.ack_reaction",
                new_callable=AsyncMock,
            ) as mock_ack,
        ):
            await esc_command(update, context)

        mock_send.assert_awaited_once_with("@1", "Escape", enter=False, literal=False)
        mock_ack.assert_awaited_once()

    async def test_tab_command_sends_tab(self, update, context):
        from ccgram.handlers.commands import tab_command

        with (
            patch("ccgram.config.config.is_user_allowed", return_value=True),
            patch(
                "ccgram.thread_router.thread_router.get_window_for_thread",
                return_value="@1",
            ),
            patch(
                "ccgram.thread_router.thread_router.resolve_chat_id", return_value=-100
            ),
            patch(
                "ccgram.tmux_manager.tmux_manager.get_sidecar_pane_id",
                return_value=None,
            ),
            patch(
                "ccgram.tmux_manager.tmux_manager.send_keys",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_send,
            patch(
                "ccgram.handlers.messaging_pipeline.message_sender.ack_reaction",
                new_callable=AsyncMock,
            ) as mock_ack,
        ):
            await tab_command(update, context)

        mock_send.assert_awaited_once_with("@1", "Tab", enter=False, literal=False)
        mock_ack.assert_awaited_once()
