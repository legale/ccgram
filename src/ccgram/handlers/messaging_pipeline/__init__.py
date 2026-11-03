"""Messaging pipeline subpackage — safe-send helpers and message formatting.

Bundles safe-send helpers, rate-limited deliveries, message editing, reactions,
and Telegram streaming wrappers.
"""

from .message_sender import (
    ALLOWED_REACTIONS,
    REACT_DONE,
    REACT_FAIL,
    REACT_INBOX,
    REACT_RUNNING,
    REACT_SEEN,
    REACT_THINKING,
    StreamingMessage,
    ack_reaction,
    clear_reaction,
    edit_with_fallback,
    is_thread_gone,
    rate_limit_send,
    rate_limit_send_message,
    react,
    safe_edit,
    safe_reply,
    safe_send,
    send_kwargs,
)

__all__ = [
    "ALLOWED_REACTIONS",
    "REACT_DONE",
    "REACT_FAIL",
    "REACT_INBOX",
    "REACT_RUNNING",
    "REACT_SEEN",
    "REACT_THINKING",
    "StreamingMessage",
    "ack_reaction",
    "clear_reaction",
    "edit_with_fallback",
    "is_thread_gone",
    "rate_limit_send",
    "rate_limit_send_message",
    "react",
    "safe_edit",
    "safe_reply",
    "safe_send",
    "send_kwargs",
]
