"""Markdown → Telegram entity-based formatting layer.

Converts markdown text to plain text plus a list of telegram.MessageEntity
objects. Entity-based formatting uses character offsets — there is no syntax
to parse and no parse errors are possible.

Key function: convert_to_entities(text) → (str, list[MessageEntity]).
"""

import re

from telegram import MessageEntity as TelegramEntity

from telegramify_markdown import config as _tm_config
from telegramify_markdown import convert as _tm_convert
from telegramify_markdown.entity import MessageEntity as _LibEntity

# Disable auto-promotion of long blockquotes to expandable blockquotes.
_tm_config.get_runtime_config().cite_expandable = False

_FENCE_RE = re.compile(r"^(`{3,}|~{3,})", re.MULTILINE)
_INDENTED_CODE_RE = re.compile(r"(?<=\n\n)((?:    .+\n?)+)")
_INDENTED_LINE_RE = re.compile(r"^    ", re.MULTILINE)


def _strip_indented_code_blocks(text: str) -> str:
    """Strip 4-space indentation that CommonMark treats as code blocks.

    The agent uses fenced ``` blocks for code; indented blocks in its
    output are typically continuation text, not code.  Pyromark (CommonMark)
    converts 4-space-indented paragraphs into code blocks, so we strip
    the leading spaces before conversion.

    Fenced code blocks are left untouched — only non-fenced segments
    are processed.
    """
    # Split text into alternating (outside-fence, inside-fence) segments
    parts: list[str] = []
    inside_fence = False
    fence_marker = ""
    last_end = 0

    for m in _FENCE_RE.finditer(text):
        marker = m.group(1)
        if not inside_fence:
            # Entering a fenced block — process the preceding non-fenced text
            parts.append(_deindent(text[last_end : m.start()], last_end == 0))
            inside_fence = True
            fence_marker = marker  # e.g. "```" or "~~~~~"
            last_end = m.start()
        elif marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
            # Closing fence — keep fenced content verbatim
            end = m.end()
            parts.append(text[last_end:end])
            last_end = end
            inside_fence = False
            fence_marker = ""

    # Remaining text after last fence (or entire text if no fences)
    tail = text[last_end:]
    if inside_fence:
        # Unclosed fence — keep verbatim
        parts.append(tail)
    else:
        parts.append(_deindent(tail, last_end == 0))

    return "".join(parts)


def _deindent(text: str, is_start: bool) -> str:
    """Strip 4-space indented code blocks from a non-fenced text segment."""
    if is_start:
        text = re.sub(
            r"^((?:    .+\n?)+)",
            lambda m: _INDENTED_LINE_RE.sub("", m.group(0)),
            text,
        )
    return _INDENTED_CODE_RE.sub(
        lambda m: _INDENTED_LINE_RE.sub("", m.group(0)),
        text,
    )


def _lib_entity_to_telegram(ent: _LibEntity) -> TelegramEntity:
    """Convert a telegramify_markdown MessageEntity to telegram.MessageEntity."""
    return TelegramEntity(
        type=ent.type,
        offset=ent.offset,
        length=ent.length,
        url=ent.url,
        language=ent.language,
        custom_emoji_id=ent.custom_emoji_id,
    )


def convert_to_entities(text: str) -> tuple[str, list[TelegramEntity]]:
    """Convert markdown text to plain text + Telegram MessageEntity list."""
    preprocessed = _strip_indented_code_blocks(text)
    plain, lib_entities = _tm_convert(preprocessed)
    tg_entities = [_lib_entity_to_telegram(e) for e in lib_entities]
    return plain, tg_entities
