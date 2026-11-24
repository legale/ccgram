"""Tests for topic lifecycle driven by managed tmux sessions."""

from unittest.mock import AsyncMock, MagicMock, patch


async def test_topic_created_claims_strict_name_match() -> None:
    from ccgram.handlers.topics import topic_lifecycle

    update = MagicMock()
    update.effective_user.id = 1
    update.effective_chat.id = -100
    update.message.message_thread_id = 42
    update.message.forum_topic_created.name = "foo"
    context = MagicMock()

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with (
        patch("ccgram.config.Config.is_user_allowed", return_value=True),
        patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ),
        patch.object(
            topic_lifecycle,
            "ensure_topic_session",
            new=AsyncMock(return_value=("cc_foo:@1", None)),
        ) as ensure,
        patch.object(
            topic_lifecycle, "PTBTelegramClient", return_value=MagicMock()
        ) as client_cls,
        patch.object(topic_lifecycle, "tmux_manager") as tmux,
    ):
        client_cls.return_value.send_message = AsyncMock()
        tmux.topic_session_name.return_value = "cc_foo"
        await topic_lifecycle.topic_created_handler(update, context)

    ensure.assert_awaited_once_with(1, -100, 42, "foo", rebind=True)
    client_cls.return_value.send_message.assert_not_awaited()
