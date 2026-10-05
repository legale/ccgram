"""Shared line-oriented rolling display buffers."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher

DEFAULT_MAX = 3800

_RE_ANSI = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]|"
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|"
    r"\x1b[@-_]",
)
_RE_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_screen_text(text: str) -> list[str]:
    text = _RE_ANSI.sub("", text)
    text = _RE_CONTROL.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    return text.splitlines()


def screen_diff(old: list[str], new: list[str]) -> list[str]:
    result: list[str] = []
    matcher = SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, _old_start, _old_end, new_start, new_end in matcher.get_opcodes():
        if tag in ("insert", "replace"):
            result.extend(new[new_start:new_end])
    return result


@dataclass
class DisplayBuffer:
    max_chars: int = DEFAULT_MAX
    previous_screen: list[str] = field(default_factory=list)
    lines: deque[str] = field(default_factory=deque)

    def update(self, screen_text: str) -> bool:
        current = normalize_screen_text(screen_text)
        if not self.previous_screen:
            self.previous_screen = current
            return False
        if current == self.previous_screen:
            return False

        diff = screen_diff(self.previous_screen, current)
        self.previous_screen = current
        if not diff:
            return False
        self.extend(diff)
        return True

    def extend(self, lines: list[str]) -> None:
        self.lines.extend(lines)
        if self.size <= self.max_chars:
            return
        while self.lines and self.size > self.max_chars:
            self.lines.popleft()

    @property
    def size(self) -> int:
        return sum(len(line) + 1 for line in self.lines)

    def text(self) -> str:
        return "\n".join(self.lines)

    def prime(self, screen_text: str) -> None:
        self.lines.clear()
        self.previous_screen = normalize_screen_text(screen_text)


_buffers: dict[tuple[int, int, str, str], DisplayBuffer] = {}


def get_display_buffer(
    chat_id: int,
    thread_id: int,
    window_id: str,
    target: str = "main",
    *,
    max_chars: int = DEFAULT_MAX,
) -> DisplayBuffer:
    key = (chat_id, thread_id, window_id, target)
    buffer = _buffers.get(key)
    if buffer is None:
        buffer = DisplayBuffer(max_chars=max_chars)
        _buffers[key] = buffer
    return buffer


def reset_display_buffers() -> None:
    _buffers.clear()


def clear_display_buffers(thread_id: int) -> None:
    for key in list(_buffers):
        if key[1] == thread_id:
            del _buffers[key]
