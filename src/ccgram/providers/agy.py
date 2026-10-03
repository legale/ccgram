"""Antigravity CLI (agy) provider — Google DeepMind agent behind AgentProvider protocol."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
import re
import shlex
import sqlite3
import time
from typing import Any, ClassVar

import structlog

from ccgram.providers._jsonl import JsonlProvider
from ccgram.providers.base import (
    AgentMessage,
    ProviderCapabilities,
    SessionStartEvent,
)

logger = structlog.get_logger(__name__)

_AGY_BUILTINS: dict[str, str] = {
    "/help": "Display available commands",
    "/plan": "Switch to plan mode",
    "/goal": "Run long-running task to completion",
    "/schedule": "Schedule recurring or timer instruction",
    "/learn": "Persist behaviors or instructions",
    "/boost": "Deep thinking and multi-perspective verification",
    "/browser": "Web browsing and search",
    "/clear": "Clear conversation context",
    "/quit": "Exit Antigravity CLI",
}

_USER_REQUEST_RE = re.compile(r"<USER_REQUEST>(.*?)</USER_REQUEST>", re.DOTALL)
_MAX_TOOL_SUMMARY = 120
_TRANSCRIPT_MAX_AGE_SECS = 120.0


def _extract_user_text(raw: str) -> str:
    if not raw:
        return ""
    match = _USER_REQUEST_RE.search(raw)
    if match:
        return match.group(1).strip()
    return raw.strip()


def _format_tool_call(name: str, args: Any) -> str:
    summary = ""
    if isinstance(args, str):
        with contextlib.suppress(json.JSONDecodeError):
            args = json.loads(args)
    if isinstance(args, dict):
        summary = (
            args.get("toolSummary")
            or args.get("toolAction")
            or args.get("Description")
            or args.get("CommandLine")
            or args.get("AbsolutePath")
            or args.get("query")
            or args.get("Url")
            or ""
        )
        if isinstance(summary, str):
            summary = summary.strip("\"' ")
    if summary:
        if len(summary) > _MAX_TOOL_SUMMARY:
            summary = summary[:_MAX_TOOL_SUMMARY] + "..."
        return f"**{name}** `{summary}`"
    return f"**{name}**"


def _parse_user_entry(entry: dict[str, Any]) -> AgentMessage | None:
    etype = entry.get("type", "")
    source = entry.get("source", "")
    if etype in ("USER_INPUT", "user") or source == "USER_EXPLICIT":
        raw_content = entry.get("content", "")
        if isinstance(raw_content, str):
            text = _extract_user_text(raw_content)
            if text:
                return AgentMessage(
                    text=text,
                    role="user",
                    content_type="text",
                    timestamp=entry.get("created_at"),
                )
    return None


def _parse_model_entry(
    entry: dict[str, Any], pending: dict[str, Any]
) -> list[AgentMessage]:
    etype = entry.get("type", "")
    if etype not in ("PLANNER_RESPONSE", "assistant"):
        return []

    messages: list[AgentMessage] = []
    created_at = entry.get("created_at")

    tool_calls = entry.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if isinstance(tc, dict):
                tname = tc.get("name", "unknown")
                cid = tc.get("id") or tc.get("call_id")
                if isinstance(cid, str):
                    pending[cid] = tname
                summary = _format_tool_call(tname, tc.get("args"))
                messages.append(
                    AgentMessage(
                        text=summary,
                        role="assistant",
                        content_type="tool_use",
                        tool_name=tname,
                        tool_use_id=cid if isinstance(cid, str) else None,
                        timestamp=created_at,
                    )
                )

    content = entry.get("content")
    if isinstance(content, str) and content.strip():
        messages.append(
            AgentMessage(
                text=content.strip(),
                role="assistant",
                content_type="text",
                timestamp=created_at,
            )
        )

    return messages


def _parse_tool_result_entry(
    entry: dict[str, Any], pending: dict[str, Any]
) -> AgentMessage | None:
    etype = entry.get("type", "")
    if etype not in ("GENERIC", "tool_result", "toolResult"):
        return None

    call_id = entry.get("tool_use_id") or entry.get("call_id")
    if isinstance(call_id, str):
        pending.pop(call_id, None)

    if entry.get("status") == "ERROR":
        err = entry.get("error") or entry.get("content", "")
        if isinstance(err, str) and err:
            return AgentMessage(
                text=f"Error: {err.strip()}",
                role="assistant",
                content_type="tool_result",
                timestamp=entry.get("created_at"),
            )
    return None


def _check_transcript_event(
    agy_dir: Path,
    cid: str,
    cwd: str,
    window_key: str,
    age_limit: float,
    now: float,
) -> SessionStartEvent | None:
    tpath = agy_dir / "brain" / cid / ".system_generated" / "logs" / "transcript.jsonl"
    if not tpath.is_file():
        return None
    try:
        mtime = tpath.stat().st_mtime
    except OSError:
        return None
    if age_limit > 0 and now - mtime > age_limit:
        return None
    return SessionStartEvent(
        session_id=cid,
        cwd=cwd,
        transcript_path=str(tpath),
        window_key=window_key,
    )


def _discover_from_db(
    agy_dir: Path,
    resolved_cwd: str,
    cwd: str,
    window_key: str,
    age_limit: float,
    now: float,
) -> SessionStartEvent | None:
    db_path = agy_dir / "conversation_summaries.db"
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        with conn:
            cur = conn.cursor()
            rows = cur.execute(
                "SELECT conversation_id, workspace_uris FROM conversation_summaries "
                "ORDER BY last_modified_time DESC LIMIT 20"
            ).fetchall()
        target_uri = f"file://{resolved_cwd}"
        for cid, uris_raw in rows:
            if not cid or not isinstance(cid, str):
                continue
            matched = False
            try:
                uris = json.loads(uris_raw)
                if isinstance(uris, list) and (
                    target_uri in uris or resolved_cwd in uris or cwd in uris
                ):
                    matched = True
            except json.JSONDecodeError:
                if target_uri in uris_raw or resolved_cwd in uris_raw:
                    matched = True
            if matched:
                event = _check_transcript_event(
                    agy_dir, cid, cwd, window_key, age_limit, now
                )
                if event is not None:
                    return event
    except (sqlite3.Error, OSError) as exc:
        logger.debug("Failed to query agy conversation_summaries.db: %s", exc)
    return None


def _discover_from_history(
    agy_dir: Path,
    resolved_cwd: str,
    cwd: str,
    window_key: str,
    age_limit: float,
    now: float,
) -> SessionStartEvent | None:
    hist_path = agy_dir / "history.jsonl"
    if not hist_path.is_file():
        return None
    try:
        with open(hist_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        for line in reversed(lines[-50:]):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            wspace = d.get("workspace", "")
            if wspace and str(Path(wspace).resolve()) == resolved_cwd:
                cid = d.get("conversationId", "")
                if cid:
                    event = _check_transcript_event(
                        agy_dir, cid, cwd, window_key, age_limit, now
                    )
                    if event is not None:
                        return event
    except OSError as exc:
        logger.debug("Failed to read agy history.jsonl: %s", exc)
    return None


class AgyProvider(JsonlProvider):
    """AgentProvider implementation for Antigravity CLI (agy)."""

    _CAPS: ClassVar[ProviderCapabilities] = ProviderCapabilities(
        name="agy",
        launch_command="agy",
        supports_hook=False,
        supports_hook_events=False,
        supports_resume=True,
        supports_continue=True,
        supports_structured_transcript=True,
        supports_incremental_read=True,
        transcript_format="jsonl",
        builtin_commands=tuple(_AGY_BUILTINS),
        supports_user_command_discovery=False,
        supports_mailbox_delivery=True,
    )
    _BUILTINS = _AGY_BUILTINS

    def make_launch_args(
        self,
        resume_id: str | None = None,
        use_continue: bool = False,
    ) -> str:
        if resume_id:
            return f"--conversation {shlex.quote(resume_id)}"
        if use_continue:
            return "--continue"
        return ""

    def parse_transcript_entries(
        self,
        entries: list[dict[str, Any]],
        pending_tools: dict[str, Any],
        cwd: str | None = None,  # noqa: ARG002
    ) -> tuple[list[AgentMessage], dict[str, Any]]:
        messages: list[AgentMessage] = []
        pending = dict(pending_tools)
        seen_steps: set[int] = pending.setdefault("__seen_steps__", set())

        for entry in entries:
            step_idx = entry.get("step_index")
            if isinstance(step_idx, int):
                if step_idx in seen_steps:
                    continue
                seen_steps.add(step_idx)

            user_msg = _parse_user_entry(entry)
            if user_msg is not None:
                messages.append(user_msg)
                continue

            model_msgs = _parse_model_entry(entry, pending)
            if model_msgs:
                messages.extend(model_msgs)
                continue

            tool_msg = _parse_tool_result_entry(entry, pending)
            if tool_msg is not None:
                messages.append(tool_msg)

        return messages, pending

    def is_user_transcript_entry(self, entry: dict[str, Any]) -> bool:
        return (
            entry.get("type") in ("USER_INPUT", "user")
            or entry.get("source") == "USER_EXPLICIT"
        )

    def parse_history_entry(self, entry: dict[str, Any]) -> AgentMessage | None:
        user_msg = _parse_user_entry(entry)
        if user_msg is not None:
            return user_msg
        model_msgs = _parse_model_entry(entry, {})
        for m in model_msgs:
            if m.content_type == "text":
                return m
        return model_msgs[0] if model_msgs else None

    def discover_transcript(
        self,
        cwd: str,
        window_key: str,
        *,
        max_age: float | None = None,
    ) -> SessionStartEvent | None:
        agy_dir = Path.home() / ".gemini" / "antigravity-cli"
        if not agy_dir.is_dir():
            return None

        resolved_cwd = str(Path(cwd).resolve())
        age_limit = _TRANSCRIPT_MAX_AGE_SECS if max_age is None else max_age
        now = time.time()

        event = _discover_from_db(
            agy_dir, resolved_cwd, cwd, window_key, age_limit, now
        )
        if event is not None:
            return event
        return _discover_from_history(
            agy_dir, resolved_cwd, cwd, window_key, age_limit, now
        )
