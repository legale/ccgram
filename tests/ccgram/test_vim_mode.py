"""Tests for vim mode detection and auto-INSERT recovery in tmux_manager."""

from unittest.mock import AsyncMock, patch

import pytest

from ccgram.tmux_manager import (
    TmuxManager,
    _VIM_PROBE_DELAY,
    has_insert_indicator,
    _vim_locks,
    _vim_state,
    clear_vim_state,
    notify_vim_insert_seen,
    reset_vim_state,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_vim_state()
    yield
    reset_vim_state()


# ── has_insert_indicator ──────────────────────────────────────────────


class TestHasInsertIndicator:
    def test_insert_on_last_line(self):
        pane = "some output\nprompt> hello\n-- INSERT --"
        assert has_insert_indicator(pane) is True

    def test_insert_second_to_last(self):
        pane = "line1\n-- INSERT --\nlast line"
        assert has_insert_indicator(pane) is True

    def test_insert_third_to_last(self):
        pane = "-- INSERT --\nsecond\nthird"
        assert has_insert_indicator(pane) is True

    def test_no_insert_indicator(self):
        pane = "some output\nprompt> hello\n"
        assert has_insert_indicator(pane) is False

    def test_insert_too_far_up(self):
        pane = "-- INSERT --\nline2\nline3\nline4"
        assert has_insert_indicator(pane) is False

    def test_empty_pane(self):
        assert has_insert_indicator("") is False

    def test_insert_with_surrounding_text(self):
        pane = "output\nstatus: -- INSERT -- (paste)\ndone"
        assert has_insert_indicator(pane) is False

    def test_insert_with_whitespace(self):
        pane = "output\n  -- INSERT --  \ndone"
        assert has_insert_indicator(pane) is True

    def test_claude_status_bar_insert(self):
        pane = "output\n-- INSERT -- ⏸ plan mode on (shift+tab to cycle)\ndone"
        assert has_insert_indicator(pane) is False


# ── notify / clear / reset ─────────────────────────────────────────────


class TestVimStateCache:
    def test_notify_sets_true(self):
        notify_vim_insert_seen("@1")
        assert _vim_state["@1"] is True

    def test_clear_removes_entry(self):
        _vim_state["@1"] = True
        clear_vim_state("@1")
        assert "@1" not in _vim_state

    def test_clear_also_removes_lock(self):
        import asyncio

        _vim_locks["@1"] = asyncio.Lock()
        clear_vim_state("@1")
        assert "@1" not in _vim_locks

    def test_clear_missing_key_is_noop(self):
        clear_vim_state("@999")

    def test_reset_clears_all(self):
        import asyncio

        _vim_state["@1"] = True
        _vim_state["@2"] = False
        _vim_locks["@1"] = asyncio.Lock()
        reset_vim_state()
        assert _vim_state == {}
        assert _vim_locks == {}


# ── _ensure_vim_insert_mode ────────────────────────────────────────────


def _make_manager() -> TmuxManager:
    m = TmuxManager.__new__(TmuxManager)
    m.session_name = "test"
    m._server = None
    return m


class TestEnsureVimInsertMode:
    @pytest.fixture()
    def manager(self):
        return _make_manager()

    async def test_cache_false_skips_entirely(self, manager):
        _vim_state["@1"] = False
        with patch.object(manager, "capture_pane", new_callable=AsyncMock) as cap:
            await manager._ensure_vim_insert_mode("@1")
            cap.assert_not_called()

    async def test_insert_visible_sets_cache_true(self, manager):
        with patch.object(
            manager,
            "capture_pane",
            new_callable=AsyncMock,
            return_value="prompt\n-- INSERT --",
        ):
            await manager._ensure_vim_insert_mode("@1")
        assert _vim_state["@1"] is True

    async def test_cache_true_normal_mode_enters_insert(self, manager):
        _vim_state["@1"] = True
        captures = iter(["prompt>", "prompt>\n-- INSERT --"])

        async def fake_capture(_wid):
            return next(captures)

        with (
            patch.object(manager, "capture_pane", side_effect=fake_capture),
            patch.object(manager, "_pane_send", return_value=True) as send,
            patch(
                "ccgram.tmux_manager.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep,
        ):
            await manager._ensure_vim_insert_mode("@1")
        assert _vim_state["@1"] is True
        send.assert_called_once_with("@1", "i", enter=False, literal=True)
        mock_sleep.assert_awaited_once_with(_VIM_PROBE_DELAY)

    async def test_cache_true_vim_turned_off_sends_backspace(self, manager):
        _vim_state["@1"] = True
        captures = iter(["prompt>", "prompt> i"])

        async def fake_capture(_wid):
            return next(captures)

        calls = []

        def fake_send(_wid, chars, *, enter, literal):
            calls.append((chars, literal))
            return True

        with (
            patch.object(manager, "capture_pane", side_effect=fake_capture),
            patch.object(manager, "_pane_send", side_effect=fake_send),
            patch("ccgram.tmux_manager.asyncio.sleep", new_callable=AsyncMock),
        ):
            await manager._ensure_vim_insert_mode("@1")
        assert _vim_state["@1"] is False
        assert calls == [("i", True), ("BSpace", False)]

    async def test_probe_unknown_vim_on(self, manager):
        # Unknown state no longer probes by typing into user input.
        assert "@1" not in _vim_state
        with (
            patch.object(
                manager, "capture_pane", new_callable=AsyncMock, return_value="prompt>"
            ),
            patch.object(manager, "_pane_send", return_value=True) as send,
        ):
            await manager._ensure_vim_insert_mode("@1")
        send.assert_not_called()
        assert _vim_state["@1"] is False

    async def test_probe_unknown_vim_off(self, manager):
        # Unknown state no longer probes by typing into user input.
        assert "@1" not in _vim_state
        with (
            patch.object(
                manager, "capture_pane", new_callable=AsyncMock, return_value="prompt>"
            ),
            patch.object(manager, "_pane_send", return_value=True) as send,
        ):
            await manager._ensure_vim_insert_mode("@1")
        send.assert_not_called()
        assert _vim_state["@1"] is False

    async def test_first_capture_failure_returns_early(self, manager):
        with (
            patch.object(
                manager, "capture_pane", new_callable=AsyncMock, return_value=None
            ),
            patch.object(manager, "_pane_send", return_value=True) as send,
        ):
            await manager._ensure_vim_insert_mode("@1")
        send.assert_not_called()

    async def test_post_probe_capture_none_leaves_state_unchanged(self, manager):
        """Second capture returns None → don't change cache, don't backspace."""
        _vim_state["@1"] = True
        captures = iter(["prompt>", None])

        async def fake_capture(_wid):
            return next(captures)

        calls = []

        def fake_send(_wid, chars, *, enter, literal):
            calls.append((chars, literal))
            return True

        with (
            patch.object(manager, "capture_pane", side_effect=fake_capture),
            patch.object(manager, "_pane_send", side_effect=fake_send),
            patch("ccgram.tmux_manager.asyncio.sleep", new_callable=AsyncMock),
        ):
            await manager._ensure_vim_insert_mode("@1")
        # State still True — not corrupted by transient failure
        assert _vim_state["@1"] is True
        # Only the probe 'i' was sent, no backspace
        assert calls == [("i", True)]

    async def test_pane_send_failure_returns_early(self, manager):
        captures = iter(["prompt>"])

        async def fake_capture(_wid):
            return next(captures)

        with (
            patch.object(manager, "capture_pane", side_effect=fake_capture),
            patch.object(manager, "_pane_send", return_value=False),
        ):
            await manager._ensure_vim_insert_mode("@1")
        # Unknown state resolves to False without probing.
        assert _vim_state["@1"] is False


# ── Self-correction scenarios ──────────────────────────────────────────


class TestSelfCorrection:
    @pytest.fixture()
    def manager(self):
        return _make_manager()

    async def test_vim_enabled_mid_session(self, manager):
        _vim_state["@1"] = False
        with patch.object(manager, "capture_pane", new_callable=AsyncMock) as cap:
            await manager._ensure_vim_insert_mode("@1")
            cap.assert_not_called()

        notify_vim_insert_seen("@1")
        assert _vim_state["@1"] is True

    async def test_vim_disabled_mid_session(self, manager):
        _vim_state["@1"] = True
        captures = iter(["prompt>", "prompt> i"])

        async def fake_capture(_wid):
            return next(captures)

        with (
            patch.object(manager, "capture_pane", side_effect=fake_capture),
            patch.object(manager, "_pane_send", return_value=True),
            patch("ccgram.tmux_manager.asyncio.sleep", new_callable=AsyncMock),
        ):
            await manager._ensure_vim_insert_mode("@1")
        assert _vim_state["@1"] is False


# ── _send_literal_then_enter integration ───────────────────────────────


