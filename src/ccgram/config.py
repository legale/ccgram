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


def _parse_bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes")


def _resolve_toolbar_path() -> str:
    """Resolve the toolbar TOML config path: env var → ~/.ccgram → empty.

    Order:
      1. ``$CCGRAM_TOOLBAR_CONFIG`` if set (used as-is, even if missing)
      2. ``~/.ccgram/toolbar.toml`` if it exists
      3. ``""`` (use built-in defaults)
    """
    env = os.getenv("CCGRAM_TOOLBAR_CONFIG", "").strip()
    if env:
        return env
    fallback = ccgram_dir() / "toolbar.toml"
    return str(fallback) if fallback.exists() else ""


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
        self.state_file = self.config_dir / "state.json"
        self.mailbox_dir = self.config_dir / "mailbox"
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

        # LLM command generation (shell provider) and toolbar config path.
        # toolbar_config_path resolution: env var → ~/.ccgram/toolbar.toml → "".
        # Empty string means "use built-in defaults". The handler layer passes
        # this path to ``toolbar_config.load_toolbar_config()`` once at startup.
        self._init_shell_and_llm()
        self._init_messaging()
        self._init_live_view()
        self._init_send()
        self._init_lifecycle()
        self._init_topic_status()

        # Global default for hiding tool_use/tool_result content in Telegram.
        # Per-window override via WindowState.tool_call_visibility takes precedence.
        self.hide_tool_calls: bool = _env_with_fallback(
            "CCGRAM_HIDE_TOOL_CALLS", "HIDE_TOOL_CALLS", "true"
        ).lower() in ("1", "true", "yes")

        logger.debug(
            "Config initialized: dir=%s, token=%s..., allowed_users=%d, "
            "tmux_session=%s",
            self.config_dir,
            self.telegram_bot_token[:8],
            len(self.allowed_users),
            self.tmux_session_name,
        )

    def _init_messaging(self) -> None:
        self.msg_auto_spawn: bool = os.getenv("CCGRAM_MSG_AUTO_SPAWN", "").lower() in (
            "1",
            "true",
            "yes",
        )
        self.msg_max_windows: int = _parse_int_env("CCGRAM_MSG_MAX_WINDOWS", 10)
        self.msg_wait_timeout: int = _parse_int_env("CCGRAM_MSG_WAIT_TIMEOUT", 60)
        self.msg_spawn_timeout: int = _parse_int_env("CCGRAM_MSG_SPAWN_TIMEOUT", 300)
        self.msg_spawn_rate: int = _parse_int_env("CCGRAM_MSG_SPAWN_RATE", 3)
        self.msg_rate_limit: int = _parse_int_env("CCGRAM_MSG_RATE_LIMIT", 10)

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

    def _init_shell_and_llm(self) -> None:
        self.prompt_mode = os.getenv("CCGRAM_PROMPT_MODE", "wrap")
        self.prompt_marker = os.getenv("CCGRAM_PROMPT_MARKER", "ccgram")
        self.toolbar_config_path: str = _resolve_toolbar_path()
        self.llm_provider: str = os.getenv("CCGRAM_LLM_PROVIDER", "")
        self.llm_api_key: str = os.getenv("CCGRAM_LLM_API_KEY", "")
        self.llm_base_url: str = os.getenv("CCGRAM_LLM_BASE_URL", "")
        self.llm_model: str = os.getenv("CCGRAM_LLM_MODEL", "")
        try:
            self.llm_temperature: float = float(
                os.getenv("CCGRAM_LLM_TEMPERATURE", "0.1")
            )
        except ValueError as e:
            raise ValueError(
                f"CCGRAM_LLM_TEMPERATURE must be a valid number: {e}"
            ) from e

    def _init_send(self) -> None:
        self.send_search_depth: int = _parse_int_env("CCGRAM_SEND_SEARCH_DEPTH", 5)
        self.send_max_results: int = _parse_int_env("CCGRAM_SEND_MAX_RESULTS", 50)
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
        self._init_miniapp()

    def _init_topic_status(self) -> None:
        self.topic_status_diff_enabled: bool = _parse_bool_env(
            "CCGRAM_TOPIC_STATUS_DIFF_ENABLED", True
        )
        self.topic_status_diff_interval: int = max(
            1, _parse_int_env("CCGRAM_TOPIC_STATUS_DIFF_INTERVAL", 3)
        )
        self.screen_diff_limit: int = max(
            100, _parse_int_env("CCGRAM_SCREEN_DIFF_LIMIT", 2000)
        )
        self.topic_idle_enabled: bool = _parse_bool_env(
            "CCGRAM_TOPIC_IDLE_ENABLED", True
        )
        self.topic_idle_delay: int = max(
            1, _parse_int_env("CCGRAM_TOPIC_IDLE_DELAY", 10)
        )
        self.topic_idle_text: str = os.getenv("CCGRAM_TOPIC_IDLE_TEXT", "idle")

    def _init_miniapp(self) -> None:
        # Mini App backend (Phase 3 / Theme 6) — disabled when base URL is empty.
        # base_url is the externally reachable URL Telegram uses to open the
        # WebApp; host/port control the local aiohttp listener.
        self.miniapp_base_url: str = os.getenv("CCGRAM_MINIAPP_BASE_URL", "").strip()
        self.miniapp_host: str = os.getenv("CCGRAM_MINIAPP_HOST", "127.0.0.1")
        self.miniapp_port: int = _parse_int_env("CCGRAM_MINIAPP_PORT", 8765)

    def is_user_allowed(self, user_id: int) -> bool:
        """Check if a user is in the allowed list."""
        return user_id in self.allowed_users


config = Config()
