# CCGram reduction analysis

Код не менялся. Кандидаты упорядочены от бесспорных к менее очевидным.

## 1. Убрать сканирование tmux-сессий без `cc_` — бесспорно

Файлы и символы:

- `src/ccgram/tmux_manager.py`
  - `TmuxManager.list_windows()`
  - `TmuxManager.list_sessions()`
- `src/ccgram/handlers/topics/topic_binding.py`
  - `_managed_sessions()`
- `src/ccgram/handlers/polling/periodic_tasks.py`
  - `_load_authoritative_sessions()`
- `src/ccgram/handlers/sessions_dashboard.py`
  - фильтрация managed sessions

Сейчас `list_windows()` отдельно разрешает `self.session_name` (`ccgram`), а `list_sessions()` получает все tmux-сессии и фильтрация выполняется позже. Это противоречит правилу: ccgram вообще не должен анализировать внешние сессии.

Удалить вместе с этим:

- специальное включение `self.session_name` в список окон;
- повторные фильтры `startswith(config.tmux_session_prefix)` у callers;
- тесты, допускающие внешние сессии.

## 2. Удалить external-session discovery — бесспорно

Файлы и символы:

- `tmux_manager.py`
  - `discover_external_sessions()`
  - `_scan_session_windows()`
  - `discover_emdash_sessions()`
  - `_external_cache`
  - `_external_cache_expires`
  - `_EXTERNAL_DISCOVERY_TTL`
  - `_kill_timed_out_proc()`, если он используется только этим кодом
  - импорт `fnmatch`
- `handlers/polling/polling_coordinator.py`
  - вызов `discover_external_sessions()`
- `config.py`
  - `tmux_external_patterns`
- `tests/ccgram/test_external_discovery.py`
- `tests/e2e/conftest.py`
  - monkeypatch `tmux_external_patterns`

Зачем существовало: поиск вручную созданных внешних tmux-сессий с AI-процессами.

Сейчас это запрещённая модель: источник правды — только `cc_*` session с `@ccgram_topic`.

## 3. Удалить emdash/foreign-session совместимость — бесспорно после пункта 2

Файлы и символы:

- `window_resolver.py`
  - `EMDASH_SESSION_PREFIX`
  - `is_foreign_window()`
  - ветки сохранения foreign IDs
- `tmux_manager.py`
  - `is_foreign_window()` в `kill_window()`
- `window_state_store.py`
  - `EXTERNAL_WINDOW_ORIGIN`
  - поле `external`
  - `WINDOW_ORIGINS`
  - `set_window_origin()`
- `window_view.py`
  - `external`, `origin`
- `session.py`
  - миграция foreign window IDs
  - проверки `is_foreign_window()`
- `mailbox.py`
  - особая обработка foreign windows
- `msg_discovery.py`
  - `external` в `WindowInfo`
- связанные тесты и документация.

Зачем существовало: не убивать и не терять окна, которыми владел emdash или другой внешний runtime.

После запрета внешних сессий это мёртвый lifecycle-код.

## 4. Удалить generalized provider detection по `pane_current_command` — бесспорно

Файлы и символы:

- `providers/__init__.py`
  - `detect_provider_from_command()`
  - `detect_provider_from_pane()`
- `tmux_manager.py`
  - provider detection внутри `_scan_session_windows()`
- `polling_state.py`
  - `PaneStatusStrategy._resolve_pane_provider()`
- поля `provider`/`provider_name`, которые нужны только для классификации провайдера;
- тесты detection/provider autodetect.

Зачем существовало: определить Claude/Codex/Gemini/Shell по foreground process в pane.

В новой модели pane не является источником типа runtime. Polling должен читать output tmux, без определения процесса.

## 5. Удалить provider façade и capability-модель — почти бесспорно

Файлы и символы:

- `providers/__init__.py`
  - `_Caps`
  - `_DummyShellProvider`
  - `get_provider_for_window()`
  - `resolve_launch_command()`
- `window_tick/observe.py`
  - `_get_provider()`
  - fallback через provider
- `window_tick/apply.py`
  - `_get_provider()`
  - проверки `supports_hook`
- provider-related поля в `WindowState`, `WindowView`, `window_query.py`.
- `providers/shell.py` как provider-класс, если оставить только прямые shell helpers.

Сейчас provider layer фактически возвращает один dummy shell provider, но вокруг него построены capability checks и интерфейс, рассчитанный на несколько AI backends.

Оставить пока отдельно проверяемые shell helpers:

- `match_prompt()`
- `setup_shell_prompt()`
- `detect_pane_shell()`

Они используются shell-командами и не обязательно относятся к provider-архитектуре.

## 6. Удалить startup recovery старой window/session-модели — почти бесспорно

Файлы и символы:

- `session.py`
  - `resolve_stale_ids()`
  - `_migrate_mailbox_ids()`
  - `sync_display_names()`
  - `prune_stale_state()`
  - `AuditIssue`, `AuditResult`, `audit_state()`
- `window_resolver.py` целиком;
- `bootstrap.py`
  - вызов `session_manager.resolve_stale_ids()`;
- миграционные тесты и state roundtrip tests.

Зачем существовало: восстанавливать старые window IDs после перезапуска tmux и поддерживать старые форматы `state.json`.

Теперь session lifecycle определяется живыми `cc_*` tmux sessions и их options. Мёртвые session/topic не должны восстанавливаться из JSON.

## 7. Убрать lifecycle-данные из `state.json` — бесспорно по требованию, но требует отдельной разбивки

Основные кандидаты:

- `session.py`
  - `_load_state()`
  - `_serialize_state()`
  - `_save_state()`
- `state_persistence.py`;
- `config.py`
  - `state_file`;
- `window_state_store.py`
  - сохранение `window_states`;
- `thread_router.py`
  - persistent `window_display_names`.

Сейчас bindings уже не восстанавливаются из `state.json`, что правильно. Но `window_states` и старые display/window записи всё ещё участвуют в определении известных окон, recovery, mailbox и dashboard.

Нужно разделить:

- допустимые пользовательские настройки;
- lifecycle и существование tmux/topic.

После этого можно будет удалить state persistence целиком либо оставить только небольшие настройки пользователя.

## 8. Упростить `ThreadRouter` до runtime-маршрутизации — почти бесспорно

Файл:

- `src/ccgram/thread_router.py`

Сейчас там одновременно:

- binding topic → window;
- reverse index;
- group chat ID;
- display names;
- persistence callbacks;
- proxy/install-механизм;
- проверка `WindowState`.

При новой модели binding уже приходит из `@ccgram_topic` tmux option. Поэтому вероятные удаления:

- `to_dict()`, `from_dict()`;
- `_schedule_save`;
- `_has_window_state`;
- display-name persistence;
- `_ThreadRouterProxy`;
- `install_thread_router()`.

Оставить временно только runtime lookup для текущего poll cycle.

## 9. Упростить polling до capture → compare → handle — кандидат, но не первый

Файлы:

- `handlers/polling/polling_coordinator.py`
- `handlers/polling/window_tick/observe.py`
- `handlers/polling/window_tick/decide.py`
- `handlers/polling/window_tick/apply.py`
- `handlers/polling/polling_state.py`
- `handlers/polling/polling_types.py`

Сейчас polling включает:

- provider status;
- shell prompt classification;
- startup timers;
- typing throttle;
- active/done/idle lifecycle;
- pane enumeration;
- pane provider classification;
- pane alerts;
- pyte screen state;
- status bubbles;
- topic emoji;
- dead-window recovery.

Целевая минимальная схема:

1. получить `cc_*` sessions;
2. получить bound sessions;
3. capture рабочего pane;
4. сравнить с предыдущим output;
5. обработать diff;
6. сохранить только previous output.

Многое из текущего polling state можно будет удалить, но сначала надо решить, сохраняем ли status emoji, live view и multi-pane функции.

## 10. Удалить multi-pane/agent-team слой — менее бесспорно

Файлы и символы:

- `polling_state.py`
  - `PaneStatusStrategy`
  - `PaneTransition`
  - `_pane_content_hash`
  - `_pane_forward_ts`
  - `_scanned_windows`
- `window_state_store.py`
  - `WindowState.panes`
  - методы pane lifecycle/subscription
- `handlers/live/pane_callbacks.py`
- pane callbacks в `screenshot_callbacks.py`
- miniapp API для отдельных panes;
- связанные тесты.

Зачем существовало: поддержка нескольких pane внутри окна, обнаружение новых pane и пересылка их вывода.

Для модели «одна `cc_*` session → один рабочий pane» это лишняя подсистема.

## 11. Удалить отдельный `topic_state_registry` и callback cleanup-архитектуру — менее бесспорно

Файлы:

- `topic_state_registry.py`;
- регистрации `@topic_state.register(...)` в:
  - `tmux_manager.py`;
  - `polling_state.py`;
  - `cleanup.py`;
  - status/live модулях.

Зачем существовало: заменить множество прямых cleanup-вызовов универсальным реестром.

Для простой модели достаточно одного явного cleanup при удалении/отвязке session. Реестр скрывает зависимости и создаёт глобальное состояние.

## 12. Удалить старые msg/discovery/session abstraction layers — пока только кандидат

Файлы:

- `msg_discovery.py`;
- `msg_cmd.py`;
- `session_resolver.py`;
- `session_query.py`;
- `mailbox.py`;
- `spawn_request.py`;
- `handlers/messaging/msg_broker.py`;
- `handlers/messaging/msg_spawn.py`.

Эти компоненты обслуживают сообщения между несколькими runtime/window, provider metadata, session IDs и spawn requests. Нужно отдельно проверить, нужны ли ещё `//send`, mailbox и spawn-flow. Удалять их бесспорно можно только после фиксации актуального набора пользовательских функций.

## 13. Удалить устаревшую provider-документацию и архитектурные схемы — после кода

Файлы:

- `docs/providers.md`;
- устаревшие части `docs/architecture.md`;
- `docs/summary.md`;
- тесты, которые фиксируют старую provider/recovery архитектуру.

В документации сейчас описаны Claude, Codex, Gemini, Pi, emdash, transcript discovery и provider picker — большая часть уже не соответствует фактическому коду и целевой модели.
