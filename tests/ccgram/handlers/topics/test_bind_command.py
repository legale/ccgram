"""Tests for /bind command."""

from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Update

from ccgram.handlers.topics.bind_command import bind_command


def _make_update(user_id: int = 100, thread_id: int | None = 42) -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_user = MagicMock(id=user_id)
    msg = MagicMock()
    msg.message_thread_id = thread_id
    msg.chat.type = "supergroup"
    msg.chat.id = -100999
    update.message = msg
    return update


class TestBindCommand:
    async def test_unbound_topic_shows_picker(self) -> None:
        update = _make_update()
        ctx = MagicMock()
        ctx.user_data = {}

        with (
            patch(
                "ccgram.handlers.topics.bind_command.config.is_user_allowed",
                return_value=True,
            ),
            patch(
                "ccgram.handlers.text.text_handler._handle_unbound_topic",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_bind,
        ):
            await bind_command(update, ctx)

        mock_bind.assert_awaited_once()

    async def test_bound_topic_reports_current_session(self) -> None:
        update = _make_update()
        ctx = MagicMock()
        ctx.user_data = {}

        with (
            patch("ccgram.handlers.topics.bind_command.config.is_user_allowed", return_value=True),
            patch("ccgram.handlers.topics.bind_command.thread_router") as mock_tr,
            patch("ccgram.handlers.topics.bind_command.safe_reply", new_callable=AsyncMock) as mock_reply,
        ):
            mock_tr.get_window_for_thread.return_value = "@5"
            mock_tr.get_display_name.return_value = "proj"
            await bind_command(update, ctx)

        mock_reply.assert_awaited_once()

    async def test_outside_topic_rejected(self) -> None:
        update = _make_update(thread_id=None)
        ctx = MagicMock()
        ctx.user_data = {}

        with patch(
            "ccgram.handlers.topics.bind_command.config.is_user_allowed",
            return_value=True,
        ), patch(
            "ccgram.handlers.topics.bind_command.safe_reply",
            new_callable=AsyncMock,
        ) as mock_reply:
            await bind_command(update, ctx)

        mock_reply.assert_awaited_once_with(update.message, "Use /bind inside a topic.")

    async def test_unauthorized_user_rejected(self) -> None:
        update = _make_update()
        ctx = MagicMock()
        ctx.user_data = {}

        with (
            patch(
                "ccgram.handlers.topics.bind_command.config.is_user_allowed",
                return_value=False,
            ),
            patch(
                "ccgram.handlers.topics.bind_command.safe_reply",
                new_callable=AsyncMock,
            ) as mock_reply,
        ):
            await bind_command(update, ctx)

        mock_reply.assert_awaited_once_with(
            update.message,
            "You are not authorized to use this bot.",
        )
