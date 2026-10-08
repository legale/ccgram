"""Tests for file_handler helper functions."""

import re
from pathlib import Path

import pytest

from ccgram.handlers.file_handler import (
    _generate_photo_filename,
    _resolve_upload_dir,
    _sanitize_caption,
    _sanitize_filename,
    _unique_dest,
    _validate_dest_path,
)


class TestSanitizeFilename:
    @pytest.mark.parametrize(
        ("input_name", "expected"),
        [
            ("document.pdf", "document.pdf"),
            ("file-name_123.txt", "file-name_123.txt"),
            ("/etc/passwd", "passwd"),
            ("../../../etc/passwd", "passwd"),
            ("../../etc/passwd", "passwd"),
            ("hello world!.txt", "hello_world_.txt"),
            ("file@#$.txt", "file___.txt"),
            ("..", "unnamed"),
            (".", "unnamed"),
            ("...", "unnamed"),
            ("", "unnamed"),
        ],
    )
    def test_sanitize(self, input_name: str, expected: str) -> None:
        assert _sanitize_filename(input_name) == expected

    def test_truncates_long_names_preserving_extension(self) -> None:
        long = "a" * 250 + ".pdf"
        result = _sanitize_filename(long)
        assert len(result) <= 200
        assert result.endswith(".pdf")


class TestUniqueDest:
    def test_returns_original_if_not_exists(self, tmp_path: Path) -> None:
        assert _unique_dest(tmp_path / "file.txt") == tmp_path / "file.txt"

    @pytest.mark.parametrize(
        ("existing_files", "expected_name"),
        [
            (["file.txt"], "file_1.txt"),
            (["file.txt", "file_1.txt", "file_2.txt"], "file_3.txt"),
            (["file"], "file_1"),
        ],
    )
    def test_increments_suffix(
        self, tmp_path: Path, existing_files: list[str], expected_name: str
    ) -> None:
        for name in existing_files:
            (tmp_path / name).write_text("x")
        assert _unique_dest(tmp_path / existing_files[0]) == tmp_path / expected_name

    def test_fallback_to_timestamp_after_100(self, tmp_path: Path) -> None:
        dest = tmp_path / "file.txt"
        for i in range(100):
            name = "file.txt" if i == 0 else f"file_{i}.txt"
            (tmp_path / name).write_text(str(i))
        result = _unique_dest(dest)
        assert result.name.startswith("file_") and result.name.endswith(".txt")
        assert result != dest

    def test_broken_symlink_treated_as_existing(self, tmp_path: Path) -> None:
        dest = tmp_path / "file.txt"
        dest.symlink_to(tmp_path / "nonexistent_target")
        assert _unique_dest(dest) == tmp_path / "file_1.txt"


class TestValidateDestPath:
    @pytest.mark.parametrize(
        ("rel_dest", "expected"),
        [
            ("file.txt", True),
            ("subdir/file.txt", True),
            ("../outside.txt", False),
        ],
    )
    def test_path_validation(
        self, tmp_path: Path, rel_dest: str, expected: bool
    ) -> None:
        upload = tmp_path / "upload"
        upload.mkdir()
        if "/" in rel_dest and not rel_dest.startswith(".."):
            (upload / Path(rel_dest).parent).mkdir(parents=True, exist_ok=True)
        assert _validate_dest_path(upload / rel_dest, upload) is expected

    def test_rejects_absolute_path_outside(self, tmp_path: Path) -> None:
        upload = tmp_path / "upload"
        upload.mkdir()
        assert _validate_dest_path(tmp_path / "outside.txt", upload) is False


class TestSanitizeCaption:
    @pytest.mark.parametrize(
        ("input_text", "expected"),
        [
            ("", ""),
            ("hello\x00\x01\x02world", "helloworld"),
            ("hello\x07\x1bworld", "helloworld"),
            ("line1\nline2\r\nline3\ttab", "line1 line2  line3\ttab"),
        ],
    )
    def test_sanitize(self, input_text: str, expected: str) -> None:
        assert _sanitize_caption(input_text) == expected

    def test_limits_to_500_chars(self) -> None:
        assert len(_sanitize_caption("a" * 600)) == 500


class TestGeneratePhotoFilename:
    def test_format(self) -> None:
        result = _generate_photo_filename("ABCDEFGHIJKLMNOP")
        assert re.match(r"^photo_\d{8}_\d{6}_ABCDEFGH\.jpg$", result)


class TestResolveUploadDir:
    async def test_uses_live_tmux_cwd(self, monkeypatch, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock
        from ccgram.tmux_manager import TmuxWindow

        live_dir = tmp_path / "sdk" / "project"
        live_dir.mkdir(parents=True)
        window = TmuxWindow(
            window_id="@1",
            window_name="proj",
            cwd=str(live_dir),
            pane_current_command="bash",
            pane_tty="/dev/pts/1",
        )
        monkeypatch.setattr(
            "ccgram.handlers.file_handler.thread_router.resolve_window_for_thread",
            lambda user_id, thread_id: "@1",
        )
        monkeypatch.setattr(
            "ccgram.handlers.file_handler.tmux_manager.find_window_by_id",
            AsyncMock(return_value=window),
        )
        window_id, upload_path, err = await _resolve_upload_dir(1, 42)
        assert err is None
        assert window_id == "@1"
        assert upload_path == live_dir / "tmp"

    async def test_fallback_to_window_store_cwd(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        from unittest.mock import AsyncMock

        fallback_dir = tmp_path / "fallback"
        fallback_dir.mkdir(parents=True)
        monkeypatch.setattr(
            "ccgram.handlers.file_handler.thread_router.resolve_window_for_thread",
            lambda user_id, thread_id: "@1",
        )
        monkeypatch.setattr(
            "ccgram.handlers.file_handler.tmux_manager.find_window_by_id",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "ccgram.handlers.file_handler.view_window",
            lambda window_id: type("WindowView", (), {"cwd": str(fallback_dir)})(),
        )
        window_id, upload_path, err = await _resolve_upload_dir(1, 42)
        assert err is None
        assert window_id == "@1"
        assert upload_path == fallback_dir / "tmp"

    async def test_unbound_topic_returns_error(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "ccgram.handlers.file_handler.thread_router.resolve_window_for_thread",
            lambda user_id, thread_id: None,
        )
        window_id, upload_path, err = await _resolve_upload_dir(1, 42)
        assert err == "No session bound to this topic."
        assert window_id is None
        assert upload_path is None
