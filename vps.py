"""Подключение к VPS по SSH.

Пароль берётся из переменных окружения (VPS_HOST / VPS_USER / VPS_PASSWORD),
чтобы не лежал в коде и не попадал в git.
"""

from __future__ import annotations

import os
import sys

import paramiko


def connect() -> paramiko.SSHClient:
    host = os.environ["VPS_HOST"]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        username=os.getenv("VPS_USER", "root"),
        password=os.environ["VPS_PASSWORD"],
        timeout=25,
        banner_timeout=25,
        auth_timeout=25,
    )
    return client


def run(client: paramiko.SSHClient, cmd: str) -> tuple[int, str]:
    _, stdout, stderr = client.exec_command(cmd, timeout=90)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    return stdout.channel.recv_exit_status(), (out + err).strip()


if __name__ == "__main__":
    with connect() as ssh:
        for command in sys.argv[1:]:
            code, output = run(ssh, command)
            print(f"$ {command}\n{output}\n[exit {code}]\n")
