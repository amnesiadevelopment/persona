"""SSH/SFTP client that routes through a profile's SOCKS proxy.

The technician works through a profile's exit IP; SSH must travel the same path
as that profile's browser. We open a SOCKS5 socket (PySocks) to the SSH host
via the profile's proxy and hand it to paramiko as the transport, so the SSH
session presents from the proxy's IP. Auth supports both a private key
(file + optional passphrase) and a password.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import paramiko
import socks


@dataclass
class SSHTarget:
    host: str
    port: int = 22
    username: str = ""
    password: str = ""
    key_path: str = ""
    key_passphrase: str = ""
    # Proxy URL to route through (socks5://user:pass@host:port). Empty = direct.
    proxy_url: str = ""


def _proxy_socket(target: SSHTarget, timeout: float) -> socks.socksocket | None:
    """Open a SOCKS5 socket to the SSH host through the proxy, or None when no
    proxy is set (paramiko then connects directly)."""
    if not target.proxy_url:
        return None
    p = urlparse(
        target.proxy_url
        if "://" in target.proxy_url
        else "socks5://" + target.proxy_url
    )
    sock = socks.socksocket()
    sock.set_proxy(
        socks.SOCKS5,
        p.hostname,
        p.port or 1080,
        username=p.username or None,
        password=p.password or None,
    )
    sock.settimeout(timeout)
    sock.connect((target.host, target.port))
    return sock


def _load_key(target: SSHTarget):
    if not target.key_path:
        return None
    pw = target.key_passphrase or None
    # try the common key types; paramiko needs the right class
    for cls in (
        paramiko.Ed25519Key,
        paramiko.ECDSAKey,
        paramiko.RSAKey,
    ):
        try:
            return cls.from_private_key_file(target.key_path, password=pw)
        except paramiko.SSHException:
            continue
    raise paramiko.SSHException("Unsupported or unreadable private key")


def connect(target: SSHTarget, timeout: float = 20.0) -> paramiko.SSHClient:
    """Open an SSH connection to target (through its proxy when set). Caller
    closes the returned client."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sock = _proxy_socket(target, timeout)
    pkey = _load_key(target)
    client.connect(
        hostname=target.host,
        port=target.port,
        username=target.username or None,
        password=target.password or None,
        pkey=pkey,
        sock=sock,
        timeout=timeout,
        allow_agent=False,
        look_for_keys=False,
    )
    return client


def run_command(
    target: SSHTarget, command: str, timeout: float = 30.0
) -> tuple[int, str, str]:
    """Run a command, return (exit_status, stdout, stderr)."""
    client = connect(target, timeout=timeout)
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        return code, out, err
    finally:
        client.close()


def sftp_list(target: SSHTarget, path: str = ".") -> list[dict]:
    """List a remote directory: name, size, is_dir, mtime per entry."""
    client = connect(target)
    try:
        sftp = client.open_sftp()
        entries = []
        for a in sftp.listdir_attr(path):
            entries.append(
                {
                    "name": a.filename,
                    "size": a.st_size,
                    "is_dir": bool(a.st_mode and (a.st_mode & 0o40000)),
                    "mtime": a.st_mtime,
                }
            )
        sftp.close()
        return entries
    finally:
        client.close()


def sftp_get(target: SSHTarget, remote_path: str, local_path: str) -> None:
    client = connect(target)
    try:
        sftp = client.open_sftp()
        sftp.get(remote_path, local_path)
        sftp.close()
    finally:
        client.close()


def sftp_put(target: SSHTarget, local_path: str, remote_path: str) -> None:
    client = connect(target)
    try:
        sftp = client.open_sftp()
        sftp.put(local_path, remote_path)
        sftp.close()
    finally:
        client.close()


# --- tmux helpers (for the MCP tools) ---

def tmux_send(target: SSHTarget, session: str, keys: str) -> tuple[int, str, str]:
    """Send keys to a tmux session (creating it if absent), then return."""
    sess = _q(session)
    cmd = (
        f"tmux has-session -t {sess} 2>/dev/null || "
        f"tmux new-session -d -s {sess}; "
        f"tmux send-keys -t {sess} {_q(keys)} Enter"
    )
    return run_command(target, cmd)


def tmux_capture(target: SSHTarget, session: str, lines: int = 200) -> str:
    """Capture the visible tmux pane (last `lines` lines)."""
    cmd = f"tmux capture-pane -p -t {_q(session)} -S -{int(lines)}"
    code, out, err = run_command(target, cmd)
    return out if code == 0 else (err or "")


def _q(s: str) -> str:
    """Single-quote a string for the remote shell."""
    return "'" + s.replace("'", "'\\''") + "'"
