"""One place that knows how to restore and how to genuinely destroy.

TrashStore holds the deleted records; this module holds the per-kind knowledge
of what putting one back means and what "delete permanently" has to destroy.
Keeping both here is what makes the two lanes agree: the UI trash page and the
REST /trash endpoints call the SAME service, so a restore through the API and a
restore through the window cannot drift apart.

The distinction the interface must state rather than hide lives here too:
trashing a proxy, an SSH host or a certificate does NOT remove its secret
material — the credentials sit in trash.json (0600, inside PERSONA_HOME, the
same protection the live store gave them) and the certificate's .p12 is still on
disk. ``destroy_entry`` is where that material actually goes.
"""

from __future__ import annotations

from ...core.logging import get_logger
from .store import (
    KIND_BOOKMARK,
    KIND_CERTIFICATE,
    KIND_POOL,
    KIND_PROFILE,
    KIND_PROXY,
    KIND_SSH_HOST,
    TrashEntry,
    TrashStore,
)

logger = get_logger("trash.service")


class TrashService:
    """Restore / permanently delete, across every record kind.

    Built with whichever stores the caller has; a missing store simply means
    that kind can't be restored here (the API and the UI both pass all of them).
    """

    def __init__(
        self,
        trash: TrashStore,
        *,
        profile_manager=None,
        bookmark_store=None,
        proxy_store=None,
        ssh_host_store=None,
        cert_store=None,
    ) -> None:
        self.trash = trash
        self.pm = profile_manager
        self.bstore = bookmark_store
        self.pstore = proxy_store
        self.ssh_store = ssh_host_store
        self.cert_store = cert_store

    # --- reads ---

    def list(self, kind: str | None = None) -> list[TrashEntry]:
        return self.trash.list(kind)

    def get(self, entry_id: str) -> TrashEntry | None:
        return self.trash.get(entry_id)

    # --- restore ---

    def restore(self, entry_id: str) -> tuple[bool, str]:
        """Put a trashed record back exactly as it was.

        Refused — with the reason — when the original name is taken. Restoring
        under a different name is deliberately NOT offered: for a profile it
        would return the cookie jar under a different fingerprint, and for every
        other kind the name IS the reference profiles hold.
        """
        entry = self.trash.get(entry_id)
        if entry is None:
            return False, "That item is no longer in the trash."
        store = self._store_for(entry.kind)
        if store is None:
            return False, f"Cannot restore a {entry.label} here."

        # Pop first so a restore can't race a second restore of the same entry;
        # put it back if the restore is refused, so a refusal costs nothing.
        popped = self.trash.pop(entry_id)
        if popped is None:
            return False, "That item is no longer in the trash."
        try:
            ok, msg = self._restore_entry(popped)
        except Exception as e:
            logger.exception("Restore of %s %r failed", popped.kind, popped.name)
            self.trash.put_back(popped)
            return False, f"Could not restore the {popped.label}: {e}"
        if not ok:
            self.trash.put_back(popped)
        return ok, msg

    def _restore_entry(self, entry: TrashEntry) -> tuple[bool, str]:
        if entry.kind == KIND_PROFILE:
            return self.pm.restore_profile(entry)
        if entry.kind == KIND_BOOKMARK:
            return self.bstore.restore_bookmark(entry)
        if entry.kind == KIND_POOL:
            return self.bstore.restore_pool(entry)
        if entry.kind == KIND_PROXY:
            return self.pstore.restore_proxy(entry)
        if entry.kind == KIND_SSH_HOST:
            return self.ssh_store.restore_host(entry)
        if entry.kind == KIND_CERTIFICATE:
            return self.cert_store.restore_certificate(entry)
        return False, f"Cannot restore a {entry.label}."

    def _store_for(self, kind: str):
        return {
            KIND_PROFILE: self.pm,
            KIND_BOOKMARK: self.bstore,
            KIND_POOL: self.bstore,
            KIND_PROXY: self.pstore,
            KIND_SSH_HOST: self.ssh_store,
            KIND_CERTIFICATE: self.cert_store,
        }.get(kind)

    # --- permanent deletion ---

    def delete_permanently(self, entry_id: str) -> tuple[bool, str]:
        """Remove one entry and its on-disk material for good, immediately.
        This one really cannot be undone — that is the point of it existing
        beside the recoverable delete."""
        entry = self.trash.pop(entry_id)
        if entry is None:
            return False, "That item is no longer in the trash."
        destroy_entry(entry, self.pm, self.cert_store)
        logger.info("Permanently deleted %s: %s", entry.label, entry.name)
        return True, ""

    def empty(self) -> int:
        """Empty the whole trash, destroying every entry's material. Returns how
        many entries were destroyed."""
        entries = self.trash.clear()
        for entry in entries:
            destroy_entry(entry, self.pm, self.cert_store)
        return len(entries)

    def purge_expired(self, retention_days: int | None = None) -> int:
        """Drop entries past the retention window and destroy their material.
        Called on app start, so an entry that has been in the trash longer than
        the window is gone after the app next starts without the operator doing
        anything."""
        from .store import RETENTION_DAYS

        days = RETENTION_DAYS if retention_days is None else retention_days
        expired = self.trash.expired(days)
        for entry in expired:
            popped = self.trash.pop(entry.id)
            if popped is not None:
                destroy_entry(popped, self.pm, self.cert_store)
        if expired:
            logger.info(
                "Purged %d trash entry/entries past the %d-day retention window",
                len(expired), days,
            )
        return len(expired)


def destroy_entry(entry: TrashEntry, profile_manager=None, cert_store=None) -> None:
    """Destroy whatever on-disk material a trash entry owns.

    A profile's parked data dir is rmtree'd through the manager's containment
    check. A certificate's .p12 holds private-key material, and is deleted
    through CertStore._delete_owned_p12, which refuses to delete a file outside
    persona's own store dir — a legacy record may point at the operator's
    ORIGINAL file, which is never persona's to delete. That restraint survives
    the trash unchanged. Every other kind is pure JSON and needs nothing beyond
    being dropped from trash.json.
    """
    if not entry.material_path:
        return
    if entry.kind == KIND_PROFILE:
        pm = profile_manager
        if pm is None:
            from ..profile.manager import ProfileManager

            pm = ProfileManager
        try:
            pm.destroy_trashed_material(entry.material_path)
        except Exception:
            logger.exception(
                "Could not delete trashed profile data at %s", entry.material_path
            )
        return
    if entry.kind == KIND_CERTIFICATE:
        store = cert_store
        if store is None:
            from ..cert.store import CertStore

            store = CertStore()
        try:
            store._delete_owned_p12(entry.material_path)
        except Exception:
            logger.exception(
                "Could not delete trashed certificate file %s", entry.material_path
            )
