# ccgram: контекст и changelog ветки `refactor/tmux-only`

## Идея из `ctx2.md`

`ccgram` должен быть простым Telegram-мостом к tmux:

```text
Telegram topic <-> ccgram <-> tmux window/session <-> shell
```

Ветка переводит проект в режим `tmux-only`:

- единственный рабочий провайдер — обычный shell;
- Telegram topic привязывается непосредственно к tmux window;
- входящий текст отправляется в tmux через `send-keys`;
- состояние терминала читается через `capture-pane` и polling;
- имя topic и имя tmux-сессии должны синхронизироваться;
- сложные прослойки, не нужные для shell-моста, удаляются.

Из архитектуры убраны AI-agent providers, transcript/log parsing, recovery/resume, hooks, remote control, voice/TTS и связанная с ними совместимость. В результате ccgram отвечает за маршрутизацию сообщений, жизненный цикл topic, tmux-сессии и отображение терминала, но не пытается быть отдельным runtime для AI-агентов.

## Актуальное состояние

- Репозиторий: `/home/ruslan/ccgram`
- Ветка: `refactor/tmux-only`
- HEAD: `06d80f9 fix: keep screen diffs aligned with session messages`
- Remote: `origin/refactor/tmux-only` синхронизирован с HEAD
- Сервис: `ccgram.service`, активен
- Единственный провайдер: `shell`
- Префикс tmux-сессий: `ccgram_`, источник — конфигурация
- Основной polling читает tmux и обновляет status, topic emoji и Screen delta
- Passive shell relay отключён из polling; Screen delta обслуживается только `topic_status_diff`

## Основные компоненты

| Компонент | Ответственность |
|---|---|
| `tmux_manager.py` | Создание, поиск, переименование, удаление и чтение tmux windows/sessions |
| `thread_router.py` | Связь `(user_id, thread_id)` с `window_id`, display names и group chat IDs |
| `session.py` | Состояние и метаданные сессий |
| `handlers/text/text_handler.py` | Ранний роутинг входящего текста, directory flow и отправка текста в tmux |
| `handlers/sessions_dashboard.py` | Dashboard sessions, rename, screenshot и kill |
| `handlers/polling/` | Фоновый polling tmux, status и dead-window detection |
| `handlers/status/topic_status_diff.py` | Единственный механизм Screen snapshot/delta сообщений |
| `handlers/topics/` | Создание topic, bind, unbind и directory/window callbacks |
| `telegram_client.py` | Telegram client protocol и PTB adapter |

## Changelog относительно `origin/main`

### Переход к tmux-only

- Удалены внешние AI-agent providers, оставлен shell provider.
- Удалены transcript parsers, hooks, recovery/resume и связанные runtime-пути.
- Удалены voice transcription/TTS, echo/upgrade и другие необязательные handler surfaces.
- Удалён remote-control и его кнопка.
- Убран transcript-dependent activity monitor и interactive UI path.
- Изменён префикс команд бота на `//`.
- Текст topic направляется напрямую в tmux.
- Создание shell-сессии выполняется непосредственно после выбора директории, без provider picker.

### Сессии и topic routing

- Добавлено создание или подключение named tmux-сессий из topic.
- Dashboard показывает tmux windows и основные действия.
- Добавлены rename, screenshot и kill для сессий.
- Добавлен alias `//ses`.
- Имена Telegram forum topics синхронизируются с именами сессий при rename.
- Исправлены false dead notifications во время переименования.
- Исправлен rebind unbound topic по имени сессии.
- Исправлена работа polling после unsafe tmux window.
- Уточнён единый источник префикса tmux-сессии в конфигурации.

### Polling и отображение терминала

- Polling синхронизирует tmux windows и polling threads.
- Screen diff больше не зависит от старых transcript/activity механизмов.
- Исправлена обработка прокрутки терминала и scrolled-out строк.
- Screen delta привязан к актуальности сообщений topic:
  - timestamp последнего входящего сообщения обновляется в начале обработки;
  - diff сравнивает timestamp последнего сообщения с timestamp текущего diff;
  - при новом сообщении создаётся новое diff-сообщение;
  - незавершённый edit отменяется при новом сообщении;
  - решения `edit`, `send` и отмена edit логируются.
- Удалён passive shell relay из polling. Он больше не отправляет отдельные сообщения с выводом команд из tmux.
- Удалено idle status bubble с кнопками; idle представлен состоянием topic.
- Остановлен лишний typing indicator во время idle polling.

### UX и команды

- Сокращено подтверждение kill session.
- Добавлено ограничение размера файлов.
- Сохранены screenshot/live/panes/toolbar/history/recall/verbose/toolcalls/send и session dashboard surfaces, относящиеся к tmux-мосту.
- Исправлена приоритизация session rename input.

### Тесты и структура

- Обновлены тесты после удаления provider/recovery/remote-control поверхностей.
- Добавлены проверки создания topic с tmux-сессией.
- Добавлены проверки roundtrip для topic command output diff.
- Обновлены mocks для session API и async queue cleanup.
- Добавлены регрессионные тесты для Screen delta и порядка сообщений.
- Сохранены layering invariants для handler-архитектуры.

## Оставшиеся вопросы из контекста

- Нужно проверить и при необходимости сделать одноразовую синхронизацию старых Telegram topic names с переименованными tmux-сессиями.
- В tmux могут оставаться старые unbound windows с двойными или тройными префиксами; автоматическая очистка не реализована.
- Нужно отдельно проверить актуальность `//bind`, `//unbind` и `//history` после tmux-only упрощения.
- Не следует возвращать passive shell relay без отдельного требования: сейчас он намеренно отключён, чтобы не дублировать Screen delta.

## Операционные команды

```bash
make -C /home/ruslan/ccgram check
systemctl --user status ccgram.service
systemctl --user restart ccgram.service
```

Для шумных проверок использовать wrapper из `ucli` skill. Файл `/home/ruslan/.ccgram/.env` не изменять.
