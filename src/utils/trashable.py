"""Shared wiring for the stores that can trash a record.

Every delete in persona routes through the trash by DEFAULT — that is the point
of PS-10 — so the trash must reach a store by construction, not by each store
remembering to look for it. A store mixes this in and gets:

* ``set_trash(store)`` — the app injects the ONE shared TrashStore (via the
  Container) so every lane, UI and REST, files into the same trash.
* ``_trash()`` — a lazily-built TrashStore when nothing was injected, so a store
  constructed directly (a test, a helper, a future entry point) still trashes
  instead of silently falling back to destroying the record. A store that ends
  up with its own instance still writes the same guarded trash.json.

``restore_kwargs`` is the shared half of the RESTORE door: every store's
``restore_*`` derives its constructor kwargs from the model's own
``dataclasses.fields`` rather than a hand-written key list, then layers its own
carve-outs on top.

``set_profile_manager`` is here for the two stores whose records are REFERENCED
by profiles (proxies, bookmark pools). Deleting one clears the dangling
reference from every profile that used it — a lingering name stranded the
profile page and made a profile launch with an empty toolbar — and restoring it
must put those references back, so the store needs to record who referenced it.
It stays optional: a store with no profile manager simply records no references.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..services.trash.store import TrashStore


def restore_kwargs(
    model,
    payload: dict,
    name: str,
    *,
    defaults: dict | None = None,
) -> dict:
    """Constructor kwargs for a trashed record, derived by REFLECTION.

    ``TrashEntry.payload`` is documented as the record's own ``to_dict()``
    verbatim, and every one of those is ``asdict(self)`` — so the SAVE half of
    the trash already enumerates a record's fields by reflection. Each restore
    used to enumerate them by hand instead, which made a field added to a
    dataclass ride into the trash payload for free, sit correctly on disk in
    trash.json, and then get silently DROPPED on the way back out unless
    someone also edited the literal in the restore method. No exception, no
    log: the operator is told the record was restored while part of it stays
    behind in the trash entry — a partial restore reported as a complete one.
    (``ProfileManager._load_profiles_locked`` and ``transfer.py``'s import both
    already build from ``dataclasses.fields`` for the same reason; the profile
    trash restore is ``Profile(**entry.payload)`` and was the precedent.)

    Deriving the keys means a new field round-trips with no edit here. Unknown
    keys in the payload are ignored, so an entry written by an OLDER build — or
    one carrying a key this build has since removed — still restores instead of
    raising TypeError at the constructor.

    ``name`` falls back to the ENTRY's name, never the payload's: a payload
    missing "name" must still restore under the key the operator sees. This is
    the one carve-out shared by all five stores; the rest (the port coercion,
    the certificate's unparked p12_path, the pool's member filter) are real
    logic and stay explicit in their own store, ON TOP of this dict.

    ``defaults`` supplies a value for a field the dataclass gives no default of
    its own (SSHHost.host, Proxy.url, Bookmark.url). The hand-written form
    reached those through ``.get(key, "")``, so a payload missing one restored
    with an empty string rather than raising — preserved here rather than
    quietly turned into a skipped record.
    """
    field_names = {f.name for f in dataclasses.fields(model)}
    kwargs = dict(defaults or {})
    kwargs.update({k: v for k, v in payload.items() if k in field_names})
    kwargs["name"] = payload.get("name", name)
    return kwargs


class TrashableMixin:
    """Give a store the shared trash (or its own) and optional profile refs."""

    _trash_store: "TrashStore | None" = None
    _profile_manager = None

    def set_trash(self, trash: "TrashStore") -> None:
        self._trash_store = trash

    def set_profile_manager(self, pm) -> None:
        self._profile_manager = pm

    def _trash(self) -> "TrashStore":
        if self._trash_store is None:
            from ..services.trash.store import TrashStore

            self._trash_store = TrashStore()
        return self._trash_store
