from unittest.mock import AsyncMock, MagicMock

import pytest

from ccgram.tmux_manager import TmuxManager, _vim_state


@pytest.fixture(autouse=True)
def _clean_vim_state():
    _vim_state.clear()
    yield
    _vim_state.clear()


class TestSendKeysVimProbe:
    async def test_unknown_vim_state_does_not_probe_shell_input(self) -> None:
        tmux = TmuxManager(session_name="test")
        tmux.capture_pane = AsyncMock(return_value="$ ")
        tmux._pane_send = MagicMock(return_value=True)

        sent = await tmux.send_keys("@1", "ls")

        assert sent is True
        calls = [call.args[1] for call in tmux._pane_send.call_args_list]
        assert calls == ["ls", ""]
        assert _vim_state["@1"] is False

    async def test_known_vim_state_still_enters_insert_mode(self) -> None:
        tmux = TmuxManager(session_name="test")
        tmux.capture_pane = AsyncMock(side_effect=["normal mode", "-- INSERT --"])
        tmux._pane_send = MagicMock(return_value=True)
        _vim_state["@1"] = True

        sent = await tmux.send_keys("@1", "hello")

        assert sent is True
        calls = [call.args[1] for call in tmux._pane_send.call_args_list]
        assert calls == ["i", "hello", ""]


class TestCreateWindowIds:
    async def test_custom_session_returns_qualified_window_id(self, tmp_path) -> None:
        tmux = TmuxManager(session_name="main")
        tmux.find_window_by_name = AsyncMock(return_value=None)

        pane = MagicMock()
        window = MagicMock()
        window.window_id = "@7"
        window.active_pane = pane
        session = MagicMock()
        session.session_name = "cc_topic"
        session.new_window.return_value = window
        tmux.get_or_create_session = MagicMock(return_value=session)

        ok, _msg, _name, window_id = await tmux.create_window(
            str(tmp_path),
            session_name="cc_topic",
            start_agent=False,
        )

        assert ok is True
        assert window_id == "cc_topic:@7"
