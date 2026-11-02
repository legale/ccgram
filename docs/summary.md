# ccgram: контекст и changelog ветки `refactor/tmux-only`

## Идея

`ccgram` — минималистичный и надежный Telegram-мост к tmux:

```text
Telegram topic <───> ccgram <───> tmux session / window <───> shell
```

Ветка полностью перевела проект в режим `tmux-only`:

- единственный рабочий провайдер — обычный shell;
- Telegram topic привязывается непосредственно к tmux window/session;
- входящий текст отправляется в tmux через `send-keys`;
- состояние терминала читается через `capture-pane` и транслируется в виде diff;
- имя topic и имя tmux-сессии синхронизируются;
- поддержка изолированного выполнения команд в sidecar pane (`!!<команда>`) с отдельным diff-сообщением;
- все лишние прослойки (AI runtimes, status line regex parsing, topic_state_registry, legacy multi-pane monitor) удалены.

---

## Статус выполнения плана рефакторинга (`docs/red.md`)

1. **Пункт 1**: Удалены AI-agent providers (Claude, Codex, Gemini, Pi) и их runtime.
2. **Пункт 2**: Удалены transcript discovery, watcher и парсинг логов.
3. **Пункт 3**: Удален resume/recovery flow, hooks и сессионные маркеры.
4. **Пункт 4**: Удален Voice / audio / TTS / Whisper стек.
5. **Пункт 5**: Удалены Remote control, status esc, notify toggle и callback-кнопки переключения режимов.
6. **Пункт 6**: Удален legacy provider picker / provider switch runtime.
7. **Пункт 7**: Удалены сессионные lock-файлы и jsonl event logs.
8. **Пункт 8**: Удалены эвристический парсинг строки статуса (regex) и dashboard активности.
9. **Пункт 9**: Реализован Sidecar execution (`!!<cmd>`): запуск команды во второй pane сессии, трансляция вывода в отдельное diff-сообщение, автоматическое закрытие по завершении команды.
10. **Пункт 10**: Удален устаревший `PaneStatusStrategy` и legacy multi-pane monitoring layer.
11. **Пункт 11**: Удален `TopicStateRegistry` и `@topic_state.register` декораторы; заменен на явные прямые вызовы очистки в `handlers/cleanup.py`.
12. **Пункт 12**: Команда `//send` и `mailbox/` сохранены как необходимый механизм межсессионного взаимодействия.
13. **Пункт 13**: Удалена устаревшая provider-документация (`docs/providers.md`), обновлены `docs/architecture.md` и `docs/summary.md`.

---

## Актуальное состояние

- Репозиторий: `/home/ruslan/ccgram`
- Ветка: `refactor/tmux-only`
- Remote: `origin/refactor/tmux-only`
- Служба: `ccgram.service` (активна в systemd)
- Тесты: 2507 unit-тестов и 148 интеграционных тестов проходят успешно.
