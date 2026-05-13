"""Topic name tracking for forum-topic bindings.

Topic names are stored as clean text. Status transitions no longer rename
Telegram topics. The module keeps the existing name-cache helpers so old
emoji-prefixed titles can still be normalized when state is loaded.
"""

import structlog

from ...telegram_client import TelegramClient
from ...topic_state_registry import topic_state

logger = structlog.get_logger()

EMOJI_GREEN_CIRCLE = "\U0001f7e2"
EMOJI_YELLOW_CIRCLE = "\U0001f7e1"
EMOJI_DONE = "\u2705"
EMOJI_DEAD = "\U0001f4a5"
EMOJI_YOLO = "\U0001f3b2"
EMOJI_RC = "\U0001f4e1"
EMOJI_ACTIVE = EMOJI_GREEN_CIRCLE
EMOJI_IDLE = EMOJI_YELLOW_CIRCLE

# Topic display names: (chat_id, thread_id) -> clean name (without emoji prefix).
# Updated when the incoming display name changes so that tmux window names and
# Telegram topic names stay aligned.
_topic_names: dict[tuple[int, int], str] = {}


def _resolve_topic_name(key: tuple[int, int], display_name: str) -> tuple[str, bool]:
    """Return the clean topic name and whether it changed.

    On first call, strips emoji and stores the clean name. On subsequent calls,
    if the incoming display_name (stripped) differs from the stored name,
    overwrites the cache so bindings keep using the clean text.
    """
    clean = strip_emoji_prefix(display_name)
    cached = _topic_names.get(key)
    if cached is None:
        _topic_names[key] = clean
        return clean, True
    if cached != clean:
        _topic_names[key] = clean
        return clean, True
    return cached, False


def format_topic_name_for_mode(display_name: str, approval_mode: str) -> str:
    """Return the clean topic name.

    Approval mode no longer changes Telegram topic titles.
    """
    _ = approval_mode
    return strip_emoji_prefix(display_name)


async def sync_topic_name(
    _client: TelegramClient,
    chat_id: int,
    thread_id: int,
    display_name: str,
) -> None:
    """Record the current clean topic name.

    Telegram titles are no longer edited.
    """
    key = (chat_id, thread_id)
    clean_name, _ = _resolve_topic_name(key, display_name)
    logger.debug(
        "Synced topic name cache: chat=%d thread=%d name='%s'",
        chat_id,
        thread_id,
        clean_name,
    )


async def update_topic_emoji(
    client: TelegramClient,
    chat_id: int,
    thread_id: int,
    state: str,
    display_name: str,
) -> None:
    """Ignore topic state updates.

    The name cache still tracks the clean topic name, but no Telegram rename
    is performed.
    """
    _ = client, state
    key = (chat_id, thread_id)
    clean_name, _ = _resolve_topic_name(key, display_name)
    logger.debug(
        "Ignored topic state update: chat=%d thread=%d state=%s name='%s'",
        chat_id,
        thread_id,
        state,
        clean_name,
    )


def strip_emoji_prefix(name: str) -> str:
    """Remove known emoji prefix from a topic name."""
    for emoji in (
        EMOJI_ACTIVE,
        EMOJI_IDLE,
        EMOJI_DONE,
        EMOJI_DEAD,
        "⚫",
        "❌",
        EMOJI_YOLO,
        EMOJI_RC,
    ):
        prefix = f"{emoji} "
        if name.startswith(prefix):
            name = name[len(prefix) :]
    return name


def update_stored_topic_name(chat_id: int, thread_id: int, new_clean_name: str) -> None:
    """Overwrite the stored clean name for a topic.

    Called from FORUM_TOPIC_EDITED handler.
    """
    _topic_names[(chat_id, thread_id)] = new_clean_name


@topic_state.register("chat")
def clear_topic_emoji_state(chat_id: int, thread_id: int) -> None:
    """Clear topic name tracking for a topic (called on topic cleanup)."""
    _topic_names.pop((chat_id, thread_id), None)


def clear_disabled_chat(chat_id: int, _thread_id: int = 0) -> None:
    """Compatibility stub for removed topic-rename permission tracking."""
    _ = chat_id


def reset_all_state() -> None:
    """Reset all tracking state (for testing)."""
    _topic_names.clear()
