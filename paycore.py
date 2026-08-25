"""Оплата через PayCore (СБП по QR и банковская карта, с выводом мерчанту в USDT).

Токен берётся в Telegram-боте @PayCoreWallet_Bot -> "🔑 API токен".
Документация: https://paycore.ltd/docs

Авторизация — один заголовок X-Api-Key, без подписи запроса. Вебхук
подтверждает себя не подписью, а IP-адресом отправителя (см. WEBHOOK_IP).
"""

from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger("paycore")

API = "https://paycore.ltd/api/init"

# Единственный IP, с которого PayCore шлёт вебхуки — все остальные отбрасываем.
WEBHOOK_IP = "89.169.187.108"


class PayCoreError(RuntimeError):
    pass


class PayCore:
    def __init__(self, api_key: str):
        self._api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    async def create_invoice(self, method: str, amount: float, description: str, return_url: str) -> dict:
        """method: 'sbp' или 'card'."""
        body = {
            "method": method,
            "amount": amount,
            "description": description[:255],
            "returnLink": return_url,
        }
        headers = {"X-Api-Key": self._api_key, "Content-Type": "application/json"}

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(
                API, json=body, timeout=aiohttp.ClientTimeout(total=25),
            ) as response:
                data = await response.json()

        if response.status != 200 or "url" not in data:
            raise PayCoreError(str(data))
        return data
