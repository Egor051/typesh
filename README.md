# Discord-бот виджета Breaking Proxy

Лёгкий Python-бот, который периодически читает виджет с `breaking.proxy.sqstat.ru`,
извлекает данные по двум карточкам (`RAAS/AAS` и `SPEC OPS`) и обновляет **одно** сообщение
в Discord-канале.

## Что делает бот

- открывает страницу источника и дожидается рендера карточек;
- парсит только нужные карточки серверов;
- извлекает онлайн и название карты;
- отправляет служебное сообщение один раз и дальше редактирует его;
- хранит `message_id` и последнее успешное состояние в JSON-файле;
- пишет короткие, читаемые логи без лишнего «шума».

## Стек

- Python 3.11+
- `discord.py`
- `playwright` (для рендеринга DOM)
- `python-dotenv`

## Структура проекта

```text
.
├── bot/
│   ├── config.py          # конфигурация из env
│   ├── embeds.py          # формирование Discord embeds
│   ├── models.py          # модели данных
│   ├── parser.py          # парсер HTML-карточек
│   ├── state.py           # JSON-хранилище состояния
│   └── widget_updater.py  # цикл обновления и редактирование сообщения
├── main.py                # точка входа
├── requirements.txt
└── state.json             # файл состояния (можно переопределить через STATE_FILE)
```

## Быстрый старт

1. Создайте виртуальное окружение и активируйте его:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

2. Установите Python-зависимости:

   ```bash
   pip install -r requirements.txt
   ```

3. Установите браузер для Playwright:

   ```bash
   python -m playwright install chromium
   ```

4. Создайте `.env` на основе примера:

   ```bash
   cp .env.example .env
   ```

5. Заполните переменные окружения (минимум `DISCORD_TOKEN` и `CHANNEL_ID`).

6. Запустите:

   ```bash
   python main.py
   ```

## Переменные окружения

| Переменная | Обязательна | По умолчанию | Описание |
|---|---:|---|---|
| `DISCORD_TOKEN` | ✅ | — | токен Discord-бота |
| `CHANNEL_ID` | ✅ | — | ID канала для виджета |
| `UPDATE_INTERVAL_SECONDS` | ❌ | `180` | интервал обновления, сек (минимум `5`) |
| `BASE_URL` | ❌ | `https://breaking.proxy.sqstat.ru` | URL источника данных |
| `STATE_FILE` | ❌ | `state.json` | путь к JSON-файлу состояния |
| `LOG_LEVEL` | ❌ | `INFO` | уровень логирования (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

## Логи

Логи сделаны в коротком и стабильном формате:

```text
12:30:18 | I | bot.widget_updater | Widget initialized | channel_id=123 | message_id=456
12:33:18 | I | bot.parser | Snapshot parsed | cards=2 | RAAS/AAS: online=74 map=Al Basrah AAS v1 | SPEC OPS: online=9 map=Narva RAAS v2
12:33:18 | I | bot.widget_updater | Widget heartbeat: data unchanged
```

Что важно:

- время в формате `HH:MM:SS`;
- уровень логирования — одной буквой (`D/I/W/E`) для удобного сканирования;
- парсер пишет сводку по снимку одной строкой вместо большого количества промежуточных сообщений.

## Локальная разработка

- Для отладки структуры источника можно использовать файлы:
  - `debug_breaking.html`
  - `debug_card_raas_aas.html`
  - `debug_card_spec.html`
- Файл `state.json` можно удалить при необходимости «сбросить» сохранённый `message_id`.

## Поведение при ошибках

- недоступность сайта/пустой ответ: ошибка логируется, цикл продолжается;
- отсутствие карточки: для неё возвращается пустая структура;
- отсутствие карты/онлайна: поля остаются пустыми без падения, в лог пишется предупреждение;
- если `message_id` не найден: создаётся новое сообщение и ID сохраняется.

## Деплой (пример: bothost.ru)

1. Поднимите окружение с Python 3.11+.
2. Загрузите проект и добавьте `.env`.
3. Установите зависимости: `pip install -r requirements.txt`.
4. Установите браузер: `python -m playwright install chromium`.
5. Запускайте `python main.py`.
6. Убедитесь, что путь `STATE_FILE` доступен для записи.

## Лицензия

Укажите нужную лицензию перед публикацией (например, MIT).