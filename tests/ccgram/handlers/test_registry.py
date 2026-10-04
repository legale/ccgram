from unittest.mock import MagicMock

import pytest
from telegram.ext import (
    CallbackQueryHandler,
    MessageHandler,
    PrefixHandler,
    filters,
)

from ccgram.handlers.registry import COMMAND_NAMES, CommandSpec, register_all


def _stub_handler():
    return MagicMock()


def _make_app():
    app = MagicMock()
    app.add_handler = MagicMock()
    return app


def test_command_spec_is_frozen():
    spec = CommandSpec("foo", _stub_handler())
    with pytest.raises(AttributeError):
        spec.name = "bar"  # type: ignore[misc]


def test_register_all_installs_expected_command_names():
    app = _make_app()
    register_all(app, filters.ALL)

    command_names: list[str] = []
    for call in app.add_handler.call_args_list:
        handler = call.args[0]
        if isinstance(handler, PrefixHandler):
            for cmd in handler.commands:
                command_names.append(cmd.removeprefix("//"))

    assert set(command_names) == set(COMMAND_NAMES)
    assert len(command_names) == len(COMMAND_NAMES)


def test_register_all_registers_all_handler_kinds():
    app = _make_app()
    register_all(app, filters.ALL)

    by_kind: dict[type, int] = {}
    for call in app.add_handler.call_args_list:
        handler = call.args[0]
        by_kind[type(handler)] = by_kind.get(type(handler), 0) + 1

    assert by_kind.get(PrefixHandler) == len(COMMAND_NAMES)
    assert by_kind.get(CallbackQueryHandler) == 1
    assert by_kind.get(MessageHandler) == 7


def test_register_all_command_handlers_precede_message_command_fallback():
    """PrefixHandlers must be registered before the text/fallback MessageHandler."""
    app = _make_app()
    register_all(app, filters.ALL)

    last_command_idx = -1
    first_message_idx = -1
    for idx, call in enumerate(app.add_handler.call_args_list):
        handler = call.args[0]
        if isinstance(handler, PrefixHandler):
            last_command_idx = idx
        elif isinstance(handler, MessageHandler) and first_message_idx == -1:
            first_message_idx = idx

    assert last_command_idx >= 0 and first_message_idx >= 0
    assert last_command_idx < first_message_idx


def test_double_slash_prefix_routes_commands_and_slash_passes_as_text():
    from telegram import Chat, Message, Update
    from datetime import datetime

    app = _make_app()
    register_all(app, filters.ALL)

    handlers = [call.args[0] for call in app.add_handler.call_args_list]
    prefix_handlers = [h for h in handlers if isinstance(h, PrefixHandler)]

    # Message with //screenshot must match PrefixHandler
    u_bot_cmd = Update(
        1, message=Message(1, datetime.now(), Chat(1, "group"), text="//screenshot")
    )
    matched_prefix = any(h.check_update(u_bot_cmd) for h in prefix_handlers)
    assert matched_prefix is True

    # Message with /bin/ls must NOT match any PrefixHandler
    u_shell_path = Update(
        2, message=Message(2, datetime.now(), Chat(1, "group"), text="/bin/ls -la")
    )
    matched_prefix_for_path = any(h.check_update(u_shell_path) for h in prefix_handlers)
    assert matched_prefix_for_path is False
