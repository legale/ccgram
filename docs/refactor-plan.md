# ccgram Refactor Commit Plan

## Summary

Плоский план из 10 независимых коммитов, в порядке снижения практической
отдачи. Цель: сначала вернуть зелёные проверки, потом убрать stale-конфиги и
самые шумные участки, без архитектуры "на будущее" и без изменения поведения.

## Commit Tasks

1. `fix(check): restore static checks`
   - Исправить `ruff` N806 в `tests/ccgram/tts/test_edge.py`.
   - Починить `pyright` для optional `edge_tts` в `src/ccgram/tts/edge.py` без
     добавления обязательной зависимости.
   - Проверки: `make lint`, `make typecheck`.

2. `test(config): guard stale ruff ignore paths`
   - Удалить устаревшие `per-file-ignores` для несуществующих путей в
     `pyproject.toml`.
   - Добавить маленький тест, что все точные пути в
     `tool.ruff.lint.per-file-ignores` существуют.
   - Проверки: `make lint`, targeted test.

3. `refactor(tts): keep optional edge backend simple`
   - Упростить import/fallback в `tts/edge.py`: один явный флаг доступности,
     один кортеж backend-ошибок, без `Any`-шума где можно.
   - Не менять public API `EdgeTtsSynthesizer`.
   - Проверки: `uv run pytest tests/ccgram/tts/ -q`.

4. `refactor(polling): stop rebuilding telegram client in tick apply`
   - В `handlers/polling/window_tick/apply.py` создать `PTBTelegramClient` один
     раз на переход и передавать дальше в локальные helpers.
   - Не менять `decide.py` и pure-модель polling.
   - Проверки: `uv run pytest tests/ccgram/handlers/polling/ -q`.

5. `refactor(live): collapse repeated topic window resolution`
   - В `handlers/live/screenshot_callbacks.py` вынести локальный helper для
     повторяющейся проверки: user allowed, topic id, bound window, live tmux
     window.
   - Оставить handler flow линейным; без нового слоя или общего
     framework-helper.
   - Проверки: `uv run pytest tests/ccgram/handlers/live/ -q`.

6. `refactor(transcript): split parse_entries by event kind`
   - Разрезать `transcript_parser.parse_entries` на маленькие private helpers по
     существующим веткам обработки.
   - Сохранить порядок сообщений, tool-use/tool-result pairing и все return
     shapes.
   - Проверки: `uv run pytest tests/ccgram/test_transcript_parser.py
     tests/ccgram/providers/ -q`.

7. `refactor(tmux): centralize tmux subprocess timeout cleanup`
   - В `tmux_manager.py` убрать повтор timeout/kill/wait вокруг
     `create_subprocess_exec`.
   - Helper оставить private и локальным для файла; не менять публичный
     `TmuxManager`.
   - Проверки: `uv run pytest tests/ccgram/test_tmux_send_keys.py
     tests/ccgram/test_tmux_autodetect.py -q`.

8. `refactor(topics): share create-and-bind window path`
   - Убрать дублирование между new-session и recovery create/bind flow.
   - Общий код держать близко к topics/recovery handlers, без нового
     service-layer.
   - Проверки: `uv run pytest tests/ccgram/handlers/topics/
     tests/ccgram/handlers/recovery/ -q`.

9. `refactor(imports): reduce cheap lazy imports`
   - Пройти top lazy-import files и поднять только те imports, которые не
     создают cycle и не ломают test wiring.
   - После каждого блока держать зелёными `scripts/lint_lazy_imports.py` и
     import-cycle test.
   - Проверки: `make lint-lazy`,
     `uv run pytest tests/integration/test_import_no_cycles.py -q`.

10. `docs(refactor): refresh architecture notes`
    - Обновить `docs/architecture.md` и `docs/ai-agents/codebase-index.md`
      только по фактически сделанным изменениям.
    - Не переписывать диаграммы целиком, если поменялись только локальные
      детали.
    - Проверки: `make test`.

## Test Plan

- После каждого коммита запускать targeted tests из задачи.
- После коммита 1 запускать `make lint` и `make typecheck`.
- После коммитов 6, 8, 9 запускать import-cycle/lazy-import проверки.
- Перед финальным коммитом запускать `make test`; если менялись docs only,
  отдельно указать, что кодовые тесты не требовались.

## Assumptions

- Python 3.14 обязателен; синтаксис `except A, B:` и PEP 695 type syntax не
  считаются проблемой.
- Behavior changes не входят в этот refactor plan.
- Новые публичные API не добавляются, кроме минимальных private helpers внутри
  существующих модулей.
