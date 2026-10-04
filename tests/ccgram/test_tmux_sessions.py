"""Tests for tmux session discovery used by the sessions dashboard."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ccgram.tmux_manager import TmuxManager


async def test_list_sessions_returns_every_tmux_session_with_cwd() -> None:
    manager = TmuxManager("ccgram")

    own = SimpleNamespace(windows=[SimpleNamespace(window_id="@0")])
    foreign = SimpleNamespace(windows=[SimpleNamespace(window_id="@7")])

    with patch(
        "ccgram.tmux_manager.subprocess.run",
        return_value=SimpleNamespace(
            returncode=0,
            stdout="ccgram\t/home/ruslan\nother\t/tmp/project\n",
        ),
    ):
        manager.get_session = MagicMock(side_effect=[own, foreign])
        sessions = await manager.list_sessions()

    assert [(session.window_name, session.cwd) for session in sessions] == [
        ("ccgram", "/home/ruslan"),
        ("other", "/tmp/project"),
    ]
    assert [session.window_id for session in sessions] == [
        "ccgram:@0",
        "other:@7",
    ]
