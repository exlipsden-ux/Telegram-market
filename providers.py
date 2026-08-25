"""Единые экземпляры платёжных провайдеров — общие для aiohttp (вебхуки) и aiogram (чат).

Раньше создавались только внутри webapi.build_app() и не были видны
чат-хендлерам; здесь — единая точка создания, чтобы оба слоя работали
с одними и теми же объектами (общий client-session, общее состояние enabled).
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .cryptobot import CryptoBot
from .lolz import Lolz
from .paycore import PayCore
from .robokassa import Robokassa
from .ton import Ton


@dataclass
class Providers:
    crypto: CryptoBot
    robokassa: Robokassa
    lolz: Lolz
    paycore: PayCore
    ton: Ton


def build_providers(cfg: Config) -> Providers:
    return Providers(
        crypto=CryptoBot(cfg.cryptobot_token),
        robokassa=Robokassa(
            cfg.robokassa_login, cfg.robokassa_password1, cfg.robokassa_password2, cfg.robokassa_test
        ),
        lolz=Lolz(
            cfg.lolz_token, cfg.lolz_merchant_id,
            callback_url=f"{cfg.webapp_url}/lolz/webhook",
            success_url=f"{cfg.webapp_url}/",
        ),
        paycore=PayCore(cfg.paycore_api_key),
        ton=Ton(cfg.ton_wallet_address, cfg.ton_api_key),
    )
