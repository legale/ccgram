"""Editable terminal-screen diff message for noisy topic status changes."""

from __future__ import annotations

import difflib
import time
from dataclasses import dataclass

from telegram.error import RetryAfter, TelegramError

from ...config import config
from ...telegram_client import TelegramClient
from ...telegram_sender import TELEGRAM_MAX_MESSAGE_LENGTH
from ...topic_state_registry import topic_state
from ...topic_tail import is_last
from ..messaging_pipeline.message_sender import edit_with_fallback, rate_limit_send_message

_BODY_LIMIT = TELEGRAM_MAX_MESSAGE_LENGTH - 256
_SNAPSHOT_LINES = 30


@dataclass
class _DiffState:
    window_id: str
    prev_bytes: bytes = b""
    message_id: int = 0
    last_edit_ts: float = 0.0


_diff_states: dict[tuple[int, int], _DiffState] = {}


def _screen_bytes(pane_text: str) -> bytes:
    return pane_text.encode("utf-8", errors="replace")


def _decode_lines(data: bytes) -> list[str]:
    return data.decode("utf-8", errors="replace").splitlines()


def _cap_lines(lines: list[str], limit: int) -> list[str]:
    out: list[str] = []
    used = 0
    for line in lines:
        cost = len(line) + 1
        if used + cost > limit:
            out.append("... truncated ...")
            break
        out.append(line)
        used += cost
    return out


def _format_snapshot(window_id: str, data: bytes) -> str:
    lines = _decode_lines(data)
    if len(lines) > _SNAPSHOT_LINES:
        lines = ["... snapshot truncated ...", *lines[-_SNAPSHOT_LINES:]]
    body = "\n".join(_cap_lines(lines, _BODY_LIMIT))
    return f"Screen snapshot {window_id} {time.strftime('%H:%M:%S')}\n```\n{body}\n```"


def _format_diff(window_id: str, old: bytes, new: bytes) -> str:
    old_lines = _decode_lines(old)
    new_lines = _decode_lines(new)
    diff = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile="previous",
            tofile="current",
            lineterm="",
        )
    )
    if not diff:
        diff = ["screen changed"]
    body = "\n".join(_cap_lines(diff, _BODY_LIMIT))
    return f"Screen diff {window_id} {time.strftime('%H:%M:%S')}\n```diff\n{body}\n```"


def _get_state(chat_id: int, thread_id: int, window_id: str) -> _DiffState:
    key = (chat_id, thread_id)
    state = _diff_states.get(key)
    if state is None or state.window_id != window_id:
        state = _DiffState(window_id=window_id)
        _diff_states[key] = state
    return state


async def _send_new(
    client: TelegramClient,
    chat_id: int,
    thread_id: int,
    state: _DiffState,
    text: str,
) -> bool:
    sent = await rate_limit_send_message(
        client,
        chat_id,
        text,
        message_thread_id=thread_id,
    )
    message_id = getattr(sent, "message_id", None)
    if message_id is None:
        return False
    state.message_id = int(message_id)
    return True


async def _edit_or_send(
    client: TelegramClient,
    chat_id: int,
    thread_id: int,
    state: _DiffState,
    text: str,
) -> bool:
    if state.message_id <= 0 or not is_last(chat_id, thread_id, state.message_id):
        return await _send_new(client, chat_id, thread_id, state, text)

    try:
        ok = await edit_with_fallback(client, chat_id, state.message_id, text)
    except RetryAfter:
        raise
    except TelegramError:
        ok = False
    if ok:
        return True
    return await _send_new(client, chat_id, thread_id, state, text)


async def update_topic_status_diff(
    client: TelegramClient,
    chat_id: int,
    thread_id: int,
    window_id: str,
    pane_text: str,
) -> None:
    if not config.topic_status_diff_enabled or not pane_text:
        return

    state = _get_state(chat_id, thread_id, window_id)
    current = _screen_bytes(pane_text)
    now = time.monotonic()

    if not state.prev_bytes:
        if await _send_new(
            client, chat_id, thread_id, state, _format_snapshot(window_id, current)
        ):
            state.prev_bytes = current
            state.last_edit_ts = now
        return

    if current == state.prev_bytes:
        return

    if now - state.last_edit_ts < config.topic_status_diff_interval:
        state.prev_bytes = current
        return

    text = _format_diff(window_id, state.prev_bytes, current)
    if await _edit_or_send(client, chat_id, thread_id, state, text):
        state.prev_bytes = current
        state.last_edit_ts = time.monotonic()


@topic_state.register("topic")
def clear_topic_status_diff_state(_user_id: int, thread_id: int) -> None:
    for key in list(_diff_states):
        if key[1] == thread_id:
            _diff_states.pop(key, None)


def reset_topic_status_diff_state() -> None:
    _diff_states.clear()
