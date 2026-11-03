"""Direct file sender for the //send command.

Sends a file from the session's workspace or /tmp to Telegram.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from telegram import Update
from telegram.error import TelegramError

from ...config import config
from ...telegram_client import PTBTelegramClient, TelegramClient
from ...thread_router import thread_router
from ...tmux_manager import tmux_manager
from ..callback_helpers import get_thread_id
from ..command_meta import command
from ..messaging_pipeline.message_sender import safe_reply

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = structlog.get_logger()

_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp"}
)
_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB Telegram limit


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_EXTENSIONS


async def upload_file(
    client: TelegramClient,
    chat_id: int,
    thread_id: int | None,
    path: Path,
) -> None:
    """Send a file to Telegram (photo if image, document otherwise)."""
    with open(path, "rb") as fh:
        if _is_image(path):
            try:
                await client.send_photo(
                    chat_id=chat_id,
                    photo=fh,
                    caption=path.name,
                    message_thread_id=thread_id,
                    read_timeout=300,
                )
                return
            except TelegramError:
                fh.seek(0)
        await client.send_document(
            chat_id=chat_id,
            document=fh,
            filename=path.name,
            message_thread_id=thread_id,
            read_timeout=300,
        )


async def _resolve_session_cwd(
    user_id: int, thread_id: int
) -> tuple[str | None, Path | None]:
    window_id = thread_router.resolve_window_for_thread(user_id, thread_id)
    if not window_id:
        return None, None
    window = await tmux_manager.find_window_by_id(window_id)
    raw_cwd = window.cwd if window and window.cwd else ""
    if not raw_cwd:
        # Lazy: session_manager lookup
        from ...session import session_manager

        raw_cwd = session_manager.get_window_cwd(window_id)
    if not raw_cwd:
        return window_id, None
    path = Path(raw_cwd).resolve()
    return window_id, (path if path.is_dir() else None)


def _validate_file(target: Path, cwd: Path, file_arg: str) -> str | None:
    if not target.exists() or not target.is_file():
        return f"File not found: {file_arg}"
    tmp_root = Path("/tmp").resolve()
    if not target.is_relative_to(cwd) and not target.is_relative_to(tmp_root):
        return "Cannot send: file is outside session directory and /tmp."
    try:
        size = target.stat().st_size
        if size > _MAX_FILE_SIZE:
            mb = size // (1024 * 1024)
            return f"File is too large ({mb} MB). Limit is 50 MB."
    except OSError as e:
        return f"Cannot access file: {e}"
    return None


@command("send", "Send file from session directory to Telegram", params="<file>")
async def send_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle //send <relative_path> — send a file from session's CWD to Telegram."""
    if not update.message:
        return
    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        await safe_reply(update.message, "Not authorized.")
        return

    thread_id = get_thread_id(update)
    if thread_id is None:
        await safe_reply(update.message, "Use this command inside a topic.")
        return

    file_arg = " ".join(context.args or []).strip()
    if not file_arg:
        await safe_reply(update.message, "Usage: //send <path_to_file>")
        return

    window_id, cwd = await _resolve_session_cwd(user.id, thread_id)
    if not window_id:
        await safe_reply(update.message, "No session bound to this topic.")
        return
    if not cwd:
        await safe_reply(update.message, "Session working directory not available.")
        return

    target = (
        (cwd / file_arg).resolve()
        if not file_arg.startswith("/")
        else Path(file_arg).resolve()
    )
    error = _validate_file(target, cwd, file_arg)
    if error:
        await safe_reply(update.message, error)
        return

    chat_id = (
        thread_router.resolve_chat_id(user.id, thread_id) or update.message.chat_id
    )
    client = PTBTelegramClient(context.bot)
    try:
        await upload_file(client, chat_id, thread_id, target)
    except TelegramError as e:
        logger.exception("Failed to send file", path=str(target))
        await safe_reply(update.message, f"Failed to send file: {e}")


COMMAND_HANDLERS = (send_command,)

__all__ = [
    "COMMAND_HANDLERS",
    "send_command",
    "upload_file",
]
