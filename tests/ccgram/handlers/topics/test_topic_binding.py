"""Tests for strict tmux-authoritative topic binding."""

from unittest.mock import AsyncMock, MagicMock, patch

from ccgram.tmux_manager import TmuxWindow


def _session(name: str = "cc_foo", ref: tuple[int, int] | None = None) -> TmuxWindow:
    return TmuxWindow(f"{name}:@1", name, "/tmp", topic_ref=ref)


async def test_existing_topic_ref_wins_over_name() -> None:
    from ccgram.handlers.topics import topic_binding

    session = _session("cc_bar", (-100, 42))
    router = MagicMock()
    with patch.object(topic_binding, "tmux_manager") as tmux:
        tmux.list_sessions = AsyncMock(return_value=[session])
        tmux.topic_name_from_session_name.return_value = "bar"
        window_id, error = await topic_binding.ensure_topic_session(
            1, -100, 42, "foo", router=router
        )

    assert error is None
    assert window_id == "cc_bar:@1"
    tmux.set_session_topic.assert_not_called()
    tmux.create_window.assert_not_called()
    router.bind_thread.assert_called_once_with(1, 42, "cc_bar:@1", window_name="bar")


async def test_claims_exact_unbound_session() -> None:
    from ccgram.handlers.topics import topic_binding

    session = _session()
    router = MagicMock()
    with patch.object(topic_binding, "tmux_manager") as tmux:
        tmux.list_sessions = AsyncMock(return_value=[session])
        tmux.topic_session_name.return_value = "cc_foo"
        tmux.topic_name_from_session_name.return_value = "foo"
        tmux.set_session_topic = AsyncMock(return_value=True)
        window_id, error = await topic_binding.ensure_topic_session(
            1, -100, 42, "foo", router=router
        )

    assert error is None
    assert window_id == "cc_foo:@1"
    tmux.set_session_topic.assert_awaited_once_with("cc_foo", -100, 42)
    tmux.create_window.assert_not_called()


async def test_creates_missing_exact_session() -> None:
    from ccgram.handlers.topics import topic_binding

    router = MagicMock()
    with (
        patch.object(topic_binding, "tmux_manager") as tmux,
        patch.object(topic_binding, "config") as config,
    ):
        tmux.list_sessions = AsyncMock(return_value=[])
        tmux.topic_session_name.return_value = "cc_foo"
        tmux.topic_name_from_session_name.return_value = "foo"
        tmux.create_window = AsyncMock(return_value=(True, "", "foo", "cc_foo:@1"))
        tmux.set_session_topic = AsyncMock(return_value=True)
        config.session_working_directory = "/tmp"
        window_id, error = await topic_binding.ensure_topic_session(
            1, -100, 42, "foo", router=router
        )

    assert error is None
    assert window_id == "cc_foo:@1"
    tmux.create_window.assert_awaited_once_with(
        "/tmp", session_name="cc_foo", window_name="foo", start_agent=False
    )
    tmux.set_session_topic.assert_awaited_once_with("cc_foo", -100, 42)


async def test_rejects_duplicate_topic_name() -> None:
    from ccgram.handlers.topics import topic_binding

    session = _session(ref=(-100, 7))
    router = MagicMock()
    with patch.object(topic_binding, "tmux_manager") as tmux:
        tmux.list_sessions = AsyncMock(return_value=[session])
        tmux.topic_session_name.return_value = "cc_foo"
        window_id, error = await topic_binding.ensure_topic_session(
            1, -100, 42, "foo", router=router
        )

    assert window_id is None
    assert "already bound" in (error or "")
    tmux.set_session_topic.assert_not_called()
    tmux.create_window.assert_not_called()
    router.bind_thread.assert_not_called()


async def test_rejects_duplicate_tmux_topic_ref() -> None:
    from ccgram.handlers.topics import topic_binding

    sessions = [_session("cc_foo", (-100, 42)), _session("cc_bar", (-100, 42))]
    with patch.object(topic_binding, "tmux_manager") as tmux:
        tmux.list_sessions = AsyncMock(return_value=sessions)
        window_id, error = await topic_binding.ensure_topic_session(1, -100, 42, "foo")

    assert window_id is None
    assert "Multiple tmux sessions" in (error or "")


async def test_explicit_rebind_moves_topic_to_requested_session() -> None:
    from ccgram.handlers.topics import topic_binding

    old = _session("cc_bar", (-100, 42))
    target = _session("cc_foo", (-100, 7))
    router = MagicMock()
    router.iter_thread_bindings.return_value = []
    with patch.object(topic_binding, "tmux_manager") as tmux:
        tmux.list_sessions = AsyncMock(return_value=[old, target])
        tmux.topic_session_name.return_value = "cc_foo"
        tmux.topic_name_from_session_name.return_value = "foo"
        tmux.clear_session_topic = AsyncMock(return_value=True)
        tmux.set_session_topic = AsyncMock(return_value=True)
        window_id, error = await topic_binding.ensure_topic_session(
            1, -100, 42, "foo", rebind=True, router=router
        )

    assert error is None
    assert window_id == "cc_foo:@1"
    tmux.clear_session_topic.assert_awaited_once_with("cc_bar")
    tmux.set_session_topic.assert_awaited_once_with("cc_foo", -100, 42)
    assert old.topic_ref is None
    assert target.topic_ref == (-100, 42)


async def test_create_from_all_rejects_existing_session_before_topic_creation() -> None:
    from ccgram.handlers.topics import topic_binding

    client = AsyncMock()
    with patch.object(topic_binding, "tmux_manager") as tmux:
        tmux.list_sessions = AsyncMock(return_value=[_session("cc_tmp2")])
        tmux.topic_session_name.return_value = "cc_tmp2"
        window_id, error = await topic_binding.create_from_all(
            1, -100, "tmp2", client
        )

    assert window_id is None
    assert error == "Session cc_tmp2 already exists."
    client.create_forum_topic.assert_not_awaited()
    tmux.create_window.assert_not_called()


async def test_create_from_all_creates_session_topic_and_binding() -> None:
    from types import SimpleNamespace
    from ccgram.handlers.topics import topic_binding

    client = AsyncMock()
    client.create_forum_topic.return_value = SimpleNamespace(message_thread_id=99)
    router = MagicMock()
    with (
        patch.object(topic_binding, "tmux_manager") as tmux,
        patch.object(topic_binding, "config") as config,
    ):
        tmux.list_sessions = AsyncMock(return_value=[])
        tmux.topic_session_name.return_value = "cc_tmp2"
        tmux.topic_name_from_session_name.return_value = "tmp2"
        tmux.create_window = AsyncMock(return_value=(True, "", "tmp2", "cc_tmp2:@1"))
        tmux.set_session_topic = AsyncMock(return_value=True)
        config.session_working_directory = "/tmp"
        window_id, error = await topic_binding.create_from_all(
            1, -100, "tmp2", client, router=router
        )

    assert error is None
    assert window_id == "cc_tmp2:@1"
    client.create_forum_topic.assert_awaited_once_with(-100, name="tmp2")
    tmux.set_session_topic.assert_awaited_once_with("cc_tmp2", -100, 99)
    router.bind_thread.assert_called_once_with(1, 99, "cc_tmp2:@1", window_name="tmp2")
    router.set_group_chat_id.assert_called_once_with(1, 99, -100)
