"""Structural tests pinning the handler-layer invariants.

Two AST walks over ``src/ccgram/handlers/**/*.py`` codify what is
currently allowed to bypass the documented seams:

* :func:`test_no_new_ptb_bot_escapes` — Handlers should depend on
  :class:`ccgram.telegram_client.TelegramClient`. Files that need
  PTB-only helpers (``get_file``, etc.) are listed in :data:`_PTB_BOT_ALLOWLIST`.
* :func:`test_no_new_lower_level_singleton_access` — Snapshots the
  file-level coupling to ``thread_router``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HANDLERS_ROOT = _REPO_ROOT / "src" / "ccgram" / "handlers"

_PTB_BOT_ALLOWLIST = frozenset(
    {
        "cleanup.py",
        "commands/__init__.py",
        "file_handler.py",
        "live/screenshot_callbacks.py",
        "sessions_dashboard.py",
        "status/topic_status_diff.py",
        "text/text_handler.py",
        "topics/topic_binding.py",
        "topics/topic_lifecycle.py",
    }
)

_SINGLETON_ATTRS = frozenset({"thread_router"})
_SINGLETON_ALLOWLIST = frozenset(
    {
        "callback_helpers.py",
        "callback_registry.py",
        "cleanup.py",
        "commands/__init__.py",
        "file_handler.py",
        "live/screenshot_callbacks.py",
        "polling/polling_coordinator.py",
        "polling/periodic_tasks.py",
        "registry.py",
        "send/send_command.py",
        "sessions_dashboard.py",
        "text/text_handler.py",
        "topics/topic_binding.py",
        "topics/topic_lifecycle.py",
    }
)


def _iter_handler_files() -> Iterator[Path]:
    for path in sorted(_HANDLERS_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.name == "__init__.py" and path.parent == _HANDLERS_ROOT:
            continue  # top-level package init is just a marker.
        yield path


def _rel(path: Path) -> str:
    return path.relative_to(_HANDLERS_ROOT).as_posix()


def _uses_ptb_bot(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if node.attr == "get_bot":
            return True
        if isinstance(node.value, ast.Attribute) and node.value.attr == "bot":
            return True
    return False


def _uses_singleton(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if not isinstance(node.value, ast.Name):
            continue
        if node.value.id in _SINGLETON_ATTRS:
            return True
    return False


@pytest.mark.parametrize("path", list(_iter_handler_files()), ids=_rel)
def test_no_new_ptb_bot_escapes(path: Path) -> None:
    rel = _rel(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    if rel in _PTB_BOT_ALLOWLIST:
        return
    assert not _uses_ptb_bot(tree), (
        f"{rel} reaches the raw PTB Bot via .get_bot() or context.bot.X. "
        "Use the TelegramClient Protocol (PTBTelegramClient(bot)) instead."
    )


@pytest.mark.parametrize("path", list(_iter_handler_files()), ids=_rel)
def test_no_new_lower_level_singleton_access(path: Path) -> None:
    rel = _rel(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    if rel in _SINGLETON_ALLOWLIST:
        return
    assert not _uses_singleton(tree), (
        f"{rel} touches one of {sorted(_SINGLETON_ATTRS)} directly."
    )
