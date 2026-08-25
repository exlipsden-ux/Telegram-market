# telegram market

Telegram-бот и Mini App: магазин цифровых товаров. На старте — VPN-подписки,
дальше Telegram Stars и остальные разделы.

## Стек

- Python 3.13, [aiogram 3](https://docs.aiogram.dev/) — бот
- aiohttp — раздача витрины и HTTP-API
- SQLite (aiosqlite) — пользователи и заказы
- Витрина — HTML/CSS/JS без сборщиков

## Запуск

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\python -m bot
```

Токен бота берётся из `.env`, его в репозитории нет.

## Как устроено

| Файл | Назначение |
|---|---|
| `bot/main.py` | точка входа: бот, веб-сервер, туннель |
| `bot/config.py` | чтение `.env` |
| `bot/catalog.py` | товары, цены, проверка заказа |
| `bot/db.py` | схема и запросы к SQLite |
| `bot/handlers.py` | команды бота и приём платежей |
| `bot/webapi.py` | статика витрины и `/api/*` |
| `bot/tunnel.py` | SSH-туннель с автоподъёмом |
| `webapp/` | витрина Mini App |

## Оплата

Подключены Telegram Stars, CryptoBot, Lolz Market, PayCore (СБП/карта) и TON напрямую.

Сумма всегда пересчитывается на сервере из `bot/catalog.py` — цене,
пришедшей от клиента, доверять нельзя. Каждый запрос к API проверяется
по подписи `initData` от Telegram.

## Локальный адрес витрины

Telegram открывает Mini App только по HTTPS, поэтому бот сам поднимает
SSH-туннель до localhost.run и подставляет адрес в кнопку меню.
Для боевого хостинга — `TUNNEL=0` и заполненный `WEBAPP_URL`.
