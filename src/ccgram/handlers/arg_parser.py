"""Small iproute2-style command matcher."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from .command_meta import get_command_info

__all__ = ["matches", "match_command"]


def matches(prefix: str, string: str) -> bool:
    """Return True if *prefix* is a non-empty prefix of *string*."""
    return bool(prefix) and string.startswith(prefix)


Handler = Callable[..., Any]


def match_command(
    argv: list[str], handlers: Iterable[Handler]
) -> tuple[Handler | None, list[str], list[str]]:
    """Return ``(handler, remaining_args, candidates)`` for *argv*.

    Exact names and aliases win. Otherwise a prefix is accepted when it
    identifies one command. Aliases belong to their handler, so ``sc`` is
    one candidate for ``screenshot`` rather than two candidates.
    """
    if not argv:
        return None, [], []

    token = argv[0]
    command_entries = [
        (get_command_info(handler), handler) for handler in handlers
    ]

    for info, handler in command_entries:
        if token == info.name or token in info.aliases:
            return handler, argv[1:], []

    matched = [
        (info.name, handler)
        for info, handler in command_entries
        if any(matches(token, name) for name in (info.name, *info.aliases))
    ]
    if not matched:
        return None, argv, []

    if len({handler for _, handler in matched}) == 1:
        return matched[0][1], argv[1:], []

    return None, argv, list(dict.fromkeys(name for name, _ in matched))
