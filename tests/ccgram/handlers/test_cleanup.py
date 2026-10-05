from unittest.mock import AsyncMock, patch

from ccgram.handlers.cleanup import clear_topic_state


class TestClearTopicState:
    async def test_enqueues_status_clear_when_bot_available(self) -> None:
        bot = AsyncMock()
        with (
            patch("ccgram.handlers.cleanup.enqueue_status_update") as mock_enqueue,
            patch("ccgram.handlers.cleanup.thread_router") as mock_tr,
        ):
            mock_tr.resolve_chat_id.return_value = -100
            await clear_topic_state(1, 42, client=bot, window_id="@0")

        mock_enqueue.assert_called_once()
        args = mock_enqueue.call_args
        assert args[0][1] == 1
        assert args[0][2] == "@0"
        assert args[0][3] is None
        assert args[1]["thread_id"] == 42

    async def test_skips_enqueue_when_no_bot(self) -> None:
        with (
            patch("ccgram.handlers.cleanup.enqueue_status_update") as mock_enqueue,
            patch("ccgram.handlers.cleanup.thread_router") as mock_tr,
        ):
            mock_tr.resolve_chat_id.return_value = -100
            await clear_topic_state(1, 42, client=None, window_id="@0")

        mock_enqueue.assert_not_called()

    async def test_enqueues_empty_window_id_when_none(self) -> None:
        bot = AsyncMock()
        with (
            patch("ccgram.handlers.cleanup.enqueue_status_update") as mock_enqueue,
            patch("ccgram.handlers.cleanup.thread_router") as mock_tr,
        ):
            mock_tr.resolve_chat_id.return_value = -100
            await clear_topic_state(1, 42, client=bot, window_id=None)

        mock_enqueue.assert_called_once()
        assert mock_enqueue.call_args[0][2] == ""

    async def test_direct_cleanup_dispatches_all_scopes(self) -> None:
        with (
            patch("ccgram.handlers.cleanup._clear_all_topic_state") as mock_clear_all,
            patch("ccgram.handlers.cleanup.thread_router") as mock_tr,
            patch("ccgram.handlers.cleanup.enqueue_status_update"),
        ):
            mock_tr.resolve_chat_id.return_value = -100
            await clear_topic_state(1, 42, client=AsyncMock(), window_id="@0", window_dead=True)

        mock_clear_all.assert_called_once()
        _, kwargs = mock_clear_all.call_args
        assert kwargs["window_id"] == "@0"
        assert kwargs["chat_id"] == -100
        assert kwargs["qualified_id"] is not None

    async def test_window_dead_false_skips_qualified_id(self) -> None:
        with (
            patch("ccgram.handlers.cleanup._clear_all_topic_state") as mock_clear_all,
            patch("ccgram.handlers.cleanup.thread_router") as mock_tr,
            patch("ccgram.handlers.cleanup.enqueue_status_update"),
        ):
            mock_tr.resolve_chat_id.return_value = -100
            await clear_topic_state(1, 42, client=AsyncMock(), window_id="@0", window_dead=False)

        mock_clear_all.assert_called_once()
        _, kwargs = mock_clear_all.call_args
        assert kwargs["window_id"] == "@0"
        assert kwargs["chat_id"] == -100
        assert kwargs["qualified_id"] is None

    def test_safe_call_catches_exceptions(self) -> None:
        from ccgram.handlers.cleanup import _safe_call

        def failing():
            raise RuntimeError("test failure")

        # Must not raise
        _safe_call(failing)
