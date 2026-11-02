from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ccgram.telegram_draft import (
    DRAFT_LEGACY,
    DraftStream,
    reset_draft_state,
)


@pytest.fixture(autouse=True)
def _reset_draft_state():
    reset_draft_state()
    yield
    reset_draft_state()


def _make_bot(*, send_id=42):
    bot = MagicMock()
    sent_msg = MagicMock(message_id=send_id)
    bot.send_message = AsyncMock(return_value=sent_msg)
    bot.edit_message_text = AsyncMock(return_value=None)
    bot.delete_message = AsyncMock(return_value=None)
    return bot


class TestDraftStreamHappyPath:
    async def test_start_sends_message(self) -> None:
        bot = _make_bot(send_id=7)
        stream = DraftStream(bot, chat_id=100, message_thread_id=5)

        mid = await stream.start("hello")

        assert mid == 7
        assert stream.mode == DRAFT_LEGACY
        bot.send_message.assert_awaited_once_with(
            chat_id=100,
            text="hello",
            message_thread_id=5,
        )

    async def test_append_then_finalize(self) -> None:
        bot = _make_bot(send_id=7)
        stream = DraftStream(bot, chat_id=100)

        await stream.start("a")
        await stream.append("b")
        await stream.append("c")
        await stream.finalize()

        assert bot.send_message.await_count == 1
        assert bot.edit_message_text.await_count == 2
        assert stream.closed is True

    async def test_finalize_with_replacement_text(self) -> None:
        bot = _make_bot(send_id=7)
        stream = DraftStream(bot, chat_id=100)

        await stream.start("draft")
        await stream.finalize("final")

        assert stream.text == "final"
        bot.edit_message_text.assert_awaited_with(
            chat_id=100,
            message_id=7,
            text="final",
            reply_markup=InlineKeyboardMarkup([]),
        )

    async def test_double_start_raises(self) -> None:
        bot = _make_bot()
        stream = DraftStream(bot, chat_id=100)
        await stream.start("hi")
        with pytest.raises(RuntimeError, match="called twice"):
            await stream.start("again")

    async def test_append_before_start_raises(self) -> None:
        bot = _make_bot()
        stream = DraftStream(bot, chat_id=100)
        with pytest.raises(RuntimeError, match="not started"):
            await stream.append("hi")

    async def test_empty_initial_text_returns_none(self) -> None:
        bot = _make_bot()
        stream = DraftStream(bot, chat_id=100)
        assert await stream.start("") is None
        bot.send_message.assert_not_awaited()


class TestDraftStreamAbort:
    async def test_abort_deletes_message(self) -> None:
        bot = _make_bot(send_id=7)
        stream = DraftStream(bot, chat_id=100)
        await stream.start("hi")
        await stream.abort()

        assert stream.closed is True
        bot.delete_message.assert_awaited_once_with(chat_id=100, message_id=7)

    async def test_abort_before_start_is_noop(self) -> None:
        bot = _make_bot()
        stream = DraftStream(bot, chat_id=100)
        await stream.abort()
        assert stream.closed is True
        bot.delete_message.assert_not_awaited()


class TestDraftStreamTextSafety:
    async def test_text_truncated_to_4096(self) -> None:
        bot = _make_bot(send_id=7)
        stream = DraftStream(bot, chat_id=100)

        await stream.start("a" * 5000)
        assert len(stream.text) <= 4096
        assert "... [truncated" in stream.text


class TestDraftStreamReplace:
    async def test_replace_pushes_full_text(self) -> None:
        bot = _make_bot(send_id=7)
        stream = DraftStream(bot, chat_id=100)

        await stream.start("first")
        await stream.replace("second")

        assert stream.text == "second"
        bot.edit_message_text.assert_awaited_with(
            chat_id=100,
            message_id=7,
            text="second",
            reply_markup=InlineKeyboardMarkup([]),
        )


class TestDraftStreamReplyMarkup:
    def _markup(self):
        return InlineKeyboardMarkup([[InlineKeyboardButton("btn", callback_data="cb")]])

    async def test_markup_passed_to_send_and_edit(self) -> None:
        bot = _make_bot(send_id=7)
        markup = self._markup()
        stream = DraftStream(bot, chat_id=100, reply_markup=markup)
        await stream.start("hi")

        bot.send_message.assert_awaited_once_with(
            chat_id=100,
            text="hi",
            reply_markup=markup,
        )

        await stream.replace("new text")
        bot.edit_message_text.assert_awaited_with(
            chat_id=100,
            message_id=7,
            text="new text",
            reply_markup=markup,
        )
