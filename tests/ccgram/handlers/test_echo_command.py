from types import SimpleNamespace
from unittest.mock import AsyncMock

from ccgram.handlers.echo_command import _json_chunks, echo_command


def _obj(data):
    return SimpleNamespace(to_dict=lambda: data)


def _make_update() -> SimpleNamespace:
    message = _obj(
        {
            "message_id": 17,
            "message_thread_id": 604771,
            "text": "/echo",
        }
    )
    message.reply_text = AsyncMock()
    return SimpleNamespace(
        to_dict=lambda: {
            "update_id": 123,
            "message": message.to_dict(),
        },
        effective_chat=_obj({"id": -100, "type": "supergroup"}),
        effective_message=message,
        effective_user=_obj({"id": 86872, "username": "ruslan"}),
    )


async def test_echo_command_replies_with_update_payload() -> None:
    update = _make_update()

    await echo_command(update, SimpleNamespace())

    update.effective_message.reply_text.assert_awaited_once()
    text = update.effective_message.reply_text.await_args.args[0]
    assert text.startswith("Telegram update echo\n")
    assert '"message_thread_id": 604771' in text
    assert '"update_id": 123' in text
    assert '"username": "ruslan"' in text


async def test_echo_command_noops_without_message() -> None:
    update = SimpleNamespace(effective_message=None)

    await echo_command(update, SimpleNamespace())


def test_json_chunks_split_large_payload() -> None:
    chunks = _json_chunks({"text": "x" * 9000})

    assert len(chunks) > 1
    assert all(len(chunk) < 4096 for chunk in chunks)
