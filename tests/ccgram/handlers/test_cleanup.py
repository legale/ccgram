from unittest.mock import patch

from ccgram.handlers.cleanup import clear_topic_state


class TestClearTopicState:
    async def test_direct_cleanup_dispatches_all_scopes(self) -> None:
        with (
            patch("ccgram.handlers.cleanup._clear_all_topic_state") as mock_clear_all,
            patch("ccgram.handlers.cleanup.thread_router") as mock_tr,
        ):
            mock_tr.resolve_chat_id.return_value = -100
            await clear_topic_state(1, 42, window_id="@0")

        mock_clear_all.assert_called_once()
        _, kwargs = mock_clear_all.call_args
        assert kwargs["window_id"] == "@0"
        assert kwargs["chat_id"] == -100

    def test_safe_call_catches_exceptions(self) -> None:
        from ccgram.handlers.cleanup import _safe_call

        def failing():
            raise RuntimeError("test failure")

        # Must not raise
        _safe_call(failing)
