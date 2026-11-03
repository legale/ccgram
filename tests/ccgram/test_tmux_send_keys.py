from unittest.mock import MagicMock

from ccgram.tmux_manager import TmuxManager


class TestSendKeys:
    async def test_direct_send_keys(self) -> None:
        tmux = TmuxManager(session_name="test")
        tmux._pane_send = MagicMock(return_value=True)

        sent = await tmux.send_keys("@1", "ls")

        assert sent is True
        tmux._pane_send.assert_called_once_with("@1", "ls", enter=True, literal=True)


class TestCreateWindowIds:
    async def test_custom_session_returns_qualified_window_id(self, tmp_path) -> None:
        tmux = TmuxManager(session_name="main")

        pane = MagicMock()
        window = MagicMock()
        window.window_id = "@7"
        window.active_pane = pane
        session = MagicMock()
        session.session_name = "cc_topic"
        tmux.get_session = MagicMock(return_value=None)
        tmux.server.new_session = MagicMock(return_value=session)
        session.windows = [window]

        ok, _msg, _name, window_id = await tmux.create_window(
            str(tmp_path),
            session_name="cc_topic",
            start_agent=False,
        )

        assert ok is True
        assert window_id == "cc_topic:@7"
