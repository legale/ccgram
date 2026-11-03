"""Split Telegram messages without exceeding the UTF-16 length limit."""

TELEGRAM_MAX_MESSAGE_LENGTH = 4096
_MIN_SPLIT_LENGTH = 2
_BMP_MAX_CODEPOINT = 0xFFFF


def split_message(
    text: str, max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH
) -> list[str]:
    """Split text on newlines where possible, otherwise at a safe boundary."""
    if max_length < _MIN_SPLIT_LENGTH:
        raise ValueError("max_length must be at least 2")
    if not text:
        return [""]

    chunks = []
    while text:
        units = 0
        end = 0
        for ch in text:
            width = 2 if ord(ch) > _BMP_MAX_CODEPOINT else 1
            if units + width > max_length:
                break
            units += width
            end += 1

        if end == len(text):
            chunks.append(text)
            break

        pos = text.rfind("\n", 0, end)
        if pos > 0:
            chunks.append(text[:pos])
            text = text[pos + 1 :]
        else:
            chunks.append(text[:end])
            text = text[end:]

    return chunks
