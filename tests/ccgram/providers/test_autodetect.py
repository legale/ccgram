from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccgram.providers import (
    _reset_provider,
    detect_provider_from_command,
    detect_provider_from_runtime,
    should_probe_pane_title_for_provider_detection,
)
from ccgram.session_monitor import SessionMonitor


class TestDetectProviderFromCommand:
    @pytest.fixture(autouse=True)
    def _reset(self):
        _reset_provider()
        yield
        _reset_provider()

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            pytest.param("bash", "shell", id="bash"),
            pytest.param("zsh", "shell", id="zsh"),
            pytest.param("fish", "shell", id="fish"),
            pytest.param("-bash", "shell", id="login-bash"),
            pytest.param("-zsh", "shell", id="login-zsh"),
            pytest.param("/bin/bash", "shell", id="full-path-bash"),
            pytest.param("/usr/bin/zsh -l", "shell", id="path-with-args"),
        ],
    )
    def test_known_shell_commands(self, command: str, expected: str) -> None:
        assert detect_provider_from_command(command) == expected

    def test_unknown_command_returns_empty(self) -> None:
        assert detect_provider_from_command("vim") == ""
        assert detect_provider_from_command("claude") == ""
        assert detect_provider_from_command("codex") == ""

    def test_empty_command_returns_empty(self) -> None:
        assert detect_provider_from_command("") == ""


class TestDetectProviderFromRuntime:
    @pytest.fixture(autouse=True)
    def _reset(self):
        _reset_provider()
        yield
        _reset_provider()

    def test_probe_hint_returns_false(self) -> None:
        assert should_probe_pane_title_for_provider_detection("bash") is False
        assert should_probe_pane_title_for_provider_detection("node") is False

    def test_detects_provider_from_ccgram_title_stamp(self) -> None:
        assert (
            detect_provider_from_runtime("bash", pane_title="ccgram:shell") == "shell"
        )

    def test_ignores_invalid_ccgram_stamp(self) -> None:
        assert detect_provider_from_runtime("vim", pane_title="ccgram:unknown") == ""


class TestHandleNewWindowAutoDetection:
    @patch("ccgram.handlers.topics.topic_orchestration.tmux_manager")
    @patch("ccgram.handlers.topics.topic_orchestration.session_manager")
    @patch("ccgram.handlers.topics.topic_orchestration.config")
    @patch(
        "ccgram.handlers.topics.topic_orchestration.detect_provider_from_pane",
        new_callable=AsyncMock,
        return_value="shell",
    )
    async def test_sets_detected_provider(
        self,
        mock_detect: MagicMock,
        mock_config: MagicMock,
        mock_sm: MagicMock,
        mock_tmux: MagicMock,
    ) -> None:
        from ccgram.handlers.topics.topic_orchestration import (
            handle_new_window as _handle_new_window,
        )
        from ccgram.session_monitor import NewWindowEvent

        mock_config.group_id = None
        mock_sm.iter_thread_bindings.return_value = []
        mock_sm.view_window.return_value = MagicMock(provider_name="")

        mock_window = MagicMock()
        mock_window.pane_current_command = "bash"
        mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)

        event = NewWindowEvent(
            window_id="@5", session_id="uuid-1", window_name="proj", cwd="/tmp/proj"
        )
        bot = AsyncMock()

        await _handle_new_window(event, bot)

        mock_detect.assert_awaited_once()
        mock_sm.set_window_provider.assert_called_once_with("@5", "shell")

    @patch("ccgram.handlers.topics.topic_orchestration.tmux_manager")
    @patch("ccgram.handlers.topics.topic_orchestration.session_manager")
    @patch("ccgram.handlers.topics.topic_orchestration.config")
    @patch(
        "ccgram.handlers.topics.topic_orchestration.detect_provider_from_pane",
        new_callable=AsyncMock,
    )
    async def test_skips_detection_when_no_pane_command(
        self,
        mock_detect: MagicMock,
        mock_config: MagicMock,
        mock_sm: MagicMock,
        mock_tmux: MagicMock,
    ) -> None:
        from ccgram.handlers.topics.topic_orchestration import (
            handle_new_window as _handle_new_window,
        )
        from ccgram.session_monitor import NewWindowEvent

        mock_config.group_id = None
        mock_sm.iter_thread_bindings.return_value = []

        mock_window = MagicMock()
        mock_window.pane_current_command = ""
        mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)

        event = NewWindowEvent(
            window_id="@6", session_id="uuid-2", window_name="proj", cwd="/tmp"
        )
        bot = AsyncMock()

        await _handle_new_window(event, bot)

        mock_detect.assert_not_called()
        mock_sm.set_window_provider.assert_not_called()

    @patch("ccgram.handlers.topics.topic_orchestration.tmux_manager")
    @patch("ccgram.handlers.topics.topic_orchestration.session_manager")
    @patch("ccgram.handlers.topics.topic_orchestration.config")
    @patch(
        "ccgram.handlers.topics.topic_orchestration.detect_provider_from_pane",
        new_callable=AsyncMock,
    )
    async def test_skips_detection_when_window_not_found(
        self,
        mock_detect: MagicMock,
        mock_config: MagicMock,
        mock_sm: MagicMock,
        mock_tmux: MagicMock,
    ) -> None:
        from ccgram.handlers.topics.topic_orchestration import (
            handle_new_window as _handle_new_window,
        )
        from ccgram.session_monitor import NewWindowEvent

        mock_config.group_id = None
        mock_sm.iter_thread_bindings.return_value = []

        mock_tmux.find_window_by_id = AsyncMock(return_value=None)

        event = NewWindowEvent(
            window_id="@7", session_id="uuid-3", window_name="proj", cwd="/tmp"
        )
        bot = AsyncMock()

        await _handle_new_window(event, bot)

        mock_detect.assert_not_called()
        mock_sm.set_window_provider.assert_not_called()


class TestSessionMonitorProviderFromMap:
    async def test_sets_provider_from_session_map(self, tmp_path) -> None:
        monitor = SessionMonitor(
            projects_path=tmp_path / "projects",
            poll_interval=0.1,
            state_file=tmp_path / "monitor_state.json",
        )
        monitor._last_session_map = {}

        new_map = {
            "@5": {
                "session_id": "uuid-1",
                "cwd": "/tmp",
                "window_name": "proj",
                "provider_name": "shell",
            }
        }

        with (
            patch.object(
                monitor,
                "_load_current_session_map",
                new_callable=AsyncMock,
                return_value=new_map,
            ),
            patch("ccgram.session.session_manager") as mock_sm,
        ):
            await monitor._detect_and_cleanup_changes()
            mock_sm.set_window_provider.assert_called_once_with("@5", "shell")

    async def test_skips_provider_when_not_in_map(self, tmp_path) -> None:
        monitor = SessionMonitor(
            projects_path=tmp_path / "projects",
            poll_interval=0.1,
            state_file=tmp_path / "monitor_state.json",
        )
        monitor._last_session_map = {}

        new_map = {
            "@6": {
                "session_id": "uuid-2",
                "cwd": "/tmp",
                "window_name": "proj",
            }
        }

        with (
            patch.object(
                monitor,
                "_load_current_session_map",
                new_callable=AsyncMock,
                return_value=new_map,
            ),
            patch("ccgram.session.session_manager") as mock_sm,
        ):
            await monitor._detect_and_cleanup_changes()
            mock_sm.set_window_provider.assert_not_called()
