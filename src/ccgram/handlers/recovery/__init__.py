"""Recovery subpackage — dead window recovery, resume, restore, history.

Bundles the modules that surface "browse past sessions" and recover from
dead windows: ``recovery_callbacks`` (callback dispatcher + shared
validators), ``recovery_banner`` (dead-window banner UX flow),
``resume_picker`` (resume-picker UX flow + transcript scan),
``restore_command`` (/restore re-renders the banner on demand),
``resume_command`` (/resume scans past Claude sessions and resumes one),
``transcript_discovery`` (hookless transcript discovery for
Codex/Gemini/Pi and provider auto-detection), ``history`` (paginated
message history send/edit), and ``history_callbacks`` (page-navigation
callback handler).

Public surface re-exported here is the entry point for ``bot.py`` and the
rest of ``handlers/``; internals stay in the per-module files.
"""

from .history import history_command, send_history
from .history_callbacks import handle_history_callback

__all__ = [
    "handle_history_callback",
    "history_command",
    "send_history",
]
