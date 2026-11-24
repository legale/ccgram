"""Tests for resilience and crash-prevention fixes."""

import asyncio
from unittest.mock import MagicMock, patch


class TestRateLimitSendLocking:
    async def test_concurrent_sends_serialized(self):
        import time

        from ccgram.handlers.messaging_pipeline.message_sender import (
            MESSAGE_SEND_INTERVAL,
            _last_send_time,
            _rate_limit_locks,
            rate_limit_send,
        )

        chat_id = 88888
        _last_send_time.pop(chat_id, None)
        _rate_limit_locks.pop(chat_id, None)

        timestamps: list[float] = []

        async def timed_send():
            await rate_limit_send(chat_id)
            timestamps.append(time.monotonic())

        # Seed the rate limiter so subsequent calls must wait
        await rate_limit_send(chat_id)
        t0 = time.monotonic()

        # Launch 2 concurrent senders — they should be serialized by the lock
        tasks = [asyncio.create_task(timed_send()) for _ in range(2)]
        await asyncio.gather(*tasks)

        assert len(timestamps) == 2
        # Each send should be spaced at least MESSAGE_SEND_INTERVAL apart from seed
        for ts in timestamps:
            assert (
                ts - t0 >= MESSAGE_SEND_INTERVAL * 0.8
            )  # 80% tolerance for scheduling jitter

        _last_send_time.pop(chat_id, None)
        _rate_limit_locks.pop(chat_id, None)


class TestGlobalExceptionHandler:
    def test_handler_logs_exception(self):
        from ccgram.bootstrap import _global_exception_handler

        loop = MagicMock()
        error = ValueError("test error")
        context = {"exception": error, "message": "test context"}

        with patch("ccgram.bootstrap.logger") as mock_logger:
            _global_exception_handler(loop, context)

        mock_logger.error.assert_called_once()
        assert "test context" in str(mock_logger.error.call_args)
