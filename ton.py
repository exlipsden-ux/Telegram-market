"""Оплата в TON: свой адрес + сверка входящих переводов по блокчейну.

У TON нет вебхуков — сеть просто не умеет их слать. Вместо этого раз в
POLL_INTERVAL секунд опрашиваем toncenter.com по нашему адресу и ищем
входящий перевод с комментарием "order-<id>" и суммой не меньше ожидаемой
(которую зафиксировали в момент создания счёта — курс TON к рублю мог
измениться к моменту оплаты, а показывать пользователю нужно то же число).
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import quote

import aiohttp
from aiogram import Bot

from .db import Database

log = logging.getLogger("ton")

TONCENTER_API = "https://toncenter.com/api/v2/getTransactions"
COINGECKO_API = "https://api.coingecko.com/api/v3/simple/price"
NANOTON = 1_000_000_000
POLL_INTERVAL = 25

_COMMENT_RE = re.compile(r"^order-(\d+)$")


class TonError(RuntimeError):
    pass


class Ton:
    def __init__(self, wallet_address: str, api_key: str = ""):
        self._address = wallet_address
        self._api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self._address)

    async def rub_to_ton(self, rub: float) -> float:
        params = {"ids": "the-open-network", "vs_currencies": "rub"}
        async with aiohttp.ClientSession() as session:
            async with session.get(
                COINGECKO_API, params=params, timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                data = await response.json()

        try:
            rate = float(data["the-open-network"]["rub"])
        except (KeyError, TypeError, ValueError):
            raise TonError(f"не удалось получить курс TON: {data}") from None
        if rate <= 0:
            raise TonError("курс TON <= 0")

        return rub / rate

    async def create_invoice(self, order_id: int, rub: float) -> dict:
        ton_amount = await self.rub_to_ton(rub)
        nanotons = int(ton_amount * NANOTON) + 1  # округляем вверх, чтобы платёж точно прошёл проверку по сумме
        comment = f"order-{order_id}"
        url = f"https://app.tonkeeper.com/transfer/{self._address}?amount={nanotons}&text={quote(comment)}"
        return {"url": url, "nanotons": nanotons, "ton_amount": nanotons / NANOTON, "comment": comment}

    async def _fetch_incoming(self) -> list[dict]:
        params = {"address": self._address, "limit": 40, "archival": "true"}
        headers = {"X-API-Key": self._api_key} if self._api_key else {}

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(
                TONCENTER_API, params=params, timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                data = await response.json()

        if not data.get("ok"):
            log.warning("toncenter вернул ошибку: %s", data)
            return []

        out = []
        for tx in data.get("result", []):
            in_msg = tx.get("in_msg") or {}
            value = int(in_msg.get("value") or "0")
            if value <= 0:
                continue
            out.append(
                {
                    "nanotons": value,
                    "comment": (in_msg.get("message") or "").strip(),
                    "hash": tx.get("transaction_id", {}).get("hash", ""),
                }
            )
        return out


async def ton_watcher(ton: Ton, db: Database, bots: dict[int, Bot]) -> None:
    """Фоновая проверка входящих TON-переводов. Работает, пока жив процесс бота."""
    seen: set[str] = set()

    while True:
        try:
            for tx in await ton._fetch_incoming():
                if not tx["hash"] or tx["hash"] in seen:
                    continue
                seen.add(tx["hash"])

                match = _COMMENT_RE.match(tx["comment"])
                if not match:
                    continue

                order_id = int(match.group(1))
                order = await db.get_order(order_id)
                if not order or order["status"] == "paid":
                    continue
                if order["ton_nanotons"] is None or tx["nanotons"] < order["ton_nanotons"]:
                    log.warning("TON: сумма меньше ожидаемой для заказа #%s", order_id)
                    continue

                first = await db.mark_paid(order_id, "ton", tx["hash"])
                if not first:
                    continue

                bot = bots.get(order["bot_id"]) or next(iter(bots.values()))
                if order["product"] == "topup":
                    balance = await db.add_balance(order["user_id"], order["amount"])
                    log.info("TON: баланс #%s пополнен на %s ₽", order["user_id"], order["amount"])
                    await bot.send_message(
                        order["user_id"],
                        f"✅ Баланс пополнен на {order['amount']:.0f} ₽\n"
                        f"Текущий баланс: <b>{balance:.0f} ₽</b>",
                    )
                else:
                    log.info("TON: заказ #%s оплачен", order_id)
                    await bot.send_message(
                        order["user_id"],
                        f"✅ Оплачено — {order['amount']:.0f} ₽\n\n"
                        f"<b>{order['title']}</b>\nЗаказ №{order_id}\n\n"
                        "Ключ доступа придёт сюда же в течение минуты.",
                    )
        except Exception:
            log.exception("ошибка опроса TON")

        await asyncio.sleep(POLL_INTERVAL)
