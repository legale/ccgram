"""Application configuration — reads env vars and exposes a singleton.

Loads TELEGRAM_BOT_TOKEN, ALLOWED_USERS, tmux/Claude paths, and
monitoring intervals from environment variables (with .env support).
.env loading priority: local .env (cwd) > $CCGRAM_DIR/.env (default ~/.ccgram).
The module-level `config` instance is imported by nearly every other module.

Key class: Config (singleton instantiated as `config`).
"""

import structlog
import os
import socket
from pathlib import Path

from dotenv import load_dotenv

from .utils import ccgram_dir

logger = structlog.get_logger()


def _env_with_fallback(new_name: str, old_name: str, default: str = "") -> str:
    """Read env var with fallback to legacy CCBOT_* name."""
    val = os.getenv(new_name)
    if val is not None:
        return val
    val = os.getenv(old_name)
    if val is not None:
        logger.warning("%s is deprecated, use %s instead", old_name, new_name)
        return val
    return default


def _parse_int_env(name: str, default: int) -> int:
    """Parse an integer from an env var with a clear error on bad values."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid integer: {exc}") from exc


def _parse_required_int_env(name: str) -> int:
    """Parse a required integer environment variable without a code default."""
    raw = os.getenv(name)
    if raw is None:
        raise ValueError(f"{name} environment variable is required")
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid integer: {exc}") from exc


def _parse_bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes")


class Config:
    """Application configuration loaded from environment variables."""

    def __init__(self) -> None:  # noqa: PLR0915
        self.config_dir = ccgram_dir()
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # Load .env: local (cwd) takes priority over config_dir
        # load_dotenv default override=False means first-loaded wins
        for env_path in (Path(".env"), self.config_dir / ".env"):
            if env_path.is_file():
                load_dotenv(env_path)
                logger.debug("Loaded env from %s", env_path.resolve())

        # Load .env-default as fallback: cwd, config_dir, or project root
        repo_default = Path(__file__).resolve().parents[2] / ".env-default"
        for default_path in (
            Path(".env-default"),
            self.config_dir / ".env-default",
            repo_default,
        ):
            if default_path.is_file():
                load_dotenv(default_path, override=False)
                logger.debug("Loaded default env from %s", default_path.resolve())

        self.telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN") or ""
        if not self.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")

        allowed_users_str = os.getenv("ALLOWED_USERS", "")
        if not allowed_users_str:
            raise ValueError("ALLOWED_USERS environment variable is required")
        try:
            self.allowed_users: set[int] = {
                int(uid.strip()) for uid in allowed_users_str.split(",") if uid.strip()
            }
        except ValueError as e:
            raise ValueError(
                f"ALLOWED_USERS contains non-numeric value: {e}. "
                "Expected comma-separated Telegram user IDs."
            ) from e

        # Tmux session naming and screen size
        self.tmux_session_prefix = os.getenv("TMUX_SESSION_PREFIX", "cc_")
        self.tmux_session_name = os.getenv("TMUX_SESSION_NAME") or "ccgram"
        self.session_working_directory = os.getenv(
            "CCGRAM_SESSION_WORKING_DIRECTORY", "~"
        )
        self.tmux_screen_x = _parse_int_env(
            "TMUX_SCREEN_X", _parse_int_env("CCGRAM_TMUX_SCREEN_X", 80)
        )
        self.tmux_screen_y = _parse_int_env(
            "TMUX_SCREEN_Y", _parse_int_env("CCGRAM_TMUX_SCREEN_Y", 120)
        )
        self.tmux_main_window_name = "__main__"
        # Own tmux window ID (set by run_bot() after auto-detect, used to skip self in list_windows)
        self.own_window_id: str | None = None

        # All state files live under config_dir
        self.status_poll_interval = max(
            0.5, float(os.getenv("CCGRAM_STATUS_POLL_INTERVAL", "1.0"))
        )

        # Multi-instance support
        group_id_str = _env_with_fallback("CCGRAM_GROUP_ID", "CCBOT_GROUP_ID")
        if group_id_str:
            try:
                self.group_id: int | None = int(group_id_str)
            except ValueError as e:
                raise ValueError(f"CCGRAM_GROUP_ID must be a valid integer: {e}") from e
        else:
            self.group_id = None

        self.instance_name: str = (
            _env_with_fallback("CCGRAM_INSTANCE_NAME", "CCBOT_INSTANCE_NAME")
            or socket.gethostname()
        )

        # Ack reaction: react to forwarded messages with an emoji (empty = disabled)
        self.ack_reaction: str = _env_with_fallback(
            "CCGRAM_ACK_REACTION", "CCBOT_ACK_REACTION"
        )

        self._init_live_view()
        self._init_send()
        self._init_lifecycle()
        self._init_topic_status()
        self._init_screenshots()

        logger.debug(
            "Config initialized: dir=%s, token=%s..., allowed_users=%d, "
            "tmux_session=%s",
            self.config_dir,
            self.telegram_bot_token[:8],
            len(self.allowed_users),
            self.tmux_session_name,
        )

    def _init_live_view(self) -> None:
        self.live_view_interval: int = max(
            1, _parse_int_env("CCGRAM_LIVE_VIEW_INTERVAL", 5)
        )
        self.live_view_timeout: int = max(
            1, _parse_int_env("CCGRAM_LIVE_VIEW_TIMEOUT", 300)
        )
        self.live_view_limit: int = max(
            100, _parse_int_env("CCGRAM_LIVE_VIEW_LIMIT", 2500)
        )

    def _init_send(self) -> None:
        self.file_size_limit_mb: int = max(
            1, _parse_int_env("CCGRAM_FILE_SIZE_LIMIT_MB", 1024)
        )

    def _init_lifecycle(self) -> None:
        self.autoclose_done_minutes: int = int(
            os.getenv("AUTOCLOSE_DONE_MINUTES", "30")
        )
        self.autoclose_dead_minutes: int = int(
            os.getenv("AUTOCLOSE_DEAD_MINUTES", "10")
        )

    def _init_topic_status(self) -> None:
        self.topic_status_diff_enabled: bool = _parse_bool_env(
            "CCGRAM_TOPIC_STATUS_DIFF_ENABLED", True
        )
        self.topic_status_diff_interval: int = max(
            1, _parse_int_env("CCGRAM_TOPIC_STATUS_DIFF_INTERVAL", 3)
        )
        self.screen_diff_limit: int = max(
            100, _parse_required_int_env("CCGRAM_SCREEN_DIFF_LIMIT")
        )
        self.topic_idle_enabled: bool = _parse_bool_env(
            "CCGRAM_TOPIC_IDLE_ENABLED", True
        )
        self.topic_idle_delay: int = max(
            1, _parse_int_env("CCGRAM_TOPIC_IDLE_DELAY", 10)
        )
        self.topic_idle_text: str = os.getenv("CCGRAM_TOPIC_IDLE_TEXT", "idle")

    def _init_screenshots(self) -> None:
        self.screenshot_font_size: int = max(
            8, _parse_int_env("CCGRAM_SCREENSHOT_FONT_SIZE", 15)
        )
        self.screenshot_palette_colors: int = max(
            0, _parse_int_env("CCGRAM_SCREENSHOT_PALETTE_COLORS", 64)
        )
        self.screenshot_as_photo: bool = _parse_bool_env(
            "CCGRAM_SCREENSHOT_AS_PHOTO", True
        )

    def is_user_allowed(self, user_id: int) -> bool:
        """Check if a user is in the allowed list."""
        return user_id in self.allowed_users


config = Config()
