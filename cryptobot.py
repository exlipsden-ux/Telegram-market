"""Оплата через CryptoBot (Crypto Pay API).

Токен приложения берётся в @CryptoBot → Crypto Pay → Create App
и кладётся в CRYPTOBOT_TOKEN в .env.

Подтверждение оплаты приходит вебхуком, а не опросом: у Crypto Pay
подпись считается HMAC-SHA256 по телу запроса ключом sha256(token).
"""

from __future__ import annotations

import hashlib
import hmac
import logging

import aiohttp

log = logging.getLogger("cryptobot")

API = "https://pay.crypt.bot/api"

# Во сколько рублей оцениваем доллар при выставлении криптосчёта.
# Держите чуть выше биржевого, иначе курсовые колебания съедают маржу.
RUB_PER_USD = 95.0


class CryptoBotError(RuntimeError):
    pass


class CryptoBot:
    def __init__(self, token: str):
        self._token = token

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    async def _call(self, method: str, payload: dict | None = None) -> dict:
        headers = {"Crypto-Pay-API-Token": self._token}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(
                f"{API}/{method}",
                json=payload or {},
                timeout=aiohttp.ClientTimeout(total=25),
            ) as response:
                data = await response.json()

        if not data.get("ok"):
            raise CryptoBotError(str(data.get("error", data)))
        return data["result"]

    async def me(self) -> dict:
        return await self._call("getMe")

    async def create_invoice(self, order_id: int, title: str, rub: float) -> dict:
        """Счёт выставляется в USDT — он стабильнее волатильных монет."""
        amount = round(rub / RUB_PER_USD, 2)

        return await self._call(
            "createInvoice",
            {
                "currency_type": "crypto",
                "asset": "USDT",
                "amount": f"{amount:.2f}",
                "description": f"{title} — заказ №{order_id}",
                "payload": f"order:{order_id}",
                "allow_comments": False,
                "allow_anonymous": False,
                "expires_in": 3600,
            },
        )

    def check_signature(self, token_header: str, body: bytes) -> bool:
        secret = hashlib.sha256(self._token.encode()).digest()
        expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, token_header or "")
