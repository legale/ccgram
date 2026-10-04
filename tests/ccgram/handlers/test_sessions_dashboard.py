"""Tests for /sessions dashboard command."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccgram.handlers.callback_data import (
    CB_SESSIONS_NEW,
    CB_SESSIONS_REFRESH,
    CB_SESSIONS_RENAME,
    CB_STATUS_SCREENSHOT,
)
from ccgram.handlers.sessions_dashboard import (
    _build_dashboard,
    _create_session_for_topic,
    apply_session_rename,
    handle_sessions_refresh,
    handle_sessions_rename,
    sessions_command,
)
from ccgram.session import WindowState


@pytest.fixture(autouse=True)
def _patch_deps():
    with (
        patch("ccgram.handlers.sessions_dashboard.view_window") as mock_view,
        patch("ccgram.handlers.sessions_dashboard.thread_router") as mock_tr,
        patch("ccgram.handlers.sessions_dashboard.tmux_manager") as mock_tm,
        patch("ccgram.handlers.sessions_dashboard.config") as mock_cfg,
    ):
        mock_tr.get_all_thread_windows.return_value = {}
        mock_tr.get_display_name.side_effect = lambda wid: wid
        mock_view.side_effect = lambda wid: WindowState()
        mock_tm.list_windows = AsyncMock(return_value=[])
        mock_tm.discover_external_sessions = AsyncMock(return_value=[])
        mock_cfg.is_user_allowed.return_value = True
        yield mock_view, mock_tr, mock_tm, mock_cfg


class TestBuildDashboard:
    async def test_empty(self, _patch_deps) -> None:
        text, keyboard = await _build_dashboard(100)
        assert "No active sessions" in text
        data = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if isinstance(btn.callback_data, str)
        ]
        assert CB_SESSIONS_REFRESH in data
        assert CB_SESSIONS_NEW in data

    async def test_alive_session(self, _patch_deps) -> None:
        mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {42: "@0"}
        mock_tr.get_display_name.side_effect = lambda wid: "myproject"
        mock_sm.side_effect = lambda wid: WindowState(cwd="/home/user/myproject")
        mock_tm.list_windows = AsyncMock(return_value=[MagicMock(window_id="@0")])

        text, _kb = await _build_dashboard(100)
        assert "+ myproject" in text

    async def test_alive_session_shows_cwd(self, _patch_deps) -> None:
        mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {42: "@0"}
        mock_tr.get_display_name.side_effect = lambda wid: "myproject"
        mock_sm.side_effect = lambda wid: WindowState(cwd="/home/user/myproject")
        mock_tm.list_windows = AsyncMock(return_value=[MagicMock(window_id="@0")])

        text, _kb = await _build_dashboard(100)
        assert "/home/user/myproject" in text

    async def test_no_cwd_shows_no_path(self, _patch_deps) -> None:
        mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {42: "@0"}
        mock_tr.get_display_name.side_effect = lambda wid: "myproject"
        mock_sm.side_effect = lambda wid: WindowState(cwd="")
        mock_tm.list_windows = AsyncMock(return_value=[MagicMock(window_id="@0")])

        text, _kb = await _build_dashboard(100)
        assert " /home/user/myproject" not in text

    async def test_dead_session(self, _patch_deps) -> None:
        _mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {42: "@0"}
        mock_tr.get_display_name.side_effect = lambda wid: "oldproject"
        mock_tm.list_windows = AsyncMock(return_value=[])

        text, _kb = await _build_dashboard(100)
        assert "- oldproject" in text

    async def test_multiple_sessions(self, _patch_deps) -> None:
        _mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {10: "@0", 20: "@5"}
        mock_tr.get_display_name.side_effect = lambda wid: {
            "@0": "alive",
            "@5": "dead",
        }[wid]
        mock_tm.list_windows = AsyncMock(return_value=[MagicMock(window_id="@0")])

        text, _kb = await _build_dashboard(100)
        assert "+ alive" in text
        assert "- dead" in text

    async def test_unbound_windows_displayed(self, _patch_deps) -> None:
        _mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {10: "@0"}
        mock_tr.get_display_name.side_effect = lambda wid: "bound-session"
        unbound_win = MagicMock(
            window_id="@99", window_name="other-session", cwd="/home/user"
        )
        mock_tm.list_windows = AsyncMock(
            return_value=[MagicMock(window_id="@0"), unbound_win]
        )

        text, _kb = await _build_dashboard(100)
        assert "+ bound-session" in text
        assert "o other-session" in text
        assert "/home/user" in text

    async def test_refresh_and_new_buttons(self, _patch_deps) -> None:
        _mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {42: "@0"}
        mock_tm.list_windows = AsyncMock(return_value=[MagicMock(window_id="@0")])

        _text, keyboard = await _build_dashboard(100)
        labels = [btn.text for row in keyboard.inline_keyboard for btn in row]
        data = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if isinstance(btn.callback_data, str)
        ]
        assert any("Refresh" in label for label in labels)
        assert any("New" in label for label in labels)
        assert CB_SESSIONS_REFRESH in data
        assert CB_SESSIONS_NEW in data

    async def test_alive_session_has_rename_screenshot_and_kill_buttons(
        self, _patch_deps
    ) -> None:
        _mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {42: "@0"}
        mock_tm.list_windows = AsyncMock(return_value=[MagicMock(window_id="@0")])

        _text, keyboard = await _build_dashboard(100)
        row = keyboard.inline_keyboard[0]
        assert [button.text for button in row] == ["+ @0", "scr", "kill"]
        assert row[0].callback_data.startswith(CB_SESSIONS_RENAME)
        assert row[1].callback_data.startswith(CB_STATUS_SCREENSHOT)
        assert row[2].callback_data.startswith("sess:kill:")

    async def test_alive_session_shows_provider(self, _patch_deps) -> None:
        mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {42: "@0"}
        mock_tr.get_display_name.side_effect = lambda wid: "myproject"
        mock_sm.side_effect = lambda wid: WindowState(
            cwd="/home/user/myproject", provider_name="codex"
        )
        mock_tm.list_windows = AsyncMock(return_value=[MagicMock(window_id="@0")])

        text, _kb = await _build_dashboard(100)
        assert "[codex]" in text

    async def test_default_provider_shows_no_tag(self, _patch_deps) -> None:
        mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {42: "@0"}
        mock_tr.get_display_name.side_effect = lambda wid: "myproject"
        mock_sm.side_effect = lambda wid: WindowState(
            cwd="/home/user/myproject", provider_name=""
        )
        mock_tm.list_windows = AsyncMock(return_value=[MagicMock(window_id="@0")])

        text, _kb = await _build_dashboard(100)
        assert text.startswith("Sessions\n\n```\n+ myproject /home/user/myproject\n")

    async def test_yolo_mode_shows_tag(self, _patch_deps) -> None:
        mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {42: "@0"}
        mock_tr.get_display_name.side_effect = lambda wid: "myproject"
        mock_sm.side_effect = lambda wid: WindowState(
            cwd="/home/user/myproject",
            provider_name="codex",
            approval_mode="yolo",
        )
        mock_tm.list_windows = AsyncMock(return_value=[MagicMock(window_id="@0")])

        text, _kb = await _build_dashboard(100)
        assert "[YOLO]" in text

    async def test_dead_session_no_action_buttons(self, _patch_deps) -> None:
        _mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {42: "@0"}
        mock_tr.get_display_name.side_effect = lambda wid: "deadproject"
        mock_tm.list_windows = AsyncMock(return_value=[])

        _text, keyboard = await _build_dashboard(100)
        data = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if isinstance(btn.callback_data, str)
        ]
        assert not any(d.startswith(CB_STATUS_SCREENSHOT) for d in data)


class TestSessionsCommand:
    async def test_calls_reply(self, _patch_deps) -> None:
        update = MagicMock()
        update.effective_user = MagicMock(id=100)
        update.message = AsyncMock()

        with patch("ccgram.handlers.sessions_dashboard.safe_reply") as mock_reply:
            await sessions_command(update, MagicMock())
            mock_reply.assert_called_once()
            assert update.message == mock_reply.call_args[0][0]
            assert "No active sessions" in mock_reply.call_args[0][1]

    async def test_unauthorized(self, _patch_deps) -> None:
        _, _, _, mock_cfg = _patch_deps
        mock_cfg.is_user_allowed.return_value = False

        update = MagicMock()
        update.effective_user = MagicMock(id=100)
        update.message = AsyncMock()

        with patch("ccgram.handlers.sessions_dashboard.safe_reply") as mock_reply:
            await sessions_command(update, MagicMock())
            mock_reply.assert_called_once()
            assert "not authorized" in mock_reply.call_args[0][1]

    async def test_no_user(self) -> None:
        update = MagicMock()
        update.effective_user = None
        update.message = AsyncMock()

        with patch("ccgram.handlers.sessions_dashboard.safe_reply") as mock_reply:
            await sessions_command(update, MagicMock())
        mock_reply.assert_not_called()


class TestSessionsNew:
    async def test_creates_session_for_topic(self, _patch_deps) -> None:
        _mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tm.topic_session_name.side_effect = lambda name: f"cc_{name}"
        mock_tm.create_window = AsyncMock(return_value=(True, "ok", "codex", "@5"))

        query = AsyncMock()
        query.message = MagicMock()
        query.message.chat.id = -100
        query.message.message_thread_id = 42
        query.message.chat.type = "supergroup"

        with patch(
            "ccgram.handlers.sessions_dashboard.get_stored_topic_name",
            return_value="codex",
        ):
            await _create_session_for_topic(query, 100, 42, MagicMock())

        mock_tm.create_window.assert_awaited_once()
        mock_tr.bind_thread.assert_called_once_with(100, 42, "@5", window_name="codex")

    async def test_no_message(self) -> None:
        update = MagicMock()
        update.effective_user = MagicMock(id=100)
        update.message = None

        with patch("ccgram.handlers.sessions_dashboard.safe_reply") as mock_reply:
            await sessions_command(update, MagicMock())
            mock_reply.assert_not_called()


class TestSessionsRefresh:
    async def test_refresh_edits(self, _patch_deps) -> None:
        query = AsyncMock()

        with patch("ccgram.handlers.sessions_dashboard.safe_edit") as mock_edit:
            await handle_sessions_refresh(query, 100)
            mock_edit.assert_called_once()
            assert query == mock_edit.call_args[0][0]
            assert "No active sessions" in mock_edit.call_args[0][1]


class TestKillButtons:
    async def test_alive_session_has_kill_button(self, _patch_deps) -> None:
        _mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {42: "@0"}
        mock_tr.get_display_name.side_effect = lambda wid: "myproject"
        mock_tm.list_windows = AsyncMock(return_value=[MagicMock(window_id="@0")])

        _text, keyboard = await _build_dashboard(100)
        data = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if isinstance(btn.callback_data, str)
        ]
        assert any(d.startswith("sess:kill:") for d in data)

    async def test_dead_session_no_kill_button(self, _patch_deps) -> None:
        _mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {42: "@0"}
        mock_tr.get_display_name.side_effect = lambda wid: "oldproject"
        mock_tm.list_windows = AsyncMock(return_value=[])

        _text, keyboard = await _build_dashboard(100)
        data = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if isinstance(btn.callback_data, str)
        ]
        assert not any(d.startswith("sess:kill:") for d in data)

    async def test_empty_dashboard_no_kill_button(self, _patch_deps) -> None:
        _text, keyboard = await _build_dashboard(100)
        data = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if isinstance(btn.callback_data, str)
        ]
        assert not any(d.startswith("sess:kill:") for d in data)


class TestRenameButtons:
    async def test_alive_session_has_rename_button(self, _patch_deps) -> None:
        _mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {42: "@0"}
        mock_tr.get_display_name.side_effect = lambda wid: "myproject"
        mock_tm.list_windows = AsyncMock(return_value=[MagicMock(window_id="@0")])

        _text, keyboard = await _build_dashboard(100)
        data = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if isinstance(btn.callback_data, str)
        ]
        assert any(d.startswith(CB_SESSIONS_RENAME) for d in data)

    @patch("ccgram.handlers.sessions_dashboard.user_owns_window", return_value=True)
    async def test_handle_sessions_rename_prompts_user(
        self, _mock_owns: MagicMock, _patch_deps: tuple
    ) -> None:
        _mock_sm, mock_tr, _, _ = _patch_deps
        mock_tr.get_display_name.return_value = "myproject"

        query = AsyncMock()
        query.message.message_thread_id = 42
        query.message.chat.id = -100123
        context = MagicMock()
        context.user_data = {}

        client = AsyncMock()
        await handle_sessions_rename(query, 100, "@0", context, client)

        from ccgram.handlers.user_state import (
            SESSION_RENAME_CHAT_ID,
            SESSION_RENAME_THREAD_ID,
            SESSION_RENAME_WINDOW_ID,
        )

        assert context.user_data[SESSION_RENAME_WINDOW_ID] == "@0"
        assert context.user_data[SESSION_RENAME_THREAD_ID] == 42
        assert context.user_data[SESSION_RENAME_CHAT_ID] == -100123
        client.send_message.assert_awaited_once()
        query.answer.assert_awaited_once_with("Rename session")

    async def test_apply_session_rename(self, _patch_deps: tuple) -> None:
        _mock_sm, mock_tr, mock_tm, mock_cfg = _patch_deps
        mock_cfg.tmux_session_prefix = "ccgram_"
        mock_window = MagicMock(window_id="cc_old:@0")
        mock_tm.find_window_by_id = AsyncMock(return_value=mock_window)
        mock_tm.rename_window = AsyncMock(return_value=True)
        mock_tm.rename_session = AsyncMock(return_value=True)

        from ccgram.handlers.user_state import (
            SESSION_RENAME_CHAT_ID,
            SESSION_RENAME_THREAD_ID,
            SESSION_RENAME_WINDOW_ID,
        )

        user_data = {
            SESSION_RENAME_WINDOW_ID: "cc_old:@0",
            SESSION_RENAME_THREAD_ID: 42,
            SESSION_RENAME_CHAT_ID: -100123,
        }
        message = AsyncMock()
        message.chat.id = -100123
        message.chat.type = "supergroup"
        client = AsyncMock()

        result = await apply_session_rename(
            user_data, 42, "new-project", message, client
        )

        assert result is True
        mock_tm.rename_window.assert_awaited_once_with("cc_old:@0", "new-project")
        mock_tm.rename_session.assert_awaited_once_with("cc_old", "ccgram_new-project")
        assert SESSION_RENAME_WINDOW_ID not in user_data
        client.edit_forum_topic.assert_awaited_once_with(
            -100123, 42, name="new-project"
        )

    async def test_apply_session_rename_cancelled(self, _patch_deps: tuple) -> None:
        from ccgram.handlers.user_state import (
            SESSION_RENAME_CHAT_ID,
            SESSION_RENAME_THREAD_ID,
            SESSION_RENAME_WINDOW_ID,
        )

        user_data = {
            SESSION_RENAME_WINDOW_ID: "@0",
            SESSION_RENAME_THREAD_ID: 42,
            SESSION_RENAME_CHAT_ID: -100123,
        }
        message = AsyncMock()

        result = await apply_session_rename(user_data, 42, "/cancel", message)

        assert result is True
        assert SESSION_RENAME_WINDOW_ID not in user_data
