"""Persistent store of client certificates for mTLS admin logins.

Each certificate is a .p12/.pfx bundle (client cert + private key) the user
drops into persona. The bundle is copied under PERSONA_HOME so it lives with
the rest of the profile data, and a profile references a certificate by name.
persona installs the referenced cert into the browser at launch instead of the
user importing it into the operating system's certificate store.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import threading
import time
from dataclasses import asdict, dataclass

from ...core.logging import get_logger
from ...utils.atomic import atomic_write_json

logger = get_logger("cert.store")


def _certs_file() -> str:
    override = os.getenv("PERSONA_CERTS_FILE")
    if override:
        return override
    from ...core.config import PERSONA_HOME

    return str(pathlib.Path(PERSONA_HOME) / "certificates.json")


def _certs_dir() -> str:
    override = os.getenv("PERSONA_CERTS_DIR")
    if override:
        return override
    from ...core.config import PERSONA_HOME

    return str(pathlib.Path(PERSONA_HOME) / "certificates")


@dataclass
class Certificate:
    name: str
    # path to the stored .p12/.pfx bundle under PERSONA_HOME
    p12_path: str
    # bundle password ("" if the .p12 has none)
    password: str = ""
    # admin URL/host the certificate logs into. The certificate is presented
    # ONLY to this host (via the terminator) — never to any other site — so it
    # can't become a fingerprint that identifies the operator elsewhere.
    url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class CertStore:
    def __init__(self) -> None:
        self.certs: dict[str, Certificate] = {}
        self._save_blocked = False
        # Mutated from the UI thread and the API thread; serialize every
        # read/write (RLock so a mutator can call _save while holding it).
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        p = pathlib.Path(_certs_file())
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            skipped = 0
            for name, d in data.items():
                # One malformed record must not abort the whole load — the next
                # save would overwrite certificates.json with only what parsed,
                # dropping every later certificate's .p12 password.
                try:
                    self.certs[name] = Certificate(
                        name=d.get("name", name),
                        p12_path=d.get("p12_path", ""),
                        password=d.get("password", ""),
                        url=d.get("url", ""),
                    )
                except Exception:
                    skipped += 1
                    logger.exception("Skipping malformed certificate %r", name)
            if skipped:
                logger.warning("Skipped %d malformed certificate record(s)", skipped)
        except Exception as e:
            logger.exception("Error loading certificates: %s", e)
            self._quarantine_certs_file()

    def _quarantine_certs_file(self) -> None:
        # An unreadable certificates.json still holds every .p12 bundle password;
        # move it aside so the next _save() can't overwrite it with the empty
        # in-memory dict.
        path = _certs_file()
        backup = f"{path}.corrupt-{int(time.time())}"
        try:
            pathlib.Path(path).rename(backup)
            logger.warning("Moved unreadable certificates file to %s", backup)
        except OSError:
            self._save_blocked = True
            logger.exception(
                "Could not back up %s; certificate saving disabled to avoid "
                "overwriting it",
                path,
            )

    def _save(self) -> None:
        if self._save_blocked:
            logger.error(
                "Not saving: certificates file failed to load and could not be "
                "backed up"
            )
            return
        try:
            # Holds .p12 bundle passwords, so 0600 + atomic.
            atomic_write_json(
                _certs_file(),
                {n: c.to_dict() for n, c in self.certs.items()},
                private=True,
            )
        except Exception as e:
            logger.exception("Error saving certificates: %s", e)

    def list(self) -> list[Certificate]:
        with self._lock:
            return list(self.certs.values())

    def names(self) -> list[str]:
        with self._lock:
            return list(self.certs.keys())

    def get(self, name: str) -> Certificate | None:
        with self._lock:
            return self.certs.get(name)

    def add(self, cert: Certificate) -> bool:
        with self._lock:
            if not cert.name or cert.name in self.certs:
                return False
            self.certs[cert.name] = cert
            self._save()
        return True

    def update(self, name: str, cert: Certificate) -> bool:
        with self._lock:
            if name not in self.certs:
                return False
            if cert.name != name and cert.name in self.certs:
                return False
            del self.certs[name]
            self.certs[cert.name] = cert
            self._save()
        return True

    def remove(self, name: str) -> bool:
        with self._lock:
            if name not in self.certs:
                return False
            del self.certs[name]
            self._save()
        return True

    def import_p12(self, name: str, source_path: str) -> str:
        """Copy a picked .p12/.pfx into persona's certificate store and return
        the stored path. The certificate lives inside persona; the user's
        original file is left untouched. The stored filename is derived from the
        certificate name so distinct certificates never collide."""
        with self._lock:
            certs_dir = _certs_dir()
            os.makedirs(certs_dir, exist_ok=True)
            ext = os.path.splitext(source_path)[1] or ".p12"
            safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
            dest = os.path.join(certs_dir, f"{safe}{ext}")
            shutil.copyfile(source_path, dest)
        return dest
