"""HTTP-слой: только серверные вебхуки подтверждения оплаты.

Каталог/покупка/оплата теперь целиком в чате бота (bot/handlers.py).
Здесь остаются исключительно колбэки провайдеров — им всё равно нужен
публичный HTTPS-адрес, и переносить их некуда.
"""

from __future__ import annotations

import json
import logging

from aiogram import Bot
from aiohttp import web

from .config import Config
from .cryptobot import CryptoBot
from .db import Database
from .lolz import Lolz
from .paycore import WEBHOOK_IP as PAYCORE_WEBHOOK_IP
from .paycore import PayCore
from .providers import Providers
from .robokassa import Robokassa

log = logging.getLogger("webapi")


@web.middleware
async def no_store(request: web.Request, handler):
    response = await handler(request)
    response.headers.setdefault("Cache-Control", "no-store")
    return response


def _bot_for(request: web.Request, order) -> Bot:
    """Заказ мог быть создан в любом из двух ботов (VPN/звёзды) — уведомляем через тот же.
    Для заказов до разделения ботов (bot_id ещё NULL) — берём первого попавшегося."""
    bots: dict[int, Bot] = request.app["bots"]
    bot_id = order["bot_id"] if order is not None else None
    return bots.get(bot_id) or next(iter(bots.values()))


async def crypto_webhook(request: web.Request) -> web.Response:
    """Подтверждение оплаты от CryptoBot."""
    crypto: CryptoBot = request.app["crypto"]
    db: Database = request.app["db"]

    raw = await request.read()

    if not crypto.check_signature(request.headers.get("crypto-pay-api-signature", ""), raw):
        log.warning("вебхук с неверной подписью отброшен")
        raise web.HTTPUnauthorized(reason="bad signature")

    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        raise web.HTTPBadRequest(reason="bad json") from None

    if event.get("update_type") != "invoice_paid":
        return web.json_response({"ok": True})

    invoice = event.get("payload", {})
    prefix, _, raw_id = str(invoice.get("payload", "")).partition(":")

    if prefix != "order" or not raw_id.isdigit():
        log.warning("вебхук с непонятным payload: %s", invoice.get("payload"))
        return web.json_response({"ok": True})

    order_id = int(raw_id)
    first = await db.mark_paid(order_id, "cryptobot", str(invoice.get("invoice_id")))
    order = await db.get_order(order_id)

    if order and order["product"] == "topup":
        balance = (
            await db.add_balance(order["user_id"], order["amount"])
            if first
            else await db.balance_of(order["user_id"])
        )
        log.info("баланс #%s пополнен на %s ₽", order["user_id"], order["amount"])
        await _bot_for(request, order).send_message(
            order["user_id"],
            f"✅ Баланс пополнен на {order['amount']:.0f} ₽\n"
            f"Текущий баланс: <b>{balance:.0f} ₽</b>",
        )
    elif order:
        log.info("заказ #%s оплачен криптой: %s %s", order_id, invoice.get("amount"), invoice.get("asset"))
        await _bot_for(request, order).send_message(
            order["user_id"],
            f"✅ Оплачено — {invoice.get('amount')} {invoice.get('asset')}\n\n"
            f"<b>{order['title']}</b>\nЗаказ №{order_id}\n\n"
            "Ключ доступа придёт сюда же в течение минуты.",
        )

    return web.json_response({"ok": True})


async def robokassa_result(request: web.Request) -> web.Response:
    """Result URL: Robokassa подтверждает оплату. Ответ должен быть 'OK{InvId}'."""
    rk: Robokassa = request.app["robokassa"]
    db: Database = request.app["db"]

    data = dict(await request.post()) if request.method == "POST" else dict(request.query)
    order_id = rk.verify_result({k: str(v) for k, v in data.items()})

    if order_id is None:
        raise web.HTTPForbidden(reason="bad signature")

    first = await db.mark_paid(order_id, "robokassa", None)
    order = await db.get_order(order_id)

    if order and order["product"] == "topup":
        balance = (
            await db.add_balance(order["user_id"], order["amount"])
            if first
            else await db.balance_of(order["user_id"])
        )
        log.info("Robokassa: баланс #%s пополнен на %s ₽", order["user_id"], order["amount"])
        await _bot_for(request, order).send_message(
            order["user_id"],
            f"✅ Баланс пополнен на {order['amount']:.0f} ₽\nТекущий баланс: <b>{balance:.0f} ₽</b>",
        )
    elif order and first:
        log.info("Robokassa: заказ #%s оплачен", order_id)
        await _bot_for(request, order).send_message(
            order["user_id"],
            f"✅ Оплачено — {order['amount']:.0f} ₽\n\n<b>{order['title']}</b>\nЗаказ №{order_id}\n\n"
            "Ключ доступа придёт сюда же в течение минуты.",
        )

    return web.Response(text=f"OK{order_id}")


async def lolz_webhook(request: web.Request) -> web.Response:
    """Подтверждение оплаты от Lolz Market."""
    lolz: Lolz = request.app["lolz"]
    db: Database = request.app["db"]

    if not lolz.check_signature(request.headers.get("x-secret-key", "")):
        log.warning("Lolz-вебхук с неверным x-secret-key отброшен")
        raise web.HTTPUnauthorized(reason="bad signature")

    try:
        event = await request.json()
    except json.JSONDecodeError:
        raise web.HTTPBadRequest(reason="bad json") from None

    if event.get("status") != "paid":
        return web.json_response({"ok": True})

    prefix, _, raw_id = str(event.get("payment_id", "")).partition("-")
    if prefix != "order" or not raw_id.isdigit():
        log.warning("Lolz-вебхук с непонятным payment_id: %s", event.get("payment_id"))
        return web.json_response({"ok": True})

    order_id = int(raw_id)
    first = await db.mark_paid(order_id, "lolz", str(event.get("invoice_id")))
    order = await db.get_order(order_id)

    if order and order["product"] == "topup":
        balance = (
            await db.add_balance(order["user_id"], order["amount"])
            if first
            else await db.balance_of(order["user_id"])
        )
        log.info("Lolz: баланс #%s пополнен на %s ₽", order["user_id"], order["amount"])
        await _bot_for(request, order).send_message(
            order["user_id"],
            f"✅ Баланс пополнен на {order['amount']:.0f} ₽\nТекущий баланс: <b>{balance:.0f} ₽</b>",
        )
    elif order and first:
        log.info("Lolz: заказ #%s оплачен", order_id)
        await _bot_for(request, order).send_message(
            order["user_id"],
            f"✅ Оплачено — {order['amount']:.0f} ₽\n\n<b>{order['title']}</b>\nЗаказ №{order_id}\n\n"
            "Ключ доступа придёт сюда же в течение минуты.",
        )

    return web.json_response({"ok": True})


def _client_ip(request: web.Request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote or "?"


async def paycore_webhook(request: web.Request) -> web.Response:
    """Подтверждение оплаты от PayCore. Подписи нет — единственная защита это IP отправителя."""
    if _client_ip(request) != PAYCORE_WEBHOOK_IP:
        log.warning("PayCore-вебхук с чужого IP отброшен: %s", _client_ip(request))
        raise web.HTTPForbidden(reason="unexpected sender ip")

    try:
        event = await request.json()
    except json.JSONDecodeError:
        raise web.HTTPBadRequest(reason="bad json") from None

    provider_ref = str(event.get("order_id", ""))
    if not provider_ref:
        return web.json_response({"ok": True})

    db: Database = request.app["db"]
    order = await db.get_order_by_provider_ref(provider_ref)
    if not order:
        log.warning("PayCore-вебхук с неизвестным order_id: %s", provider_ref)
        return web.json_response({"ok": True})

    paid_amount = float(event.get("amount", 0))
    if paid_amount < order["amount"]:
        log.warning("PayCore: сумма меньше заказа #%s (%.2f < %.2f)", order["id"], paid_amount, order["amount"])
        return web.json_response({"ok": True})

    first = await db.mark_paid(order["id"], f"paycore-{event.get('method', '')}", provider_ref)

    if order["product"] == "topup":
        balance = (
            await db.add_balance(order["user_id"], order["amount"])
            if first
            else await db.balance_of(order["user_id"])
        )
        log.info("PayCore: баланс #%s пополнен на %s ₽", order["user_id"], order["amount"])
        await _bot_for(request, order).send_message(
            order["user_id"],
            f"✅ Баланс пополнен на {order['amount']:.0f} ₽\nТекущий баланс: <b>{balance:.0f} ₽</b>",
        )
    elif first:
        log.info("PayCore: заказ #%s оплачен", order["id"])
        await _bot_for(request, order).send_message(
            order["user_id"],
            f"✅ Оплачено — {order['amount']:.0f} ₽\n\n<b>{order['title']}</b>\nЗаказ №{order['id']}\n\n"
            "Ключ доступа придёт сюда же в течение минуты.",
        )

    return web.json_response({"ok": True})


def build_app(cfg: Config, db: Database, bots: dict[int, Bot], providers: Providers) -> web.Application:
    app = web.Application(middlewares=[no_store])
    app["cfg"] = cfg
    app["db"] = db
    app["bots"] = bots
    app["crypto"] = providers.crypto
    app["robokassa"] = providers.robokassa
    app["lolz"] = providers.lolz
    app["paycore"] = providers.paycore

    app.router.add_post("/cryptobot/webhook", crypto_webhook)
    app.router.add_route("*", "/robokassa/result", robokassa_result)
    app.router.add_post("/lolz/webhook", lolz_webhook)
    app.router.add_post("/paycore/webhook", paycore_webhook)

    return app


async def start_web(cfg: Config, db: Database, bots: dict[int, Bot], port: int, providers: Providers) -> web.AppRunner:
    runner = web.AppRunner(build_app(cfg, db, bots, providers), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("вебхуки оплаты слушают http://localhost:%s", port)
    return runner
