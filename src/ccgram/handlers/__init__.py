"""Telegram bot handlers package — modular handler organization.

Handlers are grouped into feature subpackages:
  - commands/: //commands and special key commands (//ctrl-c, //ctrl-cc, //enter, //esc, //tab, //arrows)
  - live/: live terminal view and screenshots (//screenshot, //live)
  - messaging_pipeline/: rate-limiting and Telegram message sending helpers
  - polling/: status polling coordinator and tmux-authoritative reconciliation
  - send/: //send file delivery from session workspace to Telegram
  - status/: screen diff formatting and controls
  - text/: text message and sidecar (!!) routing
  - topics/: topic binding (//new, //bind, //bindraw, //rename) and lifecycle

Top-level modules (leaves and cross-cutting concerns):
  - arg_parser: iproute2-style prefix command matcher
  - callback_data: CB_* callback data constants
  - callback_helpers: shared topic/window helpers
  - callback_registry: prefix-based callback dispatch with self-registration
  - cleanup: topic teardown, //unbind, //killme
  - command_meta: @command metadata decorator
  - file_handler: photo/document upload into .ccgram-uploads/
  - registry: central PTB handler registration (register_all)
  - sessions_dashboard: //sessions interactive dashboard
  - user_state: context.user_data string key constants
"""
