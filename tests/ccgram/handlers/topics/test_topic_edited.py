"""Tests for tmux-authoritative Telegram topic rename handling."""

from unittest.mock import AsyncMock, MagicMock, patch

from ccgram.tmux_manager import TmuxWindow


def _update(
    name: str = "telegram-name", chat_id: int = -100, thread_id: int = 42
) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = 1
    update.effective_chat.id = chat_id
    update.message.message_thread_id = thread_id
    update.message.forum_topic_edited.name = name
    update.effective_message = update.message
    return update


async def test_bound_topic_rename_renames_tmux_session() -> None:
    from ccgram.handlers.topics import topic_lifecycle

    context = MagicMock()
    session = TmuxWindow("cc_foo:@1", "cc_foo", "/tmp", topic_ref=(-100, 42))
    with (
        patch("ccgram.config.Config.is_user_allowed", return_value=True),
        patch.object(
            topic_lifecycle, "find_topic_session", new=AsyncMock(return_value=session)
        ),
        patch.object(topic_lifecycle, "tmux_manager") as tmux,
        patch.object(topic_lifecycle, "bind_runtime") as mock_bind,
    ):
        tmux.topic_session_name.return_value = "cc_bar"
        tmux.rename_session = AsyncMock(return_value=True)
        tmux.set_session_topic = AsyncMock(return_value=True)
        await topic_lifecycle.topic_edited_handler(_update("bar"), context)

    tmux.rename_session.assert_awaited_once_with("cc_foo", "cc_bar")
    assert session.window_name == "cc_bar"
    assert session.window_id == "cc_bar:@1"
    tmux.set_session_topic.assert_awaited_once_with("cc_bar", -100, 42)
    mock_bind.assert_called_once_with(1, -100, 42, session)


async def test_reconcile_prefix_is_removed_from_topic_name() -> None:
    from ccgram.handlers.topics import topic_lifecycle

    context = MagicMock()
    context.bot.edit_forum_topic = AsyncMock()
    session = TmuxWindow("cc_antig:@1", "cc_antig", "/tmp", topic_ref=(-100, 42))
    with (
        patch("ccgram.config.Config.is_user_allowed", return_value=True),
        patch.object(
            topic_lifecycle, "find_topic_session", new=AsyncMock(return_value=session)
        ),
        patch.object(topic_lifecycle, "tmux_manager") as tmux,
    ):
        tmux.topic_session_name.return_value = "cc_antig"
        await topic_lifecycle.topic_edited_handler(_update("cc_antig"), context)

    context.bot.edit_forum_topic.assert_awaited_once_with(
        chat_id=-100, message_thread_id=42, name="antig"
    )
    tmux.rename_session.assert_not_called()
    assert session.window_name == "cc_antig"


async def test_unbound_renamed_topic_is_ignored() -> None:
    from ccgram.handlers.topics import topic_lifecycle

    context = MagicMock()
    with (
        patch("ccgram.config.Config.is_user_allowed", return_value=True),
        patch.object(
            topic_lifecycle, "find_topic_session", new=AsyncMock(return_value=None)
        ),
        patch.object(
            topic_lifecycle.thread_router, "get_window_for_thread", return_value=None
        ),
        patch.object(
            topic_lifecycle.thread_router,
            "get_window_for_chat_thread",
            return_value=None,
        ),
    ):
        await topic_lifecycle.topic_edited_handler(_update("bar"), context)


async def test_bound_topic_rename_fallback_to_thread_router() -> None:
    from ccgram.handlers.topics import topic_lifecycle

    context = MagicMock()
    session = TmuxWindow("cc_old:@5", "cc_old", "/tmp", topic_ref=None)
    with (
        patch("ccgram.config.Config.is_user_allowed", return_value=True),
        patch.object(
            topic_lifecycle, "find_topic_session", new=AsyncMock(return_value=None)
        ),
        patch.object(
            topic_lifecycle.thread_router,
            "get_window_for_thread",
            return_value="cc_old:@5",
        ),
        patch.object(topic_lifecycle, "tmux_manager") as tmux,
        patch.object(topic_lifecycle, "bind_runtime") as mock_bind,
    ):
        tmux.list_sessions = AsyncMock(return_value=[session])
        tmux.topic_session_name.return_value = "cc_new"
        tmux.rename_session = AsyncMock(return_value=True)
        tmux.set_session_topic = AsyncMock(return_value=True)
        await topic_lifecycle.topic_edited_handler(_update("new"), context)

    tmux.rename_session.assert_awaited_once_with("cc_old", "cc_new")
    assert session.window_name == "cc_new"
    assert session.window_id == "cc_new:@5"
    tmux.set_session_topic.assert_awaited_once_with("cc_new", -100, 42)
    mock_bind.assert_called_once_with(1, -100, 42, session)


async def test_find_topic_session_matches_by_thread_id_fallback() -> None:
    from ccgram.handlers.topics.topic_binding import find_topic_session

    session = TmuxWindow("cc_foo:@1", "cc_foo", "/tmp", topic_ref=(86872, 42))
    with patch(
        "ccgram.handlers.topics.topic_binding._managed_sessions",
        new=AsyncMock(return_value=[session]),
    ):
        found = await find_topic_session(-100, 42)
        assert found is session
