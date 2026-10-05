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


async def test_first_capture_renders_current_screen(monkeypatch) -> None:
    client = AsyncMock()
    send = AsyncMock(return_value=SimpleNamespace(message_id=10))
    monkeypatch.setattr(topic_status_diff, "rate_limit_send_message", send)
    monkeypatch.setattr(topic_status_diff, "edit_with_fallback", AsyncMock())

    await topic_status_diff.update_topic_status_diff(
        client,
        chat_id=1,
        thread_id=2,
        window_id="@7",
        pane_text="hello\n",
        active=True,
    )

    send.assert_awaited_once()
    assert "hello" in topic_status_diff._diff_states[(1, 2)].last_display_text


async def test_first_capture_strips_ansi(monkeypatch) -> None:
    client = AsyncMock()
    send = AsyncMock(return_value=SimpleNamespace(message_id=10))
    monkeypatch.setattr(topic_status_diff, "rate_limit_send_message", send)
    monkeypatch.setattr(topic_status_diff, "edit_with_fallback", AsyncMock())

    await topic_status_diff.update_topic_status_diff(
        client,
        chat_id=1,
        thread_id=2,
        window_id="@7",
        pane_text="\x1b[31mhello\x1b[0m\n\x1b[1mfooter\x1b[0m\n",
        active=True,
    )

    send.assert_awaited_once()
    assert "hello\nfooter" in topic_status_diff._diff_states[(1, 2)].last_display_text


def test_screen_render_keeps_tail_on_complete_lines() -> None:
    text = "old\n" + ("new line\n" * 500)

    rendered = topic_status_diff._format_screen("cc_ls:@1756", text)

    assert rendered.endswith("new line\n```")
    assert not rendered.startswith("Screen delta cc_ls:@1756\n```\nold")


def test_screen_render_limits_body_to_1000_chars() -> None:
    text = "\n".join(f"line {i:04d}" for i in range(200))
    rendered = topic_status_diff._format_screen("@1", text)
    body = rendered.split("```\n")[1].rsplit("\n```", 1)[0]
    assert len(body) <= 1000


def test_screen_render_collapses_long_underscores() -> None:
    long_underscores = "_" * 80
    text = f"prefix\n{long_underscores}\nsuffix"
    rendered = topic_status_diff._format_screen("@1", text)
    assert "_" * 31 not in rendered
    assert "_" * 30 in rendered


async def test_unchanged_capture_does_nothing(monkeypatch) -> None:
    client = AsyncMock()
    sent = SimpleNamespace(message_id=10)
    send = AsyncMock(return_value=sent)
    edit = AsyncMock(return_value=True)
    monkeypatch.setattr(topic_status_diff, "rate_limit_send_message", send)
    monkeypatch.setattr(topic_status_diff, "edit_with_fallback", edit)

    await topic_status_diff.update_topic_status_diff(
        client,
        chat_id=1,
        thread_id=2,
        window_id="@7",
        pane_text="hello\n",
        active=True,
    )
    send.reset_mock()
    edit.reset_mock()

    await topic_status_diff.update_topic_status_diff(
        client,
        chat_id=1,
        thread_id=2,
        window_id="@7",
        pane_text="hello\n",
        active=True,
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
        client,
        chat_id=1,
        thread_id=2,
        window_id="@7",
        pane_text="a\n",
        active=True,
    )
    send.reset_mock()

    monotonic.v = 5.0
    await topic_status_diff.update_topic_status_diff(
        client,
        chat_id=1,
        thread_id=2,
        window_id="@7",
        pane_text="b\n",
        active=True,
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
        client,
        chat_id=1,
        thread_id=2,
        window_id="@7",
        pane_text="a\n",
        active=True,
    )
    send.reset_mock()

    monotonic.v = 11.0
    await topic_status_diff.update_topic_status_diff(
        client,
        chat_id=1,
        thread_id=2,
        window_id="@7",
        pane_text="b\n",
        active=True,
    )

    edit.assert_not_called()
    send.assert_awaited_once()


async def test_new_message_timestamp_forces_new_diff_message(monkeypatch) -> None:
    client = AsyncMock()
    sent1 = SimpleNamespace(message_id=10)
    sent2 = SimpleNamespace(message_id=11)
    send = AsyncMock(side_effect=[sent1, sent2])
    edit = AsyncMock(return_value=True)
    monkeypatch.setattr(topic_status_diff, "rate_limit_send_message", send)
    monkeypatch.setattr(topic_status_diff, "edit_with_fallback", edit)
    monkeypatch.setattr(topic_status_diff, "is_last", lambda *_args, **_kw: True)

    monotonic = SimpleNamespace(v=0.0)
    monkeypatch.setattr(
        topic_status_diff.time,
        "monotonic",
        lambda: float(monotonic.v),
    )

    await topic_status_diff.update_topic_status_diff(
        client, 1, 2, "@7", "a\n", active=True
    )
    topic_status_diff.mark_topic_status_activity(1, 2, "@7", 99)
    monotonic.v = 11.0
    await topic_status_diff.update_topic_status_diff(
        client, 1, 2, "@7", "b\n", active=True
    )

    assert send.await_count == 1
    edit.assert_not_called()


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
        client,
        chat_id=1,
        thread_id=2,
        window_id="@7",
        pane_text="a\n",
        active=True,
    )

    monotonic.v = 11.0
    await topic_status_diff.update_topic_status_diff(
        client,
        chat_id=1,
        thread_id=2,
        window_id="@7",
        pane_text="b\n",
        active=True,
    )

    assert send.await_count == 1
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
        client,
        chat_id=1,
        thread_id=2,
        window_id="@7",
        pane_text="a\n",
        active=True,
    )

    monotonic.v = 11.0
    await topic_status_diff.update_topic_status_diff(
        client,
        chat_id=1,
        thread_id=2,
        window_id="@7",
        pane_text="b\n",
        active=True,
    )

    assert send.await_count == 1
    edit.assert_not_called()


async def test_render_uses_current_canonical_screen(monkeypatch) -> None:
    client = AsyncMock()
    sent1 = SimpleNamespace(message_id=10)
    sent2 = SimpleNamespace(message_id=11)
    send = AsyncMock(side_effect=[sent1, sent2])
    edit = AsyncMock(return_value=True)
    monkeypatch.setattr(topic_status_diff, "rate_limit_send_message", send)
    monkeypatch.setattr(topic_status_diff, "edit_with_fallback", edit)
    monkeypatch.setattr(topic_status_diff, "is_last", lambda *_args, **_kw: True)

    monotonic = SimpleNamespace(v=0.0)

    def _fake_monotonic() -> float:
        return float(monotonic.v)

    monkeypatch.setattr(topic_status_diff.time, "monotonic", _fake_monotonic)

    await topic_status_diff.update_topic_status_diff(
        client,
        chat_id=1,
        thread_id=2,
        window_id="@7",
        pane_text="line1\nclock 12:00\nfooter a\n",
        active=True,
    )

    monotonic.v = 11.0
    await topic_status_diff.update_topic_status_diff(
        client,
        chat_id=1,
        thread_id=2,
        window_id="@7",
        pane_text="line1\nclock 12:01\nfooter b\n",
        active=True,
    )

    assert send.await_args is not None
    body = send.await_args.args[2]
    assert "line1" in body
    assert "clock 12:01" in body
    assert "footer b" in body
    assert "clock 12:00" not in body
    assert "footer a" not in body


async def test_sidecar_diff_uses_custom_title_and_sends_new_message(
    monkeypatch,
) -> None:
    client = AsyncMock()
    sent = SimpleNamespace(message_id=42)
    send = AsyncMock(return_value=sent)
    edit = AsyncMock(return_value=True)
    monkeypatch.setattr(topic_status_diff, "rate_limit_send_message", send)
    monkeypatch.setattr(topic_status_diff, "edit_with_fallback", edit)
    monkeypatch.setattr(topic_status_diff, "is_last", lambda *_args, **_kw: True)

    monotonic = SimpleNamespace(v=0.0)
    monkeypatch.setattr(
        topic_status_diff.time, "monotonic", lambda: float(monotonic.v)
    )

    topic_status_diff.start_sidecar_diff(
        chat_id=1,
        thread_id=2,
        window_id="@7",
        title="⚡ Sidecar: ls -alh (12:00:00)",
    )

    monotonic.v = 11.0
    await topic_status_diff.update_topic_status_diff(
        client,
        chat_id=1,
        thread_id=2,
        window_id="@7",
        pane_text="initial prompt $\nfile1.txt\nfile2.txt\n",
        target="sidecar",
        active=True,
    )

    send.assert_awaited_once()
    msg_text = send.await_args.args[2]
    assert "⚡ Sidecar: ls -alh (12:00:00)" in msg_text
    assert "file1.txt" in msg_text
    assert "file2.txt" in msg_text


async def test_sidecar_and_main_diff_states_are_independent(monkeypatch) -> None:
    client = AsyncMock()
    sent_main = SimpleNamespace(message_id=100)
    sent_sidecar = SimpleNamespace(message_id=200)
    send = AsyncMock(side_effect=[sent_main, sent_sidecar])
    edit = AsyncMock(return_value=True)
    monkeypatch.setattr(topic_status_diff, "rate_limit_send_message", send)
    monkeypatch.setattr(topic_status_diff, "edit_with_fallback", edit)
    monkeypatch.setattr(topic_status_diff, "is_last", lambda *_args, **_kw: True)

    monotonic = SimpleNamespace(v=0.0)
    monkeypatch.setattr(
        topic_status_diff.time, "monotonic", lambda: float(monotonic.v)
    )

    # 1. Main pane activity
    await topic_status_diff.update_topic_status_diff(
        client, 1, 2, "@7", "main initial\n", target="main", active=True
    )
    # 2. Sidecar command started
    topic_status_diff.start_sidecar_diff(
        chat_id=1,
        thread_id=2,
        window_id="@7",
        title="⚡ Sidecar: pwd (12:00:00)",
    )

    monotonic.v = 11.0
    # Update main
    await topic_status_diff.update_topic_status_diff(
        client, 1, 2, "@7", "main initial\nagent step 1\n", target="main", active=True
    )
    # Update sidecar
    await topic_status_diff.update_topic_status_diff(
        client,
        1,
        2,
        "@7",
        "shell prompt $\n/home/ruslan\n",
        target="sidecar",
        active=True,
    )

    assert send.await_count == 2
    main_call = send.await_args_list[0].args[2]
    sidecar_call = send.await_args_list[1].args[2]

    assert "agent step 1" in main_call
    assert "⚡ Sidecar: pwd" not in main_call

    assert "⚡ Sidecar: pwd (12:00:00)" in sidecar_call
    assert "/home/ruslan" in sidecar_call
    assert "agent step 1" not in sidecar_call


async def test_option_b_successive_sidecars_create_new_messages(monkeypatch) -> None:
    client = AsyncMock()
    sent1 = SimpleNamespace(message_id=301)
    sent2 = SimpleNamespace(message_id=302)
    send = AsyncMock(side_effect=[sent1, sent2])
    edit = AsyncMock(return_value=True)
    monkeypatch.setattr(topic_status_diff, "rate_limit_send_message", send)
    monkeypatch.setattr(topic_status_diff, "edit_with_fallback", edit)
    monkeypatch.setattr(topic_status_diff, "is_last", lambda *_args, **_kw: True)

    monotonic = SimpleNamespace(v=0.0)
    monkeypatch.setattr(
        topic_status_diff.time, "monotonic", lambda: float(monotonic.v)
    )

    # First sidecar command
    topic_status_diff.start_sidecar_diff(
        chat_id=1,
        thread_id=2,
        window_id="@7",
        title="⚡ Sidecar: cmd1 (12:00:00)",
    )
    monotonic.v = 11.0
    await topic_status_diff.update_topic_status_diff(
        client, 1, 2, "@7", "prompt $\noutput1\n", target="sidecar", active=True
    )

    # Second sidecar command (Option B: must create a new message)
    monotonic.v = 20.0
    topic_status_diff.start_sidecar_diff(
        chat_id=1,
        thread_id=2,
        window_id="@7",
        title="⚡ Sidecar: cmd2 (12:00:10)",
    )
    monotonic.v = 31.0
    await topic_status_diff.update_topic_status_diff(
        client,
        1,
        2,
        "@7",
        "prompt $\noutput1\nprompt $\noutput2\n",
        target="sidecar",
        active=True,
    )

    assert send.await_count == 2
    msg1_text = send.await_args_list[0].args[2]
    msg2_text = send.await_args_list[1].args[2]

    assert "⚡ Sidecar: cmd1" in msg1_text
    assert "output1" in msg1_text

    assert "⚡ Sidecar: cmd2" in msg2_text
    assert "output2" in msg2_text
