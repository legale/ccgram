"""Tests for tmux sidecar pane management and invariant (max 2 panes)."""

from unittest.mock import AsyncMock, MagicMock, patch

from ccgram.tmux_manager import TmuxManager, PaneInfo


async def test_ensure_sidecar_pane_splits_when_single_pane() -> None:
    tm = TmuxManager("test-session")
    panes = [
        PaneInfo(pane_id="%1", index=0, active=True, command="bash", path="/tmp", width=80, height=24)
    ]

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"%2\n", b"")
    mock_proc.returncode = 0

    with (
        patch.object(tm, "list_panes", new=AsyncMock(return_value=panes)),
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)) as exec_mock,
    ):
        sidecar_id = await tm.ensure_sidecar_pane("@1")

    assert sidecar_id == "%2"
    assert tm.get_sidecar_pane_id("@1") == "%2"
    exec_mock.assert_awaited_once()
    cmd = exec_mock.await_args.args
    assert "split-window" in cmd
    assert "-d" in cmd


async def test_ensure_sidecar_pane_reuses_when_two_panes_exist() -> None:
    tm = TmuxManager("test-session")
    panes = [
        PaneInfo(pane_id="%1", index=0, active=True, command="bash", path="/tmp", width=80, height=24),
        PaneInfo(pane_id="%2", index=1, active=False, command="bash", path="/tmp", width=80, height=24),
    ]

    with (
        patch.object(tm, "list_panes", new=AsyncMock(return_value=panes)),
        patch("asyncio.create_subprocess_exec", new=AsyncMock()) as exec_mock,
    ):
        sidecar_id = await tm.ensure_sidecar_pane("@1")

    assert sidecar_id == "%2"
    assert tm.get_sidecar_pane_id("@1") == "%2"
    exec_mock.assert_not_called()


async def test_ensure_sidecar_pane_enforces_max_two_panes_by_killing_excess() -> None:
    tm = TmuxManager("test-session")
    panes = [
        PaneInfo(pane_id="%1", index=0, active=True, command="bash", path="/tmp", width=80, height=24),
        PaneInfo(pane_id="%2", index=1, active=False, command="bash", path="/tmp", width=80, height=24),
        PaneInfo(pane_id="%3", index=2, active=False, command="bash", path="/tmp", width=80, height=24),
        PaneInfo(pane_id="%4", index=3, active=False, command="bash", path="/tmp", width=80, height=24),
    ]

    kill_proc = AsyncMock()
    kill_proc.wait.return_value = 0

    with (
        patch.object(tm, "list_panes", new=AsyncMock(return_value=panes)),
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=kill_proc)) as exec_mock,
    ):
        sidecar_id = await tm.ensure_sidecar_pane("@1")

    assert sidecar_id == "%2"
    assert exec_mock.await_count == 2  # Killed %3 and %4
    killed = [call.args[3] for call in exec_mock.await_args_list]
    assert killed == ["%3", "%4"]


async def test_forget_sidecar_pane() -> None:
    tm = TmuxManager("test-session")
    tm._sidecar_panes["@1"] = "%2"
    assert tm.get_sidecar_pane_id("@1") == "%2"
    tm.forget_sidecar_pane("@1")
    assert tm.get_sidecar_pane_id("@1") is None
