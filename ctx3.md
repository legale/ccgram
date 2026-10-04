# CCGram handoff — ctx3

Дата контекста: 2026-10-04.

## Рабочее дерево

Репозиторий `/home/ruslan/ccgram`, ветка `refactor/tmux-only`.

На момент создания этого файла `git status --short` пустой. Последний коммит:

```text
0206db2 sessions: use configured cc prefix and enable actions
```

Предыдущие важные коммиты:

```text
c5c63a4 sessions: show all tmux sessions with compact actions
0a73da1 tests: verify topic creation starts tmux session
44ddc3f tests: remove stale deleted-surface checks
abd1bc3 status: remove task and interactive UI state
e9a5e36 config: remove deleted voice and monitor options
cedf592 polling: remove transcript monitor stack
```

Изменения вне репозитория:

- `/home/ruslan/.ccgram/.env`: удалён `TMUX_SESSION_NAME=ccgram-ruslan`, оставлен `TMUX_SESSION_PREFIX=cc_`.
- `/home/ruslan/bin/ccgram-status`: безопасный fallback `TMUX_SESSION_NAME:-ccgram`.
- `/home/ruslan/bin/ccgram-stop`: такой же fallback.

Не коммитить эти файлы через git репозитория ccgram: они находятся вне него.

## Уже сделано и нельзя сломать

Сохранить и проверять после каждого удаления:

- обычный Telegram → shell/tmux путь;
- создание TG topic создаёт tmux session через `topic_session_name()`;
- `//ses` (`//sessions`) показывает все записи `tmux list-sessions` в формате:
  `+-o ses_name cur_dir`;
- dashboard имеет кнопки rename, `scr`, `kill`;
- широкая кнопка имени вынесена в отдельную строку, `scr`/`kill` находятся в компактной строке под ней;
- rename синхронизирует tmux session и привязанный TG topic в обе стороны;
- имя tmux session формируется только через `config.tmux_session_prefix`;
- дефолт префикса — `cc_`, не `ccgram_`;
- `//live`, tmux screen diff/polling, screenshot и file send должны остаться рабочими;
- `scr` dashboard имеет callback `sess:scr:` и умеет работать с unbound session;
- kill/rename dashboard не требуют `user_owns_window`, чтобы работать с любой показанной tmux session.

Текущие ключевые места:

- `src/ccgram/handlers/sessions_dashboard.py`
- `src/ccgram/tmux_manager.py`, метод `list_sessions()`
- `src/ccgram/handlers/live/screenshot_callbacks.py`, параметр `allow_unowned`
- `src/ccgram/config.py`, `tmux_session_prefix`

## Тесты

После изменений dashboard/action path:

```text
104 passed
```

Полный unit-прогон до последнего изменения дефолтного префикса:

```text
3250 passed, 13 skipped
```

Перед коммитом запускай как минимум целевые тесты dashboard, kill, live/screenshot, topic binding/edited и `test_tmux_sessions.py`, затем полный `uv run pytest -q`, если срез затрагивает общие модули.

## План продолжения

Главный план: `minify-plan2.md`.

Отмечены только шаги 0 и 1. Следующий незавершённый срез — шаг 2, совместно с шагом 3:

1. Удалить остатки transcript/hook/task/session-map compatibility.
2. Оставить компактную проверку live tmux window и stale binding reconciliation.
3. Свести text path к прямому shell send/capture.
4. Не трогать `handlers/polling/`, tmux diff, `//live`, screenshot и file send.

Затем по плану идут удаление optional callback surfaces, Mini App, mailbox/msg CLI, lifecycle/status UI, конфигурации/dependencies и финальный dead-code audit.

## Важное состояние незавершённой минификации

Несмотря на отметку шага 1 в плане, в дереве всё ещё есть старые provider/transcript остатки. `rg` показывает рабочие ссылки в том числе на:

- `src/ccgram/session_map.py` и многочисленные вызовы из `session.py`;
- `src/ccgram/session_resolver.py`, `session_query.py`, `terminal_parser.py`;
- provider-aware polling/window state/toolbar/messaging code;
- старые тесты session-map, terminal parser, provider/toolbar и transcript surfaces;
- документацию и планы.

Пользователь отдельно попросил удалить совпадения `codex|claude|grok|gemini`, но `CHANGELOG.md` трогать нельзя. При удалении кода точечно чинить импорты и тесты, а не оставлять compatibility stubs только ради старых тестов.

Перед большим удалением сначала сделать read-only inventory:

```bash
rg -n -i --hidden --glob '!.git/**' --glob '!CHANGELOG.md' 'codex|claude|grok|gemini' .
```

Не удалять blindly весь файл только потому, что в docstring есть слово: сначала определить, является ли файл provider/transcript-only или содержит нужный shell/tmux/live/file путь.

## Рабочие правила

- Пользователь хочет меньше кода и poka-yoke: удалять мёртвые поверхности целиком, но сохранять минимальный рабочий мост.
- После зелёных тестов коммитить срез и держать дерево чистым.
- Не хардкодить префикс: только `config.tmux_session_prefix`.
- Не возвращать `TMUX_SESSION_NAME=ccgram-ruslan`; локальный дефолт имени tmux session — `ccgram`.
- `CHANGELOG.md` не редактировать.

## Строгие установки пользователя

- Не любит многословность в коде, планах и отчётах.
- Не любит громоздкую архитектуру, compatibility-слои и код «на всякий случай».
- Предпочитает удалить целый мёртвый модуль, чем поддерживать его заглушками.
- Главный критерий качества — не количество зелёных тестов само по себе.
- Проверять нужно именно заявленные инварианты пользователя: конкретный путь TG topic → tmux, точный формат `//ses`, число/раскладку кнопок, реальное действие `kill`, реальный screenshot, rename tmux session ↔ TG topic и сохранность `//live`/diff/file.
- Тесты должны доказывать эти конкретные свойства через проверку вызовов и результатов; широкая регрессия полезна только после точечной проверки требований.
- Если тесты зелёные, но пользовательский сценарий не доказан или формат отличается от указанного, задача не считается выполненной.
- Отчёт должен быть коротким: что изменено, какие именно пользовательские проверки пройдены, commit и состояние дерева.
