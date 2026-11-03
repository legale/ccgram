"""Tests for resilience and crash-prevention fixes.

Covers: background loop catch-all, dead worker respawn, pyte feed/resize guards,
rate_limit_send locking, JSONL malformed entry handling, probe failure clearing,
poll state cleanup, shutdown notification lifecycle.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


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


class TestShutdownNotificationLifecycle:
    async def test_post_stop_sends_notification(self):
        from ccgram.bot import post_stop

        application = MagicMock()
        application.bot = AsyncMock()

        with (
            patch(
                "ccgram.bot._send_shutdown_notification", new_callable=AsyncMock
            ) as mock_send,
        ):
            await post_stop(application)

        mock_send.assert_awaited_once_with(application)

    async def test_post_shutdown_does_not_send_notification(self):
        from ccgram.bot import post_shutdown

        application = MagicMock()

        with (
            patch(
                "ccgram.bot._send_shutdown_notification", new_callable=AsyncMock
            ) as mock_send,
            patch("ccgram.bootstrap._status_poll_task", None),
            patch("ccgram.bootstrap.session_manager"),
        ):
            await post_shutdown(application)

        mock_send.assert_not_awaited()
