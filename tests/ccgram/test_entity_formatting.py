from telegram import MessageEntity

from ccgram.entity_formatting import (
    _strip_indented_code_blocks,
    convert_to_entities,
)


class TestConvertToEntities:
    def test_plain_text(self) -> None:
        text, entities = convert_to_entities("hello world")
        assert "hello world" in text
        assert isinstance(entities, list)

    def test_bold(self) -> None:
        text, entities = convert_to_entities("**bold text**")
        assert "bold text" in text
        bold_entities = [e for e in entities if e.type == MessageEntity.BOLD]
        assert len(bold_entities) == 1
        assert (
            text[
                bold_entities[0].offset : bold_entities[0].offset
                + bold_entities[0].length
            ]
            == "bold text"
        )

    def test_italic(self) -> None:
        text, entities = convert_to_entities("*italic text*")
        assert "italic text" in text
        italic_entities = [e for e in entities if e.type == MessageEntity.ITALIC]
        assert len(italic_entities) == 1

    def test_inline_code(self) -> None:
        text, entities = convert_to_entities("`inline code`")
        assert "inline code" in text
        code_entities = [e for e in entities if e.type == MessageEntity.CODE]
        assert len(code_entities) == 1

    def test_code_block_preserved(self) -> None:
        text, entities = convert_to_entities("```python\nprint('hi')\n```")
        assert "print" in text
        pre_entities = [e for e in entities if e.type == MessageEntity.PRE]
        assert len(pre_entities) == 1

    def test_code_block_with_language(self) -> None:
        text, entities = convert_to_entities("```python\nx = 1\n```")
        pre_entities = [e for e in entities if e.type == MessageEntity.PRE]
        assert len(pre_entities) == 1
        assert pre_entities[0].language == "python"

    def test_indented_text_not_treated_as_code(self) -> None:
        text, entities = convert_to_entities(
            "Some text:\n\n    indented line\n\nMore text"
        )
        assert "indented line" in text
        pre_entities = [e for e in entities if e.type == MessageEntity.PRE]
        assert len(pre_entities) == 0

    def test_fenced_code_block_indentation_preserved(self) -> None:
        md = "```python\ndef foo():\n    x = 1\n\n    y = 2\n    return x + y\n```"
        text, entities = convert_to_entities(md)
        assert "    x = 1" in text
        assert "    y = 2" in text
        assert "    return x + y" in text

    def test_astral_unicode_in_text(self) -> None:
        text, entities = convert_to_entities("Hello \U0001d400 **world**")
        assert text == "Hello \U0001d400 world"
        bold_entities = [e for e in entities if e.type == MessageEntity.BOLD]
        assert len(bold_entities) == 1
        ent = bold_entities[0]
        utf16 = text.encode("utf-16-le")
        extracted = utf16[ent.offset * 2 : (ent.offset + ent.length) * 2].decode(
            "utf-16-le"
        )
        assert extracted == "world"

    def test_special_chars_no_parse_error(self) -> None:
        text, entities = convert_to_entities(
            "_var_name_ and `file-path.txt` and #heading"
        )
        assert "var_name" in text
        assert "file-path.txt" in text
        assert isinstance(entities, list)

    def test_link(self) -> None:
        text, entities = convert_to_entities("[click here](https://example.com)")
        assert "click here" in text
        link_entities = [e for e in entities if e.type == MessageEntity.TEXT_LINK]
        assert len(link_entities) == 1
        assert link_entities[0].url == "https://example.com"

    def test_empty_text(self) -> None:
        text, entities = convert_to_entities("")
        assert text == ""
        assert entities == []

    def test_returns_telegram_message_entity_types(self) -> None:
        text, entities = convert_to_entities("**bold** `code`")
        for ent in entities:
            assert isinstance(ent, MessageEntity)


class TestStripIndentedCodeBlocks:
    def test_strips_indented_block_after_blank_line(self) -> None:
        text = "hello\n\n    indented\n    block\n\nend"
        result = _strip_indented_code_blocks(text)
        assert "    indented" not in result
        assert "indented\nblock" in result

    def test_strips_indented_block_at_start(self) -> None:
        text = "    indented start\n\nrest"
        result = _strip_indented_code_blocks(text)
        assert "    indented" not in result
        assert "indented start" in result

    def test_preserves_fenced_block_indentation(self) -> None:
        text = "text\n\n```python\ndef foo():\n    x = 1\n\n    y = 2\n```\n\nend"
        result = _strip_indented_code_blocks(text)
        assert "    x = 1" in result
        assert "    y = 2" in result

    def test_preserves_fenced_block_at_start(self) -> None:
        text = "```\n    code\n```\n\ntext"
        result = _strip_indented_code_blocks(text)
        assert "    code" in result

    def test_mixed_fenced_and_indented(self) -> None:
        text = "```\n    keep\n```\n\n    strip this\n\nend"
        result = _strip_indented_code_blocks(text)
        assert "    keep" in result
        assert "    strip" not in result
        assert "strip this" in result

    def test_no_indentation_passthrough(self) -> None:
        text = "plain text\nno indentation"
        assert _strip_indented_code_blocks(text) == text

    def test_unclosed_fence_kept_verbatim(self) -> None:
        text = "before\n\n```python\n    indented code\n    more"
        result = _strip_indented_code_blocks(text)
        assert "    indented code" in result

    def test_nested_fence_longer_opening(self) -> None:
        text = "`````\n    keep\n```\n\n    also keep\n`````"
        result = _strip_indented_code_blocks(text)
        assert "    keep" in result
        assert "    also keep" in result

    def test_tilde_fence_preserved(self) -> None:
        text = "~~~\n    keep\n~~~\n\n    strip this\n\nend"
        result = _strip_indented_code_blocks(text)
        assert "    keep" in result
        assert "    strip" not in result
        assert "strip this" in result
