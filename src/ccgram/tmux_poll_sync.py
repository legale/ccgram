"""Synchronize tmux session captures with ccgram poll threads."""

from __future__ import annotations

import difflib
from typing import Any


class TmuxPollSynchronizer:
    """Keep one ccgram poll thread and one capture baseline per tmux session."""

    def __init__(self, *, tmux: Any, ccgram: Any, session_prefix: str) -> None:
        self._tmux = tmux
        self._ccgram = ccgram
        self._session_prefix = session_prefix
        self._previous: dict[str, str] = {}

    async def poll_once(self) -> None:
        sessions = {
            name
            for name in await self._tmux.list_sessions()
            if name.startswith(self._session_prefix)
        }
        threads = await self._ccgram.list_poll_threads()
        by_session: dict[str, list[Any]] = {}
        for thread in threads:
            by_session.setdefault(thread.session_name, []).append(thread)

        for session_name, session_threads in by_session.items():
            if session_name not in sessions:
                for thread in session_threads:
                    await self._ccgram.delete_poll_thread(thread.thread_id)
                self._previous.pop(session_name, None)
                continue
            for duplicate in session_threads[1:]:
                await self._ccgram.delete_poll_thread(duplicate.thread_id)

        for session_name in sessions:
            session_threads = by_session.get(session_name, [])
            if not session_threads:
                thread = await self._ccgram.create_poll_thread(session_name)
            else:
                thread = session_threads[0]

            current = await self._tmux.capture(session_name)
            previous = self._previous.get(session_name)
            self._previous[session_name] = current
            if previous is None or previous == current:
                continue

            diff = "".join(
                difflib.unified_diff(
                    previous.splitlines(keepends=True),
                    current.splitlines(keepends=True),
                    fromfile="prev",
                    tofile="cur",
                )
            )
            if diff:
                await self._ccgram.send_poll_diff(thread.thread_id, diff)
