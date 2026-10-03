"""Provider abstractions for multi-agent CLI backends.

Re-exports the protocol, event types, capability dataclass, and registry
so consumers can do ``from ccgram.providers import registry, ...``.
Also provides ``get_provider()`` for accessing the active provider singleton,
and ``resolve_capabilities()`` for lightweight CLI commands that don't
require Config (doctor, status).
"""

import os
import structlog

from ccgram.expandable_quote import EXPANDABLE_QUOTE_END, EXPANDABLE_QUOTE_START
from ccgram.providers.base import (
    AgentMessage,
    AgentProvider,
    DiscoveredCommand,
    ProviderCapabilities,
    SessionStartEvent,
    StatusUpdate,
)
from ccgram.providers.registry import ProviderRegistry, UnknownProviderError, registry

logger = structlog.get_logger()

# Launch-mode constants for per-session approval behavior.
_APPROVAL_MODE_NORMAL = "normal"
_APPROVAL_MODE_YOLO = "yolo"
_YOLO_FLAGS: dict[str, str] = {}


def has_yolo_mode(provider_name: str) -> bool:
    """Return True if the provider supports YOLO (permissive) launch mode."""
    return provider_name in _YOLO_FLAGS


# Singleton cache
_active: AgentProvider | None = None
_registered = False


def _ensure_registered() -> None:
    """Register all known providers into the global registry (once)."""
    global _registered
    if _registered:
        return
    # Lazy: provider classes register against the registry at import; defer until the registry factory runs
    from ccgram.providers.shell import ShellProvider

    registry.register("shell", ShellProvider)
    _registered = True


def get_provider() -> AgentProvider:
    """Return the active provider instance (lazy singleton).

    On first call, registers all providers into the global registry and
    resolves the provider name from config. Falls back to ``"shell"`` if
    the configured provider is unknown.
    """
    global _active
    if _active is None:
        _ensure_registered()

        # Lazy: config singleton is wired late at startup; importing at top
        # would freeze test overrides that monkeypatch config attrs.
        from ccgram.config import config

        try:
            _active = registry.get(config.provider_name)
        except UnknownProviderError:
            logger.warning(
                "Unknown provider %r, falling back to 'shell'",
                config.provider_name,
            )
            _active = registry.get("shell")
    return _active


def _reset_provider() -> None:
    """Reset the cached provider singleton (for tests only)."""
    global _active, _registered
    _active = None
    _registered = False


def get_provider_for_window(
    window_id: str,  # noqa: ARG001
    provider_name: str | None = None,
) -> AgentProvider:
    """Return the provider for a specific window, falling back to config default.

    Callers must supply *provider_name* (e.g. from ``window_query.get_window_provider``
    or ``view.provider_name``). When it is None or unknown, falls back to the
    config default provider.
    """
    _ensure_registered()

    if provider_name and registry.is_valid(provider_name):
        return registry.get(provider_name)
    return get_provider()


def detect_provider_from_command(pane_current_command: str) -> str:
    """Detect provider name from a tmux pane's running process.

    Returns "shell" for recognized shell binaries, or empty string.
    """
    cmd = pane_current_command.strip().lower()
    if not cmd:
        return ""

    basename = os.path.basename(cmd.split()[0])
    # Lazy: shell provider is the only one that needs KNOWN_SHELLS
    from .shell import KNOWN_SHELLS

    if basename in KNOWN_SHELLS or basename.lstrip("-") in KNOWN_SHELLS:
        return "shell"

    return ""


def detect_provider_from_transcript_path(transcript_path: str) -> str:  # noqa: ARG001
    """Infer provider name from a persisted transcript path when possible."""
    return ""


def should_probe_pane_title_for_provider_detection(
    pane_current_command: str,  # noqa: ARG001
) -> bool:
    """Return True when any provider needs pane-title context to detect runtime."""
    return False


_CCGRAM_TITLE_PREFIX = "ccgram:"


def detect_provider_from_runtime(
    pane_current_command: str,
    *,
    pane_title: str = "",
) -> str:
    """Detect provider from process name and optional pane-title hints."""
    detected = detect_provider_from_command(pane_current_command)
    if detected or not pane_title:
        return detected

    # Check for ccgram title stamp (set on launch via stamp_pane_title)
    if pane_title.startswith(_CCGRAM_TITLE_PREFIX):
        stamped = pane_title[len(_CCGRAM_TITLE_PREFIX) :].strip()
        _ensure_registered()
        if registry.is_valid(stamped):
            return stamped

    return ""


async def detect_provider_from_pane(
    pane_current_command: str,
    *,
    pane_tty: str = "",
    window_id: str = "",
) -> str:
    """Detect provider using command name and TTY process inspection."""
    detected = detect_provider_from_command(pane_current_command)
    if detected:
        return detected

    if pane_tty and pane_current_command:
        # Lazy: process_detection forks `ps` subprocesses; only load when needed
        from .process_detection import detect_provider_cached

        detected = await detect_provider_cached(window_id or "", pane_tty)
        if detected:
            return detected

    return ""


def resolve_launch_command(
    provider_name: str,
    *,
    approval_mode: str = _APPROVAL_MODE_NORMAL,  # noqa: ARG001
) -> str:
    """Resolve launch command for a provider.

    Resolution: ``CCGRAM_<NAME>_COMMAND`` if set, otherwise the provider's
    hardcoded default (``capabilities.launch_command``).
    Falls back to legacy ``CCBOT_<NAME>_COMMAND`` env var.
    """
    _ensure_registered()
    provider = provider_name.lower()
    new_env = f"CCGRAM_{provider.upper()}_COMMAND"
    old_env = f"CCBOT_{provider.upper()}_COMMAND"
    override = os.environ.get(new_env)
    if not override:
        override = os.environ.get(old_env)
        if override:
            logger.warning("%s is deprecated, use %s instead", old_env, new_env)
    if override:
        return override

    try:
        return registry.get(provider).capabilities.launch_command
    except UnknownProviderError:
        return registry.get("shell").capabilities.launch_command


def resolve_capabilities(provider_name: str | None = None) -> ProviderCapabilities:
    """Resolve provider capabilities without requiring full Config.

    Reads ``CCGRAM_PROVIDER`` (or legacy ``CCBOT_PROVIDER``) from env when
    *provider_name* is not given. Falls back to ``"shell"`` for unknown providers.
    """
    _ensure_registered()
    name = (
        provider_name
        if provider_name is not None
        else (
            os.environ.get("CCGRAM_PROVIDER")
            or os.environ.get("CCBOT_PROVIDER", "shell")
        )
    )
    try:
        return registry.get(name).capabilities
    except UnknownProviderError:
        return registry.get("shell").capabilities


__all__ = [
    "EXPANDABLE_QUOTE_END",
    "EXPANDABLE_QUOTE_START",
    "AgentMessage",
    "AgentProvider",
    "DiscoveredCommand",
    "ProviderCapabilities",
    "ProviderRegistry",
    "SessionStartEvent",
    "StatusUpdate",
    "UnknownProviderError",
    "detect_provider_from_command",
    "detect_provider_from_pane",
    "detect_provider_from_runtime",
    "detect_provider_from_transcript_path",
    "get_provider",
    "get_provider_for_window",
    "has_yolo_mode",
    "registry",
    "resolve_capabilities",
    "resolve_launch_command",
    "should_probe_pane_title_for_provider_detection",
]
