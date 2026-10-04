"""Unit tests for handlers/arg_parser.py — matches() and ArgIter."""

from __future__ import annotations

import pytest

from ccgram.handlers.arg_parser import ArgIter, IncompleteCommand, matches


# ---------------------------------------------------------------------------
# matches()


class TestMatches:
    def test_full_match(self):
        assert matches("screenshot", "screenshot") is True

    def test_prefix_match(self):
        assert matches("sc", "screenshot") is True

    def test_single_char_prefix(self):
        assert matches("s", "screenshot") is True

    def test_empty_prefix_false(self):
        assert matches("", "screenshot") is False

    def test_empty_both_false(self):
        assert matches("", "") is False

    def test_prefix_longer_than_string(self):
        assert matches("screenshots", "screenshot") is False

    def test_no_match(self):
        assert matches("xyz", "screenshot") is False

    def test_case_sensitive(self):
        assert matches("SC", "screenshot") is False
        assert matches("sc", "Screenshot") is False


# ---------------------------------------------------------------------------
# ArgIter


class TestArgIter:
    def test_next_arg_consumes(self):
        it = ArgIter(["a", "b", "c"])
        assert it.next_arg() == "a"
        assert it.next_arg() == "b"
        assert it.next_arg() == "c"

    def test_next_arg_underrun_raises(self):
        it = ArgIter([])
        with pytest.raises(IncompleteCommand):
            it.next_arg()

    def test_next_arg_ok_true(self):
        it = ArgIter(["x"])
        assert it.next_arg_ok() is True

    def test_next_arg_ok_false_when_empty(self):
        it = ArgIter([])
        assert it.next_arg_ok() is False

    def test_next_arg_ok_false_after_drain(self):
        it = ArgIter(["x"])
        it.next_arg()
        assert it.next_arg_ok() is False

    def test_prev_arg_restores(self):
        it = ArgIter(["a", "b"])
        assert it.next_arg() == "a"
        it.prev_arg()
        assert it.next_arg() == "a"

    def test_prev_arg_noop_at_start(self):
        it = ArgIter(["a"])
        it.prev_arg()  # should not raise
        assert it.next_arg() == "a"

    def test_peek_returns_next_without_consuming(self):
        it = ArgIter(["a", "b"])
        assert it.peek() == "a"
        assert it.peek() == "a"  # still same
        assert it.next_arg() == "a"
        assert it.peek() == "b"

    def test_peek_none_when_empty(self):
        it = ArgIter([])
        assert it.peek() is None

    def test_remaining(self):
        it = ArgIter(["a", "b", "c"])
        it.next_arg()
        assert it.remaining() == ["b", "c"]

    def test_remaining_does_not_advance(self):
        it = ArgIter(["a", "b"])
        _ = it.remaining()
        assert it.next_arg() == "a"

    def test_lookahead_pattern(self):
        """Simulate iproute2 lookahead: consume, check, step back."""
        it = ArgIter(["--flag", "value"])
        tok = it.next_arg()
        if not matches("--flag", tok):
            it.prev_arg()
        # consumed --flag, now at value
        assert it.next_arg() == "value"
