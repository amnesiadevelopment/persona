import json
import pathlib

from ...core.config import BOOKMARKS_FILE
from ...core.logging import get_logger
from ...models.bookmark import Bookmark, Pool

logger = get_logger("bookmark.store")

DEFAULT_BOOKMARKS = {
    "cookie-viewer": "https://httpbingo.org/cookies",
    "cookie-store": "chrome://settings/cookies",
}


class BookmarkStore:
    def __init__(self, path: str = BOOKMARKS_FILE) -> None:
        self._path = path
        self.bookmarks: dict[str, Bookmark] = {}
        self.pools: dict[str, Pool] = {}
        self._load()

    def _load(self) -> None:
        if not pathlib.Path(self._path).exists():
            self._seed_defaults()
            return
        try:
            with pathlib.Path(self._path).open(encoding="utf-8") as f:
                data = json.load(f)
            for name, b in data.get("bookmarks", {}).items():
                self.bookmarks[name] = Bookmark(name=b["name"], url=b["url"])
            for name, p in data.get("pools", {}).items():
                self.pools[name] = Pool(
                    name=p["name"], bookmark_names=p.get("bookmark_names", [])
                )
            logger.info(
                "Loaded %d bookmarks, %d pools",
                len(self.bookmarks),
                len(self.pools),
            )
        except Exception as e:
            logger.exception("Error loading bookmarks: %s", e)

    def _seed_defaults(self) -> None:
        for name, url in DEFAULT_BOOKMARKS.items():
            self.bookmarks[name] = Bookmark(name=name, url=url)
        self._save()

    def _save(self) -> None:
        try:
            with pathlib.Path(self._path).open("w", encoding="utf-8") as f:
                json.dump(
                    {
                        "bookmarks": {
                            n: b.to_dict() for n, b in self.bookmarks.items()
                        },
                        "pools": {n: p.to_dict() for n, p in self.pools.items()},
                    },
                    f,
                    indent=4,
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
        if pool_name and pool_name in self.pools:
            ordered.extend(self.pools[pool_name].bookmark_names)
        if bookmark_names:
            ordered.extend(bookmark_names)
        seen: set[str] = set()
        result: list[Bookmark] = []
        for n in ordered:
            if n in seen or n not in self.bookmarks:
                continue
            seen.add(n)
            result.append(self.bookmarks[n])
        return result
