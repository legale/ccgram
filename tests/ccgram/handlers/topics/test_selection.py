from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import InlineKeyboardMarkup

from ccgram.handlers.callback_data import (
    CB_DIR_CANCEL,
    CB_MODE_SELECT,
    CB_PROV_SELECT,
)
from ccgram.handlers.topics.directory_browser import (
    build_mode_picker,
    build_provider_picker,
)
from ccgram.handlers.topics.directory_callbacks import (
    _handle_confirm,
    _handle_provider_select,
    _try_install_messaging_skill,
)
from ccgram.handlers.user_state import (
    PENDING_THREAD_ID,
)


class TestBuildProviderPicker:
    def test_returns_text_and_keyboard(self) -> None:
        text, keyboard = build_provider_picker("/home/user/project")
        assert "Select Provider" in text
        assert isinstance(keyboard, InlineKeyboardMarkup)

    def test_shows_all_three_providers(self) -> None:
        text, keyboard = build_provider_picker("/tmp/test")
        buttons = keyboard.inline_keyboard
        labels = [btn.text for row in buttons for btn in row]
        assert any("Claude" in label for label in labels)
        assert any("Codex" in label for label in labels)
        assert any("Gemini" in label for label in labels)

    def test_claude_marked_as_default(self) -> None:
        _text, keyboard = build_provider_picker("/tmp/test")
        buttons = keyboard.inline_keyboard
        claude_labels = [
            btn.text for row in buttons for btn in row if "Claude" in btn.text
        ]
        assert any("default" in label for label in claude_labels)

    def test_callback_data_uses_prov_prefix(self) -> None:
        _text, keyboard = build_provider_picker("/tmp/test")
        buttons = keyboard.inline_keyboard
        provider_callbacks = [
            btn.callback_data
            for row in buttons
            for btn in row
            if isinstance(btn.callback_data, str)
            and btn.callback_data.startswith(CB_PROV_SELECT)
        ]
        assert f"{CB_PROV_SELECT}claude" in provider_callbacks
        assert f"{CB_PROV_SELECT}codex" in provider_callbacks
        assert f"{CB_PROV_SELECT}gemini" in provider_callbacks

    def test_has_cancel_button(self) -> None:
        _text, keyboard = build_provider_picker("/tmp/test")
        buttons = keyboard.inline_keyboard
        cancel_callbacks = [btn.callback_data for row in buttons for btn in row]
        assert CB_DIR_CANCEL in cancel_callbacks

    def test_displays_directory_path(self) -> None:
        text, _keyboard = build_provider_picker("/home/user/my-project")
        assert "my-project" in text

    def test_tilde_substitution(self) -> None:
        home = str(Path.home())
        text, _keyboard = build_provider_picker(f"{home}/project")
        assert "~/project" in text


class TestBuildModePicker:
    def test_returns_text_and_keyboard(self) -> None:
        text, keyboard = build_mode_picker("/home/user/project", "claude")
        assert "Select Session Mode" in text
        assert isinstance(keyboard, InlineKeyboardMarkup)

    def test_mode_callbacks(self) -> None:
        _text, keyboard = build_mode_picker("/tmp/test", "codex")
        callbacks = [
            btn.callback_data for row in keyboard.inline_keyboard for btn in row
        ]
        assert f"{CB_MODE_SELECT}codex:normal" in callbacks
        assert f"{CB_MODE_SELECT}codex:yolo" in callbacks
        assert CB_DIR_CANCEL in callbacks


def _make_context(user_data: dict | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.user_data = user_data if user_data is not None else {}
    ctx.bot = AsyncMock()
    return ctx


def _make_query(
    data: str = "", *, chat_type: str = "supergroup", chat_id: int = -100999
) -> AsyncMock:
    query = AsyncMock()
    query.data = data
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.chat.type = chat_type
    query.message.chat.id = chat_id
    return query


def _make_update(thread_id: int = 42) -> MagicMock:
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = 100
    update.message = None
    update.callback_query = MagicMock()
    update.callback_query.message = MagicMock()
    update.callback_query.message.message_thread_id = thread_id
    update.callback_query.message.chat.type = "supergroup"
    update.callback_query.message.chat.id = -100999
    return update


class TestHandleConfirmShowsProviderPicker:
    @patch(
        "ccgram.handlers.topics.directory_callbacks._wait_for_shell_ready",
        new_callable=AsyncMock,
    )
    @patch(
        "ccgram.handlers.shell.shell_prompt_orchestrator.ensure_setup",
        new_callable=AsyncMock,
    )
    @patch(
        "ccgram.handlers.topics.directory_callbacks.safe_edit", new_callable=AsyncMock
    )
    @patch("ccgram.handlers.topics.directory_callbacks.tmux_manager")
    @patch("ccgram.handlers.topics.directory_callbacks.session_manager")
    @patch("ccgram.handlers.topics.directory_callbacks.thread_router")
    async def test_confirm_creates_shell_session_and_clears_state(
        self,
        mock_tr: MagicMock,
        mock_sm: MagicMock,
        mock_tm: MagicMock,
        mock_edit: AsyncMock,
        mock_setup: AsyncMock,
        mock_wait: AsyncMock,
    ) -> None:
        mock_tr.get_window_for_thread.return_value = None
        mock_tm.create_window = AsyncMock(
            return_value=(True, "Window created", "test", "@1")
        )
        mock_tm.stamp_pane_title = AsyncMock()
        mock_tm.topic_session_name.return_value = "test"
        user_data = {
            "browse_path": "/tmp/test",
            "browse_page": 2,
            "browse_dirs": ["a", "b"],
            "state": "browsing_directory",
            PENDING_THREAD_ID: 42,
        }
        query = _make_query()
        update = _make_update(thread_id=42)
        context = _make_context(user_data)

        await _handle_confirm(query, 100, update, context)

        mock_tm.create_window.assert_called_once()
        assert "browse_path" not in user_data
        assert "state" not in user_data


class TestHandleProviderSelect:
    @patch(
        "ccgram.handlers.topics.directory_callbacks.safe_edit", new_callable=AsyncMock
    )
    @patch("ccgram.handlers.topics.directory_callbacks.tmux_manager")
    @patch("ccgram.handlers.topics.directory_callbacks.session_manager")
    @patch("ccgram.handlers.topics.directory_callbacks.thread_router")
    @patch(
        "ccgram.handlers.topics.directory_callbacks._wait_for_shell_ready",
        new_callable=AsyncMock,
    )
    @patch(
        "ccgram.handlers.shell.shell_prompt_orchestrator.ensure_setup",
        new_callable=AsyncMock,
    )
    async def test_creates_window_directly_without_mode_picker(
        self,
        mock_setup: AsyncMock,
        mock_wait: AsyncMock,
        mock_tr: MagicMock,
        mock_sm: MagicMock,
        mock_tmux: MagicMock,
        mock_edit: AsyncMock,
    ) -> None:
        mock_tr.get_window_for_thread.return_value = None
        mock_tmux.create_window = AsyncMock(
            return_value=(True, "Window created", "test", "@1")
        )
        mock_tmux.stamp_pane_title = AsyncMock()
        mock_tmux.topic_session_name.return_value = "test"

        user_data = {"browse_path": "/tmp/test", PENDING_THREAD_ID: 42}
        query = _make_query(data=f"{CB_PROV_SELECT}shell")
        update = _make_update(thread_id=42)
        context = _make_context(user_data)

        await _handle_provider_select(
            query, 100, f"{CB_PROV_SELECT}shell", update, context
        )

        mock_tmux.create_window.assert_called_once()

    async def test_rejects_unknown_provider(self) -> None:
        query = _make_query(data=f"{CB_PROV_SELECT}unknown")
        update = _make_update()
        context = _make_context()

        await _handle_provider_select(
            query, 100, f"{CB_PROV_SELECT}unknown", update, context
        )
        query.answer.assert_any_call("Unknown provider", show_alert=True)


class TestTryInstallMessagingSkill:
    def test_noop(self) -> None:
        _try_install_messaging_skill("shell", "/tmp/proj")
