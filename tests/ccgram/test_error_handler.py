"""Tests for bot-level error handler and signal diagnostics."""

import contextlib
import io
import signal
from typing import Any
from unittest.mock import MagicMock, patch

from telegram.error import BadRequest, Conflict, NetworkError, TelegramError

from ccgram.bot import _error_handler


def _make_context(error: BaseException) -> MagicMock:
    ctx = MagicMock()
    ctx.error = error
    return ctx


class TestErrorHandlerNetworkError:
    async def test_network_error_logged_as_info(self) -> None:
        ctx = _make_context(NetworkError("httpx.ConnectError:"))

        with patch("ccgram.bot.logger") as mock_logger:
            await _error_handler(None, ctx)

        mock_logger.info.assert_called_once()
        mock_logger.warning.assert_not_called()
        mock_logger.error.assert_not_called()


class TestErrorHandlerStaleCallback:
    async def test_bad_request_query_too_old_is_debug_not_error(self) -> None:
        ctx = _make_context(BadRequest("Query is too old and response timeout expired"))

        with patch("ccgram.bot.logger") as mock_logger:
            await _error_handler(None, ctx)

        mock_logger.debug.assert_called_once()
        assert "expired" in mock_logger.debug.call_args[0][0]
        mock_logger.error.assert_not_called()

    async def test_bad_request_query_id_invalid_is_debug(self) -> None:
        ctx = _make_context(BadRequest("query id is invalid and too old"))

        with patch("ccgram.bot.logger") as mock_logger:
            await _error_handler(None, ctx)

        mock_logger.debug.assert_called_once()
        mock_logger.error.assert_not_called()

    async def test_other_bad_request_still_logged_as_error(self) -> None:
        ctx = _make_context(BadRequest("Chat not found"))

        with patch("ccgram.bot.logger") as mock_logger:
            await _error_handler(None, ctx)

        mock_logger.error.assert_called_once()
        mock_logger.debug.assert_not_called()

    async def test_other_telegram_error_logged_as_error(self) -> None:
        ctx = _make_context(TelegramError("Network timeout"))

        with patch("ccgram.bot.logger") as mock_logger:
            await _error_handler(None, ctx)

        mock_logger.error.assert_called_once()

    async def test_conflict_triggers_shutdown(self) -> None:
        ctx = _make_context(Conflict("409 Conflict"))

        with (
            patch("ccgram.bot.logger"),
            patch("ccgram.bot.os.kill") as mock_kill,
        ):
            await _error_handler(None, ctx)

        mock_kill.assert_called_once()


def _get_signal_handler() -> Any:
    """Install signal handlers and return the SIGINT handler as a callable."""
    from ccgram.main import _install_signal_handlers

    _install_signal_handlers()
    handler = signal.getsignal(signal.SIGINT)
    assert callable(handler)
    return handler


class TestSignalDiagnostics:
    def test_signal_handler_logs_stack_in_debug(self) -> None:
        handler = _get_signal_handler()

        stderr_capture = io.StringIO()
        with (
            patch("ccgram.main.logging.getLogger") as mock_get_logger,
            patch("sys.stderr", stderr_capture),
        ):
            mock_get_logger.return_value.isEnabledFor.return_value = True
            with contextlib.suppress(SystemExit):
                handler(signal.SIGINT, None)

        output = stderr_capture.getvalue()
        assert "SIGINT" in output

    def test_signal_handler_minimal_in_info(self) -> None:
        handler = _get_signal_handler()

        stderr_capture = io.StringIO()
        with (
            patch("ccgram.main.logging.getLogger") as mock_get_logger,
            patch("sys.stderr", stderr_capture),
        ):
            mock_get_logger.return_value.isEnabledFor.return_value = False
            with contextlib.suppress(SystemExit):
                handler(signal.SIGINT, None)

        output = stderr_capture.getvalue()
        assert "SIGINT" in output
        assert "call stack" not in output
