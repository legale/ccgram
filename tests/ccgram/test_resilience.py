"""Tests for resilience and crash-prevention fixes.

Covers: background loop catch-all, dead worker respawn, pyte feed/resize guards,
rate_limit_send locking, JSONL malformed entry handling, probe failure clearing,
poll state cleanup, shutdown notification lifecycle.
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch



class TestScreenBufferResilience:
    def test_feed_malformed_ansi_does_not_raise(self):
        from ccgram.screen_buffer import ScreenBuffer

        buf = ScreenBuffer(columns=40, rows=5)
        buf.feed("normal text")
        buf.feed("\x1b[9999;9999H")
        buf.feed("\x1b[?????m")
        assert isinstance(buf.rendered_text, str)

    def test_resize_zero_dimensions_ignored(self):
        from ccgram.screen_buffer import ScreenBuffer

        buf = ScreenBuffer(columns=40, rows=5)
        buf.feed("hello")
        buf.resize(0, 0)
        assert buf.columns == 40
        assert buf.rows == 5

    def test_resize_negative_dimensions_ignored(self):
        from ccgram.screen_buffer import ScreenBuffer

        buf = ScreenBuffer(columns=40, rows=5)
        buf.resize(-1, -1)
        assert buf.columns == 40

    def test_resize_valid_dimensions_works(self):
        from ccgram.screen_buffer import ScreenBuffer

        buf = ScreenBuffer(columns=40, rows=5)
        buf.resize(80, 24)
        assert buf.columns == 80
        assert buf.rows == 24


class TestDeadWorkerRespawn:
    async def test_dead_worker_is_respawned(self):
        from ccgram.handlers.messaging_pipeline.message_queue import (
            _message_queues,
            _queue_locks,
            _queue_workers,
            get_or_create_queue,
        )

        bot = MagicMock()
        user_id = 99999

        _message_queues.pop(user_id, None)
        _queue_locks.pop(user_id, None)
        _queue_workers.pop(user_id, None)

        queue = get_or_create_queue(bot, user_id)
        assert user_id in _queue_workers
        first_worker = _queue_workers[user_id]

        first_worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await first_worker
        assert first_worker.done()

        queue2 = get_or_create_queue(bot, user_id)
        assert queue2 is queue
        second_worker = _queue_workers[user_id]
        assert second_worker is not first_worker
        assert not second_worker.done()

        second_worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await second_worker
        _message_queues.pop(user_id, None)
        _queue_locks.pop(user_id, None)
        _queue_workers.pop(user_id, None)


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


class TestProbeFailureClearing:
    def test_clear_probe_failures_resets_counter(self):
        from ccgram.handlers.polling.polling_state import (
            lifecycle_strategy,
            terminal_poll_state,
        )

        _window_poll_state = terminal_poll_state._states
        ws = terminal_poll_state.get_state("@test-probe")
        ws.probe_failures = 5

        lifecycle_strategy.clear_probe_failures("@test-probe")

        ws2 = _window_poll_state.get("@test-probe")
        assert ws2 is not None
        assert ws2.probe_failures == 0

        _window_poll_state.pop("@test-probe", None)


class TestPollStateCleanup:
    def test_clear_window_poll_state_removes_entry(self):
        from ccgram.handlers.polling.polling_state import terminal_poll_state

        _window_poll_state = terminal_poll_state._states
        terminal_poll_state.get_state("@cleanup-test")
        assert "@cleanup-test" in _window_poll_state

        terminal_poll_state.clear_state("@cleanup-test")
        assert "@cleanup-test" not in _window_poll_state


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
            patch("ccgram.bootstrap.shutdown_workers", new_callable=AsyncMock),
        ):
            await post_shutdown(application)

        mock_send.assert_not_awaited()


class TestShellDetectionSafety:
    async def test_script_shell_not_interactive(self):
        from ccgram.providers.shell_infra import _is_interactive_shell

        mock_tmux = MagicMock()
        mock_window = MagicMock()
        mock_window.pane_tty = "/dev/ttys005"
        mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)

        with (
            patch("ccgram.tmux_manager.tmux_manager", mock_tmux),
            patch(
                "ccgram.providers.shell_infra.get_foreground_args",
                new_callable=AsyncMock,
                return_value=("bash ./scripts/restart.sh run", 0),
            ),
        ):
            assert await _is_interactive_shell("@0") is False

    async def test_idle_shell_is_interactive(self):
        from ccgram.providers.shell_infra import _is_interactive_shell

        mock_tmux = MagicMock()
        mock_window = MagicMock()
        mock_window.pane_tty = "/dev/ttys005"
        mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)

        with (
            patch("ccgram.tmux_manager.tmux_manager", mock_tmux),
            patch(
                "ccgram.providers.shell_infra.get_foreground_args",
                new_callable=AsyncMock,
                return_value=("-bash", 1234),
            ),
        ):
            assert await _is_interactive_shell("@0") is True

    async def test_setup_shell_prompt_skips_own_window(self):
        from ccgram.providers.shell import setup_shell_prompt

        with (
            patch("ccgram.config.config") as mock_config,
            patch(
                "ccgram.providers.shell.has_prompt_marker",
                new_callable=AsyncMock,
            ) as mock_has_marker,
            patch(
                "ccgram.providers.shell_infra._is_interactive_shell",
                new_callable=AsyncMock,
            ),
        ):
            mock_config.own_window_id = "@99"
            await setup_shell_prompt("@99", clear=False)

        mock_has_marker.assert_not_awaited()

    async def test_setup_shell_prompt_skips_non_interactive(self):
        from ccgram.providers.shell import setup_shell_prompt

        with (
            patch("ccgram.config.config") as mock_config,
            patch(
                "ccgram.providers.shell_infra._is_interactive_shell",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "ccgram.providers.shell.has_prompt_marker",
                new_callable=AsyncMock,
            ) as mock_has_marker,
        ):
            mock_config.own_window_id = None
            await setup_shell_prompt("@5", clear=False)

        mock_has_marker.assert_not_awaited()
