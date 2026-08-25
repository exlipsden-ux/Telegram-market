"""SSH-туннель до localhost.run с автоподъёмом.

Бесплатный туннель рвётся по двум причинам: обрыв связи и «tunnel inactivity
timeout» — сервис закрывает соединение, если по нему давно не было запросов.
Поэтому здесь и переподключение, и периодический пинг собственного адреса.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

import aiohttp

log = logging.getLogger("tunnel")

URL_RE = re.compile(rb"https://[a-z0-9]+\.lhr\.life")

# Свой ключ даёт localhost.run стабильный поддомен — адрес витрины
# перестаёт прыгать при каждом переподключении.
TUNNEL_KEY = Path(__file__).resolve().parent.parent / ".tunnel_key"

SSH_ARGS = [
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ServerAliveInterval=20",
    "-o", "ServerAliveCountMax=3",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "IdentitiesOnly=yes",
    "-o", "LogLevel=ERROR",
]
if TUNNEL_KEY.exists():
    SSH_ARGS += ["-i", str(TUNNEL_KEY)]

KEEPALIVE_SECONDS = 60
RETRY_SECONDS = 5


class Tunnel:
    def __init__(self, port: int, on_url: Callable[[str], Awaitable[None]]):
        self._port = port
        self._on_url = on_url
        self.url: str | None = None

    async def _read_url(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                return
            match = URL_RE.search(line)
            if match:
                url = match.group().decode()
                if url != self.url:
                    self.url = url
                    log.info("адрес витрины: %s", url)
                    await self._on_url(url)

    async def _keepalive(self) -> None:
        """Без трафика localhost.run закрывает туннель, поэтому дёргаем его сами."""
        async with aiohttp.ClientSession() as session:
            while True:
                await asyncio.sleep(KEEPALIVE_SECONDS)
                if not self.url:
                    continue
                try:
                    async with session.get(f"{self.url}/api/config", timeout=aiohttp.ClientTimeout(total=20)) as r:
                        await r.read()
                except Exception as exc:  # noqa: BLE001 — пинг не должен ронять задачу
                    log.debug("пинг не прошёл: %s", exc)

    async def run(self) -> None:
        keepalive = asyncio.create_task(self._keepalive())
        try:
            while True:
                proc = await asyncio.create_subprocess_exec(
                    "ssh", *SSH_ARGS, "-R", f"80:localhost:{self._port}", "nokey@localhost.run",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    stdin=asyncio.subprocess.DEVNULL,
                )
                try:
                    await self._read_url(proc)
                finally:
                    if proc.returncode is None:
                        proc.terminate()
                    await proc.wait()

                self.url = None
                log.warning("туннель разорван, переподключаюсь через %s с", RETRY_SECONDS)
                await asyncio.sleep(RETRY_SECONDS)
        except asyncio.CancelledError:
            raise
        finally:
            keepalive.cancel()
