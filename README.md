# CCGram

CCGram связывает Telegram forum topics с tmux-сессиями. Сообщения из топика передаются в привязанную tmux-сессию, а изменения терминала публикуются обратно в тот же топик.

## Архитектура и спецификация

- [docs/specs.md](docs/specs.md) — актуальный список команд, интерактивных возможностей и механизм связывания топиков с tmux (`@ccgram_topic`).
- [docs/architecture.md](docs/architecture.md) — обзор подсистем и структуры модулей.

## Установка

Требования:

- Python 3.13+
- `tmux` в `PATH`
- Telegram-бот с правами администратора в супергруппе с включёнными Topics

```bash
uv tool install ccgram
# или для локальной разработки:
git clone https://github.com/alexei-led/ccgram.git
cd ccgram
uv sync --extra dev
```

## Настройка

Создайте `~/.ccgram/.env` (или локальный `.env`):

```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USERS=123456789
# Опционально: ограничить одной группой
# CCGRAM_GROUP_ID=-1001234567890
```

Полный перечень параметров и значений по умолчанию находится в [.env-default](.env-default).

## Запуск и проверка

```bash
ccgram          # запуск бота
ccgram status   # список управляемых tmux-сессий и привязок @ccgram_topic
make check      # форматирование, lint, typecheck, deptry, unit и integration тесты
```

## Лицензия

[MIT](LICENSE)
