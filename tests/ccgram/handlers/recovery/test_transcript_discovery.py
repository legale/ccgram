from types import SimpleNamespace
from unittest.mock import MagicMock

from ccgram.providers.base import SessionStartEvent


async def test_codex_open_transcript_wins_over_cwd_discovery(monkeypatch) -> None:
    from ccgram.handlers.recovery import transcript_discovery as mod

    open_event = SessionStartEvent(
        session_id="open-session",
        cwd="/proj",
        transcript_path="/tmp/open.jsonl",
        window_key="ccgram:@7",
    )
    fallback_event = SessionStartEvent(
        session_id="fallback-session",
        cwd="/proj",
        transcript_path="/tmp/fallback.jsonl",
        window_key="ccgram:@7",
    )

    provider = MagicMock()
    provider.discover_transcript.return_value = fallback_event
    state = SimpleNamespace(
        session_id="",
        cwd="/proj",
        transcript_path="",
        provider_name="codex",
    )
    session_map_sync = MagicMock()

    monkeypatch.setattr(mod, "session_map_sync", session_map_sync)
    monkeypatch.setattr(mod.config, "tmux_session_name", "ccgram")
    monkeypatch.setattr(
        mod,
        "_discover_codex_open_transcript",
        lambda window_id, cwd, window_key: open_event,
    )

    await mod._find_and_register_transcript(
        "@7",
        state,
        [("codex", provider)],
        pane_alive=True,
    )

    provider.discover_transcript.assert_not_called()
    session_map_sync.register_hookless_session.assert_called_once_with(
        window_id="@7",
        session_id="open-session",
        cwd="/proj",
        transcript_path="/tmp/open.jsonl",
        provider_name="codex",
    )
    session_map_sync.write_hookless_session_map.assert_called_once_with(
        window_id="@7",
        session_id="open-session",
        cwd="/proj",
        transcript_path="/tmp/open.jsonl",
        provider_name="codex",
    )


async def test_codex_falls_back_to_cwd_discovery_when_no_open_transcript(
    monkeypatch,
) -> None:
    from ccgram.handlers.recovery import transcript_discovery as mod

    fallback_event = SessionStartEvent(
        session_id="fallback-session",
        cwd="/proj",
        transcript_path="/tmp/fallback.jsonl",
        window_key="ccgram:@7",
    )

    provider = MagicMock()
    provider.discover_transcript.return_value = fallback_event
    state = SimpleNamespace(
        session_id="",
        cwd="/proj",
        transcript_path="",
        provider_name="codex",
    )
    session_map_sync = MagicMock()

    monkeypatch.setattr(mod, "session_map_sync", session_map_sync)
    monkeypatch.setattr(mod.config, "tmux_session_name", "ccgram")
    monkeypatch.setattr(
        mod,
        "_discover_codex_open_transcript",
        lambda window_id, cwd, window_key: None,
    )

    await mod._find_and_register_transcript(
        "@7",
        state,
        [("codex", provider)],
        pane_alive=True,
    )

    provider.discover_transcript.assert_called_once_with(
        "/proj",
        "ccgram:@7",
        max_age=0,
    )
    session_map_sync.register_hookless_session.assert_called_once_with(
        window_id="@7",
        session_id="fallback-session",
        cwd="/proj",
        transcript_path="/tmp/fallback.jsonl",
        provider_name="codex",
    )
