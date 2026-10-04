"""Tests for the tmux-authoritative /sessions dashboard."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import BadRequest

from ccgram.handlers.callback_data import CB_SESSIONS_RENAME, CB_SESSIONS_SCREENSHOT
from ccgram.handlers.sessions_dashboard import (
    _build_dashboard,
    _dispatch,
    apply_session_rename,
    handle_sessions_kill_confirm,
    handle_sessions_refresh,
    handle_sessions_rename,
    sessions_command,
)


@pytest.fixture(autouse=True)
def _patch_deps():
    with (
        patch("ccgram.handlers.sessions_dashboard.thread_router") as router,
        patch("ccgram.handlers.sessions_dashboard.tmux_manager") as tmux,
        patch("ccgram.handlers.sessions_dashboard.config") as config,
    ):
        router.get_all_thread_windows.return_value = {}
        router.get_display_name.side_effect = lambda wid: wid
        router.iter_thread_bindings.return_value = []
        tmux.list_sessions = AsyncMock(return_value=[])
        tmux.topic_name_from_session_name.side_effect = lambda name: name.removeprefix(
            "cc_"
        )
        tmux.topic_session_name.side_effect = lambda name: f"cc_{name}"
        config.tmux_session_prefix = "cc_"
        config.is_user_allowed.return_value = True
        yield router, tmux, config


def _session(
    name: str,
    *,
    wid: str = "@1",
    cwd: str = "/tmp",
    topic_ref: tuple[int, int] | None = None,
):
    return SimpleNamespace(
        window_id=f"{name}:{wid}",
        window_name=name,
        cwd=cwd,
        topic_ref=topic_ref,
    )


class TestBuildDashboard:
    async def test_empty(self, _patch_deps) -> None:
        text, keyboard = await _build_dashboard(100)
        assert "No active sessions" in text
        assert not keyboard.inline_keyboard

    async def test_lists_only_managed_sessions(self, _patch_deps) -> None:
        router, tmux, _config = _patch_deps
        router.get_all_thread_windows.return_value = {42: "cc_foo:@1"}
        tmux.list_sessions.return_value = [
            _session("cc_foo", cwd="/work/foo"),
            _session("cc_bar", wid="@2", cwd="/work/bar"),
            _session("other", wid="@3", cwd="/work/other"),
        ]

        text, keyboard = await _build_dashboard(100)

        assert "+ foo /work/foo" in text
        assert "o bar /work/bar" in text
        assert "other" not in text
        assert len(keyboard.inline_keyboard) == 4

    async def test_actions_use_managed_window_id(self, _patch_deps) -> None:
        router, tmux, _config = _patch_deps
        router.get_all_thread_windows.return_value = {42: "cc_foo:@7"}
        tmux.list_sessions.return_value = [_session("cc_foo", wid="@7")]

        _text, keyboard = await _build_dashboard(100)
        name_row, actions_row = keyboard.inline_keyboard

        assert name_row[0].text == "+ foo"
        assert name_row[0].callback_data == f"{CB_SESSIONS_RENAME}cc_foo:@7"
        assert actions_row[0].callback_data == f"{CB_SESSIONS_SCREENSHOT}cc_foo:@7"
        assert actions_row[1].callback_data == "sess:kill:cc_foo:@7"


class TestSessionsKill:
    async def test_deletes_topic_then_kills_session(self, _patch_deps) -> None:
        router, tmux, _config = _patch_deps
        tmux.list_sessions.return_value = [
            _session("cc_foo", wid="@7", topic_ref=(-100, 42))
        ]
        tmux.kill_session = AsyncMock(return_value=True)
        router.iter_thread_bindings.return_value = [(100, 42, "cc_foo:@7")]
        client = AsyncMock()
        query = AsyncMock()

        with (
            patch(
                "ccgram.handlers.sessions_dashboard.clear_topic_state",
                new_callable=AsyncMock,
            ) as clear,
            patch(
                "ccgram.handlers.sessions_dashboard.safe_edit",
                new_callable=AsyncMock,
            ),
        ):
            killed = await handle_sessions_kill_confirm(query, 100, "cc_foo:@7", client)

        assert killed is True
        client.delete_forum_topic.assert_awaited_once_with(-100, 42)
        tmux.kill_session.assert_awaited_once_with("cc_foo")
        clear.assert_awaited_once()
        router.unbind_thread.assert_called_once_with(100, 42)

    async def test_topic_delete_error_keeps_tmux(self, _patch_deps) -> None:
        _router, tmux, _config = _patch_deps
        tmux.list_sessions.return_value = [
            _session("cc_foo", wid="@7", topic_ref=(-100, 42))
        ]
        client = AsyncMock()
        client.delete_forum_topic.side_effect = BadRequest("forbidden")
        query = AsyncMock()

        with patch(
            "ccgram.handlers.sessions_dashboard.safe_edit", new_callable=AsyncMock
        ):
            killed = await handle_sessions_kill_confirm(query, 100, "cc_foo:@7", client)

        assert killed is False
        tmux.kill_session.assert_not_called()

    async def test_failed_tmux_kill_does_not_unbind(self, _patch_deps) -> None:
        router, tmux, _config = _patch_deps
        tmux.kill_session = AsyncMock(return_value=False)
        client = AsyncMock()
        query = AsyncMock()

        with patch(
            "ccgram.handlers.sessions_dashboard.safe_edit", new_callable=AsyncMock
        ):
            killed = await handle_sessions_kill_confirm(query, 100, "cc_foo:@7", client)

        assert killed is False
        router.unbind_thread.assert_not_called()


class TestSessionsCommand:
    async def test_calls_reply(self, _patch_deps) -> None:
        update = MagicMock()
        update.effective_user = MagicMock(id=100)
        update.message = AsyncMock()

        with patch(
            "ccgram.handlers.sessions_dashboard.safe_reply", new_callable=AsyncMock
        ) as reply:
            await sessions_command(update, MagicMock())

        reply.assert_awaited_once()
        assert "No active sessions" in reply.call_args.args[1]

    async def test_unauthorized(self, _patch_deps) -> None:
        _router, _tmux, config = _patch_deps
        config.is_user_allowed.return_value = False
        update = MagicMock()
        update.effective_user = MagicMock(id=100)
        update.message = AsyncMock()

        with patch(
            "ccgram.handlers.sessions_dashboard.safe_reply", new_callable=AsyncMock
        ) as reply:
            await sessions_command(update, MagicMock())

        assert "not authorized" in reply.call_args.args[1]


class TestRefreshAndDispatch:
    async def test_refresh_edits(self, _patch_deps) -> None:
        query = AsyncMock()
        with patch(
            "ccgram.handlers.sessions_dashboard.safe_edit", new_callable=AsyncMock
        ) as edit:
            await handle_sessions_refresh(query, 100)
        edit.assert_awaited_once()

    async def test_screenshot_dispatch(self, _patch_deps) -> None:
        query = AsyncMock()
        query.data = f"{CB_SESSIONS_SCREENSHOT}cc_foo:@7"
        update = MagicMock(callback_query=query)
        update.effective_user.id = 100
        context = MagicMock()

        with patch(
            "ccgram.handlers.live.screenshot_callbacks.handle_screenshot_callback",
            new_callable=AsyncMock,
        ) as screenshot:
            await _dispatch(update, context)

        screenshot.assert_awaited_once()
        assert screenshot.call_args.kwargs["allow_unowned"] is True


class TestRename:
    async def test_prompt_records_pending_rename(self, _patch_deps) -> None:
        router, _tmux, _config = _patch_deps
        router.get_display_name.return_value = "foo"
        router.get_all_thread_windows.return_value = {42: "cc_foo:@7"}
        query = AsyncMock()
        query.message.chat.id = -100
        context = MagicMock(user_data={})
        client = AsyncMock()

        await handle_sessions_rename(query, 100, "cc_foo:@7", context, client)

        assert context.user_data["_session_rename_window_id"] == "cc_foo:@7"
        assert context.user_data["_session_rename_thread_id"] == 42
        client.send_message.assert_awaited_once()

    async def test_apply_rename_only_renames_tmux(self, _patch_deps) -> None:
        _router, tmux, _config = _patch_deps
        tmux.rename_session = AsyncMock(return_value=True)
        tmux.topic_name_from_session_name.side_effect = lambda name: name.removeprefix(
            "cc_"
        )
        user_data = {
            "_session_rename_window_id": "cc_foo:@7",
            "_session_rename_thread_id": 42,
        }
        message = AsyncMock()

        with patch(
            "ccgram.handlers.sessions_dashboard.safe_reply", new_callable=AsyncMock
        ):
            handled = await apply_session_rename(user_data, 42, "bar", message)

        assert handled is True
        tmux.rename_session.assert_awaited_once_with("cc_foo", "cc_bar")
