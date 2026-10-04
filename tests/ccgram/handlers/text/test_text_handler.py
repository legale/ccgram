"""Tests for direct Telegram topic -> tmux routing."""

from unittest.mock import AsyncMock, MagicMock, patch

from ccgram.handlers.text import text_handler as module
from ccgram.tmux_manager import TmuxWindow


async def test_unbound_topic_restores_route_from_tmux_metadata() -> None:
    message = MagicMock()
    message.chat.id = -100
    session = TmuxWindow("cc_foo:@1", "cc_foo", "/tmp", topic_ref=(-100, 42))
    router = MagicMock()
    router.get_window_for_thread.return_value = None
    with (
        patch.object(module, "thread_router", router),
        patch(
            "ccgram.handlers.topics.topic_binding.find_topic_session",
            new=AsyncMock(return_value=session),
        ) as find,
        patch("ccgram.handlers.topics.topic_binding.bind_runtime") as bind,
    ):
        handled = await module._handle_unbound_topic(1, 42, message)

    assert handled is False
    find.assert_awaited_once_with(-100, 42)
    bind.assert_called_once_with(1, -100, 42, session)


async def test_unbound_topic_requires_bind() -> None:
    message = MagicMock()
    message.chat.id = -100
    router = MagicMock()
    router.get_window_for_thread.return_value = None
    with (
        patch.object(module, "thread_router", router),
        patch(
            "ccgram.handlers.topics.topic_binding.find_topic_session",
            new=AsyncMock(return_value=None),
        ),
        patch.object(module, "safe_reply", new_callable=AsyncMock) as reply,
    ):
        handled = await module._handle_unbound_topic(1, 42, message)

    assert handled is True
    reply.assert_awaited_once_with(message, "Topic is not bound. Use `//bind <name>`.")


async def test_forward_uses_reconcile_retry_path() -> None:
    message = MagicMock()
    message.chat.id = -100
    message.chat.send_action = AsyncMock()
    client = MagicMock()
    with (
        patch(
            "ccgram.handlers.polling.periodic_tasks.send_with_reconcile",
            new=AsyncMock(return_value=(True, "Sent")),
        ) as send,
        patch.object(module, "ack_reaction", new_callable=AsyncMock) as ack,
    ):
        await module._forward_message("cc_foo:@1", 1, 42, "hello", client, message)

    send.assert_awaited_once_with(
        client, 1, 42, "cc_foo:@1", "hello", raw=False, send_fn=module.send_to_window
    )
    ack.assert_awaited_once()


async def test_existing_binding_goes_directly_to_forward() -> None:
    update = MagicMock()
    update.effective_user.id = 1
    update.effective_chat = update.message.chat
    update.message.text = "hello"
    update.message.message_thread_id = 42
    update.message.chat.id = -100
    context = MagicMock()
    router = MagicMock()
    router.get_window_for_thread.return_value = "cc_foo:@1"

    with (
        patch.object(module, "thread_router", router),
        patch.object(module, "_get_thread_id", return_value=42),
        patch.object(module, "_handle_rename_captures", new=AsyncMock(return_value=False)),
        patch.object(module, "_handle_unbound_topic", new=AsyncMock(return_value=False)) as ensure,
        patch.object(module, "_forward_message", new_callable=AsyncMock) as forward,
        patch(
            "ccgram.handlers.status.topic_status_diff.mark_topic_status_activity"
        ),
    ):
        await module.handle_text_message(update, context)

    ensure.assert_awaited_once()
    forward.assert_awaited_once()


async def test_all_topic_creates_pair_instead_of_forwarding() -> None:
    update = MagicMock()
    update.effective_user.id = 1
    update.effective_chat = update.message.chat
    update.message.text = "tmp2"
    update.message.message_thread_id = 7
    update.message.chat.id = -100
    context = MagicMock()
    router = MagicMock()
    router.get_window_for_thread.return_value = "cc_all:@1"

    with (
        patch.object(module, "thread_router", router),
        patch.object(module, "_get_thread_id", return_value=7),
        patch.object(module, "_handle_rename_captures", new=AsyncMock(return_value=False)),
        patch.object(module, "_handle_unbound_topic", new=AsyncMock(return_value=False)),
        patch.object(module, "_handle_all_topic", new=AsyncMock(return_value=True)) as handle_all,
        patch.object(module, "_forward_message", new_callable=AsyncMock) as forward,
        patch(
            "ccgram.handlers.status.topic_status_diff.mark_topic_status_activity"
        ),
    ):
        await module.handle_text_message(update, context)

    handle_all.assert_awaited_once()
    forward.assert_not_awaited()
