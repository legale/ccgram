import asyncio

from ccgram.handlers.text.text_handler import (
    _bash_capture_tasks,
    cancel_bash_capture,
)


class TestCancelBashCapture:
    def test_clears_existing_task(self) -> None:
        async def _noop() -> None: ...

        task = asyncio.ensure_future(_noop())
        _bash_capture_tasks[(1, 42)] = task
        cancel_bash_capture(1, 42)
        assert (1, 42) not in _bash_capture_tasks

    def test_missing_key_no_error(self) -> None:
        _bash_capture_tasks.clear()
        cancel_bash_capture(999, 999)
