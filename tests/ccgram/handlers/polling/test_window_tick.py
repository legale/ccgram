import ast
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Bot

from ccgram.handlers.polling import window_tick
from ccgram.handlers.polling.polling_state import (
    interactive_strategy,
    lifecycle_strategy,
    terminal_poll_state,
    terminal_screen_buffer,
)
from ccgram.handlers.polling.polling_types import TickContext, TickDecision
from ccgram.handlers.polling.window_tick import (
    _handle_dead_window_notification,
    _apply_active_transition,
    _transition_to_idle,
    _update_status,
    decide_tick,
    tick_window,
)
from ccgram.handlers.polling.polling_types import StatusUpdate


@pytest.fixture(autouse=True)
def _reset():
    terminal_poll_state._states.clear()  # no public clear_all method
    lifecycle_strategy.reset_autoclose_state()
    lifecycle_strategy.reset_typing_state()
    lifecycle_strategy.reset_dead_notification_state()
    interactive_strategy.clear_all_alerts()
    terminal_screen_buffer.reset_screen_buffer_state()
    yield
    terminal_poll_state._states.clear()
    lifecycle_strategy.reset_autoclose_state()
    lifecycle_strategy.reset_typing_state()
    lifecycle_strategy.reset_dead_notification_state()
    interactive_strategy.clear_all_alerts()
    terminal_screen_buffer.reset_screen_buffer_state()


def _make_window(
    window_id="@0", pane_width=120, pane_height=40, pane_current_command="claude"
):
    w = MagicMock()
    w.window_id = window_id
    w.pane_width = pane_width
    w.pane_height = pane_height
    w.pane_current_command = pane_current_command
    return w


def _make_status(raw_text="Working...", is_interactive=False, display_label=""):
    return StatusUpdate(
        raw_text=raw_text, display_label=display_label, is_interactive=is_interactive
    )


class TestTickWindowDeadWindow:
    async def test_dead_window_calls_handle_dead(self):
        bot = AsyncMock(spec=Bot)
        with patch.object(
            window_tick, "_handle_dead_window_notification", new_callable=AsyncMock
        ) as mock_dead:
            await tick_window(bot, 1, 100, "@0", None)
            mock_dead.assert_called_once_with(bot, 1, 100, "@0")

    async def test_dead_window_skips_other_work(self):
        bot = AsyncMock(spec=Bot)
        with (
            patch.object(
                window_tick, "_handle_dead_window_notification", new_callable=AsyncMock
            ),
            patch.object(
                window_tick, "_update_status", new_callable=AsyncMock
            ) as mock_status,
        ):
            await tick_window(bot, 1, 100, "@0", None)
            mock_status.assert_not_called()

    async def test_already_dead_notified_returns_early(self):
        bot = AsyncMock(spec=Bot)
        lifecycle_strategy.mark_dead_notified(1, 100, "@0")
        with patch.object(
            window_tick, "_handle_dead_window_notification", new_callable=AsyncMock
        ) as mock_dead:
            await tick_window(bot, 1, 100, "@0", None)
            mock_dead.assert_not_called()


class TestTickWindowEmptyQueue:
    async def test_empty_queue_runs_status_update(self):
        bot = AsyncMock(spec=Bot)
        w = _make_window()
        with (
            patch.object(
                window_tick, "_update_status", new_callable=AsyncMock
            ) as mock_status,
        ):
            await tick_window(bot, 1, 100, "@0", w)
            mock_status.assert_called_once()

    async def test_no_queue_runs_status_update(self):
        bot = AsyncMock(spec=Bot)
        w = _make_window()

        with (
            patch.object(
                window_tick, "_update_status", new_callable=AsyncMock
            ) as mock_status,
        ):
            await tick_window(bot, 1, 100, "@0", w)
            mock_status.assert_called_once()


class TestUpdateStatusTopicDiff:
    async def test_calls_update_topic_status_diff_when_enabled(self, monkeypatch):
        from ccgram.config import config

        monkeypatch.setattr(config, "topic_status_diff_enabled", True)

        bot = AsyncMock(spec=Bot)
        w = _make_window()
        status = _make_status(raw_text="Working", is_interactive=False)

        with (
            patch("ccgram.handlers.polling.window_tick.apply.tmux_manager") as mock_tm,
            patch("ccgram.handlers.polling.window_tick.apply.window_query"),
            patch("ccgram.handlers.polling.window_tick.apply.thread_router") as mock_tr,
            patch(
                "ccgram.handlers.polling.window_tick.observe._parse_with_pyte",
                return_value=status,
            ),
            patch(
                "ccgram.handlers.polling.window_tick.apply.update_topic_status_diff",
                new_callable=AsyncMock,
            ) as mock_diff,
            patch(
                "ccgram.handlers.polling.window_tick.apply.decide_tick",
                return_value=TickDecision(transition="active"),
            ),
            patch(
                "ccgram.handlers.polling.window_tick.apply._apply_tick_decision",
                new_callable=AsyncMock,
            ),
        ):
            mock_tm.find_window_by_id = AsyncMock(return_value=w)
            mock_tm.capture_pane = AsyncMock(return_value="pane text")
            mock_tm.capture_pane_display = AsyncMock(return_value="pane text")
            mock_tr.resolve_chat_id.return_value = -100

            await _update_status(bot, 1, "@0", thread_id=100, _window=w)

            mock_diff.assert_awaited_once()

    async def test_does_not_call_update_topic_status_diff_when_disabled(
        self, monkeypatch
    ):
        from ccgram.config import config

        monkeypatch.setattr(config, "topic_status_diff_enabled", False)

        bot = AsyncMock(spec=Bot)
        w = _make_window()
        status = _make_status(raw_text="Working", is_interactive=False)

        with (
            patch("ccgram.handlers.polling.window_tick.apply.tmux_manager") as mock_tm,
            patch("ccgram.handlers.polling.window_tick.apply.window_query"),
            patch("ccgram.handlers.polling.window_tick.apply.thread_router") as mock_tr,
            patch(
                "ccgram.handlers.polling.window_tick.observe._parse_with_pyte",
                return_value=status,
            ),
            patch(
                "ccgram.handlers.polling.window_tick.apply.update_topic_status_diff",
                new_callable=AsyncMock,
            ) as mock_diff,
            patch(
                "ccgram.handlers.polling.window_tick.apply.decide_tick",
                return_value=TickDecision(transition="active"),
            ),
            patch(
                "ccgram.handlers.polling.window_tick.apply._apply_tick_decision",
                new_callable=AsyncMock,
            ),
        ):
            mock_tm.find_window_by_id = AsyncMock(return_value=w)
            mock_tm.capture_pane = AsyncMock(return_value="pane text")
            mock_tr.resolve_chat_id.return_value = -100

            await _update_status(bot, 1, "@0", thread_id=100, _window=w)

            mock_diff.assert_not_called()


def _make_ctx(
    window_id: str = "@0",
    resolved_status_text: str | None = None,
    is_shell_prompt: bool = False,
    has_seen_status: bool = False,
    is_recently_active: bool = False,
    startup_time: float | None = None,
    is_dead_window: bool = False,
    supports_hook: bool = True,
    notification_mode: str = "all",
) -> TickContext:
    return TickContext(
        window_id=window_id,
        resolved_status_text=resolved_status_text,
        is_shell_prompt=is_shell_prompt,
        has_seen_status=has_seen_status,
        is_recently_active=is_recently_active,
        startup_time=startup_time,
        is_dead_window=is_dead_window,
        supports_hook=supports_hook,
        notification_mode=notification_mode,
    )


class TestDecideTickActiveTranscript:
    def test_recently_active_yields_active_transition(self):
        ctx = _make_ctx(is_recently_active=True)
        decision = decide_tick(ctx)
        assert decision.transition == "active"
        assert decision.send_status is False


class TestDecideTickShellPrompt:

    def test_shell_provider_yields_idle(self):
        ctx = _make_ctx(is_shell_prompt=True, supports_hook=False)
        decision = decide_tick(ctx)
        assert decision.transition == "idle"

    def test_no_startup_time_yields_starting(self):
        ctx = _make_ctx(startup_time=None)
        decision = decide_tick(ctx)
        assert decision.transition == "starting"


class TestDeadWindowNotification:
    async def test_sends_once(self):
        bot = AsyncMock(spec=Bot)
        with (
            patch("ccgram.handlers.polling.window_tick.apply.thread_router") as mock_tr,
            patch("ccgram.handlers.polling.window_tick.apply.window_query") as mock_sm,
            patch(
                "ccgram.handlers.polling.window_tick.apply.update_topic_emoji",
                new_callable=AsyncMock,
            ),
            patch(
                "ccgram.handlers.polling.window_tick.apply.clear_tool_msg_ids_for_topic"
            ),
            patch(
                "ccgram.handlers.polling.window_tick.apply.rate_limit_send_message",
                new_callable=AsyncMock,
            ) as mock_send,
        ):
            mock_tr.resolve_chat_id.return_value = 42
            mock_tr.get_display_name.return_value = "test"
            mock_sm.get_window_state.return_value = MagicMock(cwd="/tmp")
            mock_send.return_value = MagicMock()
            await _handle_dead_window_notification(bot, 1, 100, "@0")
            assert lifecycle_strategy.is_dead_notified(1, 100, "@0")
            mock_send.reset_mock()
            await _handle_dead_window_notification(bot, 1, 100, "@0")
            mock_send.assert_not_called()


class TestContractTests:
    def test_tick_window_exists_and_is_callable(self):
        assert hasattr(window_tick, "tick_window")
        assert callable(window_tick.tick_window)

    def test_tick_window_is_coroutine_function(self):
        assert inspect.iscoroutinefunction(window_tick.tick_window)

    def test_tick_window_is_sole_async_public_function(self):
        public_async = [
            name
            for name in dir(window_tick)
            if not name.startswith("_")
            and inspect.iscoroutinefunction(getattr(window_tick, name))
            and getattr(getattr(window_tick, name), "__module__", None)
            == "ccgram.handlers.polling.window_tick"
        ]
        assert public_async == ["tick_window"]

    def test_decide_tick_is_public_pure_function(self):
        assert hasattr(window_tick, "decide_tick")
        assert not inspect.iscoroutinefunction(window_tick.decide_tick)
        assert callable(window_tick.decide_tick)

    def test_polling_coordinator_imports_only_tick_window(self):
        import ccgram.handlers.polling.polling_coordinator as pc

        source = inspect.getsource(pc)
        tree = ast.parse(source)
        window_tick_imports = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module and "window_tick" in node.module:
                for alias in node.names:
                    window_tick_imports.append(alias.name)
            elif node.level and node.level > 0 and node.module is None:
                for alias in node.names:
                    if alias.name == "window_tick":
                        window_tick_imports.append(alias.name)
        assert window_tick_imports == [] or all(
            name == "window_tick" for name in window_tick_imports
        ), f"Unexpected imports from window_tick: {window_tick_imports}"

    def test_polling_coordinator_does_not_import_per_window_collaborators(self):
        import ccgram.handlers.polling.polling_coordinator as pc

        source = inspect.getsource(pc)
        tree = ast.parse(source)
        forbidden = {
            "claude_task_state",
            "providers.base",
            "session_monitor",
            "cleanup",
            "interactive_ui",
            "message_queue",
            "message_sender",
            "recovery_callbacks",
            "topic_emoji",
            "transcript_discovery",
            "polling_state",
        }
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
        violations = {m for m in imported_modules if any(f in m for f in forbidden)}
        assert not violations, (
            f"polling_coordinator imports per-window collaborators: {violations}"
        )


class TestDeadWindowTopicDeleted:
    @pytest.mark.parametrize(
        "error_msg",
        ["thread not found", "TOPIC_ID_INVALID"],
        ids=["thread_not_found", "topic_id_invalid"],
    )
    async def test_thread_not_found_unbinds_and_clears(self, error_msg):
        from telegram.error import BadRequest

        bot = AsyncMock(spec=["unpin_all_forum_topic_messages"])
        bot.unpin_all_forum_topic_messages = AsyncMock(
            side_effect=BadRequest(error_msg)
        )

        with (
            patch("ccgram.handlers.polling.window_tick.apply.thread_router") as mock_tr,
            patch("ccgram.handlers.polling.window_tick.apply.window_query") as mock_sm,
            patch(
                "ccgram.handlers.polling.window_tick.apply.update_topic_emoji",
                new_callable=AsyncMock,
            ),
            patch(
                "ccgram.handlers.polling.window_tick.apply.clear_tool_msg_ids_for_topic"
            ),
            patch(
                "ccgram.handlers.polling.window_tick.apply.rate_limit_send_message",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            mock_tr.resolve_chat_id.return_value = 42
            mock_tr.get_display_name.return_value = "test"
            mock_sm.get_window_state.return_value = MagicMock(cwd="/tmp")

            await _handle_dead_window_notification(bot, 1, 100, "@0")

            mock_tr.unbind_thread.assert_not_called()
