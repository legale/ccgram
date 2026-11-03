from unittest.mock import MagicMock, patch

from ccgram.main import run_bot


def test_run_bot_does_not_create_control_tmux_session(monkeypatch) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_SESSION_NAME", raising=False)
    application = MagicMock()

    with (
        patch("ccgram.main.setup_logging"),
        patch("ccgram.main._install_signal_handlers"),
        patch("ccgram.main._reraise_shutdown_signal"),
        patch("ccgram.bot.create_bot", return_value=application),
        patch("ccgram.tmux_manager.TmuxManager.get_or_create_session") as create,
    ):
        run_bot()

    create.assert_not_called()
    application.run_polling.assert_called_once()
