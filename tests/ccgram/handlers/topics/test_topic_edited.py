"""Tests for tmux-authoritative Telegram topic rename handling."""

from unittest.mock import AsyncMock, MagicMock, patch

from ccgram.tmux_manager import TmuxWindow


def _update(name: str = "telegram-name") -> MagicMock:
    update = MagicMock()
    update.effective_user.id = 1
    update.effective_chat.id = -100
    update.message.message_thread_id = 42
    update.message.forum_topic_edited.name = name
    return update


async def test_bound_topic_name_is_restored_from_tmux() -> None:
    from ccgram.handlers.topics import topic_lifecycle

    context = MagicMock()
    context.bot.edit_forum_topic = AsyncMock()
    session = TmuxWindow("cc_foo:@1", "cc_foo", "/tmp", topic_ref=(-100, 42))
    with (
        patch("ccgram.config.Config.is_user_allowed", return_value=True),
        patch.object(
            topic_lifecycle, "find_topic_session", new=AsyncMock(return_value=session)
        ),
        patch.object(topic_lifecycle, "tmux_manager") as tmux,
    ):
        tmux.topic_name_from_session_name.return_value = "foo"
        await topic_lifecycle.topic_edited_handler(_update("bar"), context)

    context.bot.edit_forum_topic.assert_awaited_once_with(
        chat_id=-100, message_thread_id=42, name="foo"
    )
    tmux.rename_session.assert_not_called()


async def test_unbound_renamed_topic_is_ignored() -> None:
    from ccgram.handlers.topics import topic_lifecycle

    context = MagicMock()
    with (
        patch("ccgram.config.Config.is_user_allowed", return_value=True),
        patch.object(
            topic_lifecycle, "find_topic_session", new=AsyncMock(return_value=None)
        ),
    ):
        await topic_lifecycle.topic_edited_handler(_update("bar"), context)
