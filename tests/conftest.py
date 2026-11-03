"""Root conftest — sets env vars BEFORE any ccgram module is imported.

The config.py module-level singleton requires TELEGRAM_BOT_TOKEN and
ALLOWED_USERS at import time, so these must be set before pytest
discovers any test that transitively imports ccgram.
"""

import os
import tempfile

import pytest

# Force-set (not setdefault) to prevent real env vars from leaking into tests
os.environ["TELEGRAM_BOT_TOKEN"] = "test:0000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
os.environ["ALLOWED_USERS"] = "12345"
os.environ["CCGRAM_DIR"] = tempfile.mkdtemp(prefix="ccgram-test-")
for _key in (
    "CCGRAM_PROVIDER",
    "CCBOT_PROVIDER",
    "CCGRAM_INSTANCE_NAME",
    "CCGRAM_HIDE_TOOL_CALLS",
    "MONITOR_POLL_INTERVAL",
    "CCGRAM_STATUS_POLL_INTERVAL",
    "TMUX_SESSION_NAME",
):
    os.environ.pop(_key, None)


@pytest.fixture(autouse=True)
def _clear_session_cwds():
    from ccgram.session import session_manager

    session_manager._cwds.clear()
    yield
    session_manager._cwds.clear()



