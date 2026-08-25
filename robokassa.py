"""Оплата через Robokassa (СБП, карты) с фискализацией чека по 54-ФЗ.

Доступы из кабинета Robokassa кладутся в .env:
  ROBOKASSA_LOGIN     — идентификатор магазина (MerchantLogin)
  ROBOKASSA_PASSWORD1 — пароль №1 (подпись исходящего платежа)
  ROBOKASSA_PASSWORD2 — пароль №2 (проверка ответа на Result URL)
  ROBOKASSA_TEST      — 1 для тестового режима, 0 для боевого

Подпись формируется по схеме Robokassa: MD5 от строки, собранной через ':'.
Custom-параметры Shp_* участвуют в подписи в алфавитном порядке.
"""

from __future__ import annotations

import hashlib
import json
import logging
from urllib.parse import quote, urlencode

log = logging.getLogger("robokassa")

PAY_URL = "https://auth.robokassa.ru/Merchant/Index.aspx"


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _shp_tail(extra: dict[str, str]) -> str:
    """Часть подписи из Shp_-параметров — в алфавитном порядке ключей."""
    return "".join(f":{k}={extra[k]}" for k in sorted(extra))


class Robokassa:
    def __init__(self, login: str, password1: str, password2: str, test: bool):
        self._login = login
        self._p1 = password1
        self._p2 = password2
        self._test = test

    @property
    def enabled(self) -> bool:
        return bool(self._login and self._p1 and self._p2)

    def _receipt(self, title: str, rub: float) -> str:
        """Чек 54-ФЗ. Без НДС (самозанятый / АУСН), предмет расчёта — услуга."""
        receipt = {
            "items": [
                {
                    "name": title[:128],
                    "quantity": 1,
                    "sum": round(rub, 2),
                    "payment_method": "full_payment",
                    "payment_object": "service",
                    "tax": "none",
                }
            ]
        }
        # в подпись Receipt входит именно в URL-кодированном виде
        return quote(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")), safe="")

    def payment_link(self, order_id: int, title: str, rub: float) -> str:
        out_sum = f"{rub:.2f}"
        inv_id = str(order_id)
        receipt = self._receipt(title, rub)
        extra = {"Shp_order": inv_id}

        # MerchantLogin:OutSum:InvId:Receipt:Password1:Shp_...
        base = f"{self._login}:{out_sum}:{inv_id}:{receipt}:{self._p1}{_shp_tail(extra)}"
        signature = _md5(base)

        params = {
            "MerchantLogin": self._login,
            "OutSum": out_sum,
            "InvId": inv_id,
            "Description": title[:100],
            "Receipt": receipt,
            "SignatureValue": signature,
            "Culture": "ru",
            "Encoding": "utf-8",
            **extra,
        }
        if self._test:
            params["IsTest"] = "1"

        return f"{PAY_URL}?{urlencode(params)}"

    def verify_result(self, params: dict[str, str]) -> int | None:
        """Проверяет подпись ответа на Result URL. Возвращает InvId при успехе.

        Подпись ответа: MD5(OutSum:InvId:Password2:Shp_...).
        """
        out_sum = params.get("OutSum")
        inv_id = params.get("InvId")
        got = (params.get("SignatureValue") or "").lower()
        if not (out_sum and inv_id and got):
            return None

        extra = {k: v for k, v in params.items() if k.startswith("Shp_")}
        base = f"{out_sum}:{inv_id}:{self._p2}{_shp_tail(extra)}"

        if _md5(base).lower() != got:
            log.warning("Robokassa: подпись не сошлась для InvId=%s", inv_id)
            return None
        return int(inv_id) if inv_id.isdigit() else None
