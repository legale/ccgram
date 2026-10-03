# План минификации ccgram: Прямой мост к tmux

- [x] Шаг 1: Прямое создание shell-сессий в выбранном каталоге без диалога выбора провайдеров и режимов (377408b)
- [x] Шаг 2.1: Удаление файлов сторонних провайдеров (claude, gemini, agy, codex, pi, _jsonl)
- [x] Шаг 2.2: Очистка registry, providers/__init__.py и process_detection.py, сохранение только shell-провайдера
- [x] Шаг 2.3: Удаление устаревших тестов сторонних провайдеров и обновление тестов registry
- [x] Шаг 2.4: Верификация шага 2 (make check, make test), установка в venv, перезапуск ccgram.service
- [x] Шаг 3.1: Удаление подсистемы транскриптов и хуков (transcript_parser, transcript_reader, event_reader, hook, msg_skill) (034ceb0)
- [x] Шаг 3.2: Удаление подсистемы recovery (handlers/recovery/: resume_picker, transcript_discovery, recovery_callbacks, restore_command, recovery_banner) (034ceb0)
- [x] Шаг 3.3: Очистка вызовов и зависимостей в session_monitor.py, bootstrap.py, main.py и их тестов (034ceb0)
- [x] Шаг 3.4: Верификация шага 3 (make check, make test), установка в venv, перезапуск ccgram.service (034ceb0)
- [x] Шаг 4.1: Проверка и полировка вычисления дельты экрана tmux при отправке команд (topic_status_diff.py, shell_capture.py)
- [x] Шаг 4.2: Проверка и полировка сервиса скриншотов (//screen, кнопки, рендеринг ANSI в PNG)
- [x] Шаг 4.3: Верификация шага 4 (make check, make test), установка в venv, перезапуск ccgram.service
- [x] Шаг 5.1: Очистка неиспользуемых зависимостей в pyproject.toml (deptry)
- [x] Шаг 5.2: Обновление README.md с описанием актуальной функциональности прямого моста к tmux
