"""Unit tests for the iproute2-style command matcher."""

from __future__ import annotations

from ccgram.handlers.arg_parser import match_command, matches
from ccgram.handlers.command_meta import command


@command("screenshot", "Capture terminal", aliases=("screen",))
async def screenshot_handler(update, context):
    pass


@command("sessions", "List sessions", aliases=("ses",))
async def sessions_handler(update, context):
    pass


def test_matches_prefixes():
    assert matches("screenshot", "screenshot") is True
    assert matches("sc", "screenshot") is True
    assert matches("", "screenshot") is False
    assert matches("screenshots", "screenshot") is False
    assert matches("SC", "screenshot") is False


def test_exact_alias_wins_and_returns_remaining_args():
    handler, args, candidates = match_command(
        ["screen", "--live", "3"], [screenshot_handler, sessions_handler]
    )

    assert handler is screenshot_handler
    assert args == ["--live", "3"]
    assert candidates == []


def test_unambiguous_prefix_returns_remaining_args():
    handler, args, candidates = match_command(
        ["screens", "arg"], [screenshot_handler, sessions_handler]
    )

    assert handler is screenshot_handler
    assert args == ["arg"]
    assert candidates == []


def test_ambiguous_prefix_returns_candidates_and_original_argv():
    argv = ["s", "arg"]
    handler, args, candidates = match_command(
        argv, [screenshot_handler, sessions_handler]
    )

    assert handler is None
    assert args == argv
    assert candidates == ["screenshot", "sessions"]


def test_unknown_command_returns_original_argv():
    argv = ["unknown", "arg"]
    assert match_command(argv, [screenshot_handler, sessions_handler]) == (
        None,
        argv,
        [],
    )
