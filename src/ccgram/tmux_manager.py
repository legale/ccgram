"""Tmux session/window management via libtmux.

Wraps libtmux to provide async-friendly operations on tmux sessions:
  - list_sessions / find_window_by_id: discover and route sessions.
  - capture_pane: read terminal content (plain or with ANSI colors).
  - send_keys: forward user input directly to the active pane.
  - create_session / kill_session / rename_session: lifecycle management.
  - list_panes / capture_pane_by_id / send_keys_to_pane: pane-level ops.

All blocking libtmux calls are wrapped in asyncio.to_thread().

Key class: TmuxManager (singleton instantiated as `tmux_manager`).
"""

import asyncio
import contextlib
import shlex
import subprocess
from uuid import uuid4
from dataclasses import dataclass
from pathlib import Path

import libtmux
from libtmux.exc import LibTmuxException
import structlog

from .config import config
from .thread_router import thread_router

logger = structlog.get_logger()

_TmuxError = (
    LibTmuxException,
    OSError,
    subprocess.CalledProcessError,
)


_TOPIC_OPTION = "@ccgram_topic"


def extract_sidecar_output(text: str, marker: str) -> tuple[str | None, bool]:
    """Return output between the sidecar begin and completion markers."""
    lines = text.splitlines()
    begin_marker = f"{marker}:BEGIN"
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == begin_marker),
        None,
    )
    if start is None:
        return None, False

    end = None
    for index in range(start + 1, len(lines)):
        status = lines[index].strip().removeprefix(f"{marker}:")
        if status != lines[index].strip() and status.isdigit():
            end = index
            break

    output = lines[start + 1 : end]
    return "\n".join(output).rstrip(), end is not None


async def _kill_timed_out_proc(proc: asyncio.subprocess.Process | None) -> None:
    if proc is None:
        return
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
        await proc.wait()


@dataclass
class PaneInfo:
    """Information about a single tmux pane within a window."""

    pane_id: str  # Stable global ID, e.g. "%3"
    active: bool
    path: str = ""  # Working directory
    dead: bool = False


@dataclass
class TmuxWindow:
    """Information about a tmux window."""

    window_id: str
    window_name: str
    cwd: str  # Current working directory
    topic_ref: tuple[int, int] | None = None  # (chat_id, thread_id), session-scoped


class TmuxManager:
    """Manages tmux sessions and windows."""

    def __init__(self, session_name: str | None = None):
        """Initialize tmux manager.

        Args:
            session_name: Name of the tmux session to use (default from config)
        """
        self.session_name = session_name or config.tmux_session_name
        self._server: libtmux.Server | None = None
        self._sidecar_panes: dict[str, str] = {}
        self._sidecar_completion_markers: dict[str, str] = {}

    @property
    def server(self) -> libtmux.Server:
        """Get or create tmux server connection."""
        if self._server is None:
            self._server = libtmux.Server()
        return self._server

    def _reset_server(self) -> None:
        """Reset cached server connection (e.g. after tmux server restart)."""
        self._server = None

    def topic_session_name(self, topic_name: str) -> str:
        return f"{config.tmux_session_prefix}{topic_name}"

    def topic_name_from_session_name(self, session_name: str) -> str:
        return session_name.removeprefix(config.tmux_session_prefix)

    def _split_qualified(self, window_id: str) -> tuple[str, str]:
        if ":" in window_id and not window_id.startswith("@"):
            session_name, bare_id = window_id.rsplit(":", 1)
            return session_name, bare_id
        return self.session_name, window_id

    def get_session(self, session_name: str | None = None) -> libtmux.Session | None:
        """Get the tmux session if it exists."""
        name = session_name or self.session_name
        try:
            return self.server.sessions.get(session_name=name, default=None)
        except _TmuxError:
            self._reset_server()
            return None

    async def list_sessions(self) -> list[TmuxWindow]:
        """List every tmux session as one dashboard record.

        The returned window id targets the session's first window so existing
        dashboard actions can operate on the record without changing the
        window polling path.
        """

        def _sync_list_sessions() -> list[TmuxWindow]:
            try:
                result = subprocess.run(
                    [
                        "tmux",
                        "list-sessions",
                        "-F",
                        "#{session_name}\t#{session_path}\t#{@ccgram_topic}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return []
            if result.returncode != 0:
                return []

            sessions: list[TmuxWindow] = []
            for line in result.stdout.splitlines():
                parts = line.split("\t", 2)
                session_name = parts[0].strip()
                if not session_name:
                    continue
                cwd = parts[1] if len(parts) > 1 else ""
                topic_ref = self._parse_topic_ref(  # noqa: PLR2004
                    parts[2] if len(parts) > 2 else ""  # noqa: PLR2004
                )
                session = self.get_session(session_name)
                if not session or not session.windows:
                    continue
                window = session.windows[0]
                window_id = f"{session_name}:{window.window_id or ''}"
                sessions.append(
                    TmuxWindow(
                        window_id=window_id,
                        window_name=session_name,
                        cwd=cwd,
                        topic_ref=topic_ref,
                    )
                )
            return sessions

        return await asyncio.to_thread(_sync_list_sessions)

    @staticmethod
    def _parse_topic_ref(value: str) -> tuple[int, int] | None:
        if not value:
            return None
        chat_id, sep, thread_id = value.partition(":")
        if not sep:
            return None
        try:
            return int(chat_id), int(thread_id)
        except ValueError:
            return None

    async def set_session_topic(
        self, session_name: str, chat_id: int, thread_id: int
    ) -> bool:
        """Store the Telegram topic identity on the tmux session."""

        def _sync_set() -> bool:
            try:
                proc = subprocess.run(
                    [
                        "tmux",
                        "set-option",
                        "-t",
                        session_name,
                        _TOPIC_OPTION,
                        f"{chat_id}:{thread_id}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return False
            if proc.returncode != 0:
                logger.warning(
                    "Failed to set %s on %s: %s",
                    _TOPIC_OPTION,
                    session_name,
                    proc.stderr.strip(),
                )
                return False
            return True

        return await asyncio.to_thread(_sync_set)

    async def clear_session_topic(self, session_name: str) -> bool:
        """Remove the Telegram topic identity from the tmux session."""

        def _sync_clear() -> bool:
            try:
                proc = subprocess.run(
                    ["tmux", "set-option", "-u", "-t", session_name, _TOPIC_OPTION],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return False
            return proc.returncode == 0

        return await asyncio.to_thread(_sync_clear)

    async def find_window_by_id(self, window_id: str) -> TmuxWindow | None:
        """Find a window by its tmux window ID (e.g. 'cc_proj:@0' or '@0')."""
        session_name, win_id = self._split_qualified(window_id)
        idx_window_id = 0
        idx_window_name = 1
        idx_cwd = 2
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux",
                "list-windows",
                "-t",
                session_name,
                "-F",
                "#{window_id}\t#{window_name}\t#{pane_current_path}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            async with asyncio.timeout(5.0):
                stdout, _ = await proc.communicate()
        except TimeoutError:
            await _kill_timed_out_proc(proc)
            return None
        except OSError:
            return None
        if proc.returncode != 0:
            return None
        for line in stdout.decode().strip().split("\n"):
            parts = line.split("\t", 2)
            if parts and parts[idx_window_id] == win_id:
                return TmuxWindow(
                    window_id=window_id,
                    window_name=parts[idx_window_name]
                    if len(parts) > idx_window_name
                    else session_name,
                    cwd=parts[idx_cwd] if len(parts) > idx_cwd else "",
                )
        return None

    async def capture_pane(
        self,
        window_id: str,
        with_ansi: bool = False,
        *,
        include_history: bool = False,
    ) -> str | None:
        """Capture text content of a window's active pane.

        Args:
            window_id: The window ID to capture
            with_ansi: If True, capture with ANSI color codes
            include_history: If True, capture the tmux scrollback buffer too

        Returns:
            The captured text (stripped of trailing whitespace),
            or None on failure or empty content.
        """
        session_name, pane_id = self._split_qualified(window_id)
        if session_name.startswith(config.tmux_session_prefix):
            await self._ensure_managed_window_size(session_name, pane_id)

        if with_ansi:
            return await self._capture_pane_ansi(
                window_id, include_history=include_history
            )

        return await self._capture_pane_plain(
            window_id, include_history=include_history
        )

    async def _ensure_managed_window_size(
        self, session_name: str, window_id: str
    ) -> None:
        target = f"{session_name}:{window_id}"
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            "set-window-option",
            "-t",
            target,
            "window-size",
            "largest",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

    async def _capture_pane_ansi(
        self, window_id: str, *, include_history: bool = False
    ) -> str | None:
        """Capture pane with ANSI colors via tmux subprocess."""
        proc: asyncio.subprocess.Process | None = None
        try:
            args = ["capture-pane", "-e", "-p"]
            if include_history:
                args.extend(["-S", "-"])
            args.extend(["-t", window_id])
            proc = await asyncio.create_subprocess_exec(
                "tmux",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            async with asyncio.timeout(5.0):
                stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(
                    "Failed to capture pane %s: %s",
                    window_id,
                    stderr.decode("utf-8", errors="replace"),
                )
                return None
            text = stdout.decode("utf-8", errors="replace").rstrip()
            return text if text else None
        except TimeoutError:
            logger.warning("Capture pane %s timed out", window_id)
            await _kill_timed_out_proc(proc)
            return None
        except OSError:
            logger.exception("Unexpected error capturing pane %s", window_id)
            return None

    async def _capture_pane_plain(
        self, window_id: str, *, include_history: bool = False
    ) -> str | None:
        """Capture pane as plain text via libtmux."""

        def _sync_capture() -> str | None:
            session_name, wid = self._split_qualified(window_id)
            session = self.get_session(session_name)
            if not session:
                return None
            try:
                window = session.windows.get(window_id=wid, default=None)
                if not window:
                    return None
                pane = window.active_pane
                if not pane:
                    return None
                start = "-" if include_history else None
                lines = pane.capture_pane(start=start)
                text = "\n".join(lines) if isinstance(lines, list) else str(lines)
                text = text.rstrip()
                return text if text else None
            except _TmuxError as e:
                if "can't find window" in str(e):
                    # Window closed mid-capture — race, not an error.
                    logger.debug("Pane capture skipped (window gone): %s", window_id)
                else:
                    logger.warning("Failed to capture pane %s: %s", window_id, e)
                self._reset_server()
                return None

        return await asyncio.to_thread(_sync_capture)

    def _pane_send(
        self, window_id: str, chars: str, *, enter: bool, literal: bool
    ) -> bool:
        """Synchronous helper: send keys to the active pane of a window."""
        session_name, wid = self._split_qualified(window_id)
        session = self.get_session(session_name)
        if not session:
            logger.warning("No tmux session found")
            return False
        try:
            window = session.windows.get(window_id=wid, default=None)
            if not window:
                logger.warning("Window %s not found", window_id)
                return False
            pane = window.active_pane
            if not pane:
                logger.warning("No active pane in window %s", window_id)
                return False
            pane.send_keys(chars, enter=enter, literal=literal)
            return True
        except _TmuxError:
            logger.exception("Failed to send keys to window %s", window_id)
            return False

    async def send_keys(
        self,
        window_id: str,
        text: str,
        enter: bool = True,
        literal: bool = True,
    ) -> bool:
        """Send keys directly to the active pane of a window."""
        return await asyncio.to_thread(
            self._pane_send, window_id, text, enter=enter, literal=literal
        )

    async def kill_session(self, session_name: str) -> bool:
        """Kill a tmux session and all of its windows."""
        if session_name == self.session_name:
            logger.warning("Refusing to kill the main tmux session %s", session_name)
            return False

        for wid in list(self._sidecar_panes):
            if wid.startswith(f"{session_name}:"):
                self.forget_sidecar_pane(wid)

        def _sync_kill() -> bool:
            session = self.get_session(session_name)
            if not session:
                return False
            try:
                session.kill()
                logger.info("Killed tmux session %s", session_name)
                return True
            except _TmuxError:
                logger.exception("Failed to kill tmux session %s", session_name)
                return False

        return await asyncio.to_thread(_sync_kill)

    async def rename_session(self, session_name: str, new_name: str) -> bool:
        def _sync_rename() -> bool:
            try:
                proc = subprocess.run(
                    ["tmux", "rename-session", "-t", session_name, new_name],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return False
            if proc.returncode != 0:
                logger.debug(
                    "Failed to rename session %s to %s: %s",
                    session_name,
                    new_name,
                    proc.stderr.strip(),
                )
                return False
            self._reset_server()
            return True

        return await asyncio.to_thread(_sync_rename)

    async def session_exists(self, session_name: str) -> bool:
        """Check whether a tmux session exists."""

        def _sync_exists() -> bool:
            try:
                proc = subprocess.run(
                    ["tmux", "has-session", "-t", session_name],
                    capture_output=True,
                    timeout=5,
                )
                return proc.returncode == 0
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return False

        return await asyncio.to_thread(_sync_exists)

    # ── Pane-level operations ──────────────────────────────────────────

    def get_sidecar_pane_id(self, window_id: str) -> str | None:
        """Get tracked sidecar pane ID for a window if any."""
        return self._sidecar_panes.get(window_id)

    def forget_sidecar_pane(self, window_id: str) -> None:
        """Remove tracked sidecar pane for a window."""
        self._sidecar_panes.pop(window_id, None)
        self._sidecar_completion_markers.pop(window_id, None)

    def get_sidecar_completion_marker(self, window_id: str) -> str | None:
        """Get the completion marker for the active sidecar command."""
        return self._sidecar_completion_markers.get(window_id)

    async def start_sidecar_command(
        self, window_id: str, command: str
    ) -> tuple[str | None, str | None]:
        """Start a one-shot command in a fresh pane using the active pane cwd.

        The command is started as the pane process instead of being typed into
        an interactive shell. This prevents stale input, prompts, and heredocs
        from becoming part of the command.
        """
        tracked = self._sidecar_panes.get(window_id)
        if tracked:
            panes = await self.list_panes(window_id)
            tracked_pane = next((p for p in panes if p.pane_id == tracked), None)
            if tracked_pane is not None and not tracked_pane.dead:
                logger.info("Sidecar is already running in %s", tracked)
                return None, None
            if tracked_pane is not None:
                await self.kill_pane(tracked, window_id=window_id)
            self.forget_sidecar_pane(window_id)

        panes = await self.list_panes(window_id)
        if not panes:
            return None, None
        active = next((pane for pane in panes if pane.active), panes[0])
        cwd = active.path
        if not cwd or not Path(cwd).is_dir():
            logger.warning("Invalid sidecar cwd for %s: %r", window_id, cwd)
            return None, None

        session_name, wid = self._split_qualified(window_id)
        target = f"{session_name}:{wid}" if session_name else wid
        marker = f"__CCG_DONE_{uuid4().hex[:12]}__"
        wrapped = (
            'tmux set-option -p -t "$TMUX_PANE" remain-on-exit on '
            "2>/dev/null || true; "
            f"printf '\\n%s:BEGIN\\n' {shlex.quote(marker)}; "
            f"/bin/sh -c {shlex.quote(command)}; "
            f"_ccgram_status=$?; printf '\\n%s:%s\\n' "
            f'{shlex.quote(marker)} "$_ccgram_status"'
        )
        shell_command = shlex.join(["/bin/sh", "-c", wrapped])
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux",
                "split-window",
                "-d",
                "-t",
                target,
                "-c",
                cwd,
                "-P",
                "-F",
                "#{pane_id}",
                shell_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            async with asyncio.timeout(5.0):
                stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(
                    "Failed to split window %s for sidecar: %s",
                    target,
                    stderr.decode("utf-8", errors="replace"),
                )
                return None, None
            new_pane_id = stdout.decode("utf-8", errors="replace").strip()
            if new_pane_id:
                self._sidecar_panes[window_id] = new_pane_id
                self._sidecar_completion_markers[window_id] = marker
                return new_pane_id, marker
        except TimeoutError:
            logger.warning("Timed out splitting window %s for sidecar", target)
            await _kill_timed_out_proc(proc)
            return None, None
        except OSError:
            logger.exception("Unexpected error splitting window %s for sidecar", target)
            return None, None

        return None, None

    async def kill_pane(self, pane_id: str, *, window_id: str | None = None) -> bool:
        """Kill a pane by its stable ID without killing its tmux window."""

        def _sync_kill() -> bool:
            session_name, wid = (
                self._split_qualified(window_id) if window_id else (None, None)
            )
            session = self.get_session(session_name)
            if not session:
                return False
            try:
                pane = self._find_pane(pane_id, session, window_id=wid)
                if not pane:
                    return False
                pane.kill()
                logger.info("Killed pane %s", pane_id)
                return True
            except _TmuxError:
                logger.exception("Failed to kill pane %s", pane_id)
                return False

        return await asyncio.to_thread(_sync_kill)

    async def list_panes(self, window_id: str) -> list[PaneInfo]:
        """List all panes in a window.

        Returns an empty list if the window is not found or on error.
        """

        def _sync_list_panes() -> list[PaneInfo]:
            session_name, wid = self._split_qualified(window_id)
            session = self.get_session(session_name)
            if not session:
                return []
            try:
                window = session.windows.get(window_id=wid, default=None)
                if not window:
                    return []
                result: list[PaneInfo] = []
                for pane in window.panes:
                    result.append(
                        PaneInfo(
                            pane_id=pane.pane_id or "",
                            active=pane.pane_active == "1",
                            path=pane.pane_current_path or "",
                            dead=getattr(pane, "pane_dead", "0") == "1",
                        )
                    )
                return result
            except _TmuxError as exc:
                logger.warning("Failed to list panes for %s: %s", window_id, exc)
                self._reset_server()
                return []

        return await asyncio.to_thread(_sync_list_panes)

    async def capture_pane_by_id(
        self,
        pane_id: str,
        *,
        with_ansi: bool = False,
        include_history: bool = False,
        window_id: str | None = None,
    ) -> str | None:
        """Capture text of a specific pane (by stable pane ID like '%3').

        Unlike capture_pane() which targets the active pane of a window,
        this targets a specific pane regardless of whether it is active.

        When window_id is given, the pane must belong to that window (prevents
        cross-window access via crafted pane IDs).

        When include_history is True, the tmux scrollback buffer is included.
        """
        if window_id:
            session_name, window = self._split_qualified(window_id)
            if session_name.startswith(config.tmux_session_prefix):
                await self._ensure_managed_window_size(session_name, window)

        if with_ansi:
            if window_id:
                panes = await self.list_panes(window_id)
                if not any(p.pane_id == pane_id for p in panes):
                    logger.warning("Pane %s not found in window %s", pane_id, window_id)
                    return None
            return await self._capture_pane_ansi(
                pane_id, include_history=include_history
            )

        def _sync_capture() -> str | None:
            session_name, wid = (
                self._split_qualified(window_id) if window_id else (None, None)
            )
            session = self.get_session(session_name)
            if not session:
                return None
            try:
                pane = self._find_pane(pane_id, session, window_id=wid)
                if not pane:
                    return None
                start = "-" if include_history else None
                lines = pane.capture_pane(start=start)
                text = "\n".join(lines) if isinstance(lines, list) else str(lines)
                text = text.rstrip()
                return text if text else None
            except _TmuxError as exc:
                logger.warning("Failed to capture pane %s: %s", pane_id, exc)
                self._reset_server()
                return None

        return await asyncio.to_thread(_sync_capture)

    async def send_keys_to_pane(
        self,
        pane_id: str,
        text: str,
        *,
        enter: bool = True,
        literal: bool = True,
        window_id: str | None = None,
    ) -> bool:
        """Send keys to a specific pane (by stable pane ID like '%3').

        Unlike send_keys() which targets the active pane of a window,
        this targets a specific pane regardless of whether it is active.

        When window_id is given, the pane must belong to that window (prevents
        cross-window access via crafted pane IDs).
        """

        def _sync_send() -> bool:
            session_name, wid = (
                self._split_qualified(window_id) if window_id else (None, None)
            )
            session = self.get_session(session_name)
            if not session:
                return False
            try:
                pane = self._find_pane(pane_id, session, window_id=wid)
                if not pane:
                    logger.warning("Pane %s not found", pane_id)
                    return False
                pane.send_keys(text, enter=enter, literal=literal)
                return True
            except _TmuxError:
                logger.exception("Failed to send keys to pane %s", pane_id)
                return False

        return await asyncio.to_thread(_sync_send)

    @staticmethod
    def _find_pane(
        pane_id: str,
        session: libtmux.Session,
        *,
        window_id: str | None = None,
    ) -> libtmux.Pane | None:
        """Find a pane by its stable ID (e.g. '%3').

        When window_id is given, only searches that window's panes (prevents
        cross-window access). Otherwise searches all windows in the session.
        """
        windows = session.windows
        if window_id:
            windows = [w for w in windows if w.window_id == window_id]
        for window in windows:
            for pane in window.panes:
                if pane.pane_id == pane_id:
                    return pane
        return None

    async def create_session(
        self,
        session_name: str,
        work_dir: str,
        window_name: str | None = None,
    ) -> tuple[bool, str, str, str]:
        """Create a new dedicated tmux session for a topic."""
        path = Path(work_dir).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            return False, f"Directory does not exist: {work_dir}", "", ""

        final_name = window_name or session_name

        def _sync_create() -> tuple[bool, str, str, str]:
            try:
                session = self.server.new_session(
                    session_name=session_name,
                    start_directory=str(path),
                )
                window = session.windows[0]
                window.rename_window(final_name)
                window.set_option("automatic-rename", "off")
                wid = f"{session_name}:{window.window_id or ''}"
                logger.info(
                    "Created tmux session %s (window %s, path %s)",
                    session_name,
                    final_name,
                    path,
                )
                return True, f"Created session {session_name}", final_name, wid
            except _TmuxError as e:
                logger.exception("Failed to create tmux session %s", session_name)
                return False, f"Failed to create session: {e}", "", ""

        return await asyncio.to_thread(_sync_create)


# Global instance with default session name
tmux_manager = TmuxManager()


async def send_to_window(window_id: str, text: str) -> tuple[bool, str]:
    """Send text to a tmux window by ID.

    Returns (success, message). Looks up the display name for logging, then
    delegates to tmux_manager.find_window_by_id + send_keys.
    """

    display = thread_router.get_display_name(window_id)
    logger.debug(
        "send_to_tmux_session: tmux_session=%s (%s), text_len=%d",
        window_id,
        display,
        len(text),
    )
    window = await tmux_manager.find_window_by_id(window_id)
    if not window:
        return False, "Window not found (may have been closed)"
    success = await tmux_manager.send_keys(window.window_id, text)
    if success:
        return True, f"Sent to {display}"
    return False, "Failed to send keys"
