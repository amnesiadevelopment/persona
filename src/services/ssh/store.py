"""Persistent store of saved SSH hosts.

Each saved host carries the connection details plus an optional profile name —
when set, SSH routes through that profile's proxy (same exit IP as the
profile's browser). Stored under PERSONA_HOME so it lives with the rest of the
profile data.
"""

from __future__ import annotations

import json
import os
import pathlib
import threading
import time
from dataclasses import asdict, dataclass

from ...core.config import PERSONA_HOME
from ...core.logging import get_logger
from ...utils.atomic import atomic_write_json

logger = get_logger("ssh.store")


def _hosts_file() -> str:
    override = os.getenv("PERSONA_SSH_HOSTS_FILE")
    if override:
        return override
    return str(pathlib.Path(PERSONA_HOME) / "ssh_hosts.json")


@dataclass
class SSHHost:
    name: str
    host: str
    port: int = 22
    username: str = ""
    # auth: a key path (+ optional passphrase) and/or a password
    key_path: str = ""
    key_passphrase: str = ""
    password: str = ""
    # profile whose proxy to route through ("" = direct)
    profile: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class SSHHostStore:
    def __init__(self) -> None:
        self.hosts: dict[str, SSHHost] = {}
        self._save_blocked = False
        # Mutated from the UI thread and the API thread; serialize every
        # read/write (RLock so a mutator can call _save while holding it).
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        p = pathlib.Path(_hosts_file())
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            skipped = 0
            for name, d in data.items():
                # One malformed record must not abort the whole load — the next
                # save would overwrite ssh_hosts.json with only what parsed,
                # dropping every host's password + passphrase after it.
                try:
                    self.hosts[name] = SSHHost(
                        name=d.get("name", name),
                        host=d.get("host", ""),
                        port=int(d.get("port", 22)),
                        username=d.get("username", ""),
                        key_path=d.get("key_path", ""),
                        key_passphrase=d.get("key_passphrase", ""),
                        password=d.get("password", ""),
                        profile=d.get("profile", ""),
                    )
                except Exception:
                    skipped += 1
                    logger.exception("Skipping malformed ssh host %r", name)
            if skipped:
                logger.warning("Skipped %d malformed ssh host record(s)", skipped)
        except Exception as e:
            logger.exception("Error loading ssh hosts: %s", e)
            self._quarantine_hosts_file()

    def _quarantine_hosts_file(self) -> None:
        # An unreadable ssh_hosts.json still holds every host's password +
        # passphrase; move it aside so the next _save() can't overwrite it with
        # the empty in-memory dict.
        path = _hosts_file()
        backup = f"{path}.corrupt-{int(time.time())}"
        try:
            pathlib.Path(path).rename(backup)
            logger.warning("Moved unreadable ssh hosts file to %s", backup)
        except OSError:
            self._save_blocked = True
            logger.exception(
                "Could not back up %s; ssh host saving disabled to avoid "
                "overwriting it",
                path,
            )

    def _save(self) -> None:
        if self._save_blocked:
            logger.error(
                "Not saving: ssh hosts file failed to load and could not be "
                "backed up"
            )
            return
        try:
            # Holds SSH passwords + key passphrases, so 0600 + atomic.
            atomic_write_json(
                _hosts_file(),
                {n: h.to_dict() for n, h in self.hosts.items()},
                private=True,
            )
        except Exception as e:
            logger.exception("Error saving ssh hosts: %s", e)

    def list(self) -> list[SSHHost]:
        with self._lock:
            return list(self.hosts.values())

    def get(self, name: str) -> SSHHost | None:
        with self._lock:
            return self.hosts.get(name)

    def add(self, host: SSHHost) -> bool:
        with self._lock:
            if host.name in self.hosts:
                return False
            self.hosts[host.name] = host
            self._save()
        return True

    def update(self, name: str, host: SSHHost) -> bool:
        with self._lock:
            if name not in self.hosts:
                return False
            if host.name != name and host.name in self.hosts:
                return False
            del self.hosts[name]
            self.hosts[host.name] = host
            self._save()
        return True

    def remove(self, name: str) -> bool:
        with self._lock:
            if name not in self.hosts:
                return False
            del self.hosts[name]
            self._save()
        return True
