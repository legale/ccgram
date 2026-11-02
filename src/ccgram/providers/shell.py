"""Shell provider infrastructure re-exports."""

from ccgram.providers.shell_infra import (
    KNOWN_SHELLS,
    PromptMatch,
    detect_pane_shell,
    get_shell_name,
    has_prompt_marker,
    match_prompt,
    setup_shell_prompt,
)

__all__ = [
    "KNOWN_SHELLS",
    "PromptMatch",
    "detect_pane_shell",
    "get_shell_name",
    "has_prompt_marker",
    "match_prompt",
    "setup_shell_prompt",
]
