import ast
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccgram.expandable_quote import EXPANDABLE_QUOTE_END, EXPANDABLE_QUOTE_START
from ccgram.handlers.messaging_pipeline.message_task import (
    StatusClearTask,
    StatusUpdateTask,
)
from ccgram.handlers.status.status_bubble import (
    _status_drafts,
    _status_msg_info,
    clear_status_message,
    clear_status_msg_info,
    convert_status_to_content,
    format_pane_block,
    process_status_clear,
    process_status_update,
    send_status_text,
)
from ccgram.telegram_draft import mark_draft_unavailable, reset_draft_state
from ccgram.window_state_store import PaneInfo, WindowState, window_store

USER_ID = 1
THREAD_ID = 10
WINDOW_ID = "@0"
CHAT_ID = 42


@pytest.fixture(autouse=True)
def _clear_status_tracking():
    _status_msg_info.clear()
    _status_drafts.clear()
    reset_draft_state()
    # All tests run with draft streaming disabled (legacy edit path) so we
    # observe a deterministic bot.send_message / bot.edit_message_text call
    # pattern.  Streaming-mode behaviour is covered by test_telegram_draft.py.
    mark_draft_unavailable("test")
    yield
    _status_msg_info.clear()
    _status_drafts.clear()
    reset_draft_state()


def _make_bot(send_id: int = 99) -> AsyncMock:
    """Build an AsyncMock bot that returns a sensible Message on send."""
    bot = AsyncMock()
    sent = MagicMock()
    sent.message_id = send_id
    bot.send_message.return_value = sent
    return bot


class TestClearStatusMessage:
    async def test_deletes_tracked_message(self):
        # No DraftStream tracked → falls back to bot.delete_message.
        _status_msg_info[(USER_ID, THREAD_ID)] = (50, WINDOW_ID, "text", CHAT_ID)

        bot = AsyncMock()
        await clear_status_message(bot, USER_ID, THREAD_ID)

        bot.delete_message.assert_called_once_with(chat_id=CHAT_ID, message_id=50)
        assert (USER_ID, THREAD_ID) not in _status_msg_info

    async def test_aborts_active_draft_stream(self):
        # When a DraftStream is tracked, abort() is what cleans up the message.
        _status_msg_info[(USER_ID, THREAD_ID)] = (50, WINDOW_ID, "text", CHAT_ID)
        stream = MagicMock()
        stream.closed = False
        stream.abort = AsyncMock()
        _status_drafts[(USER_ID, THREAD_ID)] = stream

        bot = AsyncMock()
        await clear_status_message(bot, USER_ID, THREAD_ID)

        stream.abort.assert_awaited_once()
        # bot.delete_message must NOT be called when abort handles cleanup.
        bot.delete_message.assert_not_called()
        assert (USER_ID, THREAD_ID) not in _status_msg_info
        assert (USER_ID, THREAD_ID) not in _status_drafts

    async def test_noop_when_no_tracking(self):
        bot = AsyncMock()
        await clear_status_message(bot, USER_ID, THREAD_ID)

        bot.delete_message.assert_not_called()


class TestConvertStatusToContent:
    @patch(
        "ccgram.handlers.status.status_bubble.edit_with_fallback",
        new_callable=AsyncMock,
    )
    async def test_converts_status_to_content(self, mock_edit):
        _status_msg_info[(USER_ID, THREAD_ID)] = (50, WINDOW_ID, "old", CHAT_ID)
        mock_edit.return_value = True

        bot = AsyncMock()
        result = await convert_status_to_content(
            bot, USER_ID, THREAD_ID, WINDOW_ID, "content text"
        )

        assert result == 50
        mock_edit.assert_called_once()
        assert (USER_ID, THREAD_ID) not in _status_msg_info

    async def test_finalizes_draft_stream_on_convert(self):
        _status_msg_info[(USER_ID, THREAD_ID)] = (50, WINDOW_ID, "old", CHAT_ID)
        stream = MagicMock()
        stream.closed = False
        stream.finalize = AsyncMock()
        _status_drafts[(USER_ID, THREAD_ID)] = stream

        bot = AsyncMock()
        result = await convert_status_to_content(
            bot, USER_ID, THREAD_ID, WINDOW_ID, "content text"
        )

        assert result == 50
        stream.finalize.assert_awaited_once_with("content text", reply_markup=None)
        assert (USER_ID, THREAD_ID) not in _status_msg_info
        assert (USER_ID, THREAD_ID) not in _status_drafts

    async def test_returns_none_when_no_status(self):
        bot = AsyncMock()
        result = await convert_status_to_content(
            bot, USER_ID, THREAD_ID, WINDOW_ID, "content"
        )

        assert result is None

    @patch(
        "ccgram.handlers.status.status_bubble.edit_with_fallback",
        new_callable=AsyncMock,
    )
    async def test_deletes_status_from_different_window(self, mock_edit):
        _status_msg_info[(USER_ID, THREAD_ID)] = (50, "@1", "old", CHAT_ID)

        bot = AsyncMock()
        result = await convert_status_to_content(
            bot, USER_ID, THREAD_ID, WINDOW_ID, "content"
        )

        assert result is None
        bot.delete_message.assert_called_once_with(chat_id=CHAT_ID, message_id=50)


class TestClearStatusMsgInfo:
    def test_clears_specific_thread(self):
        _status_msg_info[(USER_ID, THREAD_ID)] = (50, WINDOW_ID, "text", CHAT_ID)
        _status_msg_info[(USER_ID, 20)] = (51, WINDOW_ID, "text", CHAT_ID)

        clear_status_msg_info(USER_ID, THREAD_ID)

        assert (USER_ID, THREAD_ID) not in _status_msg_info
        assert (USER_ID, 20) in _status_msg_info

    def test_noop_when_not_tracked(self):
        clear_status_msg_info(USER_ID, THREAD_ID)


