"""Tests for topic binding helpers."""

from unittest.mock import AsyncMock, MagicMock, patch


class TestRenameBoundTopic:
    @patch("ccgram.handlers.topics.topic_binding.tmux_manager")
    async def test_renames_topic_scoped_window_and_session(
        self, mock_tmux: MagicMock
    ) -> None:
        from ccgram.handlers.topics.topic_binding import rename_bound_topic

        mock_tmux.find_window_by_id = AsyncMock(
            return_value=MagicMock(window_id="cc_old:@7")
        )
        mock_tmux.rename_window = AsyncMock(return_value=True)
        mock_tmux.rename_session = AsyncMock(return_value=True)

        await rename_bound_topic(
            MagicMock(),
            user_id=1,
            thread_id=42,
            window_id="@7",
            window_name="backend-api",
            approval_mode="normal",
        )

        mock_tmux.rename_window.assert_awaited_once_with("cc_old:@7", "backend-api")
        mock_tmux.rename_session.assert_awaited_once_with(
            "cc_old", "cc_backend-api"
        )

    @patch("ccgram.handlers.topics.topic_binding.tmux_manager")
    async def test_skips_default_session_window(
        self, mock_tmux: MagicMock
    ) -> None:
        from ccgram.handlers.topics.topic_binding import rename_bound_topic

        mock_tmux.find_window_by_id = AsyncMock(
            return_value=MagicMock(window_id="@7")
        )
        mock_tmux.rename_window = AsyncMock()
        mock_tmux.rename_session = AsyncMock()

        await rename_bound_topic(
            MagicMock(),
            user_id=1,
            thread_id=42,
            window_id="@7",
            window_name="backend-api",
            approval_mode="normal",
        )

        mock_tmux.rename_window.assert_not_called()
        mock_tmux.rename_session.assert_not_called()
