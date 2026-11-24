"""Photo and document message handlers for forwarding uploaded files to tmux.

Saves uploaded files to `.ccgram-uploads/` in the session's cwd, then sends
the saved file path (plus optional sanitized caption) directly to the tmux session.

Key handlers:
  - handle_photo_message: handles filters.PHOTO
  - handle_document_message: handles filters.Document.ALL
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from telegram import Message, Update
from telegram.constants import ChatAction
from telegram.error import TelegramError

from ..config import config
from ..thread_router import thread_router
from ..tmux_manager import send_to_window, tmux_manager
from .callback_helpers import get_thread_id
from .messaging_pipeline.message_sender import safe_reply

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = structlog.get_logger()

# Upload directory name inside session cwd
_UPLOAD_DIR = ".ccgram-uploads"

# Max filename length after sanitization
_MAX_FILENAME_LEN = 200

# Pattern for allowed filename characters
_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._-]")

# Control characters to strip from captions (keep \n and \t)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Max caption length forwarded to tmux
_MAX_CAPTION_LEN = 500


def _max_file_size_bytes() -> int:
    return config.file_size_limit_mb * 1024 * 1024


def _sanitize_filename(name: str) -> str:
    """Sanitize a filename: allow a-zA-Z0-9._-, reject path traversal."""
    name = Path(name).name
    name = _SAFE_FILENAME_RE.sub("_", name)
    if not name.strip("."):
        name = "unnamed"
    if len(name) > _MAX_FILENAME_LEN:
        suffix = Path(name).suffix
        if len(suffix) >= _MAX_FILENAME_LEN:
            suffix = suffix[:10]
        stem = Path(name).stem[: _MAX_FILENAME_LEN - len(suffix)]
        name = stem + suffix
    return name or "unnamed"


def _sanitize_caption(text: str) -> str:
    """Strip control characters, collapse newlines to spaces, and limit length."""
    cleaned = _CONTROL_CHAR_RE.sub("", text)
    cleaned = cleaned.replace("\n", " ").replace("\r", " ")
    return cleaned[:_MAX_CAPTION_LEN]


def _validate_dest_path(dest: Path, upload_path: Path) -> bool:
    """Ensure dest resolves within upload_path (path traversal guard)."""
    try:
        dest.resolve().relative_to(upload_path.resolve())
        return True
    except (ValueError, OSError):  # fmt: skip
        return False


def _unique_dest(dest: Path) -> Path:
    """Return a unique path by appending _1, _2, etc. if dest already exists."""
    if not dest.exists() and not dest.is_symlink():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    parent = dest.parent
    for i in range(1, 100):
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
    ts = datetime.now(tz=timezone.utc).strftime("%H%M%S%f")
    return parent / f"{stem}_{ts}{suffix}"


def _generate_photo_filename(file_unique_id: str) -> str:
    """Generate a photo filename: photo_YYYYMMDD_HHMMSS_<8chars>.jpg."""
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    short_id = file_unique_id[:8]
    return f"photo_{timestamp}_{short_id}.jpg"


async def _resolve_upload_dir(
    user_id: int, thread_id: int | None, chat_id: int | None = None
) -> tuple[str | None, Path | None, str | None]:
    """Resolve window_id and upload directory for a thread.

    Returns (window_id, upload_path, error_message).
    """
    window_id = thread_router.resolve_window_for_thread(user_id, thread_id)
    if not window_id and thread_id is not None:
        # Lazy: avoid circular import with topic_binding
        from .topics.topic_binding import resolve_topic_window

        resolved_chat = (
            chat_id
            if chat_id is not None
            else thread_router.resolve_chat_id(user_id, thread_id)
        )
        window_id = await resolve_topic_window(user_id, resolved_chat, thread_id)
    if not window_id:
        return None, None, "No session bound to this topic."

    window = await tmux_manager.find_window_by_id(window_id)
    cwd = (
        str(Path(window.cwd).expanduser().resolve()) if window and window.cwd else None
    )
    if not cwd:
        return window_id, None, "Session has no working directory."

    upload_path = Path(cwd) / _UPLOAD_DIR
    return window_id, upload_path, None


async def _download_and_save(
    message: Message,
    upload_path: Path,
    filename: str,
    file_id: str,
    file_size: int | None,
    size_label: str,
) -> str | None:
    """Download a Telegram file and save it to the session upload directory.

    Returns the final filename on success, or None on failure (error already
    replied to the user).
    """
    max_file_size = _max_file_size_bytes()
    if file_size is not None and file_size > max_file_size:
        size_mb = file_size / (1024 * 1024)
        await safe_reply(
            message,
            f"{size_label} too large ({size_mb:.1f} MB). Maximum {config.file_size_limit_mb} MB.",
        )
        return None

    try:
        upload_path.mkdir(parents=True, exist_ok=True)
        if upload_path.is_symlink():
            logger.error("Upload dir is a symlink: %s", upload_path)
            await safe_reply(message, "Upload directory is invalid.")
            return None
        dest = upload_path / filename
        if not _validate_dest_path(dest, upload_path):
            logger.error("Path traversal attempt blocked: %s", filename)
            await safe_reply(message, "Invalid filename.")
            return None
        dest = _unique_dest(dest)
        filename = dest.name
        file = await message.get_bot().get_file(file_id)
        await file.download_to_drive(str(dest))
        actual_size = dest.stat().st_size
        if actual_size > max_file_size:
            dest.unlink(missing_ok=True)
            size_mb = actual_size / (1024 * 1024)
            await safe_reply(
                message,
                f"{size_label} too large ({size_mb:.1f} MB). Maximum {config.file_size_limit_mb} MB.",
            )
            return None
    except (OSError, TelegramError) as e:
        logger.error("Failed to save %s: %s", size_label.lower(), e)
        await safe_reply(message, "Failed to save file.")
        return None

    return filename


async def _upload_and_notify(
    message: Message,
    user_id: int,
    thread_id: int | None,
    filename: str,
    file_id: str,
    file_size: int | None,
    size_label: str,
) -> None:
    """Shared upload flow: resolve dir, download, send file path to tmux."""
    chat_id = message.chat.id if message.chat else None
    window_id, upload_path, error = await _resolve_upload_dir(
        user_id, thread_id, chat_id
    )
    if error or not window_id or not upload_path:
        await safe_reply(message, error or "Upload failed.")
        return

    await message.chat.send_action(ChatAction.TYPING)

    saved_name = await _download_and_save(
        message, upload_path, filename, file_id, file_size, size_label
    )
    if not saved_name:
        return

    rel_path = f"{_UPLOAD_DIR}/{saved_name}"
    dest_path = str(upload_path / saved_name)
    caption = _sanitize_caption(message.caption).strip() if message.caption else ""
    tmux_input = f"{dest_path} {caption}" if caption else dest_path

    success, err = await send_to_window(window_id, tmux_input)
    if success:
        await safe_reply(message, f"Uploaded `{rel_path}`")
    else:
        await safe_reply(message, f"File saved, failed to send path to tmux: {err}")


async def handle_photo_message(
    update: Update, _context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle photo uploads: save to .ccgram-uploads/ and send path to tmux."""
    user = update.effective_user
    message = update.message
    if not user or not message or not message.photo:
        return
    if not config.is_user_allowed(user.id):
        await safe_reply(message, "You are not authorized to use this bot.")
        return

    photo = message.photo[-1]
    await _upload_and_notify(
        message,
        user.id,
        get_thread_id(update),
        _generate_photo_filename(photo.file_unique_id),
        photo.file_id,
        photo.file_size,
        "Photo",
    )


async def handle_document_message(
    update: Update, _context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle document uploads: save to .ccgram-uploads/ and send path to tmux."""
    user = update.effective_user
    message = update.message
    if not user or not message or not message.document:
        return
    if not config.is_user_allowed(user.id):
        await safe_reply(message, "You are not authorized to use this bot.")
        return

    doc = message.document
    await _upload_and_notify(
        message,
        user.id,
        get_thread_id(update),
        _sanitize_filename(doc.file_name or "document"),
        doc.file_id,
        doc.file_size,
        "File",
    )
