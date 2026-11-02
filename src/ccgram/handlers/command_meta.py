"""Metadata attached directly to Telegram command handlers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

_COMMAND_INFO_ATTR = "__ccgram_command_info__"
HandlerT = TypeVar("HandlerT", bound=Callable[..., Any])


@dataclass(frozen=True, slots=True)
class CommandInfo:
    """User-facing command metadata owned by one handler function."""

    name: str
    description: str
    aliases: tuple[str, ...] = ()
    params: str = ""
    menu_name: str | None = None


def command(
    name: str,
    description: str,
    *,
    aliases: tuple[str, ...] = (),
    params: str = "",
    menu_name: str | None = None,
) -> Callable[[HandlerT], HandlerT]:
    """Attach Telegram command metadata to a handler."""
    info = CommandInfo(
        name=name,
        description=description,
        aliases=aliases,
        params=params,
        menu_name=menu_name,
    )

    def decorate(handler: HandlerT) -> HandlerT:
        setattr(handler, _COMMAND_INFO_ATTR, info)
        return handler

    return decorate


def get_command_info(handler: Callable[..., Any]) -> CommandInfo:
    """Return metadata attached to a command handler."""
    info = getattr(handler, _COMMAND_INFO_ATTR, None)
    if not isinstance(info, CommandInfo):
        raise TypeError(f"Handler {handler!r} has no command metadata")
    return info
