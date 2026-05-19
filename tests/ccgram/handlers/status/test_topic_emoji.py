from unittest.mock import AsyncMock
from collections.abc import Generator

import pytest

from ccgram.handlers.status.topic_emoji import (
    clear_topic_emoji_state,
    format_topic_name_for_mode,
    reset_all_state,
    strip_emoji_prefix,
    sync_topic_name,
    update_stored_topic_name,
    update_topic_emoji,
)


@pytest.fixture(autouse=True)
def _reset() -> Generator[None]:
    reset_all_state()
    yield
    reset_all_state()


class TestStripEmojiPrefix:
    @pytest.mark.parametrize(
        "emoji",
        [
            "\U0001f7e2",
            "\U0001f7e1",
            "\u2705",
            "\U0001f4a5",
            "\U0001f4e1",
            "\U0001f3b2",
        ],
    )
    def test_strips_legacy_emoji(self, emoji: str) -> None:
        assert strip_emoji_prefix(f"{emoji} myproject") == "myproject"

    def test_no_prefix(self) -> None:
        assert strip_emoji_prefix("myproject") == "myproject"


class TestTopicNameHelpers:
    def test_format_topic_name_returns_plain_text(self) -> None:
        assert format_topic_name_for_mode("\U0001f7e2 myproject", "yolo") == "myproject"

    @pytest.mark.asyncio
    async def test_sync_topic_name_is_cache_only(self) -> None:
        bot = AsyncMock()
        await sync_topic_name(bot, -100, 42, "\U0001f7e1 myproject")
        bot.edit_forum_topic.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_topic_emoji_is_noop(self) -> None:
        bot = AsyncMock()
        await update_topic_emoji(bot, -100, 42, "active", "myproject")
        bot.edit_forum_topic.assert_not_called()

    def test_update_stored_topic_name_tracks_clean_name(self) -> None:
        update_stored_topic_name(-100, 42, "new-name")
        assert format_topic_name_for_mode("new-name", "normal") == "new-name"

    def test_clear_topic_emoji_state_removes_cache(self) -> None:
        update_stored_topic_name(-100, 42, "old-name")
        clear_topic_emoji_state(-100, 42)
        assert format_topic_name_for_mode("new-name", "normal") == "new-name"
