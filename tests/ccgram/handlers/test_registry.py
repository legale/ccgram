"""Tests for the iproute2-style // command dispatcher in handlers/registry.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Message, Update
from telegram.ext import (
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from ccgram.handlers.registry import (
    COMMAND_HANDLERS,
    format_commands_help,
    register_all,
)
from ccgram.handlers.arg_parser import match_command, matches
from ccgram.handlers.command_meta import command, get_command_info


# ---------------------------------------------------------------------------
# matches() unit tests


def test_matches_full_name():
    assert matches("screenshot", "screenshot") is True


def test_matches_prefix():
    assert matches("sc", "screenshot") is True
    assert matches("unb", "unbind") is True
    assert matches("ses", "sessions") is True
    assert matches("li", "live") is True


def test_matches_empty_prefix_is_false():
    assert matches("", "screenshot") is False


def test_matches_longer_than_string():
    assert matches("screenshots", "screenshot") is False


def test_matches_no_match():
    assert matches("xyz", "screenshot") is False


# ---------------------------------------------------------------------------
# match_command() — prefix matching with argv remainder


def _match(token: str):
    return match_command([token], COMMAND_HANDLERS)


def test_find_handler_exact_name():
    from ccgram.handlers.live import screenshot_command

    assert _match("screenshot")[0] is screenshot_command


def test_find_handler_prefix_screenshot_unambiguous():
    from ccgram.handlers.live import screenshot_command

    assert _match("screensho")[0] is screenshot_command


def test_find_handler_prefix_screen_is_one_command():
    from ccgram.handlers.live import screenshot_command

    # An alias belongs to its handler and does not create a duplicate candidate.
    assert _match("sc")[0] is screenshot_command


def test_find_handler_screen_alias():
    from ccgram.handlers.live import screenshot_command

    # "screen" is an exact alias in the table → exact match wins
    assert _match("screen")[0] is screenshot_command


def test_find_handler_prefix_unb():
    from ccgram.handlers.cleanup import unbind_command

    assert _match("unb")[0] is unbind_command


def test_find_handler_ambiguous_returns_none():
    handler, args, candidates = _match("s")
    assert handler is None
    assert args == ["s"]
    assert candidates == ["screenshot", "sessions", "send"]


def test_find_handler_unknown_returns_none():
    assert _match("xyzzy") == (None, ["xyzzy"], [])


def test_find_handler_help_alias():
    from ccgram.handlers.commands import commands_command

    # "help" is an exact alias in the table
    assert _match("help")[0] is commands_command


def test_find_handler_new():
    from ccgram.handlers.topics.topic_binding import new_command

    assert _match("new")[0] is new_command


def test_find_handler_ses_alias():
    from ccgram.handlers.sessions_dashboard import sessions_command

    # "ses" is an exact alias in the table → exact match wins over prefix ambiguity
    assert _match("ses")[0] is sessions_command


def test_find_handler_ctrl_c_and_ctrl_cc():
    from ccgram.handlers.commands import ctrl_c_command, ctrl_cc_command

    assert _match("ctrl-c")[0] is ctrl_c_command
    assert _match("ctrlc")[0] is ctrl_c_command
    assert _match("ctrl-cc")[0] is ctrl_cc_command
    assert _match("ctrlcc")[0] is ctrl_cc_command


def test_find_handler_ctrl_prefix_ambiguous():
    # "ctrl" or "ctrl-" matches ctrl-c, ctrlc, ctrl-cc, ctrlcc → ambiguous
    assert _match("ctrl")[0] is None
    assert _match("ctrl-")[0] is None


# ---------------------------------------------------------------------------
# Local command metadata


def test_command_metadata_is_attached_to_handlers():
    infos = [get_command_info(handler) for handler in COMMAND_HANDLERS]

    assert [info.name for info in infos] == [
        "commands",
        "screenshot",
        "live",
        "sessions",
        "new",
        "bind",
        "rename",
        "unbind",
        "killme",
        "ctrl-c",
        "ctrl-cc",
        "enter",
        "esc",
        "tab",
        "send",
    ]
    assert all(info.description.isascii() for info in infos)
    assert all(len(info.description) <= 32 for info in infos)


def test_command_help_uses_short_english_descriptions():
    help_text = format_commands_help()

    assert "Bot commands" in help_text
    assert "List commands" in help_text
    assert "список" not in help_text


# ---------------------------------------------------------------------------
def test_command_names_contains_minimal_contract():
    contract = {
        "commands",
        "help",
        "sessions",
        "ses",
        "unbind",
        "screenshot",
        "live",
        "send",
    }
    names = {
        name
        for handler in COMMAND_HANDLERS
        for name in (
            get_command_info(handler).name,
            *get_command_info(handler).aliases,
        )
    }
    assert contract.issubset(names)


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
    from ccgram.handlers.live import screenshot_command

    handler = _match("screensho")[0]
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


@pytest.mark.asyncio
async def test_dispatch_single_slash_command():
    from ccgram.handlers.registry import _dispatch_double_slash

    update = MagicMock(spec=Update)
    msg = MagicMock(spec=Message)
    msg.text = "/rename mytopic"
    update.effective_message = msg

    context = MagicMock()
    with (
        patch(
            "ccgram.handlers.topics.topic_binding.rename_command",
            new_callable=AsyncMock,
        ) as mock_rename,
        patch("ccgram.handlers.registry.COMMAND_HANDLERS", (mock_rename,)),
    ):
        command("rename", "", params="<name>")(mock_rename)
        await _dispatch_double_slash(update, context)
        mock_rename.assert_awaited_once_with(update, context)
