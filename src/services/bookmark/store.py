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
        # Which pools held it, so restore can put the membership back — the pools
        # themselves are edited here, so the relationship is only recoverable if
        # we record it.
        pools = [
            p.name for p in self.pools.values() if name in p.bookmark_names
        ]
        for pool in self.pools.values():
            if name in pool.bookmark_names:
                pool.bookmark_names = [n for n in pool.bookmark_names if n != name]
        self._save()
        self._trash().add(
            "bookmark",
            name,
            {"bookmark": bookmark.to_dict(), "pools": pools},
        )
        logger.info("Moved bookmark to trash: %s", name)
        return True

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
        for pool_name in entry.payload.get("pools") or []:
            pool = self.pools.get(pool_name)
            if pool is not None and name not in pool.bookmark_names:
                pool.bookmark_names.append(name)
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
        restore brings the pool back with the same membership list."""
        if name not in self.pools:
            return False
        pool = self.pools.pop(name)
        self._save()
        # Which profiles referenced this pool, so restore can re-point them. The
        # caller clears the dangling reference (a lingering pool name made the
        # profile launch with an empty toolbar), so it is only recoverable if we
        # record it here first.
        refs = []
        pm = self._profile_manager
        if pm is not None:
            refs = [p.name for p in pm.list_profiles() if p.bookmark_pool == name]
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
        members = [
            n for n in (data.get("bookmark_names") or []) if n in self.bookmarks
        ]
        self.pools[name] = Pool(name=data.get("name", name), bookmark_names=members)
        self._save()
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
