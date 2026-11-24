"""Shared fixtures for ccgram unit tests."""

import os

import pytest


@pytest.fixture(autouse=True)
def _clean_proxy_env(monkeypatch):
    """Remove proxy env vars that cause PTB's Application builder to fail.

    PTB auto-detects socks proxies from all_proxy/ftp_proxy/etc. and tries to
    import httpx[socks], which may not be installed in the test environment.
    """
    for key in list(os.environ):
        lower = key.lower()
        if "proxy" in lower and key not in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(key, raising=False)
