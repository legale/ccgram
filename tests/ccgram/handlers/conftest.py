"""Handler test fixtures — short-circuit slow real waits."""

from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def _instant_session_map_wait(monkeypatch):
    from ccgram.session_map import session_map_sync

    monkeypatch.setattr(
        session_map_sync,
        "wait_for_session_map_entry",
        AsyncMock(return_value=True),
    )


@pytest.fixture(autouse=True)
def _disable_send_rate_limit(monkeypatch):
    """Zero out MESSAGE_SEND_INTERVAL so back-to-back sends don't sleep.

    Tests in TestRateLimitSend re-patch the interval inline when they
    need to assert on the wait calculation.
    """
    monkeypatch.setattr(
        "ccgram.handlers.messaging_pipeline.message_sender.MESSAGE_SEND_INTERVAL", 0
    )
