"""SSH/SFTP client that routes through a profile's SOCKS proxy.

The technician works through a profile's exit IP; SSH must travel the same path
as that profile's browser. We open a SOCKS5 socket (PySocks) to the SSH host
via the profile's proxy and hand it to paramiko as the transport, so the SSH
session presents from the proxy's IP. Auth supports both a private key
(file + optional passphrase) and a password.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from urllib.parse import urlparse

import paramiko
import socks

from ...core.config import PERSONA_HOME
from ...core.logging import get_logger

logger = get_logger("ssh.client")

# Trust-on-first-use host-key store. The SSH session is DELIBERATELY routed
# through an untrusted SOCKS exit (Tor / commercial / residential) — exactly the
# MITM position. AutoAddPolicy would auto-accept a spoofed key from a compromised
# exit and hand it the cleartext password + key passphrase. Instead we pin the
# host key on first sight into a persona-managed known_hosts (0600) and hard-
# reject on any later mismatch.
_KNOWN_HOSTS = os.path.join(PERSONA_HOME, "known_hosts")
_KNOWN_HOSTS_LOCK = threading.Lock()


class _TOFUPolicy(paramiko.MissingHostKeyPolicy):
    """Pin an unseen host key on first sight; a MissingHostKey callback only
    fires when the key is NOT already in known_hosts. A key that IS present but
    DIFFERENT is rejected by paramiko before this callback (raises), which is the
    MITM-detection we want. So here we only persist a genuinely-new key."""

    def missing_host_key(self, client, hostname, key):
        with _KNOWN_HOSTS_LOCK:
            try:
                os.makedirs(PERSONA_HOME, exist_ok=True)
                hostkeys = client.get_host_keys()
                hostkeys.add(hostname, key.get_name(), key)
                hostkeys.save(_KNOWN_HOSTS)
                try:
                    os.chmod(_KNOWN_HOSTS, 0o600)
                except OSError:
                    pass
                logger.info(
                    "Pinned SSH host key for %s (%s) on first sight",
                    hostname, key.get_name(),
                )
            except Exception:
                logger.exception("Could not persist SSH host key for %s", hostname)


def known_hosts_entry_name(host: str, port: int = 22) -> str:
    """The name paramiko pins a host:port under in known_hosts.

    NOT hand-rolled: this mirrors ``SSHClient.connect`` exactly — it computes
    ``server_hostkey_name`` as the bare hostname on the default port and
    ``[host]:port`` on any other, and that is the string both the TOFU pin and
    the later lookup are keyed by. Reading the rule off paramiko's own constant
    (rather than a literal 22) keeps the two in step.
    """
    try:
        p = int(port)
    except (TypeError, ValueError):
        p = paramiko.config.SSH_PORT
    return host if p == paramiko.config.SSH_PORT else f"[{host}]:{p}"


def remove_pinned_host_key(host: str, port: int = 22) -> bool:
    """Drop one host:port's TOFU pin from persona's known_hosts.

    The counterpart to :class:`_TOFUPolicy`, and the ONLY remover: a saved SSH
    host that has been connected to owns a second file outside every profile
    perimeter, and permanent deletion of the record has to be able to take it
    with it. Returns True when an entry was actually removed.

    Deliberately NOT string matching against the file. paramiko owns the
    normalization (bare name vs ``[host]:port``, and the hashed ``|1|`` form),
    so load / lookup / delete go through ``HostKeys`` and inherit whatever it
    does — including matching a hashed entry we never wrote.

    Two restraints worth keeping:

    * Every key TYPE pinned for that name goes (``HostKeys.__delitem__``
      removes one entry per call), so an RSA pin is not left behind when the
      ed25519 one is dropped.
    * An entry whose line names OTHER hosts as well is LEFT ALONE. OpenSSH
      allows ``a,b <keytype> <key>`` on one line; persona's writer never
      produces one, but a file we did not write can, and deleting that entry
      would silently un-pin ``b`` too — re-arming trust-on-first-use for a host
      nobody asked us to forget.

    Caller-facing errors are the caller's to swallow; this raises so a real
    failure is not mistaken for "there was nothing to remove".
    """
    name = known_hosts_entry_name(host, port)
    with _KNOWN_HOSTS_LOCK:
        if not os.path.exists(_KNOWN_HOSTS):
            return False
        hostkeys = paramiko.HostKeys()
        hostkeys.load(_KNOWN_HOSTS)
        removed = False
        # `del hostkeys[name]` removes ONE entry per call and raises KeyError
        # when none is left, so loop until it does — that is how every key type
        # pinned under this name goes rather than only the first.
        while hostkeys.lookup(name) is not None:
            if _names_other_hosts(hostkeys, name):
                break
            try:
                del hostkeys[name]
            except KeyError:  # pragma: no cover - lookup just said it is there
                break
            removed = True
        if removed:
            hostkeys.save(_KNOWN_HOSTS)
            try:
                os.chmod(_KNOWN_HOSTS, 0o600)
            except OSError:
                pass
            logger.info("Removed the pinned SSH host key for %s", name)
        return removed


class _OneName:
    """A stand-in entry so ``HostKeys._hostname_matches`` can be asked about a
    SINGLE hostname string — which is the only way to tell a hashed name that
    IS ours from one that belongs to another host."""

    def __init__(self, hostname: str) -> None:
        self.hostnames = [hostname]


def _names_other_hosts(hostkeys, name: str) -> bool:
    """True when the first matching entry's line also names a DIFFERENT host.

    Deleting such an entry would un-pin a host nobody asked us to forget. The
    inspection needs ``HostKeys._entries`` (paramiko exposes the hostnames of a
    matched entry nowhere public); if a future paramiko drops it, we lose the
    guard rather than the feature — persona's own writer never produces a
    multi-name line, so the guarded case cannot arise from our own file.
    """
    entries = getattr(hostkeys, "_entries", None)
    matches = getattr(hostkeys, "_hostname_matches", None)
    if entries is None or matches is None:  # pragma: no cover - paramiko drift
        return False
    for entry in entries:
        if not matches(name, entry):
            continue
        others = [h for h in entry.hostnames if not matches(name, _OneName(h))]
        if others:
            logger.warning(
                "Left the known_hosts entry for %s in place: its line also "
                "names %s",
                name,
                ", ".join(others),
            )
            return True
        return False
    return False


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
    # Load previously-pinned keys; TOFU-pin an unseen host, hard-reject a changed
    # one (paramiko raises on a known-but-mismatched key). Never AutoAddPolicy —
    # the untrusted SOCKS exit is a MITM position for a password/passphrase steal.
    if os.path.exists(_KNOWN_HOSTS):
        try:
            client.load_host_keys(_KNOWN_HOSTS)
        except Exception:
            logger.exception("Could not load %s", _KNOWN_HOSTS)
    client.set_missing_host_key_policy(_TOFUPolicy())
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
