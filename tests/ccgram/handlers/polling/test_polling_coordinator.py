import asyncio
import contextlib
from unittest.mock import AsyncMock, patch

from telegram import Bot
from telegram.error import TelegramError

from ccgram.handlers.polling.polling_coordinator import (
    _BACKOFF_MIN,
    poll_sessions,
    status_poll_loop,
)
from ccgram.tmux_manager import TmuxWindow
from tests.ccgram._helpers import FakeTelegramClient


def _make_window(window_id: str, window_name: str = "test", cwd: str = "/tmp") -> TmuxWindow:
    return TmuxWindow(window_id=window_id, window_name=window_name, cwd=cwd)


class TestPollSessions:
    async def test_active_window_updates_status_diff(self):
        client = FakeTelegramClient()
        window = _make_window("s1:@1", "s1", cwd="/tmp/project")
        bindings = [(1, 100, "s1:@1")]
        window_lookup = {"s1:@1": window}

        with (
            patch("ccgram.handlers.polling.polling_coordinator.tmux_manager") as mock_tm,
            patch("ccgram.handlers.polling.polling_coordinator.thread_router") as mock_tr,
            patch(
                "ccgram.handlers.status.topic_status_diff.update_topic_status_diff",
                new_callable=AsyncMock,
            ) as mock_diff,
            patch("ccgram.session.session_manager.set_window_cwd") as mock_set_cwd,
            patch("ccgram.config.config.topic_status_diff_enabled", True),
        ):
            mock_tm.capture_pane = AsyncMock(return_value="bash$ echo hi\n")
            mock_tm.get_sidecar_pane_id.return_value = None
            mock_tr.resolve_chat_id.return_value = -100

            await poll_sessions(client, bindings, window_lookup)

            mock_set_cwd.assert_called_once()
            mock_tm.capture_pane.assert_awaited_once_with("s1:@1", with_ansi=True)
            mock_diff.assert_awaited_once_with(client, -100, 100, "s1:@1", "bash$ echo hi\n")

    async def test_dead_window_sends_notification(self):
        client = AsyncMock()
        bindings = [(1, 100, "dead:@1")]
        window_lookup: dict[str, TmuxWindow] = {}

        with (
            patch("ccgram.handlers.polling.polling_coordinator.tmux_manager") as mock_tm,
            patch("ccgram.handlers.polling.polling_coordinator.thread_router") as mock_tr,
            patch("ccgram.handlers.topics.topic_lifecycle.is_dead_notified", return_value=False),
            patch("ccgram.handlers.topics.topic_lifecycle.mark_dead_notified") as mock_mark,
            patch("ccgram.handlers.topics.topic_lifecycle.start_autoclose_timer") as mock_timer,
            patch(
                "ccgram.handlers.messaging_pipeline.message_sender.rate_limit_send_message",
                new_callable=AsyncMock,
            ) as mock_send,
        ):
            mock_tm.session_exists = AsyncMock(return_value=False)
            mock_tr.resolve_chat_id.return_value = -100
            mock_tr.get_display_name.return_value = "dead"

            await poll_sessions(client, bindings, window_lookup)

            mock_timer.assert_called_once()
            mock_send.assert_awaited_once()
            mock_mark.assert_called_once_with(1, 100, "dead:@1")


class TestStatusPollLoop:
    async def test_poll_loop_runs_cycle_and_sleeps(self):
        bot = AsyncMock(spec=Bot)
        sleep_called = False

        async def _stop_sleep(_delay: float) -> None:
            nonlocal sleep_called
            sleep_called = True
            raise asyncio.CancelledError

        with (
            patch(
                "ccgram.handlers.polling.polling_coordinator.tmux_manager.list_windows",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "ccgram.handlers.polling.periodic_tasks.run_periodic_tasks",
                new_callable=AsyncMock,
            ) as mock_periodic,
            patch(
                "ccgram.handlers.polling.periodic_tasks.run_lifecycle_tasks",
                new_callable=AsyncMock,
            ) as mock_lifecycle,
            patch(
                "ccgram.handlers.polling.polling_coordinator.poll_sessions",
                new_callable=AsyncMock,
            ) as mock_poll,
            patch(
                "ccgram.handlers.polling.polling_coordinator.asyncio.sleep",
                side_effect=_stop_sleep,
            ),
            contextlib.suppress(asyncio.CancelledError),
        ):
            await status_poll_loop(bot)

        assert sleep_called
        mock_periodic.assert_awaited_once()
        mock_lifecycle.assert_awaited_once()
        mock_poll.assert_awaited_once()

    async def test_backoff_on_error(self):
        bot = AsyncMock(spec=Bot)
        sleep_delays: list[float] = []

        async def _capture_sleep(delay: float) -> None:
            sleep_delays.append(delay)
            if len(sleep_delays) >= 2:
                raise asyncio.CancelledError

        with (
            patch(
                "ccgram.handlers.polling.polling_coordinator.tmux_manager.list_windows",
                side_effect=[TelegramError("boom"), TelegramError("boom2")],
            ),
            patch(
                "ccgram.handlers.polling.polling_coordinator.asyncio.sleep",
                side_effect=_capture_sleep,
            ),
            contextlib.suppress(asyncio.CancelledError),
        ):
            await status_poll_loop(bot)

        assert sleep_delays[0] == _BACKOFF_MIN
        assert sleep_delays[1] == _BACKOFF_MIN * 2
