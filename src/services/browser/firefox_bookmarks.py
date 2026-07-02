"""Seed toolbar bookmarks into a Firefox profile's places.sqlite.

The stealth Firefox build ignores HTML-import prefs and policy-based bookmarks,
so bookmarks are written straight into the profile's places database. Firefox
rejects a hand-built database ("files in use"), so the engine must create
places.sqlite first (on its first launch); this then inserts rows into the
existing database under the bookmarks-toolbar root.
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


def seed_places_bookmarks(places_db: str, bookmarks: list[Bookmark]) -> bool:
    """Insert bookmarks onto the toolbar of an engine-created places.sqlite.

    Returns True when at least one bookmark was written. No-op (False) when the
    list is empty or the database doesn't exist yet.
    """
    if not bookmarks or not os.path.exists(places_db):
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
        position = cur.execute(
            "SELECT COALESCE(MAX(position)+1, 0) FROM moz_bookmarks WHERE parent=?",
            (toolbar_id,),
        ).fetchone()[0]

        for bm in bookmarks:
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
                "VALUES (?,?,?,0,0,0,?,?,1,?,?,1)",
                (bm.url, bm.name, _rev_host(host), 100, _guid(),
                 _url_hash(bm.url), origin_id),
            )
            place_id = cur.lastrowid

            cur.execute(
                "INSERT INTO moz_bookmarks"
                "(type,fk,parent,position,title,dateAdded,lastModified,guid,"
                "syncStatus,syncChangeCounter) VALUES (1,?,?,?,?,?,?,?,0,1)",
                (place_id, toolbar_id, position, bm.name, _DATE, _DATE, _guid()),
            )
            position += 1

        conn.commit()
        return True
    finally:
        conn.close()
