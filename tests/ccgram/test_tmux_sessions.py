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
            stdout="ccgram\t/home/ruslan\t-100:42\nother\t/tmp/project\t\n",
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
    assert [session.topic_ref for session in sessions] == [(-100, 42), None]


async def test_set_session_topic_stores_one_session_option() -> None:
    manager = TmuxManager("ccgram")
    result = SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch("ccgram.tmux_manager.subprocess.run", return_value=result) as run:
        assert await manager.set_session_topic("cc_foo", -100, 42) is True

    run.assert_called_once_with(
        ["tmux", "set-option", "-t", "cc_foo", "@ccgram_topic", "-100:42"],
        capture_output=True,
        text=True,
        timeout=5,
    )


async def test_clear_session_topic_unsets_session_option() -> None:
    manager = TmuxManager("ccgram")
    result = SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch("ccgram.tmux_manager.subprocess.run", return_value=result) as run:
        assert await manager.clear_session_topic("cc_foo") is True

    run.assert_called_once_with(
        ["tmux", "set-option", "-u", "-t", "cc_foo", "@ccgram_topic"],
        capture_output=True,
        text=True,
        timeout=5,
    )
