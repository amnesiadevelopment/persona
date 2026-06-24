import json
import pathlib
import uuid

from ...core.logging import get_logger
from ...models.bookmark import Bookmark

logger = get_logger("browser.bookmarks_seed")

# Fixed WebKit timestamp (microseconds since 1601). A constant value keeps the
# seeded file deterministic; Chromium overwrites it once the user touches the
# bookmark. We deliberately omit the top-level "checksum" — Chromium recomputes
# it on load rather than rejecting the file.
_FIXED_TS = "13350000000000000"

# Stable namespace so the same bookmark name always yields the same guid,
# keeping seeded files reproducible (no Math.random / uuid4 churn).
_NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00cf4fc964ff")


def _guid_for(name: str) -> str:
    return str(uuid.uuid5(_NS, name))


def _node(bookmark: Bookmark, node_id: int) -> dict:
    return {
        "date_added": _FIXED_TS,
        "date_last_used": "0",
        "guid": _guid_for(bookmark.name),
        "id": str(node_id),
        "name": bookmark.name,
        "type": "url",
        "url": bookmark.url,
    }


def build_bookmarks_doc(bookmarks: list[Bookmark]) -> dict:
    children = [_node(b, i) for i, b in enumerate(bookmarks, start=5)]
    return {
        "roots": {
            "bookmark_bar": {
                "children": children,
                "date_added": _FIXED_TS,
                "date_modified": _FIXED_TS,
                "guid": _guid_for("__bookmark_bar__"),
                "id": "1",
                "name": "Bookmarks bar",
                "type": "folder",
            },
            "other": {
                "children": [],
                "date_added": _FIXED_TS,
                "date_modified": "0",
                "guid": _guid_for("__other__"),
                "id": "2",
                "name": "Other bookmarks",
                "type": "folder",
            },
            "synced": {
                "children": [],
                "date_added": _FIXED_TS,
                "date_modified": "0",
                "guid": _guid_for("__synced__"),
                "id": "3",
                "name": "Mobile bookmarks",
                "type": "folder",
            },
        },
        "version": 1,
    }


def seed_bookmarks(profile_dir: str, bookmarks: list[Bookmark]) -> bool:
    """Write the chosen bookmarks onto a fresh profile's bookmarks bar. No-op
    when there are no bookmarks or when the profile already has a Bookmarks
    file (so it never clobbers what the user organised later).

    Returns True when a file was written, False when skipped.
    """
    if not bookmarks:
        return False
    default_dir = pathlib.Path(profile_dir) / "Default"
    path = default_dir / "Bookmarks"
    if path.exists():
        return False
    default_dir.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(build_bookmarks_doc(bookmarks), f)
        logger.info("Seeded %d bookmarks at %s", len(bookmarks), profile_dir)
        return True
    except Exception as e:
        logger.exception("Error seeding bookmarks: %s", e)
        return False
