import json
import pathlib

from ...core.config import BOOKMARKS_FILE
from ...core.logging import get_logger
from ...models.bookmark import Bookmark, Pool
from ...utils.atomic import atomic_write_json
from ...utils.store_guard import StoreGuardMixin

logger = get_logger("bookmark.store")

DEFAULT_BOOKMARKS = {
    "cookie-viewer": "https://httpbingo.org/cookies",
    "cookie-store": "chrome://settings/cookies",
    "browserleaks": "https://browserleaks.com/",
    "pixelscan": "https://pixelscan.net/",
    "iphey": "https://iphey.com/",
    "browserscan": "https://browserscan.net/",
}


class BookmarkStore(StoreGuardMixin):
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
        if name not in self.bookmarks:
            return False
        del self.bookmarks[name]
        for pool in self.pools.values():
            if name in pool.bookmark_names:
                pool.bookmark_names = [n for n in pool.bookmark_names if n != name]
        self._save()
        logger.info("Deleted bookmark: %s", name)
        return True

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
        if name not in self.pools:
            return False
        del self.pools[name]
        self._save()
        logger.info("Deleted pool: %s", name)
        return True

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
