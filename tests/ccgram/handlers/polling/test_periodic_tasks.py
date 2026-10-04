from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from ccgram.handlers.polling import periodic_tasks
from ccgram.tmux_manager import TmuxWindow


def _window(session: str, window_id: str = "@1") -> TmuxWindow:
    return TmuxWindow(
        window_id=f"{session}:{window_id}",
        window_name="foo",
        cwd="/tmp",
    )


async def test_reconcile_primes_existing_session_without_sending_snapshot() -> None:
    periodic_tasks._session_runtime.clear()
    window = _window("cc_foo")
    client = AsyncMock()

    with (
        patch.object(periodic_tasks, "tmux_manager") as tmux,
        patch.object(periodic_tasks, "thread_router") as router,
        patch.object(periodic_tasks, "config") as config,
        patch.object(periodic_tasks, "prime_topic_status_diff") as prime,
    ):
        tmux.list_sessions = AsyncMock(return_value=[window])
        tmux.capture_pane = AsyncMock(return_value="current screen")
        tmux.session_name = "ccgram"
        config.tmux_session_prefix = "cc_"
        router.iter_thread_bindings.return_value = [(1, 42, window.window_id)]
        router.resolve_chat_id.return_value = -100

        await periodic_tasks.reconcile(client, [window])

    assert periodic_tasks._session_runtime["cc_foo"].thread_id == 42
    prime.assert_called_once_with(-100, 42, window.window_id, "current screen")
    client.create_forum_topic.assert_not_awaited()


async def test_reconcile_creates_topic_for_unbound_prefixed_session() -> None:
    periodic_tasks._session_runtime.clear()
    window = _window("cc_foo")
    client = AsyncMock()
    client.create_forum_topic.return_value = SimpleNamespace(message_thread_id=42)

    with (
        patch.object(periodic_tasks, "tmux_manager") as tmux,
        patch.object(periodic_tasks, "thread_router") as router,
        patch.object(periodic_tasks, "config") as config,
        patch.object(periodic_tasks, "prime_topic_status_diff"),
    ):
        tmux.list_sessions = AsyncMock(return_value=[window])
        tmux.capture_pane = AsyncMock(return_value="current screen")
        tmux.topic_name_from_session_name.return_value = "foo"
        config.tmux_session_prefix = "cc_"
        config.group_id = -100
        config.allowed_users = {1}
        router.iter_thread_bindings.return_value = []
        router.resolve_chat_id.return_value = -100

        await periodic_tasks.reconcile(client, [window])

    client.create_forum_topic.assert_awaited_once_with(-100, name="foo")
    router.bind_thread.assert_called_once_with(1, 42, window.window_id, "foo")
    assert periodic_tasks._session_runtime["cc_foo"].thread_id == 42


async def test_reconcile_uses_discovered_forum_chat_without_configured_group() -> None:
    window = _window("cc_foo")
    client = AsyncMock()
    client.create_forum_topic.return_value = SimpleNamespace(message_thread_id=42)

    with (
        patch.object(periodic_tasks, "tmux_manager") as tmux,
        patch.object(periodic_tasks, "thread_router") as router,
        patch.object(periodic_tasks, "config") as config,
        patch.object(periodic_tasks, "prime_topic_status_diff"),
    ):
        tmux.list_sessions = AsyncMock(return_value=[window])
        tmux.capture_pane = AsyncMock(return_value="current screen")
        tmux.topic_name_from_session_name.return_value = "foo"
        config.tmux_session_prefix = "cc_"
        config.group_id = None
        config.allowed_users = {1}
        router.iter_thread_bindings.return_value = []
        router.get_forum_chat_id.return_value = -100123
        router.resolve_chat_id.return_value = -100123

        await periodic_tasks.reconcile(client, [window])

    client.create_forum_topic.assert_awaited_once_with(-100123, name="foo")
    router.set_group_chat_id.assert_called_once_with(1, 42, -100123)


async def test_reconcile_creates_prefixed_session_for_topic() -> None:
    periodic_tasks._session_runtime.clear()
    client = AsyncMock()

    with (
        patch.object(periodic_tasks, "tmux_manager") as tmux,
        patch.object(periodic_tasks, "thread_router") as router,
        patch.object(periodic_tasks, "window_query") as window_query,
        patch.object(periodic_tasks, "config") as config,
        patch.object(periodic_tasks, "prime_topic_status_diff"),
    ):
        tmux.list_sessions = AsyncMock(return_value=[])
        tmux.topic_session_name.return_value = "prefix_foo"
        tmux.create_window = AsyncMock(return_value=(True, "", "foo", "prefix_foo:@2"))
        tmux.capture_pane = AsyncMock(return_value="current screen")
        config.tmux_session_prefix = "prefix_"
        config.session_working_directory = "/tmp"
        router.iter_thread_bindings.return_value = [(1, 42, "prefix_foo:@1")]
        router.get_display_name.return_value = "foo"
        router.resolve_chat_id.return_value = -100
        window_query.view_window.return_value = None

        await periodic_tasks.reconcile(client, [])

    tmux.create_window.assert_awaited_once_with(
        "/tmp",
        session_name="prefix_foo",
        window_name="foo",
        start_agent=False,
    )
    router.bind_thread.assert_called_once_with(1, 42, "prefix_foo:@2", "foo")


async def test_send_with_reconcile_retries_once_after_missing_window() -> None:
    client = AsyncMock()
    sender = AsyncMock(
        side_effect=[
            (False, "Window not found (may have been closed)"),
            (True, "Sent to foo"),
        ]
    )

    with (
        patch.object(periodic_tasks, "reconcile", new_callable=AsyncMock) as reconcile,
        patch.object(periodic_tasks, "tmux_manager") as tmux,
        patch.object(periodic_tasks, "thread_router") as router,
    ):
        tmux.list_windows = AsyncMock(return_value=[])
        router.get_window_for_thread.return_value = "cc_foo:@2"
        result = await periodic_tasks.send_with_reconcile(
            client,
            1,
            42,
            "cc_foo:@1",
            "hello",
            send_fn=sender,
        )

    assert result == (True, "Sent to foo")
    reconcile.assert_awaited_once()
    assert sender.await_count == 2
