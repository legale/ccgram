# ccgram Architecture

## System Overview

ccgram bridges Telegram Forum topics to tmux sessions.
Each Telegram Forum topic maps directly to one tmux session (`cc_<name>`).

```text
Telegram Topic <───> ccgram <───> tmux session (cc_*)
```

### Core Invariants

1. **One Topic = One Session**: A Telegram topic is bound to exactly one tmux session via `@ccgram_topic = "<chat_id>:<thread_id>"`.
2. **Tmux as the Source of Truth**: Binding metadata and terminal state live in tmux (`@ccgram_topic`, `capture-pane`), with lazy runtime reconciliation in `ThreadRouter`.
3. **Sidecar Execution (`!!<command>`)**: Running an isolated command opens a secondary sidecar pane (`tmux split-window`), streams its output to a dedicated Telegram message, and closes the pane on completion.
4. **Direct Explicit Teardown**: `clear_topic_state` clears live view, screen diff, topic tail, and sidecar pane state directly.

## Directory Structure

```text
src/ccgram/
├── bot.py                  # PTB Application factory and lifecycle delegates
├── bootstrap.py            # Startup validation and status polling task spawning
├── cli.py                  # Click CLI (run, status)
├── config.py               # Environment configuration and allowlists
├── entity_formatting.py    # Markdown -> Telegram MessageEntity conversion
├── main.py                 # Entry point and signal handling
├── screenshot.py           # Terminal text -> PNG renderer
├── status_cmd.py           # CLI `ccgram status`
├── telegram_client.py      # TelegramClient Protocol and PTBTelegramClient adapter
├── telegram_request.py     # Resilient HTTPX polling request transport
├── telegram_sender.py      # UTF-16 message splitting (4096 limit)
├── thread_router.py        # Runtime mapping (user_id, thread_id) <-> window_id/chat_id
├── tmux_manager.py         # libtmux/subprocess wrapper for sessions, panes, capture, send-keys
├── topic_tail.py           # Tracks latest message ID per topic for safe diff edits
├── utils.py                # Config dir resolution, log throttling, task callback
└── handlers/
    ├── arg_parser.py       # iproute2-style prefix command matching
    ├── callback_data.py    # CB_* callback prefixes
    ├── callback_helpers.py # Shared topic/window lookup helpers
    ├── callback_registry.py# Prefix-based callback query dispatcher
    ├── cleanup.py          # clear_topic_state, //unbind, //killme
    ├── command_meta.py     # @command metadata decorator
    ├── file_handler.py     # Photo/document upload to .ccgram-uploads/
    ├── registry.py         # register_all() and // command dispatcher
    ├── sessions_dashboard.py # //sessions interactive dashboard
    ├── user_state.py       # context.user_data keys
    ├── commands/           # //commands, //ctrl-c, //ctrl-cc, //enter, //esc, //tab, //arrows
    ├── live/               # //screenshot, //live, inline key controls
    ├── messaging_pipeline/ # Rate limiting and entity-fallback sending
    ├── polling/            # Background polling loop and tmux reconciliation
    ├── send/               # //send file delivery from session workspace
    ├── status/             # Screen diff formatting and buffer controls
    ├── text/               # Normal text forwarding and !! sidecar commands
    └── topics/             # //new, //bind, //bindraw, //rename, forum topic events
```
