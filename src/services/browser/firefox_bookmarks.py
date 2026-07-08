"""Sync toolbar bookmarks into a Firefox profile's places.sqlite.

The stealth Firefox build ignores HTML-import prefs and policy-based bookmarks,
so bookmarks are written straight into the profile's places database. Firefox
rejects a hand-built database ("files in use"), so the engine must create
places.sqlite first (on its first launch); this then reconciles the rows under
the bookmarks-toolbar root to the desired set.
"""

import base64
import hashlib
import os
import sqlite3
from urllib.parse import urlparse

from ...models.bookmark import Bookmark

# A fixed timestamp is fine — Firefox only orders toolbar items by `position`.
_DATE = 1782758089116000


def _guid() -> str:
    return base64.urlsafe_b64encode(os.urandom(9)).decode("ascii")[:12]


def _url_hash(url: str) -> int:
    # Firefox's real hashURL is a C++ 48-bit value used for history dedup and
    # frecency, not for toolbar display. A stable 48-bit md5 slice is enough for
    # a bookmark that is never a history entry.
    return int.from_bytes(hashlib.md5(url.encode("utf-8")).digest()[:6], "big")


def _rev_host(host: str) -> str:
    return host.lower()[::-1] + "."


def places_ready(places_db: str) -> bool:
    """Whether the engine-created places database is ready to seed.

    Ready means Places has written the bookmark roots — the toolbar row the
    seeder inserts under. The file alone isn't enough: the engine writes
    places.sqlite to disk well before the roots exist, and seeding a rootless
    database silently inserts nothing. Safe to call while Firefox is still
    running (WAL allows a concurrent reader)."""
    if not os.path.exists(places_db):
        return False
    try:
        conn = sqlite3.connect(places_db, timeout=1)
        try:
            row = conn.execute(
                "SELECT 1 FROM moz_bookmarks WHERE guid='toolbar_____'"
            ).fetchone()
        finally:
            conn.close()
        return row is not None
    except sqlite3.Error:
        return False


def _place_id_for(cur: sqlite3.Cursor, bm: Bookmark) -> int:
    """The moz_places row id for this url, creating the place (and its origin)
    when it doesn't exist yet."""
    row = cur.execute(
        "SELECT id FROM moz_places WHERE url=?", (bm.url,)
    ).fetchone()
    if row is not None:
        return row[0]

    parsed = urlparse(bm.url)
    prefix = f"{parsed.scheme}://"
    host = (parsed.hostname or "").lower()

    origin = cur.execute(
        "SELECT id FROM moz_origins WHERE prefix=? AND host=?",
        (prefix, host),
    ).fetchone()
    if origin is not None:
        origin_id = origin[0]
    else:
        cur.execute(
            "INSERT INTO moz_origins(prefix,host,frecency,recalc_frecency) "
            "VALUES (?,?,?,1)",
            (prefix, host, 100),
        )
        origin_id = cur.lastrowid

    cur.execute(
        "INSERT INTO moz_places"
        "(url,title,rev_host,visit_count,hidden,typed,frecency,guid,"
        "foreign_count,url_hash,origin_id,recalc_frecency) "
        "VALUES (?,?,?,0,0,0,?,?,0,?,?,1)",
        (bm.url, bm.name, _rev_host(host), 100, _guid(),
         _url_hash(bm.url), origin_id),
    )
    return cur.lastrowid


def _drop_bookmark(cur: sqlite3.Cursor, row_id: int, place_id: int) -> None:
    """Delete a toolbar bookmark row and release its place reference; an
    unvisited place with no remaining bookmark is a pure seed artifact and
    goes with it (a visited one is the user's history — kept)."""
    cur.execute("DELETE FROM moz_bookmarks WHERE id=?", (row_id,))
    cur.execute(
        "UPDATE moz_places SET foreign_count=MAX(foreign_count-1,0) WHERE id=?",
        (place_id,),
    )
    cur.execute(
        "DELETE FROM moz_places "
        "WHERE id=? AND foreign_count=0 AND visit_count=0",
        (place_id,),
    )


def sync_places_bookmarks(places_db: str, bookmarks: list[Bookmark]) -> bool:
    """Reconcile the toolbar of an engine-created places.sqlite to exactly
    `bookmarks`, in order: rows whose url isn't in the set (and duplicate rows
    for the same url) are deleted, missing ones inserted, titles and positions
    updated in place. Idempotent — running it again with the same set changes
    nothing; an empty set clears the toolbar.

    Returns True when the toolbar now matches the set; False when the database
    doesn't exist or has no toolbar root yet.
    """
    if not os.path.exists(places_db):
        return False

    conn = sqlite3.connect(places_db)
    try:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT id FROM moz_bookmarks WHERE guid='toolbar_____'"
        ).fetchone()
        if row is None:
            return False
        toolbar_id = row[0]

        existing = {}  # url -> (row_id, title, place_id); first row per url wins
        others = []  # non-bookmark toolbar items (folders/separators)
        for row_id, btype, title, place_id, url in cur.execute(
            "SELECT b.id, b.type, b.title, b.fk, p.url FROM moz_bookmarks b "
            "LEFT JOIN moz_places p ON b.fk=p.id "
            "WHERE b.parent=? ORDER BY b.position",
            (toolbar_id,),
        ).fetchall():
            if btype != 1:
                others.append(row_id)
            elif url in existing or not any(bm.url == url for bm in bookmarks):
                _drop_bookmark(cur, row_id, place_id)
            else:
                existing[url] = (row_id, title, place_id)

        for pos, bm in enumerate(bookmarks):
            if bm.url in existing:
                row_id, title, _ = existing[bm.url]
                if title != bm.name:
                    cur.execute(
                        "UPDATE moz_bookmarks SET title=?, lastModified=? "
                        "WHERE id=?",
                        (bm.name, _DATE, row_id),
                    )
                cur.execute(
                    "UPDATE moz_bookmarks SET position=? WHERE id=?",
                    (pos, row_id),
                )
                continue
            place_id = _place_id_for(cur, bm)
            cur.execute(
                "UPDATE moz_places SET foreign_count=foreign_count+1 WHERE id=?",
                (place_id,),
            )
            cur.execute(
                "INSERT INTO moz_bookmarks"
                "(type,fk,parent,position,title,dateAdded,lastModified,guid,"
                "syncStatus,syncChangeCounter) VALUES (1,?,?,?,?,?,?,?,0,1)",
                (place_id, toolbar_id, pos, bm.name, _DATE, _DATE, _guid()),
            )

        # Non-bookmark toolbar items keep their relative order after the set.
        for offset, row_id in enumerate(others):
            cur.execute(
                "UPDATE moz_bookmarks SET position=? WHERE id=?",
                (len(bookmarks) + offset, row_id),
            )

        conn.commit()
        return True
    finally:
        conn.close()
