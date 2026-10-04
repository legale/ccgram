"""Contract test for synchronizing tmux sessions with ccgram poll topics."""

from dataclasses import dataclass

import pytest

from ccgram.tmux_poll_sync import TmuxPollSynchronizer


@dataclass
class FakePollThread:
    thread_id: int
    session_name: str


class FakeTmux:
    def __init__(self, sessions: list[str], captures: dict[str, str]) -> None:
        self.sessions = sessions
        self.captures = captures

    async def list_sessions(self) -> list[str]:
        return list(self.sessions)

    async def capture(self, session_name: str) -> str:
        return self.captures[session_name]


class FakeCcgram:
    def __init__(self, threads: list[FakePollThread]) -> None:
        self.threads = threads
        self.created: list[FakePollThread] = []
        self.deleted: list[int] = []
        self.messages: list[tuple[int, str]] = []
        self._next_thread_id = 100

    async def list_poll_threads(self) -> list[FakePollThread]:
        return list(self.threads)

    async def create_poll_thread(self, session_name: str) -> FakePollThread:
        thread = FakePollThread(self._next_thread_id, session_name)
        self._next_thread_id += 1
        self.threads.append(thread)
        self.created.append(thread)
        return thread

    async def delete_poll_thread(self, thread_id: int) -> None:
        self.threads[:] = [thread for thread in self.threads if thread.thread_id != thread_id]
        self.deleted.append(thread_id)

    async def send_poll_diff(self, thread_id: int, diff: str) -> None:
        self.messages.append((thread_id, diff))


@pytest.mark.asyncio
async def test_poll_sync_creates_reuses_diffs_and_removes_orphans() -> None:
    tmux = FakeTmux(
        sessions=["ccgram-alpha", "other"],
        captures={"ccgram-alpha": "before", "other": "ignored", "ccgram-gone": "gone"},
    )
    ccgram = FakeCcgram([FakePollThread(7, "ccgram-gone")])
    sync = TmuxPollSynchronizer(
        tmux=tmux,
        ccgram=ccgram,
        session_prefix="ccgram-",
    )

    await sync.poll_once()

    assert [thread.session_name for thread in ccgram.created] == ["ccgram-alpha"]
    assert ccgram.deleted == [7]
    assert ccgram.messages == []

    tmux.sessions = ["ccgram-alpha", "ccgram-beta"]
    tmux.captures["ccgram-alpha"] = "after\n"
    tmux.captures["ccgram-beta"] = "beta"

    await sync.poll_once()
    await sync.poll_once()

    assert [thread.session_name for thread in ccgram.created] == [
        "ccgram-alpha",
        "ccgram-beta",
    ]
    assert [thread_id for thread_id, _diff in ccgram.messages] == [100]
    assert "-before" in ccgram.messages[0][1]
    assert "+after" in ccgram.messages[0][1]
    assert ccgram.deleted == [7]
