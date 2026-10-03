"""Provider utilities for shell-to-tmux bridge."""

from __future__ import annotations

import os
from typing import Any

from ..handlers.polling.polling_types import StatusUpdate
from .shell_infra import KNOWN_SHELLS


def detect_provider_from_command(pane_current_command: str) -> str:
    """Detect provider name from a tmux pane's running process.

    Returns "shell" for recognized shell binaries, or empty string.
    """
    cmd = pane_current_command.strip().lower()
    if not cmd:
        return ""

    basename = os.path.basename(cmd.split()[0])
    if basename in KNOWN_SHELLS or basename.lstrip("-") in KNOWN_SHELLS:
        return "shell"

    return ""


async def detect_provider_from_pane(
    pane_current_command: str,
    *,
    pane_tty: str = "",  # noqa: ARG001
    window_id: str = "",  # noqa: ARG001
) -> str:
    """Detect provider from command name."""
    return detect_provider_from_command(pane_current_command)


def resolve_launch_command(
    provider_name: str = "shell",  # noqa: ARG001
    *,
    approval_mode: str = "normal",  # noqa: ARG001
) -> str:
    """Resolve launch command for shell."""
    return os.environ.get("CCGRAM_SHELL_COMMAND", "")


class _Caps:
    name: str = "shell"
    launch_command: str = ""
    transcript_format: str = "raw"
    supports_mailbox_delivery: bool = False
    supports_hook: bool = False
    has_yolo_confirmation: bool = False
    chat_first_command_path: bool = True
    has_yolo_flag: bool = False
    supports_commands_probe: bool = False
    supports_interactive_prompts: bool = False
    supports_task_tracking: bool = False
    interactive_ui_heuristic: str = "none"
    uses_pane_title: bool = False


class _DummyShellProvider:
    name: str = "shell"
    capabilities = _Caps()

    async def scrape_current_mode(self, *_args: Any, **_kwargs: Any) -> str | None:
        return None

    def parse_terminal_status(self, *_args: Any, **_kwargs: Any) -> StatusUpdate | None:
        return None


def get_provider_for_window(
    window_id: str = "",  # noqa: ARG001
    provider_name: str | None = None,  # noqa: ARG001
) -> Any:
    return _DummyShellProvider()


__all__ = [
    "KNOWN_SHELLS",
    "StatusUpdate",
    "detect_provider_from_command",
    "detect_provider_from_pane",
    "get_provider_for_window",
    "resolve_launch_command",
]
