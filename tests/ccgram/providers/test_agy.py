from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from ccgram.providers.agy import AgyProvider


class TestAgyProviderCapabilities:
    def test_capabilities(self) -> None:
        provider = AgyProvider()
        caps = provider.capabilities
        assert caps.name == "agy"
        assert caps.launch_command == "agy"
        assert caps.supports_resume is True
        assert caps.supports_continue is True
        assert caps.supports_structured_transcript is True
        assert caps.supports_incremental_read is True
        assert "/plan" in caps.builtin_commands


class TestAgyMakeLaunchArgs:
    def test_empty(self) -> None:
        assert AgyProvider().make_launch_args() == ""

    def test_continue(self) -> None:
        assert AgyProvider().make_launch_args(use_continue=True) == "--continue"

    def test_resume_conversation(self) -> None:
        cid = "195b5001-a58d-4e0e-8ce3-76359213e451"
        assert AgyProvider().make_launch_args(resume_id=cid) == f"--conversation {cid}"


class TestAgyParseTranscriptEntries:
    def test_parse_user_entry_with_request_tags(self) -> None:
        provider = AgyProvider()
        entries = [
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "created_at": "2026-10-03T12:00:00Z",
                "content": "<USER_REQUEST>\nFix bug in parser\n</USER_REQUEST>\n<META>...</META>",
            }
        ]
        messages, pending = provider.parse_transcript_entries(entries, {})
        assert len(messages) == 1
        assert messages[0].role == "user"
        assert messages[0].text == "Fix bug in parser"
        assert messages[0].content_type == "text"

    def test_parse_planner_response_with_tools_and_content(self) -> None:
        provider = AgyProvider()
        entries = [
            {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "created_at": "2026-10-03T12:00:01Z",
                "content": "I am inspecting the file.",
                "tool_calls": [
                    {
                        "name": "view_file",
                        "args": {
                            "AbsolutePath": "/home/user/file.py",
                            "toolSummary": "View file.py",
                        },
                    }
                ],
            }
        ]
        messages, pending = provider.parse_transcript_entries(entries, {})
        assert len(messages) == 2
        assert messages[0].content_type == "tool_use"
        assert messages[0].tool_name == "view_file"
        assert "**view_file** `View file.py`" in messages[0].text
        assert messages[1].content_type == "text"
        assert messages[1].text == "I am inspecting the file."

    def test_parse_generic_error_result(self) -> None:
        provider = AgyProvider()
        entries = [
            {
                "step_index": 2,
                "source": "MODEL",
                "type": "GENERIC",
                "status": "ERROR",
                "error": "File not found",
                "created_at": "2026-10-03T12:00:02Z",
            }
        ]
        messages, pending = provider.parse_transcript_entries(entries, {})
        assert len(messages) == 1
        assert messages[0].content_type == "tool_result"
        assert "Error: File not found" in messages[0].text

    def test_skips_duplicate_step_index(self) -> None:
        provider = AgyProvider()
        entry = {
            "step_index": 10,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "content": "Hello",
        }
        messages1, pending = provider.parse_transcript_entries([entry], {})
        assert len(messages1) == 1
        messages2, pending = provider.parse_transcript_entries([entry], pending)
        assert len(messages2) == 0


class TestAgyHistoryEntry:
    def test_user_history(self) -> None:
        provider = AgyProvider()
        entry = {
            "type": "USER_INPUT",
            "content": "<USER_REQUEST>Hello</USER_REQUEST>",
        }
        msg = provider.parse_history_entry(entry)
        assert msg is not None
        assert msg.role == "user"
        assert msg.text == "Hello"

    def test_model_history(self) -> None:
        provider = AgyProvider()
        entry = {
            "type": "PLANNER_RESPONSE",
            "content": "World",
        }
        msg = provider.parse_history_entry(entry)
        assert msg is not None
        assert msg.role == "assistant"
        assert msg.text == "World"


class TestAgyDiscoverTranscript:
    def test_discover_from_db(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        agy_dir = tmp_path / ".gemini" / "antigravity-cli"
        agy_dir.mkdir(parents=True)

        cid = "test-uuid-123"
        brain_log_dir = agy_dir / "brain" / cid / ".system_generated" / "logs"
        brain_log_dir.mkdir(parents=True)
        tfile = brain_log_dir / "transcript.jsonl"
        tfile.write_text('{"step_index": 0}\n')

        cwd = "/tmp/myproject"
        db_path = agy_dir / "conversation_summaries.db"
        conn = sqlite3.connect(db_path)
        with conn:
            conn.execute(
                "CREATE TABLE conversation_summaries ("
                "conversation_id text, workspace_uris text, last_modified_time datetime)"
            )
            conn.execute(
                "INSERT INTO conversation_summaries VALUES (?, ?, ?)",
                (cid, json.dumps([f"file://{cwd}"]), "2026-10-03 12:00:00"),
            )
        conn.close()

        provider = AgyProvider()
        event = provider.discover_transcript(cwd=cwd, window_key="ccgram:@1")
        assert event is not None
        assert event.session_id == cid
        assert event.transcript_path == str(tfile)
        assert event.window_key == "ccgram:@1"

    def test_discover_from_history(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        agy_dir = tmp_path / ".gemini" / "antigravity-cli"
        agy_dir.mkdir(parents=True)

        cid = "test-uuid-456"
        brain_log_dir = agy_dir / "brain" / cid / ".system_generated" / "logs"
        brain_log_dir.mkdir(parents=True)
        tfile = brain_log_dir / "transcript.jsonl"
        tfile.write_text('{"step_index": 0}\n')

        cwd = "/tmp/myotherproject"
        hist_path = agy_dir / "history.jsonl"
        hist_path.write_text(
            json.dumps({"workspace": cwd, "conversationId": cid}) + "\n"
        )

        provider = AgyProvider()
        event = provider.discover_transcript(cwd=cwd, window_key="ccgram:@2")
        assert event is not None
        assert event.session_id == cid
        assert event.transcript_path == str(tfile)
        assert event.window_key == "ccgram:@2"
