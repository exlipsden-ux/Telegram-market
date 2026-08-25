from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .catalog import (
    PREMIUM_TARIFFS,
    STAR_MAX,
    STAR_MIN,
    TOPUP_MAX,
    TOPUP_MIN,
    VPN_TARIFFS,
    InvalidOrder,
    setting,
    stars_price,
    tariff_price,
    to_stars,
    validate_stars,
    validate_topup,
)
from .config import Config
from .cryptobot import CryptoBotError
from .db import Database
from .lolz import LolzError
from .paycore import PayCoreError
from .providers import Providers
from .ton import TonError

# Ориентир для строки в $ в приветствии — только для отображения, ни на что не влияет.
RUB_PER_USD_DISPLAY = 85

router = Router()


class ShopStates(StatesGroup):
    stars_username = State()
    stars_qty = State()
    topup_amount = State()


class AdminStates(StatesGroup):
    edit_price = State()


def _usd(rub: float) -> str:
    return f"{rub / RUB_PER_USD_DISPLAY:.1f}"


def _is_stars_bot(bot: Bot, cfg: Config) -> bool:
    return bot.id == cfg.stars_bot_id


def main_menu_kb(is_stars_bot: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if is_stars_bot:
        kb.row(
            InlineKeyboardButton(text="⭐ Звёзды", callback_data="cat_stars"),
            InlineKeyboardButton(text="💎 Премиум", callback_data="cat_premium"),
        )
        kb.row(
            InlineKeyboardButton(text="✉️ Подписки", callback_data="cat_subs"),
            InlineKeyboardButton(text="🎁 Подарки", callback_data="cat_gifts"),
        )
    else:
        kb.row(InlineKeyboardButton(text="🛡 VPN", callback_data="cat_vpn"))
    kb.row(
        InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
        InlineKeyboardButton(text="🛠 Поддержка", callback_data="support"),
    )
    return kb.as_markup()


async def _welcome_text(db: Database, is_stars_bot: bool) -> str:
    overrides = await db.settings()

    if not is_stars_bot:
        vpn_1m = tariff_price(overrides, VPN_TARIFFS["vpn-1m"])
        return (
            "👋 Добро пожаловать!\n\n"
            "🛡 У вас есть возможность выгодно купить VPN по низкой цене и без KYC.\n\n"
            "Актуальная цена:\n"
            f"🛡 VPN 1 месяц - {vpn_1m}₽ | {_usd(vpn_1m)}$"
        )

    stars_100 = stars_price(100, overrides)
    premium_3m = tariff_price(overrides, PREMIUM_TARIFFS["premium-3m"])
    sold = int(setting(overrides, "stars_sold_base")) + await db.stars_sold()

    return (
        "👋 Добро пожаловать!\n\n"
        "⭐ У нас вы сможете выгодно покупать звёзды и премиум по низкой цене и без KYC.\n\n"
        f"🔍 Через наш сервис уже было куплено {sold:,} звёзд\n\n".replace(",", ".")
        + "Актуальные цены:\n"
        f"⭐ 100 звёзд - {stars_100}₽ | {_usd(stars_100)}$\n"
        f"💎 3 месяца - {premium_3m}₽ | {_usd(premium_3m)}$"
    )


@router.message(CommandStart())
async def cmd_start(message: Message, db: Database, bot: Bot, cfg: Config) -> None:
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    is_stars = _is_stars_bot(bot, cfg)
    await message.answer(await _welcome_text(db, is_stars), reply_markup=main_menu_kb(is_stars))


@router.callback_query(F.data == "menu_back")
async def cb_menu_back(callback: CallbackQuery, db: Database, bot: Bot, cfg: Config) -> None:
    is_stars = _is_stars_bot(bot, cfg)
    await callback.message.edit_text(
        await _welcome_text(db, is_stars), reply_markup=main_menu_kb(is_stars)
    )
    await callback.answer()


@router.callback_query(F.data.in_({"cat_subs", "cat_gifts"}))
async def cb_coming_soon(callback: CallbackQuery) -> None:
    await callback.answer("Раздел скоро откроется.", show_alert=True)


@router.callback_query(F.data == "support")
async def cb_support(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer("Вопрос по заказу — просто напишите сюда, ответим.")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "<b>Команды</b>\n"
        "/start — открыть магазин\n"
        "/orders — мои покупки\n"
        "/help — эта справка\n\n"
        "Вопрос по заказу — просто напишите сюда, ответим."
    )


@router.message(Command("orders"))
async def cmd_orders(message: Message, db: Database) -> None:
    active = await db.orders_of(message.from_user.id, active=True)
    if not active:
        await message.answer("Активных покупок нет. Загляните в магазин — первые 5 дней бесплатно.")
        return

    lines = ["<b>Активные покупки</b>", ""]
    for row in active:
        left = ""
        if row["expires_at"]:
            days = (datetime.fromisoformat(row["expires_at"]) - datetime.now(timezone.utc)).days
            left = f" — осталось {max(days, 0)} дн."
        lines.append(f"• {row['title']}{left}")

    await message.answer("\n".join(lines))


# ---------- профиль ----------

@router.callback_query(F.data == "profile")
async def cb_profile(callback: CallbackQuery, db: Database) -> None:
    user_id = callback.from_user.id
    balance = await db.balance_of(user_id)
    active = await db.orders_of(user_id, active=True)
    done = await db.orders_of(user_id, active=False)

    lines = [
        "<b>👤 Профиль</b>",
        f"⭐ Баланс: <b>{balance:.0f} ₽</b>",
        "",
        f"<b>Активные покупки ({len(active)})</b>",
    ]
    if active:
        for row in active:
            left = ""
            if row["expires_at"]:
                days = (datetime.fromisoformat(row["expires_at"]) - datetime.now(timezone.utc)).days
                left = f" — осталось {max(days, 0)} дн."
            lines.append(f"• {row['title']}{left}")
    else:
        lines.append("пока нет")

    lines.append("")
    lines.append(f"<b>Завершённые ({len(done)})</b>")
    if done:
        for row in done[:10]:
            lines.append(f"• {row['title']}")
    else:
        lines.append("пока нет")

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="topup"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_back"))

    await callback.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())
    await callback.answer()


# ---------- способ оплаты (общее для звёзд, VPN и пополнения) ----------

def payment_methods_kb(order_id: int, providers: Providers, allow_stars: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if allow_stars:
        kb.row(InlineKeyboardButton(text="⭐ Telegram Stars", callback_data=f"pay_stars_{order_id}"))
    if providers.crypto.enabled:
        kb.row(InlineKeyboardButton(text="💎 CryptoBot", callback_data=f"pay_cryptobot_{order_id}"))
    if providers.ton.enabled:
        kb.row(InlineKeyboardButton(text="🔷 TON", callback_data=f"pay_ton_{order_id}"))
    if providers.lolz.enabled:
        kb.row(InlineKeyboardButton(text="👁 Lolz", callback_data=f"pay_lolz_{order_id}"))
    if providers.paycore.enabled:
        kb.row(
            InlineKeyboardButton(text="🏦 СБП", callback_data=f"pay_paycore_sbp_{order_id}"),
            InlineKeyboardButton(text="💳 Банковская карта", callback_data=f"pay_paycore_sbp_{order_id}"),
        )
    return kb.as_markup()


async def _payable_order(db: Database, callback: CallbackQuery, order_id: int):
    order = await db.get_order(order_id)
    if not order or order["user_id"] != callback.from_user.id:
        await callback.answer("Заказ не найден.", show_alert=True)
        return None
    if order["status"] == "paid":
        await callback.answer("Заказ уже оплачен.", show_alert=True)
        return None
    return order


@router.callback_query(F.data.startswith("pay_stars_"))
async def pay_stars(callback: CallbackQuery, db: Database, bot: Bot) -> None:
    order_id = int(callback.data[len("pay_stars_"):])
    order = await _payable_order(db, callback, order_id)
    if order is None:
        return
    if order["product"] == "stars":
        await callback.answer("Звёзды нельзя оплатить звёздами — выберите крипту.", show_alert=True)
        return

    stars = to_stars(order["amount"])
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=order["title"][:32],
        description=f"Заказ №{order['id']} в tatarenoss market",
        payload=f"order:{order['id']}",
        currency="XTR",
        prices=[LabeledPrice(label=order["title"][:32], amount=stars)],
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay_cryptobot_"))
async def pay_cryptobot(callback: CallbackQuery, db: Database, providers: Providers) -> None:
    order_id = int(callback.data[len("pay_cryptobot_"):])
    order = await _payable_order(db, callback, order_id)
    if order is None:
        return
    try:
        invoice = await providers.crypto.create_invoice(order["id"], order["title"], order["amount"])
    except CryptoBotError as exc:
        logging.error("CryptoBot отказал: %s", exc)
        await callback.answer("CryptoBot недоступен, попробуйте позже.", show_alert=True)
        return
    link = invoice.get("mini_app_invoice_url") or invoice["bot_invoice_url"]
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💳 Оплатить", url=link))
    await callback.message.answer(f"Счёт на {invoice.get('amount')} {invoice.get('asset')} создан:", reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("pay_ton_"))
async def pay_ton(callback: CallbackQuery, db: Database, providers: Providers) -> None:
    order_id = int(callback.data[len("pay_ton_"):])
    order = await _payable_order(db, callback, order_id)
    if order is None:
        return
    try:
        invoice = await providers.ton.create_invoice(order["id"], order["amount"])
    except TonError as exc:
        logging.error("TON отказал: %s", exc)
        await callback.answer("Не удалось получить курс TON, попробуйте позже.", show_alert=True)
        return
    await db.set_ton_amount(order["id"], invoice["nanotons"])
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💳 Оплатить", url=invoice["url"]))
    await callback.message.answer(
        f"Сумма к оплате: <b>{invoice['ton_amount']:.4f} TON</b>\n\n"
        "Откройте ссылку в кошельке (Tonkeeper и т.п.) — сумма и комментарий подставятся сами. "
        "Оплата подтвердится автоматически в течение минуты после перевода.",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay_lolz_"))
async def pay_lolz(callback: CallbackQuery, db: Database, providers: Providers) -> None:
    order_id = int(callback.data[len("pay_lolz_"):])
    order = await _payable_order(db, callback, order_id)
    if order is None:
        return
    try:
        invoice = await providers.lolz.create_invoice(order["id"], order["title"], order["amount"])
    except LolzError as exc:
        logging.error("Lolz отказал: %s", exc)
        await callback.answer("Lolz недоступен, попробуйте позже.", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💳 Оплатить", url=invoice["url"]))
    await callback.message.answer("Счёт создан:", reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("pay_paycore_sbp_"))
async def pay_paycore(callback: CallbackQuery, db: Database, providers: Providers, cfg: Config) -> None:
    order_id = int(callback.data[len("pay_paycore_sbp_"):])
    order = await _payable_order(db, callback, order_id)
    if order is None:
        return
    try:
        invoice = await providers.paycore.create_invoice(
            "sbp", order["amount"], order["title"],
            return_url=f"{cfg.webapp_url}/paycore/webhook",
        )
    except PayCoreError as exc:
        logging.error("PayCore отказал: %s", exc)
        await callback.answer("PayCore недоступен, попробуйте позже.", show_alert=True)
        return
    await db.set_provider_ref(order["id"], str(invoice["order_id"]))
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💳 Оплатить (QR)", url=invoice["url"]))
    await callback.message.answer("Счёт создан:", reply_markup=kb.as_markup())
    await callback.answer()


# ---------- звёзды ----------

@router.callback_query(F.data == "cat_stars")
async def cat_stars_start(callback: CallbackQuery, state: FSMContext, bot: Bot, cfg: Config) -> None:
    if not _is_stars_bot(bot, cfg):
        await callback.answer("Звёзды продаются в другом боте.", show_alert=True)
        return
    await state.clear()
    await state.set_state(ShopStates.stars_username)
    await callback.message.answer(f"Введите @username получателя звёзд (получит от {STAR_MIN} до {STAR_MAX} звёзд):")
    await callback.answer()


@router.message(ShopStates.stars_username)
async def stars_username_received(message: Message, state: FSMContext) -> None:
    await state.update_data(stars_username=message.text or "")
    await state.set_state(ShopStates.stars_qty)
    await message.answer(f"Сколько звёзд купить? (от {STAR_MIN} до {STAR_MAX})")


@router.message(ShopStates.stars_qty)
async def stars_qty_received(message: Message, state: FSMContext, db: Database, providers: Providers, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()

    overrides = await db.settings()
    try:
        qty, username, amount = validate_stars(message.text, data.get("stars_username", ""), overrides)
    except InvalidOrder as exc:
        await message.answer(f"❌ {exc}")
        return

    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)
    title = f"{qty} Telegram Stars → @{username}"
    order_id = await db.create_order(user.id, "stars", title, amount, bot_id=bot.id)

    await message.answer(
        f"<b>{title}</b>\nК оплате: {amount:.0f} ₽\n\nВыберите способ оплаты:",
        reply_markup=payment_methods_kb(order_id, providers, allow_stars=False),
    )


# ---------- VPN ----------

def vpn_tariffs_kb(overrides: dict[str, float]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for t in VPN_TARIFFS.values():
        price = tariff_price(overrides, t)
        label = f"{t.title} — {'Бесплатно' if t.trial else f'{price} ₽'}"
        kb.row(InlineKeyboardButton(text=label, callback_data=f"vpn_pick_{t.id}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_back"))
    return kb.as_markup()


@router.callback_query(F.data == "cat_vpn")
async def cat_vpn_start(callback: CallbackQuery, db: Database, bot: Bot, cfg: Config) -> None:
    if _is_stars_bot(bot, cfg):
        await callback.answer("VPN продаётся в другом боте.", show_alert=True)
        return
    overrides = await db.settings()
    await callback.message.edit_text("Выберите тариф VPN:", reply_markup=vpn_tariffs_kb(overrides))
    await callback.answer()


@router.callback_query(F.data.startswith("vpn_pick_"))
async def vpn_pick(callback: CallbackQuery, db: Database, providers: Providers, bot: Bot) -> None:
    tariff_id = callback.data[len("vpn_pick_"):]
    tariff = VPN_TARIFFS.get(tariff_id)
    if not tariff:
        await callback.answer("Тариф не найден.", show_alert=True)
        return

    user = callback.from_user
    await db.upsert_user(user.id, user.username, user.first_name)
    overrides = await db.settings()
    amount = tariff_price(overrides, tariff)

    if tariff.trial:
        if await db.trial_used(user.id):
            await callback.answer("Пробный период уже использован.", show_alert=True)
            return
        order_id = await db.create_order(user.id, tariff.id, tariff.title, amount, days=tariff.days, bot_id=bot.id)
        await db.mark_trial_used(user.id)
        await db.set_order_status(order_id, "paid")
        await callback.message.edit_text(
            f"✅ <b>{tariff.title}</b> активирован бесплатно!\nКлюч доступа придёт сюда же в течение минуты."
        )
        await callback.answer()
        return

    order_id = await db.create_order(user.id, tariff.id, tariff.title, amount, days=tariff.days, bot_id=bot.id)
    await callback.message.edit_text(
        f"<b>{tariff.title}</b>\nК оплате: {amount} ₽\n\nВыберите способ оплаты:",
        reply_markup=payment_methods_kb(order_id, providers, allow_stars=True),
    )
    await callback.answer()


# ---------- премиум ----------

def premium_tariffs_kb(overrides: dict[str, float]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for t in PREMIUM_TARIFFS.values():
        price = tariff_price(overrides, t)
        kb.row(InlineKeyboardButton(text=f"{t.title} — {price} ₽", callback_data=f"prem_pick_{t.id}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_back"))
    return kb.as_markup()


@router.callback_query(F.data == "cat_premium")
async def cat_premium_start(callback: CallbackQuery, db: Database, bot: Bot, cfg: Config) -> None:
    if not _is_stars_bot(bot, cfg):
        await callback.answer("Премиум продаётся в другом боте.", show_alert=True)
        return
    overrides = await db.settings()
    await callback.message.edit_text("Выберите срок Telegram Premium:", reply_markup=premium_tariffs_kb(overrides))
    await callback.answer()


@router.callback_query(F.data.startswith("prem_pick_"))
async def premium_pick(callback: CallbackQuery, db: Database, providers: Providers, bot: Bot) -> None:
    tariff = PREMIUM_TARIFFS.get(callback.data[len("prem_pick_"):])
    if not tariff:
        await callback.answer("Тариф не найден.", show_alert=True)
        return

    user = callback.from_user
    await db.upsert_user(user.id, user.username, user.first_name)
    overrides = await db.settings()
    amount = tariff_price(overrides, tariff)

    order_id = await db.create_order(user.id, tariff.id, tariff.title, amount, days=tariff.days, bot_id=bot.id)
    await callback.message.edit_text(
        f"<b>{tariff.title}</b>\nК оплате: {amount} ₽\n\nВыберите способ оплаты:",
        reply_markup=payment_methods_kb(order_id, providers, allow_stars=True),
    )
    await callback.answer()


# ---------- пополнение баланса ----------

@router.callback_query(F.data == "topup")
async def cb_topup_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ShopStates.topup_amount)
    await callback.message.answer(f"Сколько рублей внести на баланс? (от {TOPUP_MIN} до {TOPUP_MAX})")
    await callback.answer()


@router.message(ShopStates.topup_amount)
async def topup_amount_received(message: Message, state: FSMContext, db: Database, providers: Providers, bot: Bot) -> None:
    await state.clear()
    try:
        amount = validate_topup(message.text)
    except InvalidOrder as exc:
        await message.answer(f"❌ {exc}")
        return

    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)
    title = f"Пополнение баланса на {amount} ₽"
    order_id = await db.create_order(user.id, "topup", title, amount, bot_id=bot.id)

    await message.answer(
        f"<b>{title}</b>\n\nВыберите способ оплаты:",
        reply_markup=payment_methods_kb(order_id, providers, allow_stars=True),
    )


# ---------- оплата звёздами (Bot Payments) ----------

def _order_id(payload: str) -> int | None:
    prefix, _, raw = payload.partition(":")
    return int(raw) if prefix == "order" and raw.isdigit() else None


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery, db: Database) -> None:
    """Telegram даёт ~10 секунд на ответ, иначе платёж отменяется."""
    order_id = _order_id(query.invoice_payload)
    order = await db.get_order(order_id) if order_id else None

    if order is None:
        await query.answer(ok=False, error_message="Заказ не найден. Оформите его заново.")
        return
    if order["status"] == "paid":
        await query.answer(ok=False, error_message="Этот заказ уже оплачен.")
        return

    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_paid(message: Message, db: Database) -> None:
    payment = message.successful_payment
    order_id = _order_id(payment.invoice_payload)

    if order_id is None:
        logging.warning("оплата с непонятным payload: %s", payment.invoice_payload)
        return

    first = await db.mark_paid(order_id, "stars", payment.telegram_payment_charge_id)
    order = await db.get_order(order_id)

    logging.info("заказ #%s оплачен: %s ⭐", order_id, payment.total_amount)

    if order["product"] == "topup":
        balance = await db.add_balance(order["user_id"], order["amount"]) if first else await db.balance_of(order["user_id"])
        await message.answer(
            f"✅ Баланс пополнен на {order['amount']:.0f} ₽\n"
            f"Текущий баланс: <b>{balance:.0f} ₽</b>"
        )
        return

    await message.answer(
        f"✅ Оплачено — {payment.total_amount} ⭐\n\n"
        f"<b>{order['title']}</b>\n"
        f"Заказ №{order_id}\n\n"
        "Ключ доступа придёт сюда же в течение минуты.\n"
        f"<i>Чек для возврата: <code>{payment.telegram_payment_charge_id}</code></i>"
    )


# ---------- админ-панель ----------

PRICE_LABELS: dict[str, str] = {
    "star_rate": "⭐ Курс продажи звёзд, ₽ за 1 шт.",
    "rub_per_paid_star": "💱 Курс оплаты звёздами, ₽ за 1 шт.",
    "stars_sold_base": "🔍 Счётчик проданных звёзд (витрина)",
    **{f"price.{t.id}": f"🛡 {t.title}" for t in VPN_TARIFFS.values() if not t.trial},
    **{f"price.{t.id}": f"💎 {t.title}" for t in PREMIUM_TARIFFS.values()},
}


def _is_admin(user_id: int, cfg: Config) -> bool:
    return user_id in cfg.admin_ids


def admin_panel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📊 Статистика", callback_data="a_stats"))
    kb.row(InlineKeyboardButton(text="💰 Цены", callback_data="a_prices"))
    kb.row(InlineKeyboardButton(text="🧾 Последние заказы", callback_data="a_orders"))
    kb.row(InlineKeyboardButton(text="⬅️ Закрыть", callback_data="a_close"))
    return kb.as_markup()


def admin_prices_kb(overrides: dict[str, float]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for key, label in PRICE_LABELS.items():
        value = setting(overrides, key)
        kb.row(InlineKeyboardButton(text=f"{label} — {value:g}", callback_data=f"a_price_{key}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="a_back"))
    return kb.as_markup()


@router.message(Command("admin"))
async def cmd_admin(message: Message, cfg: Config, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id, cfg):
        return
    await state.clear()
    await message.answer("<b>🛠 Админ-панель</b>", reply_markup=admin_panel_kb())


@router.callback_query(F.data == "a_back")
async def cb_admin_back(callback: CallbackQuery, cfg: Config) -> None:
    if not _is_admin(callback.from_user.id, cfg):
        return await callback.answer()
    await callback.message.edit_text("<b>🛠 Админ-панель</b>", reply_markup=admin_panel_kb())
    await callback.answer()


@router.callback_query(F.data == "a_close")
async def cb_admin_close(callback: CallbackQuery, cfg: Config) -> None:
    if not _is_admin(callback.from_user.id, cfg):
        return await callback.answer()
    await callback.message.edit_text("Панель закрыта. /admin — открыть снова.")
    await callback.answer()


@router.callback_query(F.data == "a_stats")
async def cb_admin_stats(callback: CallbackQuery, cfg: Config, db: Database) -> None:
    if not _is_admin(callback.from_user.id, cfg):
        return await callback.answer()
    s = await db.stats()
    text = (
        "<b>📊 Статистика</b>\n\n"
        f"Пользователей: <b>{s['users']}</b>\n"
        f"Заказов всего: <b>{s['orders']}</b>\n"
        f"Оплачено: <b>{s['paid']}</b>\n"
        f"Выручка: <b>{s['revenue']:.0f} ₽</b>"
    )
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="a_back"))
    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data == "a_orders")
async def cb_admin_orders(callback: CallbackQuery, cfg: Config, db: Database) -> None:
    if not _is_admin(callback.from_user.id, cfg):
        return await callback.answer()
    rows = await db.recent_orders(15)
    if not rows:
        text = "<b>🧾 Последние заказы</b>\n\nПока нет заказов."
    else:
        lines = ["<b>🧾 Последние заказы</b>", ""]
        for row in rows:
            mark = "✅" if row["status"] == "paid" else ("⏳" if row["status"] == "pending" else "❌")
            who = f"@{row['username']}" if row["username"] else str(row["user_id"])
            lines.append(f"{mark} #{row['id']} {who} — {row['title']} — {row['amount']:.0f} ₽")
        text = "\n".join(lines)
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="a_back"))
    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data == "a_prices")
async def cb_admin_prices(callback: CallbackQuery, cfg: Config, db: Database) -> None:
    if not _is_admin(callback.from_user.id, cfg):
        return await callback.answer()
    overrides = await db.settings()
    await callback.message.edit_text(
        "<b>💰 Цены</b>\n\nНажмите на пункт, чтобы изменить значение:",
        reply_markup=admin_prices_kb(overrides),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("a_price_"))
async def cb_admin_price_edit(callback: CallbackQuery, cfg: Config, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id, cfg):
        return await callback.answer()
    key = callback.data[len("a_price_"):]
    if key not in PRICE_LABELS:
        await callback.answer("Неизвестный параметр.", show_alert=True)
        return
    await state.set_state(AdminStates.edit_price)
    await state.update_data(price_key=key)
    await callback.message.answer(f"Введите новое значение для «{PRICE_LABELS[key]}»:")
    await callback.answer()


@router.message(AdminStates.edit_price)
async def admin_price_value_received(message: Message, state: FSMContext, cfg: Config, db: Database) -> None:
    if not _is_admin(message.from_user.id, cfg):
        await state.clear()
        return

    data = await state.get_data()
    await state.clear()
    key = data.get("price_key")

    try:
        value = float((message.text or "").replace(",", "."))
    except ValueError:
        await message.answer("❌ Введите число.")
        return
    if value < 0:
        await message.answer("❌ Значение не может быть отрицательным.")
        return

    await db.set_setting(key, value)
    overrides = await db.settings()
    await message.answer(
        f"✅ «{PRICE_LABELS.get(key, key)}» обновлено: {value:g}",
        reply_markup=admin_prices_kb(overrides),
    )
