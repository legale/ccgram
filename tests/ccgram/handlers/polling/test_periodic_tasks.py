"""Tests for tmux-authoritative reconciliation."""

from unittest.mock import AsyncMock, MagicMock, patch

from telegram.error import BadRequest

from ccgram.handlers.polling import periodic_tasks
from ccgram.thread_router import ThreadRouter
from ccgram.tmux_manager import TmuxWindow


def _session(
    name: str = "cc_foo", ref: tuple[int, int] | None = (-100, 42)
) -> TmuxWindow:
    return TmuxWindow(f"{name}:@1", name, "/tmp", topic_ref=ref)


def _router() -> ThreadRouter:
    return ThreadRouter()


async def test_reconcile_restores_runtime_from_tmux_option() -> None:
    session = _session()
    client = AsyncMock()
    router = _router()

    with (
        patch.object(periodic_tasks, "tmux_manager") as tmux,
        patch.object(periodic_tasks, "thread_router", router),
        patch.object(periodic_tasks, "config") as config,
        patch.object(periodic_tasks, "prime_topic_status_diff") as prime,
    ):
        tmux.list_sessions = AsyncMock(return_value=[session])
        tmux.topic_name_from_session_name.return_value = "foo"
        tmux.capture_pane = AsyncMock(return_value="current screen")
        config.tmux_session_prefix = "cc_"
        config.allowed_users = {1}
        await periodic_tasks.reconcile(client)

    assert router.get_window_for_thread(1, 42) == "cc_foo:@1"
    assert router.resolve_chat_id(1, 42) == -100
    prime.assert_called_once_with(-100, 42, "cc_foo:@1", "current screen")
    client.reopen_forum_topic.assert_awaited_once_with(-100, 42)
    client.edit_forum_topic.assert_awaited_once_with(-100, 42, name="foo")
    client.create_forum_topic.assert_not_called()


async def test_reconcile_ignores_unbound_prefixed_candidate() -> None:
    session = _session(ref=None)
    client = AsyncMock()
    router = _router()
    with (
        patch.object(periodic_tasks, "tmux_manager") as tmux,
        patch.object(periodic_tasks, "thread_router", router),
        patch.object(periodic_tasks, "config") as config,
    ):
        tmux.list_sessions = AsyncMock(return_value=[session])
        config.tmux_session_prefix = "cc_"
        await periodic_tasks.reconcile(client)

    client.create_forum_topic.assert_not_called()
    client.delete_forum_topic.assert_not_called()
    assert list(router.iter_thread_bindings()) == []


async def test_reconcile_drops_route_when_tmux_session_disappears() -> None:
    client = AsyncMock()
    router = _router()
    router.bind_thread(1, 42, "cc_foo:@1", "foo")
    router.set_group_chat_id(1, 42, -100)

    with (
        patch.object(periodic_tasks, "tmux_manager") as tmux,
        patch.object(periodic_tasks, "thread_router", router),
        patch.object(periodic_tasks, "config") as config,
        patch("ccgram.handlers.cleanup.clear_topic_state", new_callable=AsyncMock),
    ):
        tmux.list_sessions = AsyncMock(return_value=[])
        config.tmux_session_prefix = "cc_"
        await periodic_tasks.reconcile(client)

    client.delete_forum_topic.assert_not_called()
    tmux.create_window.assert_not_called()
    assert router.get_window_for_thread(1, 42) is None


async def test_reconcile_session_rename_keeps_topic_and_updates_route() -> None:
    session = _session("cc_bar")
    client = AsyncMock()
    router = _router()
    router.bind_thread(1, 42, "cc_foo:@1", "foo")
    router.set_group_chat_id(1, 42, -100)

    with (
        patch.object(periodic_tasks, "tmux_manager") as tmux,
        patch.object(periodic_tasks, "thread_router", router),
        patch.object(periodic_tasks, "config") as config,
        patch.object(periodic_tasks, "prime_topic_status_diff"),
    ):
        tmux.list_sessions = AsyncMock(return_value=[session])
        tmux.topic_name_from_session_name.return_value = "bar"
        tmux.capture_pane = AsyncMock(return_value=None)
        config.tmux_session_prefix = "cc_"
        config.allowed_users = {1}
        await periodic_tasks.reconcile(client)

    assert router.get_window_for_thread(1, 42) == "cc_bar:@1"
    client.delete_forum_topic.assert_not_called()
    client.edit_forum_topic.assert_awaited_once_with(-100, 42, name="bar")


async def test_reconcile_removes_session_when_telegram_topic_is_missing() -> None:
    session = _session()
    client = AsyncMock()
    client.reopen_forum_topic.side_effect = BadRequest("Message thread not found")
    router = _router()

    with (
        patch.object(periodic_tasks, "tmux_manager") as tmux,
        patch.object(periodic_tasks, "thread_router", router),
        patch.object(periodic_tasks, "config") as config,
        patch.object(periodic_tasks, "prime_topic_status_diff"),
        patch("ccgram.handlers.cleanup.clear_topic_state", new_callable=AsyncMock),
    ):
        tmux.list_sessions = AsyncMock(return_value=[session])
        tmux.capture_pane = AsyncMock(return_value=None)
        tmux.kill_session = AsyncMock(return_value=True)
        config.tmux_session_prefix = "cc_"
        config.allowed_users = {1}
        await periodic_tasks.reconcile(client)

    tmux.kill_session.assert_awaited_once_with("cc_foo")
    client.create_forum_topic.assert_not_called()
    assert session.topic_ref is None
    assert router.get_window_for_thread(1, 42) is None


async def test_reconcile_checks_existing_binding_for_dead_topic() -> None:
    session = _session()
    client = AsyncMock()
    client.reopen_forum_topic.side_effect = BadRequest("Message thread not found")
    router = _router()
    router.bind_thread(1, 42, "cc_foo:@1", "foo")
    router.set_group_chat_id(1, 42, -100)

    with (
        patch.object(periodic_tasks, "tmux_manager") as tmux,
        patch.object(periodic_tasks, "thread_router", router),
        patch.object(periodic_tasks, "config") as config,
        patch("ccgram.handlers.cleanup.clear_topic_state", new_callable=AsyncMock),
    ):
        tmux.list_sessions = AsyncMock(return_value=[session])
        tmux.kill_session = AsyncMock(return_value=True)
        config.tmux_session_prefix = "cc_"
        await periodic_tasks.reconcile(client)

    client.reopen_forum_topic.assert_awaited_once_with(-100, 42)
    tmux.kill_session.assert_awaited_once_with("cc_foo")
    assert router.get_window_for_thread(1, 42) is None


async def test_send_with_reconcile_retries_only_after_route_changes() -> None:
    client = AsyncMock()
    sender = AsyncMock(
        side_effect=[
            (False, "Window not found (may have been closed)"),
            (True, "Sent"),
        ]
    )
    router = MagicMock()
    router.get_window_for_thread.return_value = "cc_bar:@2"

    with (
        patch.object(periodic_tasks, "reconcile", new_callable=AsyncMock) as reconcile,
        patch.object(periodic_tasks, "thread_router", router),
    ):
        result = await periodic_tasks.send_with_reconcile(
            client, 1, 42, "cc_foo:@1", "hello", send_fn=sender
        )

    assert result == (True, "Sent")
    reconcile.assert_awaited_once()
    assert sender.await_count == 2
