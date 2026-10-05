"""Editable terminal-screen delta message for noisy topic status changes."""

from __future__ import annotations

import re
import time
import asyncio
from dataclasses import dataclass, field
from difflib import SequenceMatcher
import structlog

from telegram.error import RetryAfter, TelegramError

from ...config import config
from ...telegram_client import TelegramClient
from ...telegram_sender import TELEGRAM_MAX_MESSAGE_LENGTH
from ...topic_tail import is_last
from ..messaging_pipeline.message_sender import (
    edit_with_fallback,
    rate_limit_send_message,
)

_BODY_LIMIT = TELEGRAM_MAX_MESSAGE_LENGTH - 256
_RE_ANSI = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]|"
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|"
    r"\x1b[@-_]",
)
_RE_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
logger = structlog.get_logger()


@dataclass
class _DiffState:
    window_id: str
    prev_lines: list[str] = field(default_factory=list)
    message_id: int = 0
    last_edit_ts: float = 0.0
    last_msg_ts: int = 0
    diff_ts: int = 0
    edit_task: asyncio.Task[bool] | None = None
    title: str | None = None


_diff_states: dict[tuple[int, int, str], _DiffState] = {}


def _normalize_screen_text(pane_text: str) -> list[str]:
    text = _RE_ANSI.sub("", pane_text)
    text = _RE_CONTROL.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    return text.splitlines()


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


def _format_delta(
    window_id: str,
    old: list[str],
    new: list[str],
    title: str | None = None,
) -> str:
    out: list[str] = []
    matcher = SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, _old_start, _old_end, new_start, new_end in matcher.get_opcodes():
        if tag in ("insert", "replace"):
            out.extend(new[new_start:new_end])
    if not out:
        out = ["screen changed"]
    body = "\n".join(_cap_lines(out, _BODY_LIMIT))
    header = title or f"Screen delta {window_id} {time.strftime('%H:%M:%S')}"
    return f"{header}\n```\n{body}\n```"


def _get_state(
    chat_id: int, thread_id: int, window_id: str, target: str = "main"
) -> _DiffState:
    key: tuple[int, int] | tuple[int, int, str] = (
        (chat_id, thread_id) if target == "main" else (chat_id, thread_id, target)
    )
    state = _diff_states.get(key)
    if state is None or state.window_id != window_id:
        state = _DiffState(window_id=window_id)
        _diff_states[key] = state
    return state


def start_sidecar_diff(
    chat_id: int,
    thread_id: int,
    window_id: str,
    title: str,
    baseline_text: str | None = None,
) -> _DiffState:
    """Start tracking diff for a sidecar command in a new Telegram message."""
    state = _get_state(chat_id, thread_id, window_id, target="sidecar")
    state.message_id = 0
    state.title = title
    state.prev_lines = _normalize_screen_text(baseline_text) if baseline_text else []
    state.last_edit_ts = 0.0
    state.last_msg_ts = time.time_ns()
    state.diff_ts = 0
    return state


def mark_topic_status_activity(
    chat_id: int, thread_id: int, window_id: str, message_id: int
) -> None:
    """Record a Telegram message before the rest of its handler awaits."""
    if not isinstance(message_id, int) or not message_id:
        return
    now = time.time_ns()
    for key, state in _diff_states.items():
        if key[0] == chat_id and key[1] == thread_id:
            state.last_msg_ts = max(now, state.last_msg_ts + 1)
            if state.edit_task is not None and not state.edit_task.done():
                state.edit_task.cancel()
    logger.info(
        "topic_screen_diff_message_activity",
        chat_id=chat_id,
        thread_id=thread_id,
        window_id=window_id,
        last_msg_ts=now,
    )


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
    state.diff_ts = state.last_msg_ts
    return True


async def _edit_or_send(
    client: TelegramClient,
    chat_id: int,
    thread_id: int,
    state: _DiffState,
    text: str,
) -> bool:
    diff_ts = state.last_msg_ts
    action = "edit"
    if state.message_id <= 0 or state.diff_ts < diff_ts:
        action = "send"
        state.message_id = 0
    elif not is_last(chat_id, thread_id, state.message_id):
        action = "send"
    logger.info(
        "topic_screen_diff_decision",
        chat_id=chat_id,
        thread_id=thread_id,
        window_id=state.window_id,
        diff_ts=state.diff_ts,
        last_msg_ts=diff_ts,
        action=action,
        message_id=state.message_id,
    )
    if action == "send":
        return await _send_new(client, chat_id, thread_id, state, text)

    try:
        state.edit_task = asyncio.create_task(
            edit_with_fallback(client, chat_id, state.message_id, text)
        )
        ok = await state.edit_task
    except asyncio.CancelledError:
        logger.info(
            "topic_screen_diff_edit_cancelled",
            chat_id=chat_id,
            thread_id=thread_id,
            window_id=state.window_id,
            diff_ts=diff_ts,
            last_msg_ts=state.last_msg_ts,
        )
        return False
    except RetryAfter:
        raise
    except TelegramError:
        ok = False
    finally:
        state.edit_task = None
    if ok:
        if state.last_msg_ts != diff_ts:
            return await _send_new(client, chat_id, thread_id, state, text)
        state.diff_ts = diff_ts
        return True
    return await _send_new(client, chat_id, thread_id, state, text)


async def update_topic_status_diff(
    client: TelegramClient,
    chat_id: int,
    thread_id: int,
    window_id: str,
    pane_text: str,
    *,
    target: str = "main",
    active: bool = True,
) -> None:
    if not config.topic_status_diff_enabled or not pane_text or not active:
        return

    state = _get_state(chat_id, thread_id, window_id, target=target)
    current = _normalize_screen_text(pane_text)
    now = time.monotonic()

    if not state.prev_lines:
        state.prev_lines = current
        state.last_edit_ts = now
        return

    if current == state.prev_lines:
        return

    if now - state.last_edit_ts < config.topic_status_diff_interval:
        return

    text = _format_delta(window_id, state.prev_lines, current, title=state.title)
    if await _edit_or_send(client, chat_id, thread_id, state, text):
        state.prev_lines = current
        state.last_edit_ts = time.monotonic()


def clear_topic_status_diff_state(_user_id: int, thread_id: int) -> None:
    for key in list(_diff_states):
        if key[1] == thread_id:
            _diff_states.pop(key, None)


def reset_topic_status_diff_state() -> None:
    _diff_states.clear()


def prime_topic_status_diff(
    chat_id: int,
    thread_id: int,
    window_id: str,
    pane_text: str,
    *,
    target: str = "main",
) -> None:
    """Seed the screen baseline without sending the current terminal contents."""
    if not pane_text:
        return
    state = _get_state(chat_id, thread_id, window_id, target=target)
    state.prev_lines = _normalize_screen_text(pane_text)
    state.last_edit_ts = time.monotonic()
