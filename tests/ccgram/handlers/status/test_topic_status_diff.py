from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ccgram.handlers.status import topic_status_diff


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.setattr(topic_status_diff.config, "topic_status_diff_enabled", True)
    monkeypatch.setattr(topic_status_diff.config, "topic_status_diff_interval", 10)
    topic_status_diff.reset_topic_status_diff_state()
    yield
    topic_status_diff.reset_topic_status_diff_state()


async def test_first_capture_sends_snapshot(monkeypatch) -> None:
    client = AsyncMock()
    sent = SimpleNamespace(message_id=10)
    send = AsyncMock(return_value=sent)
    monkeypatch.setattr(topic_status_diff, "rate_limit_send_message", send)
    monkeypatch.setattr(topic_status_diff, "edit_with_fallback", AsyncMock())

    await topic_status_diff.update_topic_status_diff(
        client, chat_id=1, thread_id=2, window_id="@7", pane_text="hello\n"
    )

    send.assert_awaited_once()


async def test_unchanged_capture_does_nothing(monkeypatch) -> None:
    client = AsyncMock()
    sent = SimpleNamespace(message_id=10)
    send = AsyncMock(return_value=sent)
    edit = AsyncMock(return_value=True)
    monkeypatch.setattr(topic_status_diff, "rate_limit_send_message", send)
    monkeypatch.setattr(topic_status_diff, "edit_with_fallback", edit)

    await topic_status_diff.update_topic_status_diff(
        client, chat_id=1, thread_id=2, window_id="@7", pane_text="hello\n"
    )
    send.reset_mock()
    edit.reset_mock()

    await topic_status_diff.update_topic_status_diff(
        client, chat_id=1, thread_id=2, window_id="@7", pane_text="hello\n"
    )

    send.assert_not_called()
    edit.assert_not_called()


async def test_changed_before_interval_does_not_send_or_edit(monkeypatch) -> None:
    client = AsyncMock()
    sent = SimpleNamespace(message_id=10)
    send = AsyncMock(return_value=sent)
    edit = AsyncMock(return_value=True)
    monkeypatch.setattr(topic_status_diff, "rate_limit_send_message", send)
    monkeypatch.setattr(topic_status_diff, "edit_with_fallback", edit)
    monkeypatch.setattr(topic_status_diff, "is_last", lambda *_args, **_kw: True)

    monotonic = SimpleNamespace(v=0.0)

    def _fake_monotonic() -> float:
        return float(monotonic.v)

    monkeypatch.setattr(topic_status_diff.time, "monotonic", _fake_monotonic)

    await topic_status_diff.update_topic_status_diff(
        client, chat_id=1, thread_id=2, window_id="@7", pane_text="a\n"
    )
    send.reset_mock()

    monotonic.v = 5.0
    await topic_status_diff.update_topic_status_diff(
        client, chat_id=1, thread_id=2, window_id="@7", pane_text="b\n"
    )

    send.assert_not_called()
    edit.assert_not_called()


async def test_changed_after_interval_edits_when_last(monkeypatch) -> None:
    client = AsyncMock()
    sent = SimpleNamespace(message_id=10)
    send = AsyncMock(return_value=sent)
    edit = AsyncMock(return_value=True)
    monkeypatch.setattr(topic_status_diff, "rate_limit_send_message", send)
    monkeypatch.setattr(topic_status_diff, "edit_with_fallback", edit)
    monkeypatch.setattr(topic_status_diff, "is_last", lambda *_args, **_kw: True)

    monotonic = SimpleNamespace(v=0.0)

    def _fake_monotonic() -> float:
        return float(monotonic.v)

    monkeypatch.setattr(topic_status_diff.time, "monotonic", _fake_monotonic)

    await topic_status_diff.update_topic_status_diff(
        client, chat_id=1, thread_id=2, window_id="@7", pane_text="a\n"
    )
    send.reset_mock()

    monotonic.v = 11.0
    await topic_status_diff.update_topic_status_diff(
        client, chat_id=1, thread_id=2, window_id="@7", pane_text="b\n"
    )

    edit.assert_awaited_once()
    send.assert_not_called()


async def test_changed_after_interval_sends_new_when_not_last(monkeypatch) -> None:
    client = AsyncMock()
    sent1 = SimpleNamespace(message_id=10)
    sent2 = SimpleNamespace(message_id=11)
    send = AsyncMock(side_effect=[sent1, sent2])
    edit = AsyncMock(return_value=True)
    monkeypatch.setattr(topic_status_diff, "rate_limit_send_message", send)
    monkeypatch.setattr(topic_status_diff, "edit_with_fallback", edit)
    monkeypatch.setattr(topic_status_diff, "is_last", lambda *_args, **_kw: False)

    monotonic = SimpleNamespace(v=0.0)

    def _fake_monotonic() -> float:
        return float(monotonic.v)

    monkeypatch.setattr(topic_status_diff.time, "monotonic", _fake_monotonic)

    await topic_status_diff.update_topic_status_diff(
        client, chat_id=1, thread_id=2, window_id="@7", pane_text="a\n"
    )

    monotonic.v = 11.0
    await topic_status_diff.update_topic_status_diff(
        client, chat_id=1, thread_id=2, window_id="@7", pane_text="b\n"
    )

    assert send.await_count == 2
    edit.assert_not_called()


async def test_edit_failure_sends_new(monkeypatch) -> None:
    client = AsyncMock()
    sent1 = SimpleNamespace(message_id=10)
    sent2 = SimpleNamespace(message_id=11)
    send = AsyncMock(side_effect=[sent1, sent2])
    edit = AsyncMock(return_value=False)
    monkeypatch.setattr(topic_status_diff, "rate_limit_send_message", send)
    monkeypatch.setattr(topic_status_diff, "edit_with_fallback", edit)
    monkeypatch.setattr(topic_status_diff, "is_last", lambda *_args, **_kw: True)

    monotonic = SimpleNamespace(v=0.0)

    def _fake_monotonic() -> float:
        return float(monotonic.v)

    monkeypatch.setattr(topic_status_diff.time, "monotonic", _fake_monotonic)

    await topic_status_diff.update_topic_status_diff(
        client, chat_id=1, thread_id=2, window_id="@7", pane_text="a\n"
    )

    monotonic.v = 11.0
    await topic_status_diff.update_topic_status_diff(
        client, chat_id=1, thread_id=2, window_id="@7", pane_text="b\n"
    )

    assert send.await_count == 2
    edit.assert_awaited_once()

