from unittest.mock import patch

import pytest

from ccgram.providers.base import ProviderCapabilities
from ccgram.providers.registry import ProviderRegistry, UnknownProviderError
from test_contracts import StubProvider as _StubProvider


class TestProviderRegistry:
    def test_register_and_get(self) -> None:
        reg = ProviderRegistry()
        reg.register("stub", _StubProvider)
        provider = reg.get("stub")
        assert provider.capabilities.name == "stub"

    def test_get_unknown_raises(self) -> None:
        reg = ProviderRegistry()
        with pytest.raises(UnknownProviderError, match="nope"):
            reg.get("nope")

    def test_register_overwrites(self) -> None:
        class _OtherProvider(_StubProvider):
            _CAPS = ProviderCapabilities(name="other", launch_command="other-cli")

        reg = ProviderRegistry()
        reg.register("stub", _StubProvider)
        reg.register("stub", _OtherProvider)
        assert reg.get("stub").capabilities.name == "other"

    def test_get_caches_instance_per_name(self) -> None:
        reg = ProviderRegistry()
        reg.register("stub", _StubProvider)
        a = reg.get("stub")
        b = reg.get("stub")
        assert a is b

    def test_re_register_invalidates_cache(self) -> None:
        reg = ProviderRegistry()
        reg.register("stub", _StubProvider)
        a = reg.get("stub")
        reg.register("stub", _StubProvider)
        b = reg.get("stub")
        assert a is not b

    def test_error_message_lists_available(self) -> None:
        reg = ProviderRegistry()
        reg.register("alpha", _StubProvider)
        reg.register("bravo", _StubProvider)
        with pytest.raises(UnknownProviderError, match="alpha, bravo"):
            reg.get("missing")


class TestConfigProviderSettings:
    def test_default_provider_name(self) -> None:
        env = {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "ALLOWED_USERS": "123",
            "HOME": "/tmp",
        }
        with patch.dict("os.environ", env, clear=True):
            from ccgram.config import Config

            cfg = Config()
            assert cfg.provider_name == "shell"

    def test_override_provider_via_env(self) -> None:
        env = {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "ALLOWED_USERS": "123",
            "HOME": "/tmp",
            "CCGRAM_PROVIDER": "custom",
        }
        with patch.dict("os.environ", env, clear=True):
            from ccgram.config import Config

            cfg = Config()
            assert cfg.provider_name == "custom"


class TestResolveLaunchCommand:
    @pytest.fixture(autouse=True)
    def _reset(self):
        from ccgram.providers import _reset_provider

        _reset_provider()
        yield
        _reset_provider()

    def test_default_returns_shell_command(self) -> None:
        from ccgram.providers import resolve_launch_command

        assert resolve_launch_command("shell") == ""

    def test_per_provider_env_override(self, monkeypatch) -> None:
        from ccgram.providers import resolve_launch_command

        monkeypatch.setenv("CCGRAM_SHELL_COMMAND", "/bin/bash -l")
        assert resolve_launch_command("shell") == "/bin/bash -l"

    def test_unknown_provider_falls_back_to_shell_default(self) -> None:
        from ccgram.providers import resolve_launch_command

        assert resolve_launch_command("nonexistent") == ""


class TestModuleLevelRegistry:
    def test_singleton_exists_with_shell(self, monkeypatch) -> None:
        from ccgram.providers import _reset_provider, get_provider, registry

        _reset_provider()
        try:
            get_provider()
            assert isinstance(registry, ProviderRegistry)
            assert "shell" in sorted(registry._providers)
        finally:
            _reset_provider()

    def test_unknown_provider_falls_back_to_shell(self, monkeypatch) -> None:
        from ccgram.providers import _reset_provider, get_provider

        _reset_provider()
        monkeypatch.setenv("CCGRAM_PROVIDER", "doesnotexist")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
        monkeypatch.setenv("ALLOWED_USERS", "123")
        try:
            provider = get_provider()
            assert provider.capabilities.name == "shell"
        finally:
            _reset_provider()

    def test_resolve_capabilities_unknown_falls_back(self) -> None:
        from ccgram.providers import _reset_provider, resolve_capabilities

        _reset_provider()
        try:
            caps = resolve_capabilities("nonexistent")
            assert caps.name == "shell"
        finally:
            _reset_provider()


class TestRegistryIsValid:
    def test_valid_name(self) -> None:
        reg = ProviderRegistry()
        reg.register("stub", _StubProvider)
        assert reg.is_valid("stub") is True

    def test_invalid_name(self) -> None:
        reg = ProviderRegistry()
        assert reg.is_valid("nonexistent") is False


class TestEnsureRegistered:
    @pytest.fixture(autouse=True)
    def _reset(self):
        from ccgram.providers import _reset_provider

        _reset_provider()
        yield
        _reset_provider()

    def test_shell_registered(self) -> None:
        from ccgram.providers import _ensure_registered, registry

        _ensure_registered()
        assert registry.is_valid("shell"), "Provider 'shell' not registered"


class TestGetProviderForWindow:
    @pytest.fixture(autouse=True)
    def _reset(self):
        from ccgram.providers import _reset_provider

        _reset_provider()
        yield
        _reset_provider()

    def test_returns_window_specific_provider(self, monkeypatch) -> None:
        from ccgram.providers import get_provider_for_window

        provider = get_provider_for_window("@1", provider_name="shell")
        assert provider.capabilities.name == "shell"

    def test_falls_back_to_global_when_empty(self, monkeypatch) -> None:
        from ccgram.providers import get_provider_for_window

        provider = get_provider_for_window("@2", provider_name="")
        assert provider.capabilities.name == "shell"

    def test_falls_back_when_window_not_in_state(self, monkeypatch) -> None:
        from ccgram.providers import get_provider_for_window

        provider = get_provider_for_window("@999", provider_name=None)
        assert provider.capabilities.name == "shell"

    def test_falls_back_on_invalid_provider_name(self, monkeypatch) -> None:
        from ccgram.providers import get_provider_for_window

        provider = get_provider_for_window("@3", provider_name="nonexistent")
        assert provider.capabilities.name == "shell"
