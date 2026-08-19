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
        pools = [
            p.name for p in self.pools.values() if name in p.bookmark_names
        ]
        positions = {
            p.name: p.bookmark_names.index(name)
            for p in self.pools.values()
            if name in p.bookmark_names
        }
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
                "pool_positions": positions,
            },
        )
        logger.info("Moved bookmark to trash: %s", name)
        return True

    @staticmethod
    def _insert_member(members: list[str], name: str, position: int | None) -> None:
        """Put a member back at the position it was deleted from.

        A pool's order IS the order the bookmarks appear on the profile's
        toolbar, so appending a restored member to the end hands back a visibly
        different toolbar. The index is clamped because peers may have been
        deleted meanwhile, and a stale index must degrade to "at the end" rather
        than raise.
        """
        if name in members:
            return
        if position is None:
            members.append(name)
            return
        members.insert(max(0, min(int(position), len(members))), name)

    def _record_membership(
        self, bookmark_name: str, pool_name: str, position: int | None = None
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
                self._insert_member(pool.bookmark_names, bookmark_name, position)
                self._save()
                return
            # The pool is back, the bookmark is still in the trash. Remember the
            # edge ON the bookmark so its own restore completes it — dropping it
            # here is what lost the membership for good. The position rides along
            # so the later restore still lands the member where it was.
            entry = self._trash().find("bookmark", bookmark_name)
            if entry is None:
                return

            def _add_pool(e, pool_name=pool_name, position=position) -> None:
                pools = e.payload.setdefault("pools", [])
                if pool_name not in pools:
                    pools.append(pool_name)
                if position is not None:
                    e.payload.setdefault("pool_positions", {})[pool_name] = position

            self._trash().update_entry(entry.id, _add_pool)
            return
        # The pool itself is still in the trash. Write the member into its
        # snapshot, so restoring the pool brings the bookmark back with it.
        entry = self._trash().find("pool", pool_name)
        if entry is None:
            return

        def _add_member(e, bookmark_name=bookmark_name, position=position) -> None:
            data = e.payload.setdefault("pool", {})
            members = data.setdefault("bookmark_names", [])
            self._insert_member(members, bookmark_name, position)

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
        positions = entry.payload.get("pool_positions") or {}
        for pool_name in entry.payload.get("pools") or []:
            self._record_membership(name, pool_name, positions.get(pool_name))
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
        self._trash().add(
            "pool",
            name,
            {"pool": pool.to_dict(), "profiles": refs},
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
        # restore rejoins this pool, at the position it holds in this snapshot;
        # a member that is neither live nor trashed really has been permanently
        # deleted, and stays dropped.
        for position, member in enumerate(snapshot):
            if member not in self.bookmarks:
                self._record_membership(member, name, position)
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
