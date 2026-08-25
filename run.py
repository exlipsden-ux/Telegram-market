import os
import sys

import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(
    hostname=os.environ["VPS_HOST"],
    username=os.environ.get("VPS_USER", "root"),
    password=os.environ["VPS_PASSWORD"],
    timeout=25, banner_timeout=25, auth_timeout=25,
)

for cmd in sys.argv[1:]:
    _, stdout, stderr = client.exec_command(cmd, timeout=120)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    sys.stdout.buffer.write(f"$ {cmd}\n".encode("utf-8"))
    sys.stdout.buffer.write((out + err).encode("utf-8"))
    sys.stdout.buffer.write(f"\n[exit {code}]\n\n".encode("utf-8"))
    sys.stdout.flush()

client.close()
