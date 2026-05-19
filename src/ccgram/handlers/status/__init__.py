"""Status subpackage — status bubble, status-bar callbacks, topic names.

Bundles the modules that own the per-topic status surface:
``status_bubble`` (status message lifecycle, keyboard layout, task-list
formatting, status-to-content conversion), ``status_bar_actions``
(inline-button callbacks for the status bubble — notify toggle, recall,
remote control, esc, quick keys), and ``topic_emoji`` (legacy forum topic
name normalization/cache).

Public surface re-exported here is the entry point for ``bot.py`` and
the rest of ``handlers/``; internals stay in the per-module files.
"""

from .status_bar_actions import build_dashboard_button
from .status_bubble import (
    build_status_keyboard,
    clear_status_message,
    clear_status_msg_info,
    convert_status_to_content,
    process_status_clear,
    process_status_update,
    register_rc_active_provider,
    send_status_text,
)
from .topic_emoji import (
    clear_topic_emoji_state,
    format_topic_name_for_mode,
    reset_all_state,
    strip_emoji_prefix,
    sync_topic_name,
    update_stored_topic_name,
    update_topic_emoji,
)

__all__ = [
    "build_dashboard_button",
    "build_status_keyboard",
    "clear_status_message",
    "clear_status_msg_info",
    "clear_topic_emoji_state",
    "convert_status_to_content",
    "format_topic_name_for_mode",
    "process_status_clear",
    "process_status_update",
    "register_rc_active_provider",
    "reset_all_state",
    "send_status_text",
    "strip_emoji_prefix",
    "sync_topic_name",
    "update_stored_topic_name",
    "update_topic_emoji",
]
