"""Split Telegram messages without exceeding the UTF-16 length limit."""

TELEGRAM_MAX_MESSAGE_LENGTH = 4096


def split_message(
    text: str, max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH
) -> list[str]:
    """Split text on newlines where possible, otherwise at a safe boundary."""
    if max_length < 2:
        raise ValueError("max_length must be at least 2")
    if not text:
        return [""]

    chunks = []
    while text:
        units = 0
        end = 0
        for ch in text:
            width = 2 if ord(ch) > 0xFFFF else 1
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
