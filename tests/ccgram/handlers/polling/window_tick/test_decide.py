import time

import pytest

from ccgram.handlers.polling.polling_types import (
    STARTUP_TIMEOUT,
    StatusUpdate,
    TickContext,
)
from ccgram.handlers.polling.window_tick.decide import (
    build_status_line,
    decide_tick,
    is_shell_prompt,
)


def _make_ctx(
    *,
    window_id: str = "@0",
    resolved_status_text: str | None = None,
    is_shell_prompt: bool = False,
    has_seen_status: bool = False,
    is_recently_active: bool = False,
    startup_time: float | None = None,
    is_dead_window: bool = False,
    supports_hook: bool = True,
    notification_mode: str = "all",
) -> TickContext:
    return TickContext(
        window_id=window_id,
        resolved_status_text=resolved_status_text,
        is_shell_prompt=is_shell_prompt,
        has_seen_status=has_seen_status,
        is_recently_active=is_recently_active,
        startup_time=startup_time,
        is_dead_window=is_dead_window,
        supports_hook=supports_hook,
        notification_mode=notification_mode,
    )


class TestDecideTickDeadWindow:
    def test_dead_window_yields_recovery(self):
        ctx = _make_ctx(is_dead_window=True)
        decision = decide_tick(ctx)
        assert decision.show_recovery is True
        assert decision.transition is None

    def test_dead_window_overrides_other_signals(self):
        ctx = _make_ctx(
            is_dead_window=True,
            resolved_status_text="Working",
            is_recently_active=True,
            is_shell_prompt=True,
        )
        decision = decide_tick(ctx)
        assert decision.show_recovery is True


class TestDecideTickShellPrompt:

    def test_no_hook_provider_yields_idle(self):
        ctx = _make_ctx(is_shell_prompt=True, supports_hook=False)
        decision = decide_tick(ctx)
        assert decision.transition == "idle"


class TestDecideTickIdleAndStarting:
    def test_seen_status_with_no_signal_yields_idle(self):
        ctx = _make_ctx(has_seen_status=True)
        decision = decide_tick(ctx)
        assert decision.transition == "idle"

    def test_no_signal_no_startup_yields_idle(self):
        ctx = _make_ctx(startup_time=None)
        decision = decide_tick(ctx)
        assert decision.transition == "idle"

    def test_startup_within_grace_period_yields_idle(self):
        ctx = _make_ctx(startup_time=time.monotonic())
        decision = decide_tick(ctx)
        assert decision.transition == "idle"

    def test_startup_expired_yields_idle(self):
        old_start = time.monotonic() - STARTUP_TIMEOUT - 1.0
        ctx = _make_ctx(startup_time=old_start)
        decision = decide_tick(ctx)
        assert decision.transition == "idle"


class TestIsShellPrompt:
    @pytest.mark.parametrize(
        "command,expected",
        [
            ("bash", True),
            ("zsh", True),
            ("fish", True),
            ("sh", True),
            ("/bin/bash", True),
            ("/usr/local/bin/zsh", True),
            ("claude", False),
            ("codex", False),
            ("python3", False),
            ("", False),
        ],
        ids=[
            "bash",
            "zsh",
            "fish",
            "sh",
            "absolute_bash",
            "absolute_zsh",
            "claude",
            "codex",
            "python3",
            "empty",
        ],
    )
    def test_classification(self, command, expected):
        assert is_shell_prompt(command) is expected
