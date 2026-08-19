import json
import pathlib

from ...core.config import BOOKMARKS_FILE
from ...core.logging import get_logger
from ...models.bookmark import Bookmark, Pool
from ...utils.atomic import atomic_write_json
from ...utils.store_guard import StoreGuardMixin
from ...utils.trashable import TrashableMixin

logger = get_logger("bookmark.store")

DEFAULT_BOOKMARKS = {
    "cookie-viewer": "https://httpbingo.org/cookies",
    "cookie-store": "chrome://settings/cookies",
    "browserleaks": "https://browserleaks.com/",
    "pixelscan": "https://pixelscan.net/",
    "iphey": "https://iphey.com/",
    "browserscan": "https://browserscan.net/",
}


class BookmarkStore(StoreGuardMixin, TrashableMixin):
    _guard_logger = logger
    _guard_noun_plural = "bookmarks"
    _guard_noun_singular = "bookmark"

    def __init__(self, path: str = BOOKMARKS_FILE) -> None:
        self._path = path
        self.bookmarks: dict[str, Bookmark] = {}
        self.pools: dict[str, Pool] = {}
        self._save_blocked = False
        self._load()

    def _load(self) -> None:
        if not pathlib.Path(self._path).exists():
            self._seed_defaults()
            return
        try:
            with pathlib.Path(self._path).open(encoding="utf-8") as f:
                data = json.load(f)
            skipped = 0
            # One malformed record must not abort the whole load — the next save
            # would overwrite bookmarks.json with only what parsed (or nothing),
            # silently losing every bookmark + pool after it.
            for name, b in data.get("bookmarks", {}).items():
                try:
                    self.bookmarks[name] = Bookmark(
                        name=b.get("name", name), url=b["url"]
                    )
                except Exception:
                    skipped += 1
                    logger.exception("Skipping malformed bookmark %r", name)
            for name, p in data.get("pools", {}).items():
                try:
                    self.pools[name] = Pool(
                        name=p.get("name", name),
                        bookmark_names=p.get("bookmark_names", []),
                    )
                except Exception:
                    skipped += 1
                    logger.exception("Skipping malformed pool %r", name)
            if skipped:
                logger.warning(
                    "Skipped %d malformed bookmark/pool record(s)", skipped
                )
            # A store created before a default was added never had that default
            # seeded. Add any missing defaults ONCE (guarded by a marker) so an
            # existing user gets the anti-detect testers too, without resurrecting
            # a default the user has since deleted.
            if not data.get("defaults_seeded"):
                added = False
                for name, url in DEFAULT_BOOKMARKS.items():
                    if name not in self.bookmarks:
                        self.bookmarks[name] = Bookmark(name=name, url=url)
                        added = True
                self._defaults_seeded = True
                if added:
                    self._save()
            logger.info(
                "Loaded %d bookmarks, %d pools",
                len(self.bookmarks),
                len(self.pools),
            )
        except Exception as e:
            logger.exception("Error loading bookmarks: %s", e)
            self._quarantine_bookmarks_file()

    def _store_path(self) -> str:
        return self._path

    def _quarantine_bookmarks_file(self) -> None:
        # An unreadable bookmarks.json still holds the user's bookmarks + pools;
        # move it aside so the next _save() can't overwrite it with an empty (or
        # defaults-only) store.
        self._quarantine_store_file()

    def _seed_defaults(self) -> None:
        for name, url in DEFAULT_BOOKMARKS.items():
            self.bookmarks[name] = Bookmark(name=name, url=url)
        self._defaults_seeded = True
        self._save()

    def _save(self) -> None:
        if self._save_is_blocked():
            return
        try:
            # Atomic (temp + os.replace) so a crash mid-save can't leave a
            # half-written file that silently overwrites every bookmark on the
            # next load.
            atomic_write_json(
                self._path,
                {
                    "defaults_seeded": getattr(self, "_defaults_seeded", True),
                    "bookmarks": {
                        n: b.to_dict() for n, b in self.bookmarks.items()
                    },
                    "pools": {n: p.to_dict() for n, p in self.pools.items()},
                },
            )
        except Exception as e:
            logger.exception("Error saving bookmarks: %s", e)

    # --- bookmarks ---

    def list_bookmarks(self) -> list[Bookmark]:
        return list(self.bookmarks.values())

    def bookmark_names(self) -> list[str]:
        return list(self.bookmarks.keys())

    def get(self, name: str) -> Bookmark | None:
        return self.bookmarks.get(name)

    def add(self, name: str, url: str) -> bool:
        if not name or name in self.bookmarks:
            return False
        self.bookmarks[name] = Bookmark(name=name, url=url)
        self._save()
        logger.info("Added bookmark: %s", name)
        return True

    def update(self, original_name: str, new_name: str, new_url: str) -> bool:
        if original_name not in self.bookmarks:
            return False
        if new_name != original_name and new_name in self.bookmarks:
            return False
        del self.bookmarks[original_name]
        self.bookmarks[new_name] = Bookmark(name=new_name, url=new_url)
        if new_name != original_name:
            for pool in self.pools.values():
                pool.bookmark_names = [
                    new_name if n == original_name else n
                    for n in pool.bookmark_names
                ]
        self._save()
        logger.info("Updated bookmark: %s -> %s", original_name, new_name)
        return True

    def _pool_ordering(self, pool_name: str, base: list[str]) -> list[str]:
        """The pool's member ordering INCLUDING members already in the trash.

        Capturing the ordering off the LIVE pool is not enough: a sibling that
        was trashed earlier has already been stripped from it, so a member
        deleted second records an ordering its predecessor is missing from, and
        comes back in front of it. (That is root cause #1 — the recorded
        descriptor was relative to a list that no longer existed.)

        A trashed peer is not gone, and it still knows where it sat, so fold each
        one back in using its OWN recorded ordering and the same anchor rule that
        restore uses. Oldest deletion first, because the earliest snapshot is the
        one that still remembers the most peers. The result is the pool as it
        stood before any of them were deleted, which is the only ordering a
        restore can be measured against.
        """
        ordering = list(base)
        trashed = sorted(self._trash().list("bookmark"), key=lambda e: e.deleted_at)
        for entry in trashed:
            payload = entry.payload or {}
            if pool_name not in (payload.get("pools") or []):
                continue
            self._insert_member(
                ordering,
                entry.name,
                (payload.get("pool_orders") or {}).get(pool_name),
            )
        return ordering

    def delete(self, name: str) -> bool:
        """Move a bookmark to the trash. It leaves the bookmark list and every
        pool that held it, and comes back to both on restore."""
        if name not in self.bookmarks:
            return False
        bookmark = self.bookmarks.pop(name)
        # Which pools held it AND WHERE, so restore can put the membership back
        # exactly as it was — the pools themselves are edited here, so the
        # relationship is only recoverable if we record it. A pool's order is the
        # order its bookmarks appear on the profile's toolbar, so restoring a
        # member to the END of the list would hand back a visibly different
        # toolbar and would not be the same record.
        #
        # Record the pool's ORDERING, not this member's index. An absolute
        # integer is not a stable descriptor of position: it is applied later to
        # a list that may still be missing an arbitrary subset of peers, and no
        # amount of clamping makes it stable — see _insert_member.
        #
        # The ordering is taken through _pool_ordering, not off the live pool,
        # because a peer trashed EARLIER has already been stripped from it. Every
        # member's recorded ordering therefore describes the same pre-delete
        # pool, whichever order the members were deleted in.
        orders = {
            p.name: self._pool_ordering(p.name, p.bookmark_names)
            for p in self.pools.values()
            if name in p.bookmark_names
        }
        pools = list(orders)
        for pool in self.pools.values():
            if name in pool.bookmark_names:
                pool.bookmark_names = [n for n in pool.bookmark_names if n != name]
        self._save()
        self._trash().add(
            "bookmark",
            name,
            {
                "bookmark": bookmark.to_dict(),
                "pools": pools,
                "pool_orders": orders,
            },
        )
        logger.info("Moved bookmark to trash: %s", name)
        return True

    @staticmethod
    def _insert_member(
        members: list[str], name: str, order: list[str] | None = None
    ) -> None:
        """Put a member back where it belongs RELATIVE to its surviving peers.

        A pool's order IS the order the bookmarks appear on the profile's
        toolbar, so appending a restored member to the end hands back a visibly
        different toolbar and is not "the same working state".

        The descriptor is the pool's member ORDERING at delete time, never an
        absolute index. An integer slot is only meaningful when every peer that
        preceded it is present, and neither end of that holds here: the index was
        recorded against a pool a sibling delete had already shifted, and it was
        applied to a list still missing an arbitrary subset of peers. Clamping
        only converted "wrong slot" into "at the end" — it could not make an
        absolute index stable, because it isn't.

        So place the name AFTER THE LAST MEMBER THAT PRECEDED IT and is currently
        present, falling back to the front, and to the end when the ordering says
        nothing about it. That rule is stable under any number of absent peers
        and any restore order, and it collapses both failure modes into one:
        peers that are still trashed simply do not anchor anything, and each one
        lands correctly as it comes back.
        """
        if name in members:
            return
        if not order or name not in order:
            members.append(name)
            return
        preceded_by = set(order[: order.index(name)])
        anchor = -1
        for i, existing in enumerate(members):
            if existing in preceded_by:
                anchor = i
        members.insert(anchor + 1, name)

    def _record_membership(
        self, bookmark_name: str, pool_name: str, order: list[str] | None = None
    ) -> None:
        """File the bookmark<->pool edge wherever each half currently lives.

        The two halves of a membership can be in different places: one live, one
        still in the trash. Each restore used to write only against the LIVE
        store — restore_bookmark skipped a pool it could not see, and
        restore_pool filtered out a member it could not see — so whichever half
        came back FIRST silently dropped the relationship, and by then the trash
        was empty and there was nothing left to undo from. The safe order even
        inverted with the deletion order, so there was no rule the trash page
        could have taught the operator.

        This method is the ONE place that resolves the edge, the same way the
        pool/proxy reference fix made one method own capture-then-clear:

        * both halves live -> record it on the live pool.
        * pool live, bookmark still trashed -> park the edge on the trashed
          BOOKMARK's payload, so restoring it later rejoins the pool.
        * pool still trashed -> park the edge on the trashed POOL's snapshot, so
          restoring it later brings the bookmark back as a member.
        * neither -> the counterpart was permanently deleted or has expired;
          drop it, exactly as add_pool/update_pool refuse to hold a name that
          resolves to nothing.
        """
        pool = self.pools.get(pool_name)
        if pool is not None:
            if bookmark_name in self.bookmarks:
                self._insert_member(pool.bookmark_names, bookmark_name, order)
                self._save()
                return
            # The pool is back, the bookmark is still in the trash. Remember the
            # edge ON the bookmark so its own restore completes it — dropping it
            # here is what lost the membership for good. The ORDERING rides along
            # so the later restore still lands the member among its peers, and it
            # stays correct however many of those peers are still trashed.
            entry = self._trash().find("bookmark", bookmark_name)
            if entry is None:
                return

            def _add_pool(e, pool_name=pool_name, order=order) -> None:
                pools = e.payload.setdefault("pools", [])
                if pool_name not in pools:
                    pools.append(pool_name)
                if order:
                    e.payload.setdefault("pool_orders", {})[pool_name] = list(order)

            self._trash().update_entry(entry.id, _add_pool)
            return
        # The pool itself is still in the trash. Write the member into its
        # snapshot, so restoring the pool brings the bookmark back with it.
        entry = self._trash().find("pool", pool_name)
        if entry is None:
            return

        def _add_member(e, bookmark_name=bookmark_name, order=order) -> None:
            data = e.payload.setdefault("pool", {})
            members = data.setdefault("bookmark_names", [])
            self._insert_member(members, bookmark_name, order)

        self._trash().update_entry(entry.id, _add_member)

    def restore_bookmark(self, entry) -> tuple[bool, str]:
        """Put a trashed bookmark back, including its pool memberships."""
        name = entry.name
        if name in self.bookmarks:
            return False, (
                f"A bookmark named '{name}' already exists. Rename or delete it, "
                "then restore again."
            )
        data = entry.payload.get("bookmark") or {}
        self.bookmarks[name] = Bookmark(
            name=data.get("name", name), url=data.get("url", "")
        )
        # Resolve each membership against the trash as well as the live store: a
        # pool that has not been restored YET must not silently lose this member.
        orders = entry.payload.get("pool_orders") or {}
        for pool_name in entry.payload.get("pools") or []:
            self._record_membership(name, pool_name, orders.get(pool_name))
        self._save()
        logger.info("Restored bookmark from trash: %s", name)
        return True, ""

    # --- pools ---

    def list_pools(self) -> list[Pool]:
        return list(self.pools.values())

    def pool_names(self) -> list[str]:
        return list(self.pools.keys())

    def get_pool(self, name: str) -> Pool | None:
        return self.pools.get(name)

    def add_pool(self, name: str, bookmark_names: list[str]) -> bool:
        if not name or name in self.pools:
            return False
        members = [n for n in bookmark_names if n in self.bookmarks]
        self.pools[name] = Pool(name=name, bookmark_names=members)
        self._save()
        logger.info("Added pool: %s (%d bookmarks)", name, len(members))
        return True

    def update_pool(
        self, original_name: str, new_name: str, bookmark_names: list[str]
    ) -> bool:
        if original_name not in self.pools:
            return False
        if new_name != original_name and new_name in self.pools:
            return False
        del self.pools[original_name]
        members = [n for n in bookmark_names if n in self.bookmarks]
        self.pools[new_name] = Pool(name=new_name, bookmark_names=members)
        self._save()
        logger.info("Updated pool: %s -> %s", original_name, new_name)
        return True

    def delete_pool(self, name: str) -> bool:
        """Move a pool to the trash. The bookmarks themselves are untouched;
        restore brings the pool back with the same membership list.

        This method owns the WHOLE operation, including dropping the pool from
        every profile that referenced it. That is deliberate: the reference has
        to be RECORDED before it is cleared, and when the two halves lived in the
        caller the UI did them in the opposite order (clear_bookmark_pool
        first), so the store recorded no referencing profiles at all and a
        restore silently returned a pool nothing pointed at. Owning both here
        makes that ordering impossible to get wrong from a new lane.
        """
        if name not in self.pools:
            return False
        pool = self.pools.pop(name)
        # Snapshot the ordering INCLUDING members already in the trash, the same
        # way delete() does. The live list has already lost any member trashed
        # earlier, and a member trashed LATER inherits its ordering from this
        # snapshot — so a snapshot missing a peer teaches every later member a
        # pool that peer was never in, and it comes back on the wrong side of it.
        # Reconstructing here is what makes the parked pool snapshot and the
        # parked bookmark edges agree by construction rather than by coincidence.
        #
        # A trashed member listed here is NOT resurrected: restore_pool filters
        # the snapshot to live bookmarks, and a member that was permanently
        # deleted resolves to nothing in either store and stays dropped.
        snapshot = self._pool_ordering(name, pool.bookmark_names)
        self._save()
        # RECORD the referencing profiles first, then clear them — never the
        # reverse. A deleted pool left lingering as a name made the profile
        # launch with an empty toolbar (audit5 #4), so the reference must go;
        # recording it here is the only thing that lets restore put it back.
        refs = []
        pm = self._profile_manager
        if pm is not None:
            refs = [p.name for p in pm.list_profiles() if p.bookmark_pool == name]
            pm.clear_bookmark_pool(name)
        payload = pool.to_dict()
        payload["bookmark_names"] = snapshot
        self._trash().add(
            "pool",
            name,
            {"pool": payload, "profiles": refs},
        )
        logger.info("Moved pool to trash: %s", name)
        return True

    def restore_pool(self, entry) -> tuple[bool, str]:
        """Put a trashed pool back, re-pointing the profiles that used it."""
        name = entry.name
        if name in self.pools:
            return False, (
                f"A pool named '{name}' already exists. Rename or delete it, "
                "then restore again."
            )
        data = entry.payload.get("pool") or {}
        # Skip members that no longer exist — add_pool/update_pool filter the
        # same way, so a restored pool can't hold a name nothing resolves.
        snapshot = data.get("bookmark_names") or []
        members = [n for n in snapshot if n in self.bookmarks]
        self.pools[name] = Pool(name=data.get("name", name), bookmark_names=members)
        self._save()
        # A member that is merely STILL IN THE TRASH is not gone — filtering it
        # away here is what silently lost the membership when the pool was
        # restored first. Park the edge on that bookmark's own entry so its
        # restore rejoins this pool, among the peers it sat between in THIS
        # snapshot; a member that is neither live nor trashed really has been
        # permanently deleted, and stays dropped.
        #
        # The snapshot is passed WHOLE, as the ordering, rather than as each
        # member's index in it. Several members can be absent at once, and an
        # index into this list is only meaningful once every earlier one is back
        # — which is exactly the assumption that put a member at the wrong slot
        # when two had been deleted. The ordering re-anchors each member as it
        # returns, in any order.
        for member in snapshot:
            if member not in self.bookmarks:
                self._record_membership(member, name, snapshot)
        pm = self._profile_manager
        if pm is not None:
            for profile_name in entry.payload.get("profiles") or []:
                profile = pm.profiles.get(profile_name)
                if profile is not None and profile.bookmark_pool is None:
                    profile.bookmark_pool = name
            pm.save_profiles()
        logger.info("Restored pool from trash: %s", name)
        return True, ""

    # --- resolution ---

    def resolve_selection(
        self, pool_name: str | None, bookmark_names: list[str] | None
    ) -> list[Bookmark]:
        """Resolve a profile's bookmark choice to actual Bookmark objects:
        the pool's members first, then any individually-checked bookmarks,
        de-duplicated and skipping names that no longer exist.
        """
        ordered: list[str] = []
        pool_known = bool(pool_name) and pool_name in self.pools
        if pool_known:
            ordered.extend(self.pools[pool_name].bookmark_names)
        elif pool_name:
            # A truthy but unknown pool (the pool was deleted while a profile
            # still referenced it) is treated as no pool — otherwise it fell
            # through to [] and the profile opened with an EMPTY toolbar instead
            # of the defaults, unlike how an unknown individual bookmark name is
            # gracefully skipped (audit5 #4).
            logger.info("Profile references unknown bookmark pool %r; using defaults", pool_name)
        if bookmark_names:
            ordered.extend(bookmark_names)
        # An unconfigured profile (no usable pool, and bookmark_names is None
        # because it was never chosen) gets the stock default bookmarks so it
        # doesn't open with an empty bar. A profile that was configured to an
        # empty set (bookmark_names == []) is honored as empty — the user cleared
        # them on purpose and they must not come back. Any explicit list is used
        # as-is.
        if not ordered and not pool_known and bookmark_names is None:
            ordered = [n for n in DEFAULT_BOOKMARKS if n in self.bookmarks]
        seen: set[str] = set()
        result: list[Bookmark] = []
        for n in ordered:
            if n in seen or n not in self.bookmarks:
                continue
            seen.add(n)
            result.append(self.bookmarks[n])
        return result
