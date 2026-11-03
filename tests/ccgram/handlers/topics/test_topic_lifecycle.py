"""Tests for topic lifecycle driven by managed tmux sessions."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccgram.handlers.topics.topic_lifecycle import (
    reset_lifecycle_state,
    start_autoclose_timer,
)
from ccgram.tmux_manager import TmuxWindow


@pytest.fixture(autouse=True)
def _clean_strategy_state():
    reset_lifecycle_state()
    yield
    reset_lifecycle_state()


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
    client_cls.return_value.send_message.assert_awaited_once_with(
        -100, "cc_foo", message_thread_id=42
    )


async def test_expired_topic_deletes_topic_then_kills_managed_session() -> None:
    from ccgram.handlers.topics import topic_lifecycle

    start_autoclose_timer(1, 42, "done", time.monotonic() - 9999)
    client = AsyncMock()
    session = TmuxWindow("cc_foo:@1", "cc_foo", "/tmp", topic_ref=(-100, 42))
    router = MagicMock()
    router.resolve_chat_id.return_value = -100
    router.get_window_for_thread.return_value = "cc_foo:@1"

    with (
        patch.object(topic_lifecycle, "config") as config,
        patch.object(topic_lifecycle, "thread_router", router),
        patch.object(
            topic_lifecycle, "find_topic_session", new=AsyncMock(return_value=session)
        ),
        patch.object(topic_lifecycle, "tmux_manager") as tmux,
        patch.object(topic_lifecycle, "clear_topic_state", new_callable=AsyncMock),
    ):
        config.autoclose_done_minutes = 1
        tmux.kill_session = AsyncMock(return_value=True)
        await topic_lifecycle.check_autoclose_timers(client)

    client.delete_forum_topic.assert_awaited_once_with(
        chat_id=-100, message_thread_id=42
    )
    tmux.kill_session.assert_awaited_once_with("cc_foo")
    router.unbind_thread.assert_called_once_with(1, 42)
