"""Integration tests for TmuxManager with a real tmux server."""

import asyncio
import contextlib
import shutil

import pytest
from libtmux.exc import LibTmuxException

from ccgram.tmux_manager import TmuxManager

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed"),
]

TEST_SESSION = "cc_test_integration"


@pytest.fixture()
async def tmux(tmp_path):
    mgr = TmuxManager(session_name=TEST_SESSION)
    created: list[str] = [TEST_SESSION]
    mgr.get_or_create_session()
    orig = mgr.create_session

    async def track(
        session_name: str,
        work_dir: str,
        window_name: str | None = None,
    ) -> tuple[bool, str, str, str]:
        created.append(session_name)
        return await orig(session_name, work_dir, window_name=window_name)

    mgr.create_session = track
    yield mgr
    for s in created:
        sess = mgr.get_session(s)
        if sess:
            with contextlib.suppress(LibTmuxException, OSError):
                sess.kill()


async def test_create_and_list_windows(tmux, tmp_path) -> None:
    ok, _msg, name, window_id = await tmux.create_window(
        str(tmp_path), window_name="test-win", start_agent=False
    )
    assert ok
    assert name == "test-win"

    windows = await tmux.list_windows()
    ids = [w.window_id for w in windows]
    assert window_id in ids

    match = next(w for w in windows if w.window_id == window_id)
    assert match.window_name == "test-win"


async def test_find_window_by_id(tmux, tmp_path) -> None:
    ok, _msg, _name, window_id = await tmux.create_window(
        str(tmp_path), window_name="find-me", start_agent=False
    )
    assert ok

    found = await tmux.find_window_by_id(window_id)
    assert found is not None
    assert found.window_name == "find-me"

    missing = await tmux.find_window_by_id("@99999")
    assert missing is None


async def test_send_keys_and_capture_pane(tmux, tmp_path) -> None:
    ok, _msg, _name, window_id = await tmux.create_window(
        str(tmp_path), window_name="echo-win", start_agent=False
    )
    assert ok

    await tmux.send_keys(window_id, "echo hello-integration")
    await asyncio.sleep(0.5)

    output = await tmux.capture_pane(window_id)
    assert output is not None
    assert "hello-integration" in output


async def test_no_agent_disables_automatic_rename(tmux, tmp_path) -> None:
    ok, _msg, _name, window_id = await tmux.create_window(
        str(tmp_path), window_name="shell-norename", start_agent=False
    )
    assert ok

    session = tmux.get_session("shell-norename")
    assert session
    window = session.windows[0]
    assert window.show_option("automatic-rename") is False

    await asyncio.sleep(0.5)
    await tmux.send_keys(window_id, "echo should-not-rename")
    await asyncio.sleep(1.0)
    found = await tmux.find_window_by_id(window_id)
    assert found is not None
    assert found.window_name == "shell-norename"


async def test_kill_window(tmux, tmp_path) -> None:
    ok, _msg, _name, window_id = await tmux.create_window(
        str(tmp_path), window_name="kill-me", start_agent=False
    )
    assert ok

    killed = await tmux.kill_window(window_id)
    assert killed is True

    windows = await tmux.list_windows()
    ids = [w.window_id for w in windows]
    assert window_id not in ids


async def test_kill_session_removes_the_tmux_session(tmux, tmp_path) -> None:
    session_name = "cc_kill-session"
    ok, _msg, _name, _window_id = await tmux.create_window(
        str(tmp_path),
        session_name=session_name,
        window_name="kill-me",
        start_agent=False,
    )
    assert ok
    assert tmux.get_session(session_name) is not None

    killed = await tmux.kill_session(session_name)

    assert killed is True
    assert tmux.get_session(session_name) is None


async def test_reset_server_reconnects(tmux, tmp_path) -> None:
    ok, _msg, _name, window_id = await tmux.create_window(
        str(tmp_path), window_name="reset-test", start_agent=False
    )
    assert ok

    tmux._reset_server()

    windows = await tmux.list_windows()
    ids = [w.window_id for w in windows]
    assert window_id in ids


async def test_capture_pane_raw_returns_tuple(tmux, tmp_path) -> None:
    ok, _msg, _name, window_id = await tmux.create_window(
        str(tmp_path), window_name="raw-test", start_agent=False
    )
    assert ok

    await tmux.send_keys(window_id, "echo raw-test-output")
    await asyncio.sleep(0.5)

    result = await tmux.capture_pane_raw(window_id)
    assert result is not None
    content, cols, rows = result
    assert isinstance(content, str)
    assert "raw-test-output" in content
    assert cols > 0
    assert rows > 0


# ── Pane-level operations ────────────────────────────────────────────


async def test_list_panes_single(tmux, tmp_path) -> None:
    ok, _msg, _name, window_id = await tmux.create_window(
        str(tmp_path), window_name="pane-list", start_agent=False
    )
    assert ok

    panes = await tmux.list_panes(window_id)
    assert len(panes) == 1
    assert panes[0].active is True
    assert panes[0].pane_id.startswith("%")
    assert panes[0].width > 0
    assert panes[0].height > 0


async def test_list_panes_multiple(tmux, tmp_path) -> None:
    ok, _msg, _name, window_id = await tmux.create_window(
        str(tmp_path), window_name="multi-pane", start_agent=False
    )
    assert ok

    session = tmux.get_session("multi-pane")
    assert session
    window = session.windows[0]
    window.split()

    panes = await tmux.list_panes(window_id)
    assert len(panes) == 2
    pane_ids = [p.pane_id for p in panes]
    assert len(set(pane_ids)) == 2
    active_count = sum(1 for p in panes if p.active)
    assert active_count == 1


async def test_list_panes_missing_window(tmux) -> None:
    panes = await tmux.list_panes("@99999")
    assert panes == []


async def test_capture_pane_by_id(tmux, tmp_path) -> None:
    ok, _msg, _name, window_id = await tmux.create_window(
        str(tmp_path), window_name="cap-pane", start_agent=False
    )
    assert ok

    panes = await tmux.list_panes(window_id)
    pane_id = panes[0].pane_id

    await tmux.send_keys(window_id, "echo pane-capture-test")
    await asyncio.sleep(0.5)

    output = await tmux.capture_pane_by_id(pane_id, window_id=window_id)
    assert output is not None
    assert "pane-capture-test" in output


async def test_capture_pane_by_id_missing(tmux) -> None:
    output = await tmux.capture_pane_by_id("%99999")
    assert output is None


async def test_send_keys_to_pane(tmux, tmp_path) -> None:
    ok, _msg, _name, window_id = await tmux.create_window(
        str(tmp_path), window_name="send-pane", start_agent=False
    )
    assert ok

    session = tmux.get_session("send-pane")
    assert session
    window = session.windows[0]
    window.split()

    panes = await tmux.list_panes(window_id)
    assert len(panes) == 2

    inactive = next(p for p in panes if not p.active)
    sent = await tmux.send_keys_to_pane(
        inactive.pane_id, "echo pane-target-test", window_id=window_id
    )
    assert sent is True
    await asyncio.sleep(0.5)

    output = await tmux.capture_pane_by_id(inactive.pane_id, window_id=window_id)
    assert output is not None
    assert "pane-target-test" in output


async def test_send_keys_to_pane_missing(tmux) -> None:
    sent = await tmux.send_keys_to_pane("%99999", "hello")
    assert sent is False


# ── ANSI capture ───────────────────────────────────────────────────────


async def test_capture_pane_with_ansi(tmux, tmp_path) -> None:
    ok, _msg, _name, window_id = await tmux.create_window(
        str(tmp_path), window_name="ansi-test", start_agent=False
    )
    assert ok

    await tmux.send_keys(window_id, r'printf "\033[31mred\033[0m normal"')
    await asyncio.sleep(0.5)

    plain = await tmux.capture_pane(window_id, with_ansi=False)
    ansi = await tmux.capture_pane(window_id, with_ansi=True)
    assert plain is not None
    assert ansi is not None
    assert "red" in plain
    assert "normal" in plain
    assert "\x1b[" in ansi
    assert "red" in ansi


async def test_capture_pane_can_include_history(tmux, tmp_path) -> None:
    ok, _msg, _name, window_id = await tmux.create_window(
        str(tmp_path), window_name="history-test", start_agent=False
    )
    assert ok

    await tmux.send_keys(window_id, "yes HISTORY-MARKER | head -80")
    await asyncio.sleep(0.5)

    visible = await tmux.capture_pane(window_id, with_ansi=True)
    history = await tmux.capture_pane(window_id, with_ansi=True, include_history=True)
    assert visible is not None
    assert history is not None
    assert len(history.splitlines()) > len(visible.splitlines())
    assert "HISTORY-MARKER" in history


async def test_create_window_does_not_inject_environment_commands(
    tmux, tmp_path
) -> None:
    ok, _msg, _name, window_id = await tmux.create_window(
        str(tmp_path), window_name="env-test", start_agent=False
    )
    assert ok

    await asyncio.sleep(0.5)
    await tmux.send_keys(window_id, "echo ${CCGRAM_WINDOW_ID-unset}")
    await asyncio.sleep(0.5)

    output = await tmux.capture_pane(window_id)
    assert output is not None
    assert "unset" in output


async def test_send_keys_with_special_chars(tmux, tmp_path) -> None:
    marker = "CCGRAM_LAUNCH_TEST_MARKER"
    launch_cmd = (
        f"env {marker}=special/path/value sh -c 'echo ${{CCGRAM_LAUNCH_TEST_MARKER}}'"
    )
    ok, _msg, _name, window_id = await tmux.create_window(
        str(tmp_path),
        window_name="literal-launch",
        start_agent=False,
    )
    assert ok

    await tmux.send_keys(window_id, launch_cmd)
    await asyncio.sleep(1.0)
    output = await tmux.capture_pane(window_id)
    assert output is not None
    assert "special/path/value" in output, (
        f"Command with = and / was not sent literally. Pane output: {output!r}"
    )
