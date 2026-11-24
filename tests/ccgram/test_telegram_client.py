from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import InputMedia, Message
from telegram.error import TelegramError

from ccgram.telegram_client import (
    PTBTelegramClient,
    TelegramClient,
)
from tests.ccgram._helpers import FakeTelegramClient


@pytest.fixture
def fake_bot() -> MagicMock:
    bot = MagicMock()
    for name in (
        "send_message",
        "edit_message_text",
        "edit_message_media",
        "edit_message_caption",
        "edit_message_reply_markup",
        "send_photo",
        "send_document",
        "send_chat_action",
        "edit_forum_topic",
        "close_forum_topic",
        "reopen_forum_topic",
        "delete_forum_topic",
    ):
        setattr(bot, name, AsyncMock(return_value=True))
    return bot


class TestProtocolStructure:
    def test_protocol_is_runtime_checkable(self):
        assert isinstance(FakeTelegramClient(), TelegramClient)

    def test_ptb_adapter_satisfies_protocol(self, fake_bot: MagicMock):
        client = PTBTelegramClient(fake_bot)
        assert isinstance(client, TelegramClient)


class TestPTBTelegramClient:
    async def test_send_message_delegates_with_kwargs(self, fake_bot: MagicMock):
        sentinel = MagicMock(spec=Message)
        fake_bot.send_message.return_value = sentinel

        client = PTBTelegramClient(fake_bot)
        result = await client.send_message(
            chat_id=42, text="hi", message_thread_id=7, parse_mode="MarkdownV2"
        )

        assert result is sentinel
        fake_bot.send_message.assert_awaited_once_with(
            chat_id=42, text="hi", message_thread_id=7, parse_mode="MarkdownV2"
        )

    async def test_edit_message_text_delegates(self, fake_bot: MagicMock):
        client = PTBTelegramClient(fake_bot)
        await client.edit_message_text(text="new", chat_id=1, message_id=2)
        fake_bot.edit_message_text.assert_awaited_once_with(
            text="new", chat_id=1, message_id=2
        )

    async def test_send_chat_action_delegates(self, fake_bot: MagicMock):
        client = PTBTelegramClient(fake_bot)
        await client.send_chat_action(chat_id=1, action="typing")
        fake_bot.send_chat_action.assert_awaited_once_with(chat_id=1, action="typing")

    async def test_send_photo_delegates(self, fake_bot: MagicMock):
        client = PTBTelegramClient(fake_bot)
        photo = object()
        await client.send_photo(chat_id=1, photo=photo, caption="x")
        fake_bot.send_photo.assert_awaited_once_with(
            chat_id=1, photo=photo, caption="x"
        )

    async def test_send_document_delegates(self, fake_bot: MagicMock):
        client = PTBTelegramClient(fake_bot)
        doc = object()
        await client.send_document(chat_id=1, document=doc)
        fake_bot.send_document.assert_awaited_once_with(chat_id=1, document=doc)

    async def test_edit_forum_topic_delegates(self, fake_bot: MagicMock):
        client = PTBTelegramClient(fake_bot)
        await client.edit_forum_topic(chat_id=1, message_thread_id=7, name="new")
        fake_bot.edit_forum_topic.assert_awaited_once_with(
            chat_id=1, message_thread_id=7, name="new"
        )

    async def test_close_forum_topic_delegates(self, fake_bot: MagicMock):
        client = PTBTelegramClient(fake_bot)
        await client.close_forum_topic(chat_id=1, message_thread_id=7)
        fake_bot.close_forum_topic.assert_awaited_once_with(
            chat_id=1, message_thread_id=7
        )

    async def test_reopen_forum_topic_delegates(self, fake_bot: MagicMock):
        client = PTBTelegramClient(fake_bot)
        await client.reopen_forum_topic(chat_id=1, message_thread_id=7)
        fake_bot.reopen_forum_topic.assert_awaited_once_with(
            chat_id=1, message_thread_id=7
        )

    async def test_delete_forum_topic_delegates(self, fake_bot: MagicMock):
        client = PTBTelegramClient(fake_bot)
        await client.delete_forum_topic(chat_id=1, message_thread_id=7)
        fake_bot.delete_forum_topic.assert_awaited_once_with(
            chat_id=1, message_thread_id=7
        )

    async def test_edit_message_caption_delegates(self, fake_bot: MagicMock):
        client = PTBTelegramClient(fake_bot)
        await client.edit_message_caption(chat_id=1, message_id=2, caption="new")
        fake_bot.edit_message_caption.assert_awaited_once_with(
            chat_id=1, message_id=2, caption="new"
        )

    async def test_edit_message_media_delegates(self, fake_bot: MagicMock):
        client = PTBTelegramClient(fake_bot)
        media = MagicMock(spec=InputMedia)
        await client.edit_message_media(chat_id=1, message_id=2, media=media)
        fake_bot.edit_message_media.assert_awaited_once_with(
            chat_id=1, message_id=2, media=media
        )


class TestFakeTelegramClient:
    async def test_records_call_metadata(self):
        client = FakeTelegramClient()
        await client.send_message(chat_id=1, text="hello", message_thread_id=7)

        assert client.call_count("send_message") == 1
        last = client.last_call("send_message")
        assert last is not None
        assert last.method == "send_message"
        assert last.kwargs == {"chat_id": 1, "text": "hello", "message_thread_id": 7}

    async def test_records_multiple_calls_in_order(self):
        client = FakeTelegramClient()
        await client.send_message(chat_id=1, text="a")
        await client.close_forum_topic(chat_id=1, message_thread_id=10)
        await client.send_message(chat_id=2, text="b")

        assert [c.method for c in client.calls] == [
            "send_message",
            "close_forum_topic",
            "send_message",
        ]
        assert client.call_count("send_message") == 2
        assert client.call_count("close_forum_topic") == 1

    async def test_returns_default_true_for_bool_methods(self):
        client = FakeTelegramClient()
        assert await client.close_forum_topic(chat_id=1, message_thread_id=2) is True
        assert await client.send_chat_action(chat_id=1, action="typing") is True

    async def test_custom_return_value_via_returns_dict(self):
        client = FakeTelegramClient()
        sentinel = MagicMock(spec=Message)
        client.returns["send_message"] = sentinel
        result = await client.send_message(chat_id=1, text="hi")
        assert result is sentinel

    async def test_custom_return_value_via_callable(self):
        client = FakeTelegramClient()
        client.returns["send_message"] = lambda **kw: kw["text"].upper()
        result = await client.send_message(chat_id=1, text="hi")
        assert result == "HI"

    async def test_last_call_returns_none_for_unseen_method(self):
        client = FakeTelegramClient()
        assert client.last_call("send_message") is None

    async def test_kwargs_snapshot_isolated_from_caller(self):
        client = FakeTelegramClient()
        kwargs = {"chat_id": 1, "text": "a"}
        await client.send_message(**kwargs)
        kwargs["text"] = "mutated"

        last = client.last_call("send_message")
        assert last is not None
        assert last.kwargs["text"] == "a"

    async def test_records_full_kwargs_for_diverse_methods(self):
        client = FakeTelegramClient()
        await client.edit_forum_topic(
            chat_id=1, message_thread_id=7, name="x", icon_custom_emoji_id=None
        )

        edit = client.last_call("edit_forum_topic")
        assert edit is not None
        assert edit.kwargs == {
            "chat_id": 1,
            "message_thread_id": 7,
            "name": "x",
            "icon_custom_emoji_id": None,
        }


class TestSetSideEffect:
    async def test_returns_values_in_order(self):
        client = FakeTelegramClient()
        msg_a = MagicMock(spec=Message)
        msg_b = MagicMock(spec=Message)
        client.set_side_effect("send_message", [msg_a, msg_b])

        first = await client.send_message(chat_id=1, text="a")
        second = await client.send_message(chat_id=1, text="b")

        assert first is msg_a
        assert second is msg_b

    async def test_raises_exceptions_in_sequence(self):
        client = FakeTelegramClient()
        sent = MagicMock(spec=Message)
        client.set_side_effect("send_message", [TelegramError("boom"), sent])

        with pytest.raises(TelegramError):
            await client.send_message(chat_id=1, text="a")

        result = await client.send_message(chat_id=1, text="b")
        assert result is sent
