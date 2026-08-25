from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    trial_used  INTEGER NOT NULL DEFAULT 0,
    balance     REAL    NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    product     TEXT    NOT NULL,
    title       TEXT    NOT NULL,
    amount      REAL    NOT NULL,
    method      TEXT,
    status      TEXT    NOT NULL DEFAULT 'pending',
    payload     TEXT,
    provider_ref TEXT,
    created_at  TEXT    NOT NULL,
    expires_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, status);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        try:
            await self._conn.execute("ALTER TABLE orders ADD COLUMN provider_ref TEXT")
        except Exception:
            pass
        try:
            await self._conn.execute("ALTER TABLE orders ADD COLUMN bot_id INTEGER")
        except Exception:
            pass
        try:
            await self._conn.execute("ALTER TABLE orders ADD COLUMN ton_nanotons INTEGER")
        except Exception:
            pass
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() не вызван")
        return self._conn

    async def upsert_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        await self.conn.execute(
            """
            INSERT INTO users (id, username, first_name, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET username = excluded.username,
                                          first_name = excluded.first_name
            """,
            (user_id, username, first_name, now()),
        )
        await self.conn.commit()

    async def get_user(self, user_id: int) -> aiosqlite.Row | None:
        async with self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cur:
            return await cur.fetchone()

    async def trial_used(self, user_id: int) -> bool:
        row = await self.get_user(user_id)
        return bool(row and row["trial_used"])

    async def mark_trial_used(self, user_id: int) -> None:
        await self.conn.execute("UPDATE users SET trial_used = 1 WHERE id = ?", (user_id,))
        await self.conn.commit()

    async def create_order(
        self,
        user_id: int,
        product: str,
        title: str,
        amount: float,
        method: str | None = None,
        days: int | None = None,
        bot_id: int | None = None,
    ) -> int:
        expires_at = None
        if days:
            expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")

        cur = await self.conn.execute(
            """
            INSERT INTO orders (user_id, product, title, amount, method, created_at, expires_at, bot_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, product, title, amount, method, now(), expires_at, bot_id),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def set_order_status(self, order_id: int, status: str, payload: str | None = None) -> None:
        await self.conn.execute(
            "UPDATE orders SET status = ?, payload = COALESCE(?, payload) WHERE id = ?",
            (status, payload, order_id),
        )
        await self.conn.commit()

    async def set_provider_ref(self, order_id: int, provider_ref: str) -> None:
        """Сохранить внешний id платежа у провайдера, который не принимает наш order_id напрямую (PayCore)."""
        await self.conn.execute("UPDATE orders SET provider_ref = ? WHERE id = ?", (provider_ref, order_id))
        await self.conn.commit()

    async def get_order_by_provider_ref(self, provider_ref: str) -> aiosqlite.Row | None:
        async with self.conn.execute(
            "SELECT * FROM orders WHERE provider_ref = ?", (provider_ref,)
        ) as cur:
            return await cur.fetchone()

    async def set_ton_amount(self, order_id: int, nanotons: int) -> None:
        """Сумма в нанотонах, зафиксированная в момент создания TON-счёта — по ней сверяем входящий перевод."""
        await self.conn.execute("UPDATE orders SET ton_nanotons = ? WHERE id = ?", (nanotons, order_id))
        await self.conn.commit()

    async def settings(self) -> dict[str, float]:
        """Изменяемые из админки числа: курсы и цены тарифов."""
        async with self.conn.execute("SELECT key, value FROM settings") as cur:
            out: dict[str, float] = {}
            for row in await cur.fetchall():
                try:
                    out[row["key"]] = float(row["value"])
                except ValueError:
                    continue
            return out

    async def set_setting(self, key: str, value: float) -> None:
        await self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        await self.conn.commit()

    async def stats(self) -> dict[str, float | int]:
        async with self.conn.execute(
            """
            SELECT
              COUNT(*)                                            AS orders_total,
              COALESCE(SUM(CASE WHEN status='paid' THEN 1 END), 0) AS paid,
              COALESCE(SUM(CASE WHEN status='paid' THEN amount END), 0) AS revenue
            FROM orders
            """
        ) as cur:
            row = await cur.fetchone()

        async with self.conn.execute("SELECT COUNT(*) AS n FROM users") as cur:
            users = (await cur.fetchone())["n"]

        return {
            "orders": row["orders_total"],
            "paid": row["paid"],
            "revenue": round(row["revenue"], 2),
            "users": users,
        }

    async def recent_orders(self, limit: int = 50) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            """
            SELECT o.*, u.username
            FROM orders o LEFT JOIN users u ON u.id = o.user_id
            ORDER BY o.id DESC LIMIT ?
            """,
            (limit,),
        ) as cur:
            return list(await cur.fetchall())

    async def all_users(self, limit: int = 100) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            """
            SELECT u.*,
                   (SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id AND o.status='paid') AS paid,
                   (SELECT COALESCE(SUM(amount),0) FROM orders o WHERE o.user_id = u.id AND o.status='paid') AS spent
            FROM users u ORDER BY u.created_at DESC LIMIT ?
            """,
            (limit,),
        ) as cur:
            return list(await cur.fetchall())

    async def stars_sold(self) -> int:
        """Сколько звёзд реально продано — для счётчика на витрине.
        Количество берём из начала заголовка заказа (его формирует сам бот: "<qty> Telegram Stars → @...")."""
        async with self.conn.execute(
            "SELECT title FROM orders WHERE product = 'stars' AND status = 'paid'"
        ) as cur:
            total = 0
            for row in await cur.fetchall():
                qty, _, _ = str(row["title"]).partition(" ")
                if qty.isdigit():
                    total += int(qty)
            return total

    async def sales_counts(self) -> dict[str, int]:
        """Сколько раз каждый товар реально куплен — для счётчика на витрине."""
        async with self.conn.execute(
            "SELECT product, COUNT(*) AS n FROM orders WHERE status = 'paid' GROUP BY product"
        ) as cur:
            return {row["product"]: row["n"] for row in await cur.fetchall()}

    async def get_order(self, order_id: int) -> aiosqlite.Row | None:
        async with self.conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)) as cur:
            return await cur.fetchone()

    async def mark_paid(self, order_id: int, method: str, payload: str | None = None) -> bool:
        """Засчитывает оплату один раз. Возвращает True, если это первое зачисление
        (чтобы не начислить баланс дважды при повторном вебхуке)."""
        cur = await self.conn.execute(
            """
            UPDATE orders SET status = 'paid', method = ?, payload = COALESCE(?, payload)
            WHERE id = ? AND status != 'paid'
            """,
            (method, payload, order_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def add_balance(self, user_id: int, amount: float) -> float:
        await self.conn.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ?", (amount, user_id)
        )
        await self.conn.commit()
        row = await self.get_user(user_id)
        return round(row["balance"], 2) if row else 0.0

    async def balance_of(self, user_id: int) -> float:
        row = await self.get_user(user_id)
        return round(row["balance"], 2) if row else 0.0

    async def orders_of(self, user_id: int, active: bool | None = None) -> list[aiosqlite.Row]:
        sql = "SELECT * FROM orders WHERE user_id = ? AND status = 'paid'"
        params: list[object] = [user_id]

        if active is True:
            sql += " AND (expires_at IS NULL OR expires_at > ?)"
            params.append(now())
        elif active is False:
            sql += " AND expires_at IS NOT NULL AND expires_at <= ?"
            params.append(now())

        sql += " ORDER BY id DESC"
        async with self.conn.execute(sql, params) as cur:
            return list(await cur.fetchall())
