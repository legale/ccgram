"""Status subpackage — topic status diff and topic emoji normalization."""

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
    "clear_topic_emoji_state",
    "format_topic_name_for_mode",
    "reset_all_state",
    "strip_emoji_prefix",
    "sync_topic_name",
    "update_stored_topic_name",
    "update_topic_emoji",
]
