from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, MenuButtonCommands

from .config import load_config
from .db import Database
from .handlers import router
from .providers import build_providers
from .runtime import Runtime
from .ton import ton_watcher
from .tunnel import Tunnel
from .webapi import start_web

COMMANDS = [
    BotCommand(command="start", description="Открыть магазин"),
    BotCommand(command="orders", description="Мои покупки"),
    BotCommand(command="help", description="Справка"),
]


async def set_menu(bot: Bot) -> None:
    """Каталога-мини-аппа больше нет — магазин целиком в чате, меню всегда обычное."""
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = load_config()
    db = Database(cfg.db_path)
    await db.connect()

    rt = Runtime(webapp_url=cfg.webapp_url)
    providers = build_providers(cfg)

    # Два разных бота на одном бэкенде: BOT_TOKEN продаёт VPN, STARS_BOT_TOKEN — звёзды.
    # Общие база, баланс, админка и провайдеры оплаты — только Telegram-аккаунт разный.
    bot_vpn = Bot(cfg.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot_stars = Bot(cfg.stars_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bots = {bot_vpn.id: bot_vpn, bot_stars.id: bot_stars}

    dp = Dispatcher(db=db, cfg=cfg, rt=rt, providers=providers)
    dp.include_router(router)

    runner = await start_web(cfg, db, bots, cfg.web_port, providers)

    async def on_url(url: str) -> None:
        """Туннель нужен только чтобы вебхуки оплаты были достижимы извне."""
        rt.webapp_url = url

    tunnel_task = None
    if cfg.use_tunnel:
        tunnel_task = asyncio.create_task(Tunnel(cfg.web_port, on_url).run())

    ton_task = None
    if providers.ton.enabled:
        ton_task = asyncio.create_task(ton_watcher(providers.ton, db, bots))

    try:
        for bot in bots.values():
            me = await bot.get_me()
            await bot.set_my_commands(COMMANDS)
            await set_menu(bot)
            await bot.delete_webhook(drop_pending_updates=True)
            logging.info("бот @%s запущен", me.username)

        await dp.start_polling(*bots.values())
    finally:
        if tunnel_task:
            tunnel_task.cancel()
        if ton_task:
            ton_task.cancel()
        await runner.cleanup()
        await db.close()
        for bot in bots.values():
            await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as exc:
        if str(exc):
            print(exc)
