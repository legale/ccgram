import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from ccgram import bootstrap


def _app() -> MagicMock:
    app = MagicMock()
    app.bot = AsyncMock()
    return app


def test_reset_for_testing_clears_poll_task() -> None:
    bootstrap._status_poll_task = MagicMock()
    bootstrap.reset_for_testing()
    assert bootstrap._status_poll_task is None


async def test_bootstrap_starts_polling() -> None:
    order: list[str] = []
    with (
        patch("ccgram.bootstrap.install_global_exception_handler"),
        patch(
            "ccgram.bootstrap.start_status_polling",
            side_effect=lambda _app: order.append("polling"),
        ),
    ):
        await bootstrap.bootstrap_application(_app())
    assert order == ["polling"]


async def test_shutdown_cancels_polling() -> None:
    async def _noop() -> None:
        return None

    bootstrap._status_poll_task = asyncio.create_task(_noop())
    await bootstrap.shutdown_runtime()
    assert bootstrap._status_poll_task is None
