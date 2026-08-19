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

``set_profile_manager`` is here for the two stores whose records are REFERENCED
by profiles (proxies, bookmark pools). Deleting one clears the dangling
reference from every profile that used it — a lingering name stranded the
profile page and made a profile launch with an empty toolbar — and restoring it
must put those references back, so the store needs to record who referenced it.
It stays optional: a store with no profile manager simply records no references.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..services.trash.store import TrashStore


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
