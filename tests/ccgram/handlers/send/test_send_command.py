from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Message, Update, User

from ccgram.handlers.send.send_command import send_command, upload_file
from ccgram.tmux_manager import TmuxWindow
from tests.ccgram._helpers import FakeTelegramClient


def _make_update(user_id: int = 1, thread_id: int | None = 42) -> tuple[Update, Message, MagicMock]:
    update = MagicMock(spec=Update)
    user = User(id=user_id, is_bot=False, first_name="Test")
    update.effective_user = user
    msg = AsyncMock(spec=Message)
    msg.message_thread_id = thread_id
    msg.chat_id = -100
    update.message = msg
    update.effective_message = msg

    context = MagicMock()
    context.bot = AsyncMock()
    context.args = []
    return update, msg, context


class TestSendCommand:
    async def test_no_args_shows_usage(self):
        update, msg, context = _make_update()
        context.args = []

        with (
            patch("ccgram.config.config.is_user_allowed", return_value=True),
            patch("ccgram.handlers.send.send_command.safe_reply") as mock_reply,
        ):
            await send_command(update, context)

        mock_reply.assert_awaited_once_with(msg, "Usage: //send <path_to_file>")

    async def test_outside_topic_rejected(self):
        update, msg, context = _make_update(thread_id=None)
        context.args = ["foo.txt"]

        with (
            patch("ccgram.config.config.is_user_allowed", return_value=True),
            patch("ccgram.handlers.send.send_command.safe_reply") as mock_reply,
        ):
            await send_command(update, context)

        mock_reply.assert_awaited_once_with(msg, "Use this command inside a topic.")

    async def test_unauthorized_rejected(self):
        update, msg, context = _make_update(user_id=999)
        context.args = ["foo.txt"]

        with (
            patch("ccgram.config.config.is_user_allowed", return_value=False),
            patch("ccgram.handlers.send.send_command.safe_reply") as mock_reply,
        ):
            await send_command(update, context)

        mock_reply.assert_awaited_once_with(msg, "Not authorized.")

    async def test_unbound_topic_rejected(self):
        update, msg, context = _make_update()
        context.args = ["foo.txt"]

        with (
            patch("ccgram.config.config.is_user_allowed", return_value=True),
            patch("ccgram.handlers.send.send_command.thread_router.resolve_window_for_thread", return_value=None),
            patch("ccgram.handlers.send.send_command.safe_reply") as mock_reply,
        ):
            await send_command(update, context)

        mock_reply.assert_awaited_once_with(msg, "No session bound to this topic.")

    async def test_file_not_found(self, tmp_path: Path):
        update, msg, context = _make_update()
        context.args = ["nonexistent.txt"]
        window = TmuxWindow("s1:@1", "s1", cwd=str(tmp_path))

        with (
            patch("ccgram.config.config.is_user_allowed", return_value=True),
            patch("ccgram.handlers.send.send_command.thread_router.resolve_window_for_thread", return_value="s1:@1"),
            patch("ccgram.handlers.send.send_command.tmux_manager.find_window_by_id", new_callable=AsyncMock, return_value=window),
            patch("ccgram.handlers.send.send_command.safe_reply") as mock_reply,
        ):
            await send_command(update, context)

        mock_reply.assert_awaited_once_with(msg, "File not found: nonexistent.txt")

    async def test_outside_directory_rejected(self, tmp_path: Path):
        update, msg, context = _make_update()
        # File is in home or etc, outside cwd and /tmp
        outside_file = Path("/etc/hosts")
        if not outside_file.exists():
            outside_file = tmp_path.parent / "outside.txt"
            outside_file.write_text("hello")

        context.args = [str(outside_file)]
        session_cwd = tmp_path / "session_dir"
        session_cwd.mkdir()
        window = TmuxWindow("s1:@1", "s1", cwd=str(session_cwd))

        with (
            patch("ccgram.config.config.is_user_allowed", return_value=True),
            patch("ccgram.handlers.send.send_command.thread_router.resolve_window_for_thread", return_value="s1:@1"),
            patch("ccgram.handlers.send.send_command.tmux_manager.find_window_by_id", new_callable=AsyncMock, return_value=window),
            patch("ccgram.handlers.send.send_command.safe_reply") as mock_reply,
        ):
            await send_command(update, context)

        mock_reply.assert_awaited_once_with(
            msg, "Cannot send: file is outside session directory and /tmp."
        )

    async def test_valid_file_sends_document(self, tmp_path: Path):
        update, msg, context = _make_update()
        doc_file = tmp_path / "result.txt"
        doc_file.write_text("success content")

        context.args = ["result.txt"]
        window = TmuxWindow("s1:@1", "s1", cwd=str(tmp_path))

        fake_client = FakeTelegramClient()

        with (
            patch("ccgram.config.config.is_user_allowed", return_value=True),
            patch("ccgram.handlers.send.send_command.thread_router.resolve_window_for_thread", return_value="s1:@1"),
            patch("ccgram.handlers.send.send_command.thread_router.resolve_chat_id", return_value=-100),
            patch("ccgram.handlers.send.send_command.tmux_manager.find_window_by_id", new_callable=AsyncMock, return_value=window),
            patch("ccgram.handlers.send.send_command.PTBTelegramClient", return_value=fake_client),
        ):
            await send_command(update, context)

        send_doc_calls = [c for c in fake_client.calls if c.method == "send_document"]
        assert len(send_doc_calls) == 1
        assert send_doc_calls[0].kwargs["filename"] == "result.txt"
        assert send_doc_calls[0].kwargs["message_thread_id"] == 42


class TestUploadFile:
    async def test_upload_image_sends_photo(self, tmp_path: Path):
        client = AsyncMock()
        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\nfake")

        await upload_file(client, chat_id=-100, thread_id=42, path=img)

        client.send_photo.assert_awaited_once()

    async def test_upload_text_sends_document(self, tmp_path: Path):
        client = AsyncMock()
        doc = tmp_path / "notes.md"
        doc.write_text("# Notes")

        await upload_file(client, chat_id=-100, thread_id=42, path=doc)

        client.send_document.assert_awaited_once()
