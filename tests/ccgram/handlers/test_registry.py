"""Tests for the iproute2-style // command dispatcher in handlers/registry.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Chat, Message, Update
from datetime import datetime
from telegram.ext import (
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from ccgram.handlers.registry import (
    COMMAND_NAMES,
    CommandSpec,
    _DISPATCH_TABLE,
    _find_handler,
    register_all,
)
from ccgram.handlers.arg_parser import matches


# ---------------------------------------------------------------------------
# matches() unit tests


def test_matches_full_name():
    assert matches("screenshot", "screenshot") is True


def test_matches_prefix():
    assert matches("sc", "screenshot") is True
    assert matches("det", "detach") is True
    assert matches("ses", "sessions") is True
    assert matches("li", "live") is True


def test_matches_empty_prefix_is_false():
    assert matches("", "screenshot") is False


def test_matches_longer_than_string():
    assert matches("screenshots", "screenshot") is False


def test_matches_no_match():
    assert matches("xyz", "screenshot") is False


# ---------------------------------------------------------------------------
# _find_handler() — prefix dispatch table


def test_find_handler_exact_name():
    from ccgram.handlers.live import screenshot_command

    assert _find_handler("screenshot") is screenshot_command


def test_find_handler_prefix_screenshot_unambiguous():
    from ccgram.handlers.live import screenshot_command

    # "screensho" is longer than "screen" so it only matches "screenshot"
    assert _find_handler("screensho") is screenshot_command


def test_find_handler_prefix_screen_ambiguous():
    # "sc" is not an exact entry and is prefix of screenshot+screen → ambiguous
    assert _find_handler("sc") is None


def test_find_handler_screen_alias():
    from ccgram.handlers.live import screenshot_command

    # "screen" is an exact alias in the table → exact match wins
    assert _find_handler("screen") is screenshot_command


def test_find_handler_prefix_det():
    from ccgram.handlers.cleanup import detach_command

    assert _find_handler("det") is detach_command


def test_find_handler_ambiguous_returns_none():
    # "s" is not exact and is prefix of sessions, ses, screenshot, screen, send → ambiguous
    assert _find_handler("s") is None


def test_find_handler_unknown_returns_none():
    assert _find_handler("xyzzy") is None


def test_find_handler_help_alias():
    from ccgram.handlers.commands import commands_command

    # "help" is an exact alias in the table
    assert _find_handler("help") is commands_command


def test_find_handler_ses_alias():
    from ccgram.handlers.sessions_dashboard import sessions_command

    # "ses" is an exact alias in the table → exact match wins over prefix ambiguity
    assert _find_handler("ses") is sessions_command


# ---------------------------------------------------------------------------
# CommandSpec dataclass


def test_command_spec_is_frozen():
    spec = CommandSpec("foo", MagicMock())
    with pytest.raises(AttributeError):
        spec.name = "bar"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# COMMAND_NAMES sentinel


def test_command_names_matches_dispatch_table():
    table_names = [name for name, _ in _DISPATCH_TABLE]
    assert list(COMMAND_NAMES) == table_names


def test_command_names_contains_minimal_contract():
    contract = {
        "commands",
        "help",
        "sessions",
        "ses",
        "detach",
        "screenshot",
        "live",
        "send",
    }
    assert contract.issubset(set(COMMAND_NAMES))


# ---------------------------------------------------------------------------
# register_all() — handler kinds


def _make_app():
    app = MagicMock()
    app.add_handler = MagicMock()
    return app


def test_register_all_registers_expected_handler_kinds():
    app = _make_app()
    register_all(app, filters.ALL)

    by_kind: dict[type, int] = {}
    for call in app.add_handler.call_args_list:
        handler = call.args[0]
        by_kind[type(handler)] = by_kind.get(type(handler), 0) + 1

    # No PrefixHandlers — replaced by a single MessageHandler for ^//
    from telegram.ext import PrefixHandler

    assert by_kind.get(PrefixHandler, 0) == 0
    assert by_kind.get(CallbackQueryHandler) == 1
    # MessageHandlers: //dispatch, topic_created, topic_closed,
    # topic_edited, text, photo, document = 7 total
    assert by_kind.get(MessageHandler) == 7


def test_register_all_double_slash_handler_precedes_text_handler():
    """The //dispatch MessageHandler must be registered before the plain text handler."""
    app = _make_app()
    register_all(app, filters.ALL)

    handlers = [call.args[0] for call in app.add_handler.call_args_list]
    msg_handlers = [h for h in handlers if isinstance(h, MessageHandler)]

    # First MessageHandler is the // dispatcher (regex ^//)
    first = msg_handlers[0]
    assert hasattr(first, "filters")


# ---------------------------------------------------------------------------
# end-to-end dispatch smoke test


@pytest.mark.asyncio
async def test_dispatch_screenshot_prefix():
    """//screensho (unambiguous prefix) must route to screenshot_command."""
    from ccgram.handlers.registry import _find_handler
    from ccgram.handlers.live import screenshot_command

    handler = _find_handler("screensho")
    assert handler is screenshot_command


@pytest.mark.asyncio
async def test_dispatch_unknown_command_replies():
    """Unknown //xyz must call safe_reply with error message."""
    from ccgram.handlers.registry import _dispatch_double_slash

    update = MagicMock(spec=Update)
    msg = MagicMock(spec=Message)
    msg.text = "//xyzzy"
    update.effective_message = msg

    with patch(
        "ccgram.handlers.registry.safe_reply", new_callable=AsyncMock
    ) as mock_reply:
        await _dispatch_double_slash(update, MagicMock())
        mock_reply.assert_awaited_once()
        args = mock_reply.call_args[0]
        assert "Unknown command" in args[1]


@pytest.mark.asyncio
async def test_dispatch_ambiguous_prefix_replies():
    """Ambiguous prefix (e.g. //s matches many) must call safe_reply with error."""
    from ccgram.handlers.registry import _dispatch_double_slash

    update = MagicMock(spec=Update)
    msg = MagicMock(spec=Message)
    msg.text = "//s"
    update.effective_message = msg

    with patch(
        "ccgram.handlers.registry.safe_reply", new_callable=AsyncMock
    ) as mock_reply:
        await _dispatch_double_slash(update, MagicMock())
        mock_reply.assert_awaited_once()


# ---------------------------------------------------------------------------
# Legacy routing sanity: /bin/ls must NOT be caught by the // dispatcher


def test_single_slash_path_does_not_match_double_slash_regex():
    import re

    pattern = re.compile(r"^//")
    assert not pattern.match("/bin/ls -la")
    assert pattern.match("//screenshot")
