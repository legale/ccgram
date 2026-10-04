"""CLI `ccgram status` — show tmux-authoritative managed sessions."""

import os
import subprocess
import sys

_TMUX_FORMAT_PARTS = 3
_TOPIC_OPTION = "@ccgram_topic"


def _list_managed_sessions(prefix: str) -> list[dict[str, str]]:
    """Return live prefixed tmux sessions and their Telegram identity."""
    try:
        result = subprocess.run(
            [
                "tmux",
                "list-sessions",
                "-F",
                f"#{{session_name}}\t#{{session_path}}\t#{{{_TOPIC_OPTION}}}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):  # fmt: skip
        return []
    if result.returncode != 0:
        return []

    sessions: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != _TMUX_FORMAT_PARTS or not parts[0].startswith(prefix):
            continue
        sessions.append({"name": parts[0], "cwd": parts[1], "topic": parts[2]})
    return sessions


def _capability_summary() -> tuple[str, str]:
    """Return (provider_name, comma-separated capability flags)."""
    return "shell", "none"


def status_main() -> None:
    """Entry point for `ccgram status`."""
    # Lazy: version metadata is needed only for the CLI status command.
    from . import __version__

    provider_name, cap_flags = _capability_summary()
    prefix = os.getenv("TMUX_SESSION_PREFIX", "cc_")
    sessions = _list_managed_sessions(prefix)

    print(f"ccgram {__version__}")
    print(f"Provider: {provider_name} ({cap_flags})")
    print(f"Managed tmux sessions: {len(sessions)}")
    if sessions:
        print()
    for session in sessions:
        topic = session["topic"] or "unbound"
        cwd = session["cwd"]
        suffix = f" {cwd}" if cwd else ""
        print(f"  {session['name']} -> {topic}{suffix}")

    sys.exit(0)
