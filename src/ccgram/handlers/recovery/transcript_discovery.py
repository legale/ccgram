"""Transcript discovery for hookless providers.

Discovers and registers transcripts for providers without hook support
(Codex, Gemini). Also handles provider auto-detection from pane process
and shell ↔ agent transitions.

Key components:
  - discover_and_register_transcript: main discovery function called per topic
  - _detect_and_apply_provider: provider auto-detection from running process
  - _find_and_register_transcript: transcript search for hookless providers
"""

import asyncio
import os
import subprocess
from typing import TYPE_CHECKING, Any
from pathlib import Path

import structlog

from ...config import config
from ...providers import (
    detect_provider_from_pane,
    detect_provider_from_runtime,
    detect_provider_from_transcript_path,
    get_provider_for_window,
    should_probe_pane_title_for_provider_detection,
)
from ...session import session_manager
from ...session_map import session_map_sync
from ...telegram_client import TelegramClient
from ...tmux_manager import tmux_manager
from ...window_resolver import is_foreign_window
from ...window_state_store import window_store
from ...providers.base import SessionStartEvent

if TYPE_CHECKING:
    from ...providers.base import AgentProvider
    from ...session import WindowState
    from ...tmux_manager import TmuxWindow

logger = structlog.get_logger()

_TMUX_TIMEOUT_SEC = 2.0
_PROC_STAT_MIN_FIELDS_AFTER_COMM = 2


async def _detect_and_apply_provider(
    window_id: str,
    state: "WindowState",
    w: "TmuxWindow",
    *,
    client: TelegramClient | None = None,
    chat_id: int = 0,
    thread_id: int = 0,
) -> None:
    """Detect provider from pane process and apply transitions."""
    detected = await detect_provider_from_pane(
        w.pane_current_command, pane_tty=w.pane_tty, window_id=window_id
    )
    if not detected and should_probe_pane_title_for_provider_detection(
        w.pane_current_command
    ):
        pane_title = await tmux_manager.get_pane_title(window_id)
        detected = detect_provider_from_runtime(
            w.pane_current_command,
            pane_title=pane_title,
        )

    if detected and detected != state.provider_name:
        old_provider = state.provider_name
        session_manager.set_window_provider(window_id, detected, cwd=w.cwd or None)
        # Lazy: providers/__init__.py reaches back into transcript code
        # via provider format modules.
        from ...providers import get_provider_for_window

        new_caps = get_provider_for_window(window_id, detected)
        old_caps = (
            get_provider_for_window(window_id, old_provider) if old_provider else None
        )
        if new_caps and new_caps.capabilities.chat_first_command_path:
            state.transcript_path = ""
            # Lazy: shell.shell_prompt_orchestrator hits the recovery
            # subpackage's discovery code via send-keys callbacks.
            from ..shell.shell_prompt_orchestrator import ensure_setup

            await ensure_setup(
                window_id,
                "provider_switch",
                client=client,
                chat_id=chat_id,
                thread_id=thread_id,
            )
        elif old_caps and old_caps.capabilities.chat_first_command_path:
            # Lazy: same shell ↔ recovery cycle as above.
            from ..shell.shell_capture import clear_shell_monitor_state

            # Lazy: same shell ↔ recovery cycle as above.
            from ..shell.shell_prompt_orchestrator import (
                clear_state as clear_orchestrator,
            )

            clear_shell_monitor_state(window_id)
            clear_orchestrator(window_id)
    elif not detected and state.transcript_path:
        inferred = detect_provider_from_transcript_path(state.transcript_path)
        if inferred and inferred != state.provider_name:
            session_manager.set_window_provider(window_id, inferred, cwd=w.cwd or None)


def _resolve_providers_to_try(
    window_id: str, state: "WindowState", w: "TmuxWindow | None"
) -> list[tuple[str, "AgentProvider"]] | None:
    """Determine which providers to probe for transcripts.

    Returns a list of (name, provider) pairs, or ``None`` to signal the
    caller should set up a shell provider.
    """
    # Lazy: hoisting forms polling/__init__ → window_tick →
    # recovery.transcript_discovery → polling_state partial-init
    # cycle (worker-order-dependent; verified during F6.2). polling_types
    # is leaf-level — Task 5 of Round 5 may hoist this once cycle test covers it.
    # Lazy: polling_types is leaf-pure; importing here at module load would touch the polling subpackage __init__
    from ..polling.polling_types import is_shell_prompt

    # Lazy: providers registry reaches back through transcripts
    from ...providers import registry

    if state.provider_name:
        provider = get_provider_for_window(window_id, state.provider_name)
        if not provider.capabilities.supports_mailbox_delivery:
            return []
        return [(provider.capabilities.name, provider)]

    if w and is_shell_prompt(w.pane_current_command):
        return None  # signals caller to set up shell

    return [
        (name, registry.get(name))
        for name in registry.provider_names()
        if not registry.get(name).capabilities.supports_hook and name != "shell"
    ]


def _tmux_target(window_id: str) -> str:
    if is_foreign_window(window_id):
        return window_id
    return f"{config.tmux_session_name}:{window_id}"


def _read_tmux_pane_pid(window_id: str) -> int | None:
    try:
        proc = subprocess.run(
            [
                "tmux",
                "display-message",
                "-p",
                "-t",
                _tmux_target(window_id),
                "#{pane_pid}",
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=_TMUX_TIMEOUT_SEC,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    raw = proc.stdout.strip()
    return int(raw) if raw.isdigit() else None


def _read_proc_ppid(stat_path: Path) -> tuple[int, int] | None:
    try:
        pid = int(stat_path.parent.name)
        data = stat_path.read_text()
    except (OSError, ValueError):
        return None
    if ")" not in data:
        return None
    rest = data.rsplit(")", 1)[1].strip().split()
    if len(rest) < _PROC_STAT_MIN_FIELDS_AFTER_COMM:
        return None
    try:
        return pid, int(rest[1])
    except ValueError:
        return None


def _proc_descendants(root_pid: int) -> set[int]:
    children: dict[int, list[int]] = {}
    for stat_path in Path("/proc").glob("[0-9]*/stat"):
        item = _read_proc_ppid(stat_path)
        if item is None:
            continue
        pid, ppid = item
        children.setdefault(ppid, []).append(pid)

    seen: set[int] = set()
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        stack.extend(children.get(pid, ()))
    return seen


def _iter_proc_fds(pid: int) -> list[Path]:
    fd_dir = Path("/proc") / str(pid) / "fd"
    try:
        return list(fd_dir.iterdir())
    except OSError:
        return []


def _codex_event_from_fd(
    fd_path: Path,
    cwd: str,
    window_key: str,
) -> tuple[float, SessionStartEvent] | None:
    try:
        target = Path(os.readlink(fd_path))
    except OSError:
        return None
    sessions_dir = Path.home() / ".codex" / "sessions"
    if target.suffix != ".jsonl":
        return None
    try:
        target.relative_to(sessions_dir)
    except ValueError:
        return None

    # Lazy: reuse Codex provider's transcript metadata rules without importing
    # provider-specific code on the recovery module cold path.
    from ...providers.codex import _is_primary_codex_session, _read_codex_session_meta

    meta = _read_codex_session_meta(target)
    if not meta or not _is_primary_codex_session(meta):
        return None
    file_cwd = meta.get("cwd", "")
    if not file_cwd:
        return None
    if cwd and str(Path(file_cwd).resolve()) != str(Path(cwd).resolve()):
        return None
    session_id = meta.get("id", "")
    if not session_id:
        return None
    try:
        mtime = target.stat().st_mtime
    except OSError:
        mtime = 0.0
    return mtime, SessionStartEvent(
        session_id=session_id,
        cwd=file_cwd,
        transcript_path=str(target),
        window_key=window_key,
    )


def _discover_codex_open_transcript(
    window_id: str,
    cwd: str,
    window_key: str,
) -> SessionStartEvent | None:
    pane_pid = _read_tmux_pane_pid(window_id)
    if pane_pid is None:
        return None

    candidates: list[tuple[float, SessionStartEvent]] = []
    for pid in _proc_descendants(pane_pid):
        for fd_path in _iter_proc_fds(pid):
            event = _codex_event_from_fd(fd_path, cwd, window_key)
            if event is not None:
                candidates.append(event)
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


async def _find_and_register_transcript(
    window_id: str,
    state: Any,
    providers_to_try: list[tuple[str, "AgentProvider"]],
    pane_alive: bool,
) -> None:
    """Search for transcripts among candidate providers and register if found."""
    window_key = (
        window_id
        if is_foreign_window(window_id)
        else f"{config.tmux_session_name}:{window_id}"
    )

    for provider_name, provider in providers_to_try:
        event = None
        if provider_name == "codex":
            event = await asyncio.to_thread(
                _discover_codex_open_transcript,
                window_id,
                state.cwd,
                window_key,
            )
        max_age = 0 if pane_alive else None
        if event is None:
            event = await asyncio.to_thread(
                provider.discover_transcript,
                state.cwd,
                window_key,
                max_age=max_age,
            )
        if not event:
            continue

        if (
            state.session_id == event.session_id
            and state.transcript_path == event.transcript_path
            and state.provider_name == provider_name
        ):
            return

        session_map_sync.register_hookless_session(
            window_id=window_id,
            session_id=event.session_id,
            cwd=event.cwd,
            transcript_path=event.transcript_path,
            provider_name=provider_name,
        )
        await asyncio.to_thread(
            session_map_sync.write_hookless_session_map,
            window_id=window_id,
            session_id=event.session_id,
            cwd=event.cwd,
            transcript_path=event.transcript_path,
            provider_name=provider_name,
        )
        return


async def discover_and_register_transcript(
    window_id: str,
    *,
    _window: "TmuxWindow | None" = None,
    client: TelegramClient | None = None,
    user_id: int = 0,
    thread_id: int = 0,
) -> None:
    """Discover and register transcript for hookless providers (Codex, Gemini).

    Also handles provider auto-detection from pane process name
    and shell ↔ agent transitions with prompt marker setup.
    """
    # Lazy: same polling/__init__ cycle as _resolve_providers_to_try.
    from ..polling.polling_types import is_shell_prompt

    # Lazy: thread_router proxy resolved when transcript discovery is invoked
    from ...thread_router import thread_router

    state = window_store.window_states.get(window_id)
    if not state:
        return

    chat_id = thread_router.resolve_chat_id(user_id, thread_id) if user_id else 0

    w = _window or await tmux_manager.find_window_by_id(window_id)

    if w and w.pane_current_command:
        await _detect_and_apply_provider(
            window_id, state, w, client=client, chat_id=chat_id, thread_id=thread_id
        )

    if state.provider_name:
        provider = get_provider_for_window(window_id, state.provider_name)
        if provider.capabilities.supports_hook:
            return

    providers_to_try = _resolve_providers_to_try(window_id, state, w)
    if providers_to_try is None:
        session_manager.set_window_provider(window_id, "shell")
        state.transcript_path = ""
        # Lazy: same shell ↔ recovery cycle as _detect_and_apply_provider.
        from ..shell.shell_prompt_orchestrator import ensure_setup

        await ensure_setup(
            window_id,
            "provider_switch",
            client=client,
            chat_id=chat_id,
            thread_id=thread_id,
        )
        return
    if not providers_to_try:
        return

    pane_alive = w is not None and not is_shell_prompt(w.pane_current_command)
    await _find_and_register_transcript(window_id, state, providers_to_try, pane_alive)
