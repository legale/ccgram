"""Stateful strategy classes + module-level singletons for the polling subsystem.

Pure types and constants live in ``polling_types``; this module owns the
classes that maintain mutable state across poll cycles and the five
module-level instances that wire them together. Importing this module
executes those instantiations as a side effect — that is intentional
(production callers want the singletons), but it is exactly why
``window_tick.decide`` must NOT import from here. Use ``polling_types``
for the contract; reach into ``polling_state`` only when stateful
behaviour is required.

The five singletons (``terminal_poll_state``, ``terminal_screen_buffer``,
``interactive_strategy``, ``lifecycle_strategy``, ``pane_status_strategy``)
live here. Pure types (``TickContext``, ``TickDecision``, ``PaneTransition``,
constants, ``is_shell_prompt``) live in ``polling_types`` — depend on that
module instead when stateful behaviour is not required.
"""

from __future__ import annotations

import time
import zlib
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import structlog

from .polling_types import (
    ACTIVITY_THRESHOLD as _ACTIVITY_THRESHOLD,
    MAX_PROBE_FAILURES,
    PANE_COUNT_TTL,
    STARTUP_TIMEOUT,
    TYPING_INTERVAL,
    StatusUpdate,
    TopicPollState,
    WindowPollState,
)

if TYPE_CHECKING:
    from telegram import Bot

    from ...screen_buffer import ScreenBuffer
    from ...tmux_manager import PaneInfo as TmuxPaneInfo

    AgentProvider = Any

logger = structlog.get_logger()

# ── TerminalScreenBuffer ───────────────────────────────────────────────

class TerminalScreenBuffer:
    """Pyte screen buffer, RC debounce, pane count cache, content-hash cache.

    Reads WindowPollState from a shared TerminalPollState instance for
    screen-buffer fields. Domain-specific parsing functions live in
    window_tick.py.
    """

    def __init__(self, poll_state: TerminalPollState) -> None:
        self._poll_state = poll_state

    def clear_screen_buffer(self, window_id: str) -> None:
        """Remove a window's ScreenBuffer, caches, and pyte results."""
        ws = self._poll_state.peek_state(window_id)
        if ws:
            ws.screen_buffer = None
            ws.pane_count_cache = None
            ws.last_pane_hash = None
            ws.last_pyte_result = None
            ws.last_rendered_text = None

    def reset_screen_buffer_state(self) -> None:
        """Reset all ScreenBuffers and caches (for testing)."""
        for ws in self._poll_state.iter_states():
            ws.screen_buffer = None
            ws.pane_count_cache = None
            ws.last_pane_hash = None
            ws.last_pyte_result = None
            ws.last_rendered_text = None

    def update_pane_count_cache(self, window_id: str, count: int) -> None:
        """Record freshly-fetched pane count with TTL expiry."""
        self._poll_state.get_state(window_id).pane_count_cache = (
            count,
            time.monotonic() + PANE_COUNT_TTL,
        )

    def is_single_pane_cached(self, window_id: str) -> bool:
        """Check if pane count cache confirms single pane (skip subprocess)."""
        ws = self._poll_state.peek_state(window_id)
        if not ws or not ws.pane_count_cache:
            return False
        count, expiry = ws.pane_count_cache
        return count <= 1 and expiry > time.monotonic()

    def get_rendered_text(self, window_id: str, fallback: str) -> str:
        """Return last rendered text if available, otherwise fallback."""
        ws = self._poll_state.peek_state(window_id)
        if ws and ws.last_rendered_text is not None:
            return ws.last_rendered_text
        return fallback

    def get_screen_buffer(
        self, window_id: str, columns: int, rows: int
    ) -> ScreenBuffer:
        """Get or create a ScreenBuffer for a window, resizing if needed."""
        # Lazy: screen_buffer pulls in pyte; only the pyte-based strategy
        # actually uses it, so leaf-level callers stay light.
        # Lazy: ScreenBuffer constructed lazily so tests can stub it
        from ...screen_buffer import ScreenBuffer

        ws = self._poll_state.get_state(window_id)
        buf = ws.screen_buffer
        if buf is None or not isinstance(buf, ScreenBuffer):
            buf = ScreenBuffer(columns=columns, rows=rows)
            ws.screen_buffer = buf
        elif buf.columns != columns or buf.rows != rows:
            buf.resize(columns, rows)
        else:
            buf.reset()
        return buf

    def parse_with_pyte(
        self,
        window_id: str,
        pane_text: str,
        columns: int = 0,
        rows: int = 0,
    ) -> StatusUpdate | None:
        """Parse terminal via pyte screen buffer for status and interactive UI.

        Content-hash optimization: unchanged pane content returns cached result
        without re-parsing.
        """
        # Lazy: terminal_parser imports terminal_parser_v100 (pyte
        # heavyweight) — match the get_screen_buffer pattern.
        # Lazy: terminal_parser pulls pyte; defer until first parse
        from ...terminal_parser import (
            format_status_display,
            parse_from_screen,
            parse_status_block_from_screen,
        )

        if (
            not isinstance(columns, int)
            or not isinstance(rows, int)
            or columns <= 0
            or rows <= 0
        ):
            columns, rows = 200, 50

        ws = self._poll_state.get_state(window_id)
        content_hash = hash((pane_text, columns, rows))
        if (
            ws.last_pane_hash is not None
            and content_hash == ws.last_pane_hash
            and (ws.last_pyte_result is None or not ws.last_pyte_result.is_interactive)
        ):
            return ws.last_pyte_result

        buf = self.get_screen_buffer(window_id, columns, rows)
        buf.feed(pane_text)
        ws.last_rendered_text = buf.rendered_text

        interactive = parse_from_screen(buf)
        if interactive:
            result = StatusUpdate(
                raw_text=interactive.content,
                display_label=interactive.name,
                is_interactive=True,
                ui_type=interactive.name,
            )
            ws.last_pane_hash = content_hash
            ws.last_pyte_result = result
            return result

        ws.last_pane_hash = content_hash
        ws.last_pyte_result = None
        return None

# ── TerminalPollState ──────────────────────────────────────────────────

class TerminalPollState:
    """Per-window poll state: seen-status, startup grace, probe failures, unbound timers.

    Owns the WindowPollState dict. TerminalScreenBuffer accesses it for
    screen-buffer-related fields.
    """

    def __init__(self) -> None:
        self._states: dict[str, WindowPollState] = {}

    def get_state(self, window_id: str) -> WindowPollState:
        """Get or create WindowPollState for a window."""
        return self._states.setdefault(window_id, WindowPollState())

    def peek_state(self, window_id: str) -> WindowPollState | None:
        """Return existing state without creating it, or None."""
        return self._states.get(window_id)

    def iter_states(self) -> Iterable[WindowPollState]:
        """Iterate over all existing window poll states (snapshot-safe)."""
        return list(self._states.values())

    def clear_state(self, window_id: str) -> None:
        """Remove all polling state for a window."""
        self._states.pop(window_id, None)

    def clear_unbound_timers(self, bound_ids: set[str], live_ids: set[str]) -> None:
        """Clear unbound timers for windows that are now bound or gone."""
        for wid, ws in list(self._states.items()):
            if ws.unbound_timer is not None and (
                wid in bound_ids or wid not in live_ids
            ):
                ws.unbound_timer = None

    def get_expired_unbound(self, now: float, timeout: float) -> list[str]:
        """Return window IDs whose unbound timer has expired."""
        return [
            wid
            for wid, ws in self._states.items()
            if ws.unbound_timer is not None and now - ws.unbound_timer >= timeout
        ]

    def get_orphaned_window_ids(
        self, live_ids: set[str], bound_ids: set[str]
    ) -> list[str]:
        """Return window IDs that are neither live nor bound."""
        return [
            wid for wid in self._states if wid not in live_ids and wid not in bound_ids
        ]

    def reset_probe_failures(self, window_id: str) -> None:
        """Reset probe failure counter for a single window."""
        ws = self._states.get(window_id)
        if ws:
            ws.probe_failures = 0

    def clear_seen_status(self, window_id: str) -> None:
        """Clear startup status tracking for a single window."""
        ws = self._states.get(window_id)
        if ws:
            ws.has_seen_status = False
            ws.startup_time = None

    def set_unbound_timer(self, window_id: str, ts: float) -> None:
        """Set unbound timer for a window (creates state if needed)."""
        ws = self.get_state(window_id)
        ws.unbound_timer = ts

    def clear_unbound_timer(self, window_id: str) -> None:
        """Clear unbound timer for a single window."""
        ws = self._states.get(window_id)
        if ws:
            ws.unbound_timer = None

    def reset_all_probe_failures(self) -> None:
        """Reset probe failure counters for all windows."""
        for ws in self._states.values():
            ws.probe_failures = 0

    def reset_all_seen_status(self) -> None:
        """Reset startup status tracking for all windows."""
        for ws in self._states.values():
            ws.has_seen_status = False
            ws.startup_time = None

    def reset_all_unbound_timers(self) -> None:
        """Reset unbound timers for all windows."""
        for ws in self._states.values():
            ws.unbound_timer = None

    def cancel_startup_timer(self, window_id: str) -> None:
        """Clear startup grace period without touching has_seen_status."""
        ws = self._states.get(window_id)
        if ws:
            ws.startup_time = None

    def begin_startup_timer(self, window_id: str, now: float) -> None:
        """Record the moment a window's startup grace period begins."""
        self.get_state(window_id).startup_time = now

    def check_seen_status(self, window_id: str) -> bool:
        """Return True if this window has received at least one status update."""
        ws = self._states.get(window_id)
        return ws.has_seen_status if ws else False

    def is_recently_active(self, window_id: str, last_activity: float | None) -> bool:
        """Check if recent transcript activity indicates an active agent.

        Side effect: marks window as having seen status if active.
        """
        if not last_activity:
            return False
        if (time.monotonic() - last_activity) < _ACTIVITY_THRESHOLD:
            self.mark_seen_status(window_id)
            return True
        return False

    def is_startup_expired(self, window_id: str) -> bool:
        """Check if a window's startup grace period has elapsed."""
        ws = self._states.get(window_id)
        if not ws or ws.startup_time is None:
            return False
        return (time.monotonic() - ws.startup_time) >= STARTUP_TIMEOUT

    def mark_seen_status(self, window_id: str) -> None:
        """Mark a window as having seen its first status update."""
        ws = self.get_state(window_id)
        ws.has_seen_status = True
        ws.startup_time = None

# ── InteractiveUIStrategy ───────────────────────────────────────────────

class InteractiveUIStrategy:
    """Pane alert hash state for multi-pane interactive prompt deduplication.

    Async scanning functions (scan_window_panes, check_interactive_only) live
    in window_tick.py and access state through this strategy.
    """

    def __init__(self) -> None:
        self._pane_alert_hashes: dict[str, tuple[str, float, str]] = {}

    def has_pane_alert(self, pane_id: str) -> bool:
        """Check whether a pane currently has an active alert."""
        return pane_id in self._pane_alert_hashes

    def get_pane_alert(self, pane_id: str) -> tuple[str, float, str] | None:
        """Return pane alert tuple (hash, timestamp, window_id), or None."""
        return self._pane_alert_hashes.get(pane_id)

    def set_pane_alert(
        self, pane_id: str, content_hash: str, timestamp: float, window_id: str
    ) -> None:
        """Record a pane alert entry."""
        self._pane_alert_hashes[pane_id] = (content_hash, timestamp, window_id)

    def remove_pane_alert(self, pane_id: str) -> None:
        """Remove a single pane alert entry."""
        self._pane_alert_hashes.pop(pane_id, None)

    def prune_stale_pane_alerts(self, window_id: str, live_pane_ids: set[str]) -> None:
        """Remove alerts for panes of a window that no longer exist."""
        stale = [
            pid
            for pid, v in self._pane_alert_hashes.items()
            if v[2] == window_id and pid not in live_pane_ids
        ]
        for pid in stale:
            self._pane_alert_hashes.pop(pid, None)

    def clear_pane_alerts(self, window_id: str) -> None:
        """Remove pane alert state for a specific window only."""
        stale = [pid for pid, v in self._pane_alert_hashes.items() if v[2] == window_id]
        for pid in stale:
            self._pane_alert_hashes.pop(pid, None)

    def clear_all_alerts(self) -> None:
        """Clear all pane alert state (for testing)."""
        self._pane_alert_hashes.clear()

# ── TopicLifecycleStrategy ──────────────────────────────────────────────

class TopicLifecycleStrategy:
    """Autoclose timers, dead notification tracking, probe failure state.

    Async lifecycle functions (for example check_autoclose_timers) live in
    topic_lifecycle.py; handle_dead_window_notification lives in window_tick.py.
    Both access state through this strategy.
    """

    def __init__(self, poll_state: TerminalPollState) -> None:
        self._poll_state = poll_state
        self._states: dict[tuple[int, int], TopicPollState] = {}
        self._dead_notified: set[tuple[int, int, str]] = set()

    def get_state(self, user_id: int, thread_id: int) -> TopicPollState:
        """Get or create TopicPollState for a topic."""
        return self._states.setdefault((user_id, thread_id), TopicPollState())

    def is_dead_notified(self, user_id: int, thread_id: int, window_id: str) -> bool:
        """Check if a dead notification was already sent for this topic/window."""
        return (user_id, thread_id, window_id) in self._dead_notified

    def mark_dead_notified(self, user_id: int, thread_id: int, window_id: str) -> None:
        """Record that a dead notification was sent."""
        self._dead_notified.add((user_id, thread_id, window_id))

    def iter_topic_states(self) -> list[tuple[int, int, TopicPollState]]:
        """Return list of (user_id, thread_id, state) for all tracked topics."""
        return [(uid, tid, ts) for (uid, tid), ts in self._states.items()]

    def clear_state(self, user_id: int, thread_id: int) -> None:
        """Remove all polling state for a topic."""
        self._states.pop((user_id, thread_id), None)

    def start_autoclose_timer(
        self, user_id: int, thread_id: int, state: str, now: float
    ) -> None:
        """Start or maintain an autoclose timer for done/dead state."""
        ts = self.get_state(user_id, thread_id)
        existing = ts.autoclose
        if existing is None or existing[0] != state:
            ts.autoclose = (state, now)

    def clear_autoclose_timer(self, user_id: int, thread_id: int) -> None:
        """Clear autoclose timer for a topic (on cleanup or when active)."""
        ts = self._states.get((user_id, thread_id))
        if ts:
            ts.autoclose = None

    def reset_autoclose_state(self) -> None:
        """Reset all autoclose tracking (for testing)."""
        for ts in self._states.values():
            ts.autoclose = None
        self._poll_state.reset_all_unbound_timers()

    def clear_dead_notification(self, user_id: int, thread_id: int) -> None:
        """Remove dead notification tracking for a topic."""
        self._dead_notified.difference_update(
            {k for k in self._dead_notified if k[0] == user_id and k[1] == thread_id}
        )

    def reset_dead_notification_state(self) -> None:
        """Reset all dead notification tracking (for testing)."""
        self._dead_notified.clear()

    def clear_probe_failures(self, window_id: str) -> None:
        """Reset probe failure counter for a window."""
        self._poll_state.reset_probe_failures(window_id)

    def clear_typing_state(self, user_id: int, thread_id: int) -> None:
        """Clear typing indicator throttle for a topic."""
        ts = self._states.get((user_id, thread_id))
        if ts:
            ts.last_typing_sent = None

    def reset_typing_state(self) -> None:
        """Reset all typing indicator tracking (for testing)."""
        for ts in self._states.values():
            ts.last_typing_sent = None

    def record_typing_sent(self, user_id: int, thread_id: int) -> None:
        """Stamp the current time as the last typing indicator sent."""
        self.get_state(user_id, thread_id).last_typing_sent = time.monotonic()

    def is_typing_throttled(self, user_id: int, thread_id: int) -> bool:
        """Check if typing indicator was sent too recently."""
        ts = self._states.get((user_id, thread_id))
        if not ts or ts.last_typing_sent is None:
            return False
        return (time.monotonic() - ts.last_typing_sent) < TYPING_INTERVAL

    def should_skip_probe(self, window_id: str) -> bool:
        """Check if a window has exceeded the probe failure threshold."""
        ws = self._poll_state.get_state(window_id)
        return ws.probe_failures >= MAX_PROBE_FAILURES

    def record_probe_failure(self, window_id: str) -> int:
        """Increment probe failure counter; log once when threshold is reached."""
        ws = self._poll_state.get_state(window_id)
        ws.probe_failures += 1
        count = ws.probe_failures
        if count == MAX_PROBE_FAILURES:
            logger.info(
                "Suspending topic probe for %s after %d consecutive failures",
                window_id,
                count,
            )
        return count


# ── Module-level strategy singletons ────────────────────────────────────

terminal_poll_state = TerminalPollState()
terminal_screen_buffer = TerminalScreenBuffer(terminal_poll_state)
interactive_strategy = InteractiveUIStrategy()
lifecycle_strategy = TopicLifecycleStrategy(terminal_poll_state)


def reset_window_polling_state(window_id: str) -> None:
    """Reset all per-window polling state in one call.

    Use after /clear, window restart, or any event that requires a clean
    slate for the next poll cycle. Callers should not call the individual
    strategy methods directly — this is the single reset contract.
    """
    terminal_poll_state.clear_seen_status(window_id)
    terminal_screen_buffer.clear_screen_buffer(window_id)
