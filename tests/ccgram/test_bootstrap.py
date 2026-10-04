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


async def test_bootstrap_starts_polling_before_miniapp() -> None:
    order: list[str] = []
    with (
        patch("ccgram.bootstrap.install_global_exception_handler"),
        patch("ccgram.bootstrap.session_manager") as manager,
        patch(
            "ccgram.bootstrap.start_status_polling",
            side_effect=lambda _app: order.append("polling"),
        ),
        patch(
            "ccgram.main.start_miniapp_if_enabled",
            new=AsyncMock(side_effect=lambda: order.append("miniapp")),
        ),
    ):
        manager.resolve_stale_ids = AsyncMock()
        await bootstrap.bootstrap_application(_app())
    assert order == ["polling", "miniapp"]


async def test_shutdown_cancels_polling_and_flushes_state() -> None:
    async def _noop() -> None:
        return None

    bootstrap._status_poll_task = asyncio.create_task(_noop())
    with (
        patch("ccgram.bootstrap.shutdown_workers", new_callable=AsyncMock),
        patch("ccgram.mailbox.Mailbox"),
        patch("ccgram.main.stop_miniapp_if_enabled", new_callable=AsyncMock),
        patch("ccgram.bootstrap.session_manager") as manager,
    ):
        await bootstrap.shutdown_runtime()
    assert bootstrap._status_poll_task is None
    manager.flush_state.assert_called_once()
