"""Tests for FORUM_TOPIC_CLOSED under tmux ownership."""

from unittest.mock import AsyncMock, MagicMock, patch

from ccgram.tmux_manager import TmuxWindow


def _update() -> MagicMock:
    update = MagicMock()
    update.effective_user.id = 1
    update.effective_chat.id = -100
    update.message.message_thread_id = 42
    return update


async def test_closes_bound_managed_session() -> None:
    from ccgram.handlers.topics import topic_lifecycle

    context = MagicMock()
    session = TmuxWindow("cc_foo:@1", "cc_foo", "/tmp", topic_ref=(-100, 42))
    with (
        patch("ccgram.config.Config.is_user_allowed", return_value=True),
        patch.object(
            topic_lifecycle, "find_topic_session", new=AsyncMock(return_value=session)
        ),
        patch.object(topic_lifecycle, "tmux_manager") as tmux,
        patch.object(topic_lifecycle, "thread_router") as router,
        patch.object(
            topic_lifecycle, "clear_topic_state", new_callable=AsyncMock
        ) as clear,
    ):
        tmux.kill_session = AsyncMock(return_value=True)
        await topic_lifecycle.topic_closed_handler(_update(), context)

    tmux.kill_session.assert_awaited_once_with("cc_foo")
    clear.assert_awaited_once()
    router.unbind_thread.assert_called_once_with(1, 42)


async def test_ignores_unmanaged_topic_close() -> None:
    from ccgram.handlers.topics import topic_lifecycle

    context = MagicMock()
    context.bot.reopen_forum_topic = AsyncMock()
    with (
        patch("ccgram.config.Config.is_user_allowed", return_value=True),
        patch.object(
            topic_lifecycle, "find_topic_session", new=AsyncMock(return_value=None)
        ),
    ):
        await topic_lifecycle.topic_closed_handler(_update(), context)

    context.bot.reopen_forum_topic.assert_not_called()
