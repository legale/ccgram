"""Tests for one-shot tmux sidecar pane management."""

from unittest.mock import AsyncMock, patch

from ccgram.tmux_manager import PaneInfo, TmuxManager, extract_sidecar_output


async def test_start_sidecar_command_uses_active_pane_cwd() -> None:
    tm = TmuxManager("test-session")
    panes = [
        PaneInfo(
            pane_id="%1",
            index=0,
            active=True,
            command="claude",
            path="/tmp",
            width=80,
            height=24,
        )
    ]
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"%2\n", b"")
    mock_proc.returncode = 0

    with (
        patch.object(tm, "list_panes", new=AsyncMock(return_value=panes)),
        patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)
        ) as exec_mock,
    ):
        sidecar_id, marker = await tm.start_sidecar_command(
            "test-session:@1", "cat <<'EOF'\nhello\nEOF"
        )

    assert sidecar_id == "%2"
    assert marker is not None
    assert tm.get_sidecar_pane_id("test-session:@1") == "%2"
    assert tm.get_sidecar_completion_marker("test-session:@1") == marker
    cmd = exec_mock.await_args.args
    assert "split-window" in cmd
    assert "-d" in cmd
    assert cmd[cmd.index("-c") + 1] == "/tmp"
    assert "send_keys_to_pane" not in cmd
    assert marker in cmd[-1]
    assert ":BEGIN" in cmd[-1]


async def test_start_sidecar_command_rejects_concurrent_command() -> None:
    tm = TmuxManager("test-session")
    tm._sidecar_panes["test-session:@1"] = "%2"
    panes = [
        PaneInfo("%1", 0, True, "bash", "/tmp", 80, 24),
        PaneInfo("%2", 1, False, "sh", "/tmp", 80, 24),
    ]

    with (
        patch.object(tm, "list_panes", new=AsyncMock(return_value=panes)),
        patch("asyncio.create_subprocess_exec", new=AsyncMock()) as exec_mock,
    ):
        sidecar_id, marker = await tm.start_sidecar_command("test-session:@1", "pwd")

    assert sidecar_id is None
    assert marker is None
    exec_mock.assert_not_awaited()


def test_extract_sidecar_output_ignores_pane_history() -> None:
    marker = "__CCGRAM_SIDECAR_DONE_marker__"
    text = f"old prompt\n$ cat <<EOF\n{marker}:BEGIN\npwd output\n{marker}:0\n"

    output, completed = extract_sidecar_output(text, marker)

    assert output == "pwd output"
    assert completed is True


async def test_forget_sidecar_pane() -> None:
    tm = TmuxManager("test-session")
    tm._sidecar_panes["@1"] = "%2"
    tm._sidecar_completion_markers["@1"] = "marker"
    assert tm.get_sidecar_pane_id("@1") == "%2"
    assert tm.get_sidecar_completion_marker("@1") == "marker"
    tm.forget_sidecar_pane("@1")
    assert tm.get_sidecar_pane_id("@1") is None
    assert tm.get_sidecar_completion_marker("@1") is None
