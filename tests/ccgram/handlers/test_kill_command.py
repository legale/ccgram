"""Tests for session kill via sessions dashboard (two-step confirmation)."""

from unittest.mock import AsyncMock, patch

import pytest

from ccgram.handlers.sessions_dashboard import (
    handle_sessions_kill,
    handle_sessions_kill_confirm,
)
from ccgram.session import WindowState


@pytest.fixture(autouse=True)
def _patch_deps():
    with (
        patch("ccgram.handlers.sessions_dashboard.view_window") as mock_view,
        patch("ccgram.handlers.sessions_dashboard.thread_router") as mock_tr,
        patch("ccgram.handlers.sessions_dashboard.tmux_manager") as mock_tm,
        patch(
            "ccgram.handlers.sessions_dashboard.clear_topic_state",
            new_callable=AsyncMock,
        ) as mock_clear,
    ):
        mock_tr.get_display_name.side_effect = lambda wid: wid
        mock_view.side_effect = lambda wid: WindowState()
        mock_tr.get_all_thread_windows.return_value = {}
        mock_tm.list_sessions = AsyncMock(return_value=[])
        yield mock_view, mock_tr, mock_tm, mock_clear


class TestHandleSessionsKill:
    async def test_shows_confirmation(self, _patch_deps) -> None:
        _mock_sm, mock_tr, _, _ = _patch_deps
        mock_tr.get_display_name.side_effect = lambda wid: "myproj"

        query = AsyncMock()
        with patch("ccgram.handlers.sessions_dashboard.safe_edit") as mock_edit:
            await handle_sessions_kill(query, 100, "@5")
            mock_edit.assert_called_once()
            text = mock_edit.call_args[0][1]
            assert text == "Kill session 'myproj'?"
            assert "myproj" in text
            keyboard = mock_edit.call_args.kwargs["reply_markup"]
            data = [
                btn.callback_data for row in keyboard.inline_keyboard for btn in row
            ]
            assert any("sess:killok:" in d for d in data)


class TestHandleSessionsKillConfirm:
    async def test_kills_and_unbinds(self, _patch_deps) -> None:
        _mock_sm, mock_tr, mock_tm, mock_clear = _patch_deps
        mock_tr.get_display_name.side_effect = lambda wid: "myproj"
        mock_tr.iter_thread_bindings.return_value = [
            (100, 42, "@5"),
            (200, 99, "@5"),
            (300, 10, "@9"),
        ]
        mock_tm.session_name = "ccgram"
        mock_tm.kill_session = AsyncMock(return_value=True)

        query = AsyncMock()
        bot = AsyncMock()
        with patch("ccgram.handlers.sessions_dashboard.safe_edit"):
            await handle_sessions_kill_confirm(query, 100, "@5", bot)

        mock_tm.kill_session.assert_awaited_once_with("ccgram")
        assert mock_tr.unbind_thread.call_count == 2
        mock_tr.unbind_thread.assert_any_call(100, 42)
        mock_tr.unbind_thread.assert_any_call(200, 99)
        assert mock_clear.call_count == 2

    async def test_window_already_gone(self, _patch_deps) -> None:
        _mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_display_name.side_effect = lambda wid: "myproj"
        mock_tr.iter_thread_bindings.return_value = [(100, 42, "@5")]
        mock_tm.session_name = "ccgram"
        mock_tm.kill_session = AsyncMock(return_value=True)

        query = AsyncMock()
        bot = AsyncMock()
        with patch("ccgram.handlers.sessions_dashboard.safe_edit"):
            await handle_sessions_kill_confirm(query, 100, "@5", bot)

        mock_tm.kill_session.assert_awaited_once_with("ccgram")
        mock_tr.unbind_thread.assert_called_once_with(100, 42)

    async def test_refreshes_dashboard_after_kill(self, _patch_deps) -> None:
        _mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_display_name.side_effect = lambda wid: "proj"
        mock_tr.iter_thread_bindings.return_value = [(100, 42, "@5")]
        mock_tm.session_name = "ccgram"
        mock_tm.kill_session = AsyncMock(return_value=True)

        query = AsyncMock()
        bot = AsyncMock()
        with patch("ccgram.handlers.sessions_dashboard.safe_edit") as mock_edit:
            await handle_sessions_kill_confirm(query, 100, "@5", bot)
            mock_edit.assert_called_once()
            text = mock_edit.call_args[0][1]
            assert "Killed" in text
