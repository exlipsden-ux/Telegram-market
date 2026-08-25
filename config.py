import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Config:
    token: str
    stars_bot_token: str
    webapp_url: str
    admin_ids: frozenset[int]
    db_path: Path
    web_port: int
    use_tunnel: bool
    cryptobot_token: str
    robokassa_login: str
    robokassa_password1: str
    robokassa_password2: str
    robokassa_test: bool
    lolz_token: str
    lolz_merchant_id: str
    paycore_api_key: str
    ton_wallet_address: str
    ton_api_key: str

    @property
    def has_webapp(self) -> bool:
        return self.webapp_url.startswith("https://")

    @property
    def stars_bot_id(self) -> int:
        return int(self.stars_bot_token.split(":", 1)[0])


def _admin_ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(";", ",").split(",") if x.strip().isdigit())


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "BOT_TOKEN пуст.\n"
            "Создайте бота в @BotFather командой /newbot, "
            f"затем впишите токен в {ROOT / '.env'}"
        )

    stars_bot_token = os.getenv("STARS_BOT_TOKEN", "").strip()
    if not stars_bot_token:
        raise SystemExit(
            "STARS_BOT_TOKEN пуст.\n"
            "Это токен отдельного бота для продажи звёзд (BOT_TOKEN — бот для VPN). "
            f"Впишите его в {ROOT / '.env'}"
        )

    db_path = Path(os.getenv("DB_PATH", "shop.db"))
    if not db_path.is_absolute():
        db_path = ROOT / db_path

    return Config(
        token=token,
        stars_bot_token=stars_bot_token,
        webapp_url=os.getenv("WEBAPP_URL", "").strip().rstrip("/"),
        admin_ids=_admin_ids(os.getenv("ADMIN_IDS", "")),
        db_path=db_path,
        web_port=int(os.getenv("WEB_PORT", "8080")),
        use_tunnel=os.getenv("TUNNEL", "1").strip().lower() in {"1", "true", "yes", "on"},
        cryptobot_token=os.getenv("CRYPTOBOT_TOKEN", "").strip(),
        robokassa_login=os.getenv("ROBOKASSA_LOGIN", "").strip(),
        robokassa_password1=os.getenv("ROBOKASSA_PASSWORD1", "").strip(),
        robokassa_password2=os.getenv("ROBOKASSA_PASSWORD2", "").strip(),
        robokassa_test=os.getenv("ROBOKASSA_TEST", "1").strip().lower() in {"1", "true", "yes", "on"},
        lolz_token=os.getenv("LOLZ_TOKEN", "").strip(),
        lolz_merchant_id=os.getenv("LOLZ_MERCHANT_ID", "").strip(),
        paycore_api_key=os.getenv("PAYCORE_API_KEY", "").strip(),
        ton_wallet_address=os.getenv("TON_WALLET_ADDRESS", "").strip(),
        ton_api_key=os.getenv("TON_API_KEY", "").strip(),
    )
