"""Единый источник правды по товарам и ценам.

Витрина показывает эти же цифры, но доверять присланной из неё цене нельзя —
сумма всегда пересчитывается здесь, на сервере.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

STAR_RATE = 1.40
STAR_MIN = 50
STAR_MAX = 100_000

# Пополнение баланса кошелька, рубли
TOPUP_MIN = 50
TOPUP_MAX = 100_000

# Во сколько рублей оцениваем одну звезду, когда ЕЙ с нами расплачиваются.
# Чем ниже курс, тем больше звёзд просим за тот же тариф.
RUB_PER_PAID_STAR = 0.9


@dataclass(frozen=True)
class Tariff:
    id: str
    title: str
    price: int
    days: int
    trial: bool = False


VPN_TARIFFS: dict[str, Tariff] = {
    t.id: t
    for t in (
        Tariff("vpn-trial", "Пробный доступ на 5 дней", 0, 5, trial=True),
        Tariff("vpn-1w", "VPN на 1 неделю", 49, 7),
        Tariff("vpn-1m", "VPN на 1 месяц", 179, 30),
        Tariff("vpn-3m", "VPN на 3 месяца", 499, 90),
        Tariff("vpn-6m", "VPN на 6 месяцев", 899, 180),
        Tariff("vpn-12m", "VPN на 1 год", 1499, 365),
    )
}


PREMIUM_TARIFFS: dict[str, Tariff] = {
    t.id: t
    for t in (
        Tariff("premium-3m", "Telegram Premium 3 месяца", 1153, 90),
        Tariff("premium-6m", "Telegram Premium 6 месяцев", 1590, 180),
        Tariff("premium-12m", "Telegram Premium 1 год", 2790, 365),
    )
}


class InvalidOrder(ValueError):
    """Заказ не проходит проверку — текст исключения показывается покупателю."""


# Сколько звёзд «уже продано» показывать на витрине сверх реально проданных.
# Витринный счётчик, не влияет ни на цены, ни на заказы.
STARS_SOLD_BASE = 35_520_324


# Что можно менять из админки. Значения по умолчанию — здесь,
# переопределения лежат в таблице settings и подхватываются на каждый запрос.
EDITABLE: dict[str, float] = {
    "star_rate": STAR_RATE,
    "rub_per_paid_star": RUB_PER_PAID_STAR,
    "stars_sold_base": float(STARS_SOLD_BASE),
    **{f"price.{t.id}": float(t.price) for t in VPN_TARIFFS.values() if not t.trial},
    **{f"price.{t.id}": float(t.price) for t in PREMIUM_TARIFFS.values()},
}


def setting(overrides: dict[str, float], key: str) -> float:
    return overrides.get(key, EDITABLE[key])


def tariff_price(overrides: dict[str, float], tariff: Tariff) -> int:
    if tariff.trial:
        return 0
    return int(round(setting(overrides, f"price.{tariff.id}")))


def stars_price(qty: int, overrides: dict[str, float] | None = None) -> int:
    return math.ceil(qty * setting(overrides or {}, "star_rate"))


def to_stars(rub: float, overrides: dict[str, float] | None = None) -> int:
    """Сколько Telegram Stars просить за сумму в рублях."""
    return max(1, math.ceil(rub / setting(overrides or {}, "rub_per_paid_star")))


def normalize_username(raw: str) -> str:
    name = raw.strip().lstrip("@")
    if not 5 <= len(name) <= 32:
        raise InvalidOrder("Юзернейм должен быть от 5 до 32 символов.")
    if not all(c.isascii() and (c.isalnum() or c == "_") for c in name):
        raise InvalidOrder("В юзернейме допустимы только латиница, цифры и подчёркивание.")
    return name


def validate_topup(amount_raw: object) -> int:
    try:
        amount = int(amount_raw)
    except (TypeError, ValueError):
        raise InvalidOrder("Сумма указана неверно.") from None
    if not TOPUP_MIN <= amount <= TOPUP_MAX:
        raise InvalidOrder(
            f"Сумма пополнения — от {TOPUP_MIN} до {TOPUP_MAX:,} ₽.".replace(",", " ")
        )
    return amount


def validate_stars(
    qty_raw: object, username_raw: object, overrides: dict[str, float] | None = None
) -> tuple[int, str, int]:
    try:
        qty = int(qty_raw)
    except (TypeError, ValueError):
        raise InvalidOrder("Количество звёзд указано неверно.") from None

    if not STAR_MIN <= qty <= STAR_MAX:
        raise InvalidOrder(f"Количество должно быть от {STAR_MIN} до {STAR_MAX:,} звёзд.".replace(",", " "))

    if not isinstance(username_raw, str):
        raise InvalidOrder("Укажите юзернейм получателя.")

    return qty, normalize_username(username_raw), stars_price(qty, overrides)
