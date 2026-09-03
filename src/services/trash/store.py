"""The recovery floor beneath every delete.

Every delete in persona used to be immediate and total: a profile's whole data
dir (cookies, logins) was rmtree'd, a proxy's SOCKS5 creds / an SSH host's
password / a certificate's private-key bundle were dropped on the spot, and the
confirmation dialog's "This action cannot be undone." was simply true. A
mis-click cost a logged-in identity.

This store is the recoverable middle: a deleted record is *moved* here, stays
visible with the date it was deleted, and can be restored exactly as it was.

Three properties this store exists to guarantee, and how it gets them:

1. **A trashed record is no weaker than the live one.** trash.json lives inside
   PERSONA_HOME (0700, like every other store), is written atomically and 0600
   (``private=True``) because the payloads carry proxy credentials, SSH
   passwords and .p12 bundle passwords verbatim, and inherits the shared
   corrupt-file quarantine via StoreGuardMixin. The trash is the same store's
   own guarded area, never a second, less-guarded copy of the operator's
   identity.
2. **Nothing in the trash is reachable as a live record.** Trashed records live
   ONLY here — they are removed from the live store's dict before they land, so
   no enumeration path can see them. A trashed profile's data dir is moved out
   of the launchable area into the trash area under an opaque token.
3. **"Permanently deleted" means it.** ``remove()``/``clear()`` drop the record
   here; the *caller* is responsible for destroying the on-disk material it
   points at (a profile data dir, a stored .p12), which is why entries carry
   ``material_path``. The panic wipe calls ``clear()`` and destroys that
   material, so a wipe that claims everything is gone is telling the truth.

Retention is 30 days, enforced on app start (see ``expired()``). It is a floor
beneath a mis-click, not an archive.
"""

from __future__ import annotations

import json
import os
import pathlib
import threading
import time
import uuid
from dataclasses import dataclass, field

from ...core.logging import get_logger
from ...utils.atomic import atomic_write_json
from ...utils.store_guard import StoreGuardMixin

logger = get_logger("trash.store")

#: How long a trashed record survives before app start purges it. Long enough
#: to notice a mistake, short enough that abandoned identities don't pile up.
RETENTION_DAYS = 30

#: How close to destruction an entry has to be before the rail says anything.
#:
#: A PRODUCT CHOICE, and it is the whole difference between a signal and
#: permanent chrome. The number is bounded from both sides:
#:
#: * TOO SHORT (24-48h) is missed by exactly the operator this exists for. The
#:   gap needs someone who trashed something and did not come back; a two-day
#:   window can fall entirely inside one weekend away, so the badge lights and
#:   goes out again with nobody ever at the machine.
#: * TOO LONG (14 days, half the window) is worse than nothing. It would be lit
#:   for most of the entry's life, which makes it chrome rather than a state —
#:   and that is precisely the noise ``App._status_needs_reveal`` refuses: an
#:   affordance on a line that is already whole invites a click that does
#:   nothing. A badge that is always on stops meaning "act now".
#:
#: Seven days is the smallest window that survives an ordinary absence. Someone
#: who opens persona on working days sees it on ~5 separate starts before the
#: entry is destroyed; someone away for a weekend or a short trip still gets
#: several. It is also under a quarter of ``RETENTION_DAYS``, so the rail is
#: quiet for the great majority of every entry's life.
#:
#: It does NOT change the floor. ``RETENTION_DAYS`` is still what enforcement
#: reads; this only decides when the operator is told.
EXPIRY_WARNING_DAYS = 7

KIND_PROFILE = "profile"
KIND_BOOKMARK = "bookmark"
KIND_POOL = "pool"
KIND_PROXY = "proxy"
KIND_SSH_HOST = "ssh_host"
KIND_CERTIFICATE = "certificate"

KINDS = (
    KIND_PROFILE,
    KIND_BOOKMARK,
    KIND_POOL,
    KIND_PROXY,
    KIND_SSH_HOST,
    KIND_CERTIFICATE,
)

#: Human labels for log lines and the UI/API, so the six kinds are named in one
#: place instead of being spelled out at every call site.
KIND_LABELS = {
    KIND_PROFILE: "profile",
    KIND_BOOKMARK: "bookmark",
    KIND_POOL: "bookmark pool",
    KIND_PROXY: "proxy",
    KIND_SSH_HOST: "SSH host",
    KIND_CERTIFICATE: "certificate",
}

#: Kinds whose payload still holds secret material after trashing. The honest
#: consequence the interface must state rather than hide: trashing one of these
#: does NOT remove its secret from disk — permanent deletion does.
KINDS_WITH_SECRETS = (KIND_PROXY, KIND_SSH_HOST, KIND_CERTIFICATE)


def trash_file() -> str:
    """Path of trash.json. Recomputed per call (PERSONA_TRASH_FILE can point
    elsewhere, and the specs monkeypatch the env var)."""
    override = os.getenv("PERSONA_TRASH_FILE")
    if override:
        return override
    from ...core.config import PERSONA_HOME

    return str(pathlib.Path(PERSONA_HOME) / "trash.json")


@dataclass
class TrashEntry:
    """One deleted record, held whole so restore returns it exactly as it was."""

    id: str
    kind: str
    name: str
    deleted_at: float
    #: The record's own to_dict(), verbatim — restore rebuilds from this, so a
    #: restored proxy/host/certificate keeps its credentials and a restored
    #: profile keeps its fingerprint-bearing name, settings and assignments.
    payload: dict = field(default_factory=dict)
    #: On-disk material this entry owns and that permanent deletion must
    #: destroy: a profile's moved data dir, or a certificate's moved .p12.
    #: Empty for records that are pure JSON.
    material_path: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "deleted_at": self.deleted_at,
            "payload": self.payload,
            "material_path": self.material_path,
        }

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def holds_secret_material(self) -> bool:
        """True when this entry's stored data is still secret material on disk,
        so the UI/API can say so instead of implying trashing shredded it."""
        return self.kind in KINDS_WITH_SECRETS

    def expires_at(self, retention_days: int = RETENTION_DAYS) -> float:
        return self.deleted_at + retention_days * 86400


class TrashStore(StoreGuardMixin):
    _guard_logger = logger
    _guard_noun_plural = "trash"
    _guard_noun_singular = "trash"

    def __init__(self, now=time.time) -> None:
        self.entries: dict[str, TrashEntry] = {}
        self._now = now
        self._save_blocked = False
        # Mutated from the UI thread and the API thread; serialize every
        # read/write (RLock so a mutator can call _save while holding it).
        self._lock = threading.RLock()
        self._load()

    # --- persistence ---

    def _load(self) -> None:
        p = pathlib.Path(trash_file())
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            skipped = 0
            for entry_id, d in data.items():
                # One malformed record must not abort the whole load — the next
                # save would overwrite trash.json with only what parsed, which
                # would destroy the very records this store exists to preserve.
                try:
                    self.entries[entry_id] = TrashEntry(
                        id=d.get("id", entry_id),
                        kind=d["kind"],
                        name=d.get("name", ""),
                        deleted_at=float(d.get("deleted_at", 0.0)),
                        payload=d.get("payload") or {},
                        material_path=d.get("material_path", ""),
                    )
                except Exception:
                    skipped += 1
                    logger.exception("Skipping malformed trash entry %r", entry_id)
            if skipped:
                logger.warning("Skipped %d malformed trash entry/entries", skipped)
        except Exception as e:
            logger.exception("Error loading trash: %s", e)
            self._quarantine_store_file()

    def _store_path(self) -> str:
        return trash_file()

    def _save(self) -> None:
        if self._save_is_blocked():
            return
        try:
            # Payloads carry proxy creds, SSH passwords and .p12 passwords —
            # exactly what the live stores write 0600 — so the trashed copy is
            # written at the SAME protection, never looser.
            atomic_write_json(
                trash_file(),
                {eid: e.to_dict() for eid, e in self.entries.items()},
                private=True,
            )
        except Exception as e:
            logger.exception("Error saving trash: %s", e)

    # --- mutation ---

    def new_id(self) -> str:
        """An opaque token for a trash entry. Also names the trashed profile's
        data dir, so the trash area never carries a profile name in cleartext."""
        return uuid.uuid4().hex

    def add(
        self,
        kind: str,
        name: str,
        payload: dict,
        *,
        entry_id: str | None = None,
        material_path: str = "",
    ) -> TrashEntry:
        """Record a deleted item. The caller has already removed it from the live
        store and moved any material to ``material_path``."""
        if kind not in KINDS:
            raise ValueError(f"unknown trash kind: {kind!r}")
        entry = TrashEntry(
            id=entry_id or self.new_id(),
            kind=kind,
            name=name,
            deleted_at=self._now(),
            payload=payload,
            material_path=material_path,
        )
        with self._lock:
            self.entries[entry.id] = entry
            self._save()
        logger.info("Trashed %s: %s", entry.label, name)
        return entry

    def pop(self, entry_id: str) -> TrashEntry | None:
        """Take an entry OUT of the trash (restore/permanent-delete). Saving
        before the caller acts would be wrong for a restore that then fails, so
        callers that can fail must put it back — see TrashService.restore."""
        with self._lock:
            entry = self.entries.pop(entry_id, None)
            if entry is not None:
                self._save()
            return entry

    def put_back(self, entry: TrashEntry) -> None:
        """Re-file an entry a caller popped but could not complete, so a failed
        restore leaves the record still recoverable instead of destroyed."""
        with self._lock:
            self.entries[entry.id] = entry
            self._save()

    def find(self, kind: str, name: str) -> TrashEntry | None:
        """The trashed entry of this kind under this name, or None.

        Restoring one half of a relationship has to be able to see whether the
        OTHER half is still in the trash — otherwise it writes only against the
        live store and silently drops the edge (a bookmark restored before its
        pool lost its membership for good). Names are unique per kind in every
        live store, and restore refuses a name that is already taken, so at most
        one entry can answer here; the most recently deleted wins if a legacy
        trash.json somehow holds two.
        """
        with self._lock:
            matches = [
                e
                for e in self.entries.values()
                if e.kind == kind and e.name == name
            ]
        if not matches:
            return None
        return max(matches, key=lambda e: e.deleted_at)

    def update_entry(self, entry_id: str, mutate) -> bool:
        """Amend a still-trashed entry's payload in place, atomically.

        Used when restoring one record has to record a relationship onto another
        record that is still in the trash, so the edge survives until that one is
        restored too. Held under the same lock and persisted the same way as any
        other mutation — a parked edge is no less durable than the entry itself.
        """
        with self._lock:
            entry = self.entries.get(entry_id)
            if entry is None:
                return False
            mutate(entry)
            self._save()
            return True

    def clear(self) -> list[TrashEntry]:
        """Empty the trash, returning what was in it so the caller can destroy
        the on-disk material each entry owns. Used by "empty trash" and by the
        panic wipe, which must leave nothing recoverable behind."""
        with self._lock:
            entries = list(self.entries.values())
            self.entries.clear()
            self._save()
        if entries:
            logger.info("Emptied trash (%d entries)", len(entries))
        return entries

    # --- reads ---

    def get(self, entry_id: str) -> TrashEntry | None:
        with self._lock:
            return self.entries.get(entry_id)

    def list(self, kind: str | None = None) -> list[TrashEntry]:
        """Trashed records, most recently deleted first."""
        with self._lock:
            items = [
                e for e in self.entries.values() if kind is None or e.kind == kind
            ]
        return sorted(items, key=lambda e: e.deleted_at, reverse=True)

    def by_urgency(
        self,
        kind: str | None = None,
        retention_days: int = RETENTION_DAYS,
    ) -> list[TrashEntry]:
        """Trashed records, nearest destruction FIRST — READ ONLY.

        The same entries :meth:`list` returns, in the other useful order.
        ``list`` is deliberately left alone: its recency order is a published
        contract (``GET /trash`` serves it, and ``test_list_is_newest_first``
        pins it), and with a constant ``RETENTION_DAYS`` recency-DESC *is*
        time-remaining-DESC — so the entry nearest destruction is last there
        for every possible data shape, not just for some. A reader that needs
        to act on the clock needs the opposite order, and asking for it must
        not silently re-order the REST lane.

        Built the way :meth:`expiring_within` is, and sorted on the same key
        (``expires_at``), so "most urgent first" means the same thing to the
        nav rail's count and to the page it sends the operator to. It removes
        nothing, writes no trash.json and never touches ``deleted_at`` — an
        entry that is merely LOOKED AT must not age.

        Ties (identical ``deleted_at``) fall back to recency-then-name so the
        order is total and a repaint cannot shuffle rows under the pointer.
        """
        with self._lock:
            items = [
                e for e in self.entries.values() if kind is None or e.kind == kind
            ]
        return sorted(
            items, key=lambda e: (e.expires_at(retention_days), e.name, e.id)
        )

    def names(self, kind: str) -> list[str]:
        return [e.name for e in self.list(kind)]

    def expired(self, retention_days: int = RETENTION_DAYS) -> list[TrashEntry]:
        """Entries past the retention window, without removing them."""
        cutoff = self._now()
        with self._lock:
            return [
                e
                for e in self.entries.values()
                if e.expires_at(retention_days) <= cutoff
            ]

    def expiring_within(
        self,
        days: int = EXPIRY_WARNING_DAYS,
        retention_days: int = RETENTION_DAYS,
    ) -> list[TrashEntry]:
        """Entries whose destruction falls inside the next ``days`` — READ ONLY.

        The forward-looking sibling of :meth:`expired`, and deliberately built
        the same way: it reads ``self.entries`` under the lock and returns
        objects. It removes nothing, destroys no material, writes no
        trash.json, and never touches ``deleted_at`` — an entry that is merely
        LOOKED AT must not age, or the act of warning about the clock would
        move it.

        ALREADY-EXPIRED ENTRIES COUNT. An entry past the window is not yet
        gone (nothing destroys it until the next app start), and it is the most
        urgent thing the trash can hold — excluding it would leave the rail
        silent for exactly the entry that is about to be destroyed on the very
        next launch.

        Most urgent first, so the caller can report the nearest deadline
        without re-sorting.
        """
        horizon = self._now() + days * 86400
        with self._lock:
            due = [
                e
                for e in self.entries.values()
                if e.expires_at(retention_days) <= horizon
            ]
        return sorted(due, key=lambda e: e.expires_at(retention_days))
