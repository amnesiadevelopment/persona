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
    EXPIRY_WARNING_DAYS,
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

    def by_urgency(self, kind: str | None = None) -> list[TrashEntry]:
        """Trashed records, nearest destruction first — READ ONLY.

        Surfaced here the way :meth:`expiring_within` is, so the UI never
        reaches past the service into the store. :meth:`list`'s recency order
        is untouched: the REST lane and ``_empty_trash``'s count still get
        exactly what they got before.
        """
        return self.trash.by_urgency(kind)

    def expiring_within(self, days: int = EXPIRY_WARNING_DAYS) -> list[TrashEntry]:
        """Entries about to be destroyed, most urgent first — READ ONLY.

        Surfaced here the way :meth:`list` surfaces ``store.list()``, so the UI
        never reaches past the service into the store. Purely a read: it is
        what the nav rail asks on every rebuild, and a question asked on every
        repaint must not be able to change anything.
        """
        return self.trash.expiring_within(days)

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
        destroy_entry(
            entry, self.pm, self.cert_store,
            ssh_store=self.ssh_store, trash=self.trash,
        )
        logger.info("Permanently deleted %s: %s", entry.label, entry.name)
        return True, ""

    def empty(self) -> int:
        """Empty the whole trash, destroying every entry's material. Returns how
        many entries were destroyed."""
        entries = self.trash.clear()
        for entry in entries:
            destroy_entry(
                entry, self.pm, self.cert_store,
                ssh_store=self.ssh_store, trash=self.trash,
            )
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
                destroy_entry(
                    popped, self.pm, self.cert_store,
                    ssh_store=self.ssh_store, trash=self.trash,
                )
        if expired:
            logger.info(
                "Purged %d trash entry/entries past the %d-day retention window",
                len(expired), days,
            )
        return len(expired)


def destroy_entry(
    entry: TrashEntry,
    profile_manager=None,
    cert_store=None,
    *,
    ssh_store=None,
    trash=None,
) -> None:
    """Destroy whatever on-disk material a trash entry owns.

    A profile's parked data dir is rmtree'd through the manager's containment
    check. A certificate's .p12 holds private-key material, and is deleted
    through CertStore._delete_owned_p12, which refuses to delete a file outside
    persona's own store dir — a legacy record may point at the operator's
    ORIGINAL file, which is never persona's to delete. That restraint survives
    the trash unchanged.

    An SSH host owns a second file too, and this docstring used to deny it:
    connecting pins the host key into persona's own ``known_hosts`` (0600 in
    PERSONA_HOME, outside every profile perimeter), and nothing removed it. The
    operator performed the product's irreversible delete gesture and the name of
    the machine they were reaching stayed on disk. That pin is dropped here —
    conditionally; see :func:`_destroy_ssh_host_pin` for the two conditions,
    both of which are about NOT re-arming trust-on-first-use for a host that is
    still saved. Every remaining kind really is pure JSON and needs nothing
    beyond being dropped from trash.json.

    ``ssh_store`` and ``trash`` are keyword-only and default to None because one
    call site — ``ProfileManager._purge_trash_for_wipe`` — structurally has
    neither to give. Without the live store the "is this pin still reachable?"
    question is unanswerable, and the safe answer to an unanswerable safety
    question is to leave the pin standing, which is also exactly the wipe's
    existing behaviour (it deliberately does not destroy the live credential
    stores either).
    """
    # ABOVE the material_path guard, DELIBERATELY. An SSH host is trashed with
    # material_path='' — SSHHostStore.remove calls trash.add(...) with no
    # material_path — so a branch placed below the guard could never execute.
    # This return is what the guard would have done for this kind anyway.
    if entry.kind == KIND_SSH_HOST:
        _destroy_ssh_host_pin(entry, ssh_store, trash)
        return
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


def _destroy_ssh_host_pin(entry: TrashEntry, ssh_store, trash) -> None:
    """Drop the permanently-deleted host's known_hosts pin — when it is safe.

    THE PIN IS SHARED BY HOST:PORT, NOT BY RECORD. known_hosts keys on the
    machine, and two saved records can name the same one. Removing the pin
    because ONE of them was deleted would silently re-arm trust-on-first-use for
    the survivor: the next connection would accept whatever key an untrusted
    SOCKS exit offered, turning `_TOFUPolicy`'s MITM *detection* into MITM
    *acceptance*. So the pin only goes when nothing reachable still points at
    that host:port.

    Two conditions, both refusals:

    1. **No live store, no removal.** The wipe's call site has no SSH store to
       give (ProfileManager._purge_trash_for_wipe), so the "is it shared?"
       question cannot be answered there — and an unanswerable safety question
       is answered by leaving the pin. This also keeps the wipe byte-identical
       to what it did before, which is deliberate: the wipe does not destroy the
       live credential stores either.
    2. **A remaining record reaches it, no removal.** Live records first; and
       DECISION — records still sitting in the TRASH count as blocking too.
       They are restorable (`TrashService.restore` -> `restore_host`), and a
       restored record re-arms TOFU exactly as a live one would, so a pin that a
       restore would need is not ours to drop yet. The conservative direction:
       when that trashed record is itself permanently deleted, this runs again
       and the pin goes then. When no trash store was supplied we can no longer
       see the trash, so we do not remove — same rule as condition 1.

    Best-effort and NON-FATAL, mirroring the per-kind try/except above: a delete
    that raised because the file was locked (Windows) would be worse than the
    residue it failed to clear.
    """
    try:
        payload = entry.payload.get("host") or {}
        host = payload.get("host") or ""
        if not host:
            return
        port = int(payload.get("port", 22) or 22)

        if ssh_store is None:
            logger.debug(
                "Left the pinned SSH host key for %s in place: no live store to "
                "check whether another record still reaches it", entry.name
            )
            return
        if trash is None:
            logger.debug(
                "Left the pinned SSH host key for %s in place: no trash to check "
                "whether a restorable record still reaches it", entry.name
            )
            return

        for other in ssh_store.list():
            if other.host == host and int(other.port or 22) == port:
                logger.info(
                    "Kept the pinned SSH host key for %s:%s — SSH host %r still "
                    "reaches it", host, port, other.name
                )
                return
        for other_entry in trash.list(KIND_SSH_HOST):
            if other_entry.id == entry.id:
                continue
            other = other_entry.payload.get("host") or {}
            if other.get("host") == host and int(other.get("port", 22) or 22) == port:
                logger.info(
                    "Kept the pinned SSH host key for %s:%s — trashed SSH host "
                    "%r could still be restored onto it",
                    host, port, other_entry.name,
                )
                return

        from ..ssh.client import remove_pinned_host_key

        remove_pinned_host_key(host, port)
    except Exception:
        logger.exception(
            "Could not remove the pinned SSH host key for %s", entry.name
        )
