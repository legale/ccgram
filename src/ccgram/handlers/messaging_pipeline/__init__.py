"""Telegram message sending helpers."""

from .message_sender import (
    edit_with_fallback,
    is_thread_gone,
    rate_limit_send,
    rate_limit_send_message,
    safe_edit,
    safe_reply,
)

__all__ = [
    "edit_with_fallback",
    "is_thread_gone",
    "rate_limit_send",
    "rate_limit_send_message",
    "safe_edit",
    "safe_reply",
]
