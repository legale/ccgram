# CCGram

CCGram связывает Telegram forum topics с tmux-сессиями. Сообщения из topic
передаются в его tmux-сессию, а изменения терминала публикуются обратно в
тот же topic.

## Модель данных

Для каждого рабочего topic используется одна tmux-сессия:

```text
Telegram topic: antig
tmux session:   cc_antig
binding:        @ccgram_topic = <chat_id>:<thread_id>
```

Префикс `cc_` — служебная часть имени tmux-сессии. В названии Telegram topic
он не показывается.

Источником истины для рабочих сессий являются tmux-сессии с настроенным
префиксом. `reconcile`:

- восстанавливает runtime-маршрутизацию из `@ccgram_topic`;
- синхронизирует имя Telegram topic с именем tmux-сессии без `cc_`;
- очищает runtime-привязки, которых больше нет среди живых tmux-сессий;
- проверяет привязанные Telegram thread IDs и удаляет мёртвые привязки вместе
  с tmux-сессией.

## Создание и привязка topic

Создай topic в Telegram вручную. `topic_created_handler` найдёт существующую
сессию `cc_<name>` или создаст её, запишет Telegram identity в tmux и свяжет
объекты.

После создания бот отправляет служебное сообщение:

```text
topic foo created
tmux cc_foo created and bound
```

General topic (`all`) — служебный. Обычный текст в нём не выполняется в shell
и не создаёт `cc_all`; бот показывает подсказку использовать именованный
topic.

## Команды

Команды бота начинаются с `//`, чтобы обычные shell-команды с `/` не
перехватывались.

| Команда | Назначение |
| --- | --- |
| `//commands` | Показать список команд |
| `//bind <name>` | Привязать текущий topic к `cc_<name>` |
| `//unbind` | Отвязать topic и закрыть его, сохранив tmux-сессию |
| `//sessions` / `//ses` | Показать рабочие tmux-сессии |
| `//screenshot` / `//screen` | Получить снимок терминала |

`//detach` больше не используется; актуальное имя команды — `//unbind`.

## Синхронизация имён и закрытие

При ручном переименовании topic `foo` → `bar` обработчик переименовывает
tmux-сессию `cc_foo` → `cc_bar`. Reconcile не добавляет `cc_` в Telegram
название и не запускает цикл переименований.

При ручном закрытии или удалении Telegram topic связанная tmux-сессия и
runtime binding удаляются. Чтобы закрыть topic без удаления tmux-сессии,
используй `//unbind`.

## Установка

Требования:

- Python 3.13+;
- установленный `tmux` в `PATH`;
- Telegram-бот с доступом к forum topics.

```bash
uv tool install ccgram
# или
pipx install ccgram
```

Для локальной разработки:

```bash
git clone https://github.com/alexei-led/ccgram.git
cd ccgram
uv sync --extra dev
```

## Настройка Telegram

1. Создай бота через [@BotFather](https://t.me/BotFather).
2. Отключи Group Privacy, чтобы бот видел сообщения topic.
3. Добавь бота в Telegram-группу с включёнными Topics.
4. Выдай боту права администратора на создание и удаление topic и закрепление
   сообщений.
5. Создай `~/.ccgram/.env`:

```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USERS=your_telegram_user_id
CCGRAM_GROUP_ID=your_telegram_group_id
```

`CCGRAM_GROUP_ID` необязателен. Без него бот принимает сообщения из всех
групп, доступных ему.

Запуск:

```bash
ccgram
```

## Основные настройки

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | — | Токен Telegram-бота |
| `ALLOWED_USERS` | — | Разрешённые Telegram user IDs |
| `CCGRAM_GROUP_ID` | все группы | Ограничение одной группой |
| `CCGRAM_DIR` | `~/.ccgram` | Каталог конфигурации и состояния |
| `TMUX_SESSION_PREFIX` | `cc_` | Префикс управляемых tmux-сессий |
| `CCGRAM_PROVIDER` | `claude` | Провайдер запуска сессий |
| `CCGRAM_TOPIC_STATUS_DIFF_INTERVAL` | `10` | Интервал обновления screen diff |
| `AUTOCLOSE_DONE_MINUTES` | `30` | Автозакрытие завершённых topic |
| `AUTOCLOSE_DEAD_MINUTES` | `10` | Автозакрытие мёртвых сессий |
| `CCGRAM_MINIAPP_BASE_URL` | выключен | Публичный HTTPS URL Mini App |
| `CCGRAM_MINIAPP_HOST` | `127.0.0.1` | Локальный адрес Mini App |
| `CCGRAM_MINIAPP_PORT` | `8765` | Локальный порт Mini App |

Дополнительные параметры описаны в
[docs/guides.md](docs/guides.md#configuration).

## Screen diff

Первый снимок новой сессии используется только как baseline и не отправляется
в Telegram. Следующее изменение экрана отправляется как `Screen delta`; бот
редактирует последнее сообщение diff, если это безопасно, или создаёт новое.

## Mini App

Mini App отключён по умолчанию. При настройке
`CCGRAM_MINIAPP_BASE_URL` доступны live terminal, transcript и multi-pane
dashboard внутри Telegram WebApp. Публичный URL должен работать по HTTPS;
локальный aiohttp-сервер можно закрыть reverse proxy.

## Разработка и проверки

```bash
uv sync --extra dev
make test
make check
```

`make check` выполняет форматирование, lint, typecheck, unit-тесты и
integration-тесты. Для быстрых изменений запускай только затронутые тесты:

```bash
uv run pytest -q tests/ccgram/handlers/topics/
```

## Документация

- [docs/guides.md](docs/guides.md) — конфигурация, CLI, восстановление и
  тестирование;
- [docs/providers.md](docs/providers.md) — провайдеры и команды запуска.

## Лицензия

[MIT](LICENSE)
