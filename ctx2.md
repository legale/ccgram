# ccgram — Контекст рефакторинга (tmux-only)

## 1. Суть проекта

**ccgram** — Telegram-бот, который создаёт tmux-сессии, привязывает их к топикам (forum topics) Telegram-группы и транслирует ввод/вывод терминала через чат.

**Репозиторий**: `/home/ruslan/ccgram`
**Ветка**: `refactor/tmux-only` (запушена на `origin`)
**Версия**: `0.1.dev511`
**Сервис**: `ccgram.service` (systemd), активен

---

## 2. Архитектура (после рефакторинга)

### Что убрано
- Провайдеры AI-агентов (Claude, Gemini, Codex, Pi, AGY) — удалены полностью
- Парсинг логов/транскриптов (JSONL, SQLite) — удалён
- Recovery/resume подсистема (`recovery_banner.py`, `resume_command.py`, `restore_command.py`, `transcript_discovery.py`) — удалена
- Remote-control (RC) кнопка и функциональность — удалены
- Хуки (`hook.py`, `msg_skill.py`, `event_reader.py`) — удалены
- Команды: `/new`, `/resume`, `/restore` — удалены из registry

### Что осталось
- **Единственный провайдер**: `shell` — чистый мост к tmux
- **Команды бота** (префикс `//`):
  - `//sessions` / `//ses` — дашборд сессий: кнопки `<статус> <имя>` (rename), `scr` (screenshot), `kill`
  - `//bind` — привязать существующее tmux-окно к топику
  - `//unbind` — отвязать топик от сессии (сессия продолжает жить)
  - `//screenshot` / `//screen` — скриншот терминала (PNG с ANSI-цветами)
  - `//live` — автообновляемый просмотр терминала
  - `//panes` — управление панелями окна
  - `//toolbar` — панель кнопок
  - `//sync` — синхронизация и аудит состояния
  - `//history` — история сообщений топика
  - `//recall` — повтор недавних команд
  - `//verbose` — переключить детальность
  - `//toolcalls` — переключить показ tool calls
  - `//send` — отправить файл в tmux-сессию
  - `//upgrade` — обновление ccgram и перезапуск
  - `//echo` — эхо-тест (raw Telegram update)
  - `//commands` / `//help` — список команд

### Ключевые модули
| Модуль | Назначение |
|--------|------------|
| `tmux_manager.py` | Управление tmux-сессиями (create/kill/rename window/session, send-keys, capture-pane) |
| `thread_router.py` | Привязка user+thread → window_id, display names, group_chat_ids |
| `session_manager.py` (`session.py`) | Абстракция сессий, display names |
| `handlers/sessions_dashboard.py` | Дашборд `//ses`: список, rename, screenshot и kill с edit_forum_topic |
| `handlers/text/text_handler.py` | Маршрутизация текстовых сообщений (send-keys в tmux, rename captures, directory browser) |
| `handlers/polling/` | Фоновый опрос tmux-окон: diff экрана, статус, dead detection |
| `handlers/topics/` | Topic lifecycle, directory browser, bind/window callbacks |
| `handlers/status/` | Status bar, topic emoji, status bubble |
| `telegram_client.py` | Protocol `TelegramClient` + `PTBTelegramClient` обёртка |
| `config.py` | Конфигурация: `tmux_session_prefix` = `"ccgram_"` (единственный источник истины для префикса) |

### Кодовая база
- **src/**: 152 файла, ~36K строк
- **tests/**: 182 файла, ~3800+ тестов
- **Проверки** (`make check`): ruff format + lint, pyright, deptry, pytest

---

## 3. Текущее состояние tmux

```
Tmux session: ccgram-ruslan (10 windows)

  agy:@217                    (unbound)
  cc_Hi:@219                  (unbound)
  cc_cc_cc_codex2:@4          (unbound)
  cc_cc_cc_codex2:@102        (unbound)
  cc_hi:@98                   (unbound)
  @298 ruslan                  (unbound)
  ccgram_cc_cc_Shell:@238     (unbound)
  ccgram_cc_ccgram:@637       → topic 622829 (user 86872)  alive
  ccgram_cc_ses:@635          → topic 622798 (user 86872)  alive
  ccgram_cc_shell:@300        → topic 622713 (user 86872)  alive
```

**Проблема**: Мусорные unbound-окна с устаревшими/кривыми именами (`agy`, `cc_Hi`, `cc_cc_cc_codex2`, `cc_hi`). Нет механизма автоочистки.
**Проблема**: Telegram-вкладки (forum topics) привязанных сессий показывают старые имена (до rename), потому что `edit_forum_topic` добавлен только в последнем коммите.

---

## 4. Коммиты ветки `refactor/tmux-only` (от main)

```
da10275 sessions: rename telegram forum topic on session rename
83330b4 sessions: prevent dead session false alarm on session rename
12cc276 sessions: add session rename button and //ses alias in dashboard
76662fc tmux: fix screen diff, session prefix source of truth and picker discovery
4a0f3e7 feat(status): remove remote-control functionality and RC button
fb1c83b test: mock _create_shell_session_for_directory
ac72048 feat(tmux): complete steps 4 and 5 of minify plan
6d035b2 docs: update minify-plan.md with completed Step 3
034ceb0 refactor(session): remove transcripts, hooks and recovery subpackages
6e954c0 handlers: change bot command prefix to // instead of /
fd4ee5d refactor(providers): remove external agent providers, retain shell only
135390a docs: add flat minification plan for tmux-only refactor
377408b feat: directly create shell session on directory selection without provider picker
783a7d7 chore: save working state before tmux-only refactoring
```

Итого: -21626 строк удалено, +1531 добавлено.

---

## 5. Известные проблемы и TODO

### Баги
1. **Telegram-вкладки не синхронизированы** — сессии переименованные ДО коммита `da10275` имеют старые имена вкладок. Нужна одноразовая синхронизация или sync при старте.
2. **Двойные/тройные префиксы** в именах unbound-окон (`cc_cc_cc_codex2`, `cc_cc_Shell`) — артефакты прошлых переименований.

### Устаревшие команды / мёртвый код
3. **`//bind`** — вызывает `_handle_unbound_topic` из text_handler. Работает, но поведение дублирует то, что происходит при обычной отправке сообщения в unbound-топик. Вопрос: нужна ли отдельная команда?
4. **`//unbind`** — отвязывает топик, но сессия продолжает жить в tmux. Работает корректно. Вопрос: после unbind в топик можно отправить сообщение и rebind — это ожидаемое поведение?
5. **`cc_commands.py`** (`_BOT_COMMANDS`) — содержит устаревшие записи: `new`, `resume`, `restore` (команды удалены из registry), `commands` (описание всё ещё "List commands for this topic provider").
6. **`//history`** — `history_command` (`recovery/__init__.py`) — осталась после удаления recovery, нужно проверить работоспособность.

### Архитектурный долг
7. **Layering**: `sessions_dashboard.py` в allowlist `_SINGLETON_ALLOWLIST` — напрямую трогает `thread_router`, `lifecycle_strategy`. Допустимо, но зафиксировано в тестах.
8. **Polling dead detection**: убрана проверка через `thread_router` из `window_tick/__init__.py` (нарушала layering invariant). Защита от ложных dead-нотификаций при rename реализована через `mark_dead_notified` в `sessions_dashboard.py`.

---

## 6. Операционные команды

| Действие | Команда |
|----------|---------|
| Проверка (lint + type + test) | `make -C /home/ruslan/ccgram check` |
| Деплой и перезапуск | `~/bin/ccgram-use-custom` |
| Статус сервиса | `~/bin/ccgram-status` |
| Шумные команды | `/home/ruslan/.ai/skills/ucli/scripts/ucli.sh -- <cmd>` |
| **Нельзя** трогать | `/home/ruslan/.ccgram/.env` |
| **Нельзя** | `cd` |

---

## 7. Конвенции

- **Commit style**: `subsystem: message` (Linux kernel style)
- **Префикс tmux-сессий**: `ccgram_` (из `config.tmux_session_prefix`, единственный источник истины)
- **Сокращения**: session = ses, window ≠ "window" в UI (используем "session/ses")
- **Тесты**: pytest, 32 workers, `pytest-xdist`, marks: `integration`, `e2e` (исключены из `make check`)
- **Линтеры**: ruff (format + check), pyright, deptry
- **Layering tests**: `test_handler_layering_invariants.py` — PTB bot escapes allowlist + singleton access allowlist
- **Никаких эмодзи**: ни в коде, ни в коммитах, ни в ответах ассистента, ни в документах/планах
- **Отметки в плане**: `[x]` - done, `[/]` - in progress, `[ ]` - not done
