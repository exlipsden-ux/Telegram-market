"""Оплата через Lolz Market (lzt.market) — карты, СБП, крипта их шлюзом.

Мерчант-доступы берутся в личном кабинете lolz.live:
  1. https://lolz.live/account/api/client-add — создать API-клиент со скоупом invoice
  2. https://lolz.live/account/api/get-token — получить Access Token
  3. merchant_id — id мерчант-кабинета в разделе платежей на lzt.market

Оплата подтверждается вебхуком на url_callback. Подлинность вебхука
проверяется сверкой заголовка x-secret-key с самим API-токеном (без HMAC,
по документации Lolz Market).
"""

from __future__ import annotations

import hmac
import logging

import aiohttp

log = logging.getLogger("lolz")

API = "https://api.lzt.market/invoice"


class LolzError(RuntimeError):
    pass


class Lolz:
    def __init__(self, token: str, merchant_id: str, callback_url: str, success_url: str):
        self._token = token
        self._merchant_id = merchant_id
        self._callback_url = callback_url
        self._success_url = success_url

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._merchant_id)

    async def create_invoice(self, order_id: int, title: str, rub: float) -> dict:
        payload = {
            "currency": "RUB",
            "amount": round(rub, 2),
            "payment_id": f"order-{order_id}",
            "merchant_id": int(self._merchant_id),
            "comment": title[:255],
            "url_success": self._success_url,
            "url_callback": self._callback_url,
            "lifetime": 3600,
        }
        headers = {"Authorization": f"Bearer {self._token}"}

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(
                API, json=payload, timeout=aiohttp.ClientTimeout(total=25),
            ) as response:
                data = await response.json()

        if not response.ok or "invoice" not in data:
            raise LolzError(str(data.get("errors") or data))
        return data["invoice"]

    def check_signature(self, secret_header: str) -> bool:
        return hmac.compare_digest(secret_header or "", self._token)
