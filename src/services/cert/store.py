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
import uuid
from dataclasses import asdict, dataclass

from ...core.logging import get_logger
from ...utils.atomic import atomic_write_json
from ...utils.store_guard import StoreGuardMixin
from ...utils.trashable import TrashableMixin, restore_kwargs

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


class CertStore(StoreGuardMixin, TrashableMixin):
    _guard_logger = logger
    _guard_noun_plural = "certificates"
    # Singular differs from a naive plural-minus-s: the existing log says
    # "certificate saving disabled", not "certificates saving disabled".
    _guard_noun_singular = "certificate"

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

    def _store_path(self) -> str:
        # Recomputed per call: PERSONA_CERTS_FILE can point elsewhere.
        return _certs_file()

    def _quarantine_certs_file(self) -> None:
        # An unreadable certificates.json still holds every .p12 bundle password;
        # move it aside so the next _save() can't overwrite it with the empty
        # in-memory dict.
        self._quarantine_store_file()

    def _save(self) -> None:
        if self._save_is_blocked():
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
            old_path = self.certs[name].p12_path
            del self.certs[name]
            self.certs[cert.name] = cert
            self._save()
            # A re-import points the record at a new store-owned .p12; delete the
            # superseded one so stale private-key material doesn't linger.
            if old_path != cert.p12_path:
                self._delete_owned_p12(old_path)
        return True

    def remove(self, name: str) -> bool:
        """Move a certificate to the trash. The .p12 — private-key material — is
        MOVED into a trash area inside persona's own certificate store, not
        deleted: trashing a certificate does NOT remove its key bundle from disk,
        and its bundle password rides along in trash.json (0600, like
        certificates.json). Permanent deletion is what actually destroys both."""
        with self._lock:
            if name not in self.certs:
                return False
            cert = self.certs.pop(name)
            self._save()
            parked = self._park_owned_p12(cert.p12_path)
        self._trash().add(
            "certificate",
            name,
            {"certificate": cert.to_dict(), "parked_p12": parked},
            material_path=parked,
        )
        logger.info("Moved certificate to trash: %s", name)
        return True

    def restore_certificate(self, entry) -> tuple[bool, str]:
        """Put a trashed certificate back, moving its .p12 out of the trash area
        so the restored record points at a working bundle again."""
        name = entry.name
        with self._lock:
            if name in self.certs:
                return False, (
                    f"A certificate named '{name}' already exists. Rename or "
                    "delete it, then restore again."
                )
            d = entry.payload.get("certificate") or {}
            p12_path = d.get("p12_path", "")
            parked = entry.payload.get("parked_p12") or ""
            if parked:
                restored = self._unpark_owned_p12(parked, p12_path)
                if restored is None:
                    return False, (
                        "Could not restore the certificate's .p12 bundle; the "
                        "certificate was left in the trash."
                    )
                p12_path = restored
            self.certs[name] = Certificate(
                **{
                    **restore_kwargs(Certificate, d, name),
                    # CARVE-OUT: p12_path does NOT come from the payload. The
                    # payload records where the bundle lived when the record
                    # was live; remove() then MOVED that bundle into the trash
                    # area. Restoring the payload's value verbatim would point
                    # the certificate at the parked location (or, once the
                    # unpark has moved the file, at nothing at all), so the
                    # unparked path computed above wins — and the early return
                    # above it still fires first when the bundle cannot be
                    # moved back, leaving the certificate in the trash rather
                    # than restoring a record with no key material.
                    "p12_path": p12_path,
                }
            )
            self._save()
        logger.info("Restored certificate from trash: %s", name)
        return True, ""

    def _trash_certs_dir(self) -> str:
        """Where a trashed certificate's .p12 is parked: INSIDE persona's own
        certificate store dir, so it keeps exactly the protection it had and
        stays covered by _delete_owned_p12's containment check."""
        return os.path.join(_certs_dir(), ".trash")

    def _park_owned_p12(self, path: str) -> str:
        """Move a store-owned .p12 into the trash area, returning its new path.

        Returns "" when there is nothing of ours to move — either no path at all,
        or a LEGACY record pointing at the operator's original file outside the
        store. That file is never persona's to move any more than it is ours to
        delete, so it is left exactly where it is (and restore points back at
        it unchanged).
        """
        if not path or not self._is_owned_p12(path):
            return ""
        try:
            trash_dir = self._trash_certs_dir()
            os.makedirs(trash_dir, exist_ok=True)
            dest = os.path.join(trash_dir, os.path.basename(path))
            os.replace(path, dest)
            return dest
        except OSError:
            logger.exception("Could not park certificate file %s in the trash", path)
            return ""

    def _unpark_owned_p12(self, parked: str, original: str) -> str | None:
        """Move a parked .p12 back to where the restored record expects it."""
        if not os.path.exists(parked):
            # Nothing to move back; the record still points at its original path.
            return original
        dest = original if original and self._is_owned_p12(original) else (
            os.path.join(_certs_dir(), os.path.basename(parked))
        )
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            os.replace(parked, dest)
            return dest
        except OSError:
            logger.exception("Could not restore certificate file %s", parked)
            return None

    @staticmethod
    def _is_owned_p12(path: str) -> bool:
        """True when a .p12 lives inside persona's own store dir — the same test
        _delete_owned_p12 applies, kept in one place so parking and deleting can
        never disagree about what persona owns."""
        certs_dir = os.path.abspath(_certs_dir())
        return os.path.abspath(path).startswith(certs_dir + os.sep)

    def _delete_owned_p12(self, path: str) -> None:
        """Delete a .p12 ONLY if it lives inside persona's own store dir. A
        legacy record may point at the user's original file outside the store —
        that is never ours to delete."""
        if not path:
            return
        try:
            if self._is_owned_p12(path):
                os.remove(path)
        except OSError:
            logger.exception("Could not delete stored certificate file %s", path)

    def import_p12(self, name: str, source_path: str) -> str:
        """Copy a picked .p12/.pfx into persona's certificate store and return
        the stored path. The certificate lives inside persona; the user's
        original file is left untouched. The stored filename is a fresh UUID, so
        two certificates whose names sanitize alike ("acme admin" vs
        "acme.admin") can never share a file and clobber each other's key."""
        with self._lock:
            certs_dir = _certs_dir()
            os.makedirs(certs_dir, exist_ok=True)
            ext = os.path.splitext(source_path)[1] or ".p12"
            dest = os.path.join(certs_dir, f"{uuid.uuid4().hex}{ext}")
            shutil.copyfile(source_path, dest)
        return dest
