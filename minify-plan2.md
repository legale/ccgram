# План минификации ccgram, раунд 2: минимальный Telegram → tmux мост

## Цель

Оставить один понятный пользовательский сценарий:

1. Пользователь пишет в forum topic без привязки.
2. Бот принимает путь к каталогу (или текущий каталог), создаёт shell-окно tmux и привязывает его к topic.
3. Последующие текстовые сообщения отправляются в это окно; бот возвращает обычный текстовый результат команды/изменение экрана.
4. Пользователь может отвязать или закрыть привязку и создать новую.

В этот раунд не входят новые возможности, миграция старого состояния и сохранение обратной совместимости с конфигурацией `CCBOT_*`, Claude/другими агентами или прежними callback data. Код, нужный только для этих сценариев, удаляется вместе с его тестами.

## Границы минимального продукта

Оставить:

- запуск бота, авторизацию `ALLOWED_USERS`, ограничение `CCGRAM_GROUP_ID`;
- persistent mapping `user + topic -> tmux window`, создание shell-окна, поиск и проверку живого окна;
- ввод пути, создание в нём shell-сессии, отправку текста в tmux и короткую доставку shell-output в Telegram;
- обработку закрытия topic и минимальные `//commands`, `//unbind`, `//sessions` (в dashboard достаточно показать и закрыть привязку);
- состояние и tmux-операции, без которых это невозможно.

Удалить как не базовые поверхности:

- LLM-подсказки и summaries, Claude command discovery/menu, transcript/hook/task-state совместимость;
- voice/Whisper/TTS, фото и документы, inline queries;
- Mini App, live view, PNG screenshots, panes и интерактивное remote control;
- toolbar, file `//send`, history/recall, topic-tail/drafts, mailbox/`ccgram msg`, spawn requests;
- `//bind`, `//sync`, `//upgrade`, `//echo`, переименование сессий, автозакрытие, избранное каталогов и любые UI-кнопки помимо выбора/отмены каталога и минимального dashboard.

`//sessions` остаётся как dashboard привязок. Для каждой живой сессии показываются три кнопки в фиксированном порядке: `<статус> <имя>` (`+`/`-`/`o`, первая кнопка открывает rename-диалог), `scr` (screenshot) и `kill` (с подтверждением). Dashboard не должен управлять внешними окнами tmux. Если этот UI окажется заметно дороже простой команды `//unbind`, удалить его отдельным коммитом после проверки реального использования.

## Порядок работ

- [x] **Шаг 0. Зафиксировать исходную точку и контракт.** До изменений сохранить `git status --short`, не смешивать минификацию с посторонними изменениями worktree. В отдельном тесте регистрации зафиксировать ровно четыре команды минимального контракта: `commands`, `unbind`, `sessions`, `ses`. Записать фактические команды в `README.md` и удалить из него обещания, не входящие в контракт.

- [x] **Шаг 1. Удалить остатки модели провайдеров и Claude-команд.** Удалить `cc_commands.py`, `command_catalog.py`, `providers/base.py`, `providers/registry.py`, `providers/process_detection.py` и лишние API `providers/__init__.py`; заменить `ShellProvider`/`get_provider*` прямой shell-логикой там, где она действительно нужна. Удалить из `bootstrap.py` регистрацию provider command menu и из `config.py` `provider`, `claude_config_dir`, `claude_projects_path`, `session_map_file` и связанные legacy-env fallback. Удалить тесты provider/command-catalog/cc-commands. Результат: ни `AgentProvider`, ни `ProviderCapabilities`, ни пустые методы shell-провайдера не остаются в дереве.

- [ ] **Шаг 2. Вырезать остатки транскриптов, хуков и task state.** Выполнять вместе с началом шага 3: сначала отделить прямой shell send/capture path от `NewMessage` routing, затем удалить `claude_task_state.py`, `session_lifecycle.py`, `session_map.py`, `monitor_state.py`, `monitor_events.py`, `idle_tracker.py`, `session_monitor.py`, `handlers/hook_events.py` и неиспользуемые типы событий. Убрать monitor bootstrap и callback-регистрации. Оставить один компактный tmux reconciliation path: при старте и перед отправкой сверять привязку с существующим окном; удалить stale binding, если окно исчезло. Удалить тесты transcript/hook/session-monitor/monitor-state/session-map/claude-task-state и поправить bootstrap-тесты.

- [ ] **Шаг 3. Свести доставку shell-команд к прямому пути.** Выполнить вместе с шагом 2: в `handlers/text/text_handler.py` оставить unbound topic → directory input → create/bind и bound topic → `send_to_window`; оставить один capture/output loop с ограничением Telegram. Убрать `NewMessage` routing, режимы `!`/approval, LLM command generation, prompt orchestration, tool-call batching, status snapshot, draft stream, реакции и очереди. Удалить `handlers/shell/shell_commands.py`, `shell_context.py`, `shell_prompt_orchestrator.py`, большую часть `shell_capture.py`, весь `handlers/messaging_pipeline/`, `handlers/messaging/`, `response_builder.py`, `reactions.py`, `telegram_draft.py`, `telegram_sender.py` (перенести нужный маленький split/send helper локально). В `bootstrap.py` убрать worker shutdown и periodic broker wiring. Сохранить тесты только для создания, отправки, capture, отмены/ошибки и лимита сообщения.

- [ ] **Шаг 4. Удалить все неосновные Telegram-входы и callback UI.** Удалить регистрации и модули: `handlers/voice/`, `whisper/`, `tts/`, `handlers/file_handler.py`, `handlers/inline.py`, `handlers/interactive/`, live view/panes/remote-control, `terminal_parser.py`, `screen_buffer.py`, `handlers/send/`, `handlers/toolbar/`, `toolbar_config.py`, `handlers/command_history.py`, `topic_tail.py`, `handlers/sync_command.py`, `handlers/upgrade.py`, `handlers/echo_command.py`. Сохранить минимальный screenshot callback, необходимый для кнопки `scr` в `//ses`; остальные live/screenshot callbacks удалить. Упростить `callback_registry.load_handlers()` до directory-confirm/cancel и sessions callbacks; затем удалить registry целиком, если directory selection станет текстовым без callback. Удалить соответствующие handler/unit/integration/e2e тесты, fixtures и статические/шрифтовые assets.

- [ ] **Шаг 5. Удалить Mini App и HTTP-слой.** Удалить `miniapp/`, `start_miniapp_if_enabled()`/`stop_miniapp_if_enabled()` из `main.py`, lifecycle-вызовы из `bootstrap.py`, dashboard web-app кнопку и API-тесты. После этого удалить `aiohttp` из runtime dependencies и все `CCGRAM_MINIAPP_*` параметры.

- [ ] **Шаг 6. Удалить mailbox и самостоятельный CLI обмен между агентами.** Выполнить после замены session-map reconciliation из шага 2: удалить `mailbox.py`, `msg_cmd.py`, `msg_discovery.py`, `spawn_request.py`, handler-модули messaging broker/spawn и их callback imports. Удалить `msg` Click group из `cli.py`, миграцию/prune mailbox из `session.py` и `mailbox_dir`/`CCGRAM_MSG_*` конфигурацию. Удалить все связанные тесты.

- [ ] **Шаг 7. Сжать lifecycle/topic/session UI.** Убрать adoption внешних окон, bind picker, favorites, user preferences, pane lifecycle, autoclose и status bubble/status polling. Сохранить rename, необходимый первой кнопке `//ses`, включая обновление имени Telegram topic. Сократить `directory_browser.py` до текста «введи путь» с кнопками `Select current`/`Cancel` либо полностью текстового выбора. Свести `sessions_dashboard.py` к списку собственных привязок + кнопки rename/scr/kill и удалить его, если после измерения он не оправдан. В `thread_router.py`, `session.py`, `window_state_store.py`, `window_query.py`, `window_resolver.py` и `tmux_manager.py` оставить только поля и операции минимального сценария; убрать provider/approval/origin/transcript/display-state поля и миграции.

- [ ] **Шаг 8. Сжать конфигурацию, CLI, зависимости и документацию.** В `config.py` оставить только token, allowed users, group id, config/state path, tmux session/prefix и capture timeout/лимит; удалить все неиспользуемые env vars и `CCBOT_*` aliases. В `cli.py` оставить `run` и при необходимости компактный `status`; удалить `doctor`, hook и прочие ветви, если они не нужны для запуска моста. Удалить зависимости после последнего использования: `httpx`, `Pillow`, `telegramify-markdown`, `aiofiles`, `pyte`, `pathspec`, `aiohttp`, optional `edge-tts`; обновить lockfile через `uv lock`. Сократить README, `docs/guides.md`, примеры, CHANGELOG и scripts до фактической установки/настройки моста.

- [ ] **Шаг 9. Финальный аудит мёртвого кода и тестового груза.** Построить список импортов от entry point `ccgram.main:main` и зарегистрированных PTB handlers; для каждого production-модуля оставить явный путь достижимости. Найти оставшиеся импортные ссылки через `rg`, удалить orphan tests и test-only compatibility APIs. Проверить, что `pyproject.toml` не содержит dependency, config option, script или extra без production use. Не добавлять compatibility stubs ради старых тестов: тест удаляется или переписывается под минимальный контракт.

## Разбиение на коммиты

1. `providers: remove shell-provider compatibility and command catalog`
2. `monitor: route shell directly and remove transcript/hook compatibility`
3. `mailbox: remove inter-agent messaging from reconciliation`
4. `telegram: remove optional input and callback surfaces`
5. `miniapp: remove dashboard server`
6. `sessions: retain bridge bindings with rename/scr/kill dashboard`
7. `config: remove unused options and dependencies`
8. `docs: describe minimal tmux bridge and final audit`

Один коммит — один удаляемый вертикальный срез, включая его config, docs и tests. Не смешивать форматирование всего репозитория с удалением.

## Проверка после каждого среза

- `make fmt lint typecheck deptry test`;
- целевые integration tests для создания topic → выбора каталога → отправки команды → получения output → unbind/dead-window cleanup;
- ручной smoke: запустить `ccgram`, создать новый forum topic, выбрать текущий каталог, отправить `pwd`, убедиться, что `//unbind` перестаёт отправлять текст в окно;
- в финале `make check` и `uv build`.

Если существующий `make check` зависит от удалённой возможности, сначала заменить его тестом минимального контракта, а не оставлять заглушку только ради прохождения набора.

## Риски

- Удаление LLM path меняет семантику обычного текста: он становится буквальной строкой для shell. Это целевое поведение и должно быть прямо указано в README.
- Удаление live view и лишнего screenshot UI не должно удалять минимальный `scr` callback из `//ses`; после обычной команды пользователь получает текстовый output, а не постоянно обновляемую панель.
- Удаление старых state migrations не переносит существующие mailbox/transcript/provider записи. Допустимый способ восстановления — отвязать topic и создать чистую shell-привязку.
- Код tmux discovery и persistent bindings нужно проверять с реальными `@window_id`: нельзя заменить его парсингом названия окна, иначе повторятся проблемы со старыми/двойными префиксами.
