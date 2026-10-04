"""Tests for /sessions dashboard command."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccgram.handlers.callback_data import CB_SESSIONS_RENAME, CB_SESSIONS_SCREENSHOT
from ccgram.handlers.sessions_dashboard import (
    _build_dashboard,
    _create_session_for_topic,
    _dispatch,
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
        mock_tm.list_sessions = AsyncMock(return_value=[])
        mock_tm.discover_external_sessions = AsyncMock(return_value=[])
        mock_tm.topic_session_name.side_effect = lambda name: f"cc_{name}"
        mock_tm.topic_name_from_session_name.side_effect = lambda name: (
            name.removeprefix("cc_")
        )
        mock_cfg.is_user_allowed.return_value = True
        yield mock_view, mock_tr, mock_tm, mock_cfg


class TestBuildDashboard:
    async def test_empty(self, _patch_deps) -> None:
        text, keyboard = await _build_dashboard(100)
        assert "No active sessions" in text
        assert not keyboard.inline_keyboard

    async def test_alive_session(self, _patch_deps) -> None:
        mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {42: "ccgram_myproject:@0"}
        mock_tr.get_display_name.side_effect = lambda wid: "myproject"
        mock_sm.side_effect = lambda wid: WindowState(cwd="/home/user/myproject")
        mock_tm.list_sessions = AsyncMock(
            return_value=[MagicMock(window_id="ccgram_myproject:@0", window_name="myproject", cwd="/home/user/myproject")]
        )

        text, _kb = await _build_dashboard(100)
        assert "+ myproject /home/user/myproject" in text

    async def test_dead_session(self, _patch_deps) -> None:
        _mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {42: "ccgram_myproject:@0"}
        mock_tr.get_display_name.side_effect = lambda wid: "oldproject"
        mock_tm.list_sessions = AsyncMock(return_value=[])

        text, _kb = await _build_dashboard(100)
        assert "No active sessions" in text

    async def test_multiple_sessions(self, _patch_deps) -> None:
        _mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {10: "ccgram_alive:@0"}
        mock_tr.get_display_name.side_effect = lambda wid: wid
        mock_tm.list_sessions = AsyncMock(
            return_value=[
                MagicMock(window_id="ccgram_alive:@0", window_name="alive", cwd="/alive"),
                MagicMock(window_id="foreign:@5", window_name="foreign", cwd="/foreign"),
            ]
        )

        text, _kb = await _build_dashboard(100)
        assert "+ alive /alive" in text
        assert "o foreign /foreign" in text

    async def test_unbound_windows_displayed(self, _patch_deps) -> None:
        _mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {10: "ccgram_bound-session:@0"}
        mock_tr.get_display_name.side_effect = lambda wid: "bound-session"
        unbound_win = MagicMock(
            window_id="@99", window_name="other-session", cwd="/home/user"
        )
        mock_tm.list_sessions = AsyncMock(
            return_value=[
                MagicMock(window_id="ccgram_bound-session:@0", window_name="bound-session", cwd="/bound"),
                unbound_win,
            ]
        )

        text, _kb = await _build_dashboard(100)
        assert "+ bound-session" in text
        assert "o other-session" in text
        assert "/home/user" in text
        assert "/bound" in text

    async def test_alive_session_has_rename_screenshot_and_kill_buttons(
        self, _patch_deps
    ) -> None:
        _mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {42: "ccgram_myproject:@0"}
        mock_tm.list_sessions = AsyncMock(
            return_value=[MagicMock(window_id="ccgram_myproject:@0", window_name="myproject", cwd="/tmp")]
        )

        _text, keyboard = await _build_dashboard(100)
        name_row, actions_row = keyboard.inline_keyboard
        assert [button.text for button in name_row] == ["+ myproject"]
        assert [button.text for button in actions_row] == ["scr", "kill"]
        assert name_row[0].callback_data.startswith(CB_SESSIONS_RENAME)
        assert actions_row[0].callback_data.startswith(CB_SESSIONS_SCREENSHOT)
        assert actions_row[1].callback_data.startswith("sess:kill:")

    async def test_session_lines_have_strict_status_name_format(
        self, _patch_deps
    ) -> None:
        _mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {42: "ccgram_myproject:@0"}
        mock_tr.get_display_name.side_effect = lambda wid: "myproject"
        mock_tm.list_sessions = AsyncMock(
            return_value=[
                MagicMock(window_id="ccgram_myproject:@0", window_name="myproject", cwd="/home/ruslan"),
                MagicMock(window_id="other:@1", window_name="other", cwd="/tmp"),
            ]
        )

        text, keyboard = await _build_dashboard(100)
        assert "```\n+ myproject /home/ruslan\no other /tmp\n```" in text
        assert len(keyboard.inline_keyboard) == 4
        assert [len(row) for row in keyboard.inline_keyboard] == [1, 2, 1, 2]

    async def test_dead_session_no_action_buttons(self, _patch_deps) -> None:
        _mock_sm, mock_tr, mock_tm, _ = _patch_deps
        mock_tr.get_all_thread_windows.return_value = {42: "ccgram_myproject:@0"}
        mock_tr.get_display_name.side_effect = lambda wid: "deadproject"
        mock_tm.list_sessions = AsyncMock(return_value=[])

        _text, keyboard = await _build_dashboard(100)
        data = [
            btn.callback_data
            for row in keyboard.inline_keyboard
            for btn in row
            if isinstance(btn.callback_data, str)
        ]
        assert data == []


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


class TestSessionButtons:
    async def test_screenshot_button_dispatches_for_unbound_session(
        self, _patch_deps
    ) -> None:
        query = AsyncMock()
        query.data = f"{CB_SESSIONS_SCREENSHOT}other:@7"
        update = MagicMock(callback_query=query)
        update.effective_user.id = 100
        context = MagicMock()

        with patch(
            "ccgram.handlers.live.screenshot_callbacks.handle_screenshot_callback",
            new_callable=AsyncMock,
        ) as mock_screenshot:
            await _dispatch(update, context)

        mock_screenshot.assert_awaited_once()
        assert mock_screenshot.call_args.kwargs["allow_unowned"] is True


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
        assert mock_tm.create_window.call_args.kwargs == {
            "session_name": "cc_codex",
            "window_name": "codex",
        }
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
        mock_tr.get_all_thread_windows.return_value = {42: "ccgram_myproject:@0"}
        mock_tr.get_display_name.side_effect = lambda wid: "myproject"
        mock_tm.list_sessions = AsyncMock(
            return_value=[MagicMock(window_id="ccgram_myproject:@0", window_name="myproject", cwd="/tmp")]
        )

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
        mock_tr.get_all_thread_windows.return_value = {42: "ccgram_myproject:@0"}
        mock_tr.get_display_name.side_effect = lambda wid: "oldproject"
        mock_tm.list_sessions = AsyncMock(return_value=[])

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
        mock_tm.list_sessions = AsyncMock(
            return_value=[MagicMock(window_id="ccgram_myproject:@0", window_name="myproject", cwd="/tmp")]
        )

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
        mock_tr.get_all_thread_windows.return_value = {42: "@0"}

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
        mock_cfg.tmux_session_prefix = "cc_"
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
        mock_tm.rename_session.assert_awaited_once_with("cc_old", "cc_new-project")
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
