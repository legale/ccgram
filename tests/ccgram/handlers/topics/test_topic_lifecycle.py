"""Tests for topic lifecycle driven by managed tmux sessions."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccgram.handlers.polling.polling_state import lifecycle_strategy
from ccgram.tmux_manager import TmuxWindow


@pytest.fixture(autouse=True)
def _clean_strategy_state():
    lifecycle_strategy._states.clear()
    lifecycle_strategy._dead_notified.clear()
    yield
    lifecycle_strategy._states.clear()
    lifecycle_strategy._dead_notified.clear()


async def test_topic_created_claims_strict_name_match() -> None:
    from ccgram.handlers.topics import topic_lifecycle

    update = MagicMock()
    update.effective_user.id = 1
    update.effective_chat.id = -100
    update.message.message_thread_id = 42
    update.message.forum_topic_created.name = "foo"
    context = MagicMock()

    with (
        patch("ccgram.config.Config.is_user_allowed", return_value=True),
        patch.object(
            topic_lifecycle, "ensure_topic_session", new=AsyncMock(return_value=("cc_foo:@1", None))
        ) as ensure,
        patch(
            "ccgram.handlers.status.topic_emoji.sync_topic_name", new_callable=AsyncMock
        ),
    ):
        await topic_lifecycle.topic_created_handler(update, context)

    ensure.assert_awaited_once_with(1, -100, 42, "foo")


async def test_expired_topic_deletes_topic_then_kills_managed_session() -> None:
    from ccgram.handlers.topics import topic_lifecycle

    lifecycle_strategy.start_autoclose_timer(1, 42, "done", time.monotonic() - 9999)
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


async def test_prune_stale_state_only_syncs_live_windows() -> None:
    from ccgram.handlers.topics import topic_lifecycle

    window = MagicMock(window_id="cc_foo:@1", window_name="foo")
    with patch.object(topic_lifecycle, "session_manager") as sessions:
        await topic_lifecycle.prune_stale_state([window])

    sessions.sync_display_names.assert_called_once_with([("cc_foo:@1", "foo")])
    sessions.prune_stale_state.assert_called_once_with({"cc_foo:@1"})
