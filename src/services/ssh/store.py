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
from dataclasses import asdict, dataclass

from ...core.config import PERSONA_HOME
from ...core.logging import get_logger

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
        self._load()

    def _load(self) -> None:
        p = pathlib.Path(_hosts_file())
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for name, d in data.items():
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
        except Exception as e:
            logger.exception("Error loading ssh hosts: %s", e)

    def _save(self) -> None:
        try:
            pathlib.Path(_hosts_file()).write_text(
                json.dumps({n: h.to_dict() for n, h in self.hosts.items()}, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.exception("Error saving ssh hosts: %s", e)

    def list(self) -> list[SSHHost]:
        return list(self.hosts.values())

    def get(self, name: str) -> SSHHost | None:
        return self.hosts.get(name)

    def add(self, host: SSHHost) -> bool:
        if host.name in self.hosts:
            return False
        self.hosts[host.name] = host
        self._save()
        return True

    def update(self, name: str, host: SSHHost) -> bool:
        if name not in self.hosts:
            return False
        if host.name != name and host.name in self.hosts:
            return False
        del self.hosts[name]
        self.hosts[host.name] = host
        self._save()
        return True

    def remove(self, name: str) -> bool:
        if name not in self.hosts:
            return False
        del self.hosts[name]
        self._save()
        return True
