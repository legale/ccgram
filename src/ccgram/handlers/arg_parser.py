"""Minimal argv iterator — iproute2-style argument traversal.

matches(prefix, string)
    True if *prefix* is a non-empty prefix of *string*.
    Mirrors the C ``matches()`` helper from iproute2/lib/utils.c.

ArgIter
    Thin wrapper around a list[str] cursor.  Callers advance it with
    next_arg() (raises IncompleteCommand on underrun), peek with
    next_arg_ok(), or step back with prev_arg().
"""

from __future__ import annotations

__all__ = [
    "matches",
    "ArgIter",
    "IncompleteCommand",
]


class IncompleteCommand(ValueError):
    """Raised when next_arg() is called with no arguments left."""


def matches(prefix: str, string: str) -> bool:
    """Return True if *prefix* is a non-empty prefix of *string*.

    Empty prefix always returns False (matches nothing), mirroring the
    iproute2 C original::

        static bool matches(const char *prefix, const char *string) {
            if (!*prefix) return false;
            while (*string && *prefix == *string) { prefix++; string++; }
            return !*prefix;
        }
    """
    if not prefix:
        return False
    return string.startswith(prefix)


class ArgIter:
    """Cursor over a list of string tokens.

    argv    -- the full token list (not mutated)
    pos     -- current index (points at the *next* token to consume)
    """

    __slots__ = ("argv", "pos")

    def __init__(self, argv: list[str]) -> None:
        self.argv = argv
        self.pos = 0

    # ------------------------------------------------------------------
    # core interface

    def next_arg(self) -> str:
        """Consume and return the next token.

        Raises IncompleteCommand if no tokens remain.
        """
        if self.pos >= len(self.argv):
            raise IncompleteCommand("command line is not complete")
        token = self.argv[self.pos]
        self.pos += 1
        return token

    def next_arg_ok(self) -> bool:
        """Return True if at least one more token is available."""
        return self.pos < len(self.argv)

    def prev_arg(self) -> None:
        """Step the cursor back by one (undo last next_arg)."""
        if self.pos > 0:
            self.pos -= 1

    # ------------------------------------------------------------------
    # convenience

    def peek(self) -> str | None:
        """Return the next token without consuming it, or None."""
        if self.pos < len(self.argv):
            return self.argv[self.pos]
        return None

    def remaining(self) -> list[str]:
        """Return all unconsumed tokens (does not advance cursor)."""
        return self.argv[self.pos :]

    def __repr__(self) -> str:  # pragma: no cover
        return f"ArgIter(pos={self.pos}, argv={self.argv!r})"
