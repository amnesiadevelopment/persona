import sqlite3

from src.models.bookmark import Bookmark
from src.services.browser.firefox_bookmarks import seed_places_bookmarks

# Minimal slice of the Firefox 150 places.sqlite schema + roots, matching what
# the engine creates on first launch. The seeder inserts under the toolbar root.
_SCHEMA = [
    "CREATE TABLE moz_origins ( id INTEGER PRIMARY KEY, prefix TEXT NOT NULL, "
    "host TEXT NOT NULL, frecency INTEGER NOT NULL, recalc_frecency INTEGER NOT "
    "NULL DEFAULT 0, UNIQUE (prefix, host) )",
    "CREATE TABLE moz_places ( id INTEGER PRIMARY KEY, url LONGVARCHAR, title "
    "LONGVARCHAR, rev_host LONGVARCHAR, visit_count INTEGER DEFAULT 0, hidden "
    "INTEGER DEFAULT 0 NOT NULL, typed INTEGER DEFAULT 0 NOT NULL, frecency "
    "INTEGER DEFAULT -1 NOT NULL, guid TEXT, foreign_count INTEGER DEFAULT 0 NOT "
    "NULL, url_hash INTEGER DEFAULT 0 NOT NULL, origin_id INTEGER, "
    "recalc_frecency INTEGER NOT NULL DEFAULT 0 )",
    "CREATE TABLE moz_bookmarks ( id INTEGER PRIMARY KEY, type INTEGER, fk "
    "INTEGER DEFAULT NULL, parent INTEGER, position INTEGER, title LONGVARCHAR, "
    "dateAdded INTEGER, lastModified INTEGER, guid TEXT, syncStatus INTEGER NOT "
    "NULL DEFAULT 0, syncChangeCounter INTEGER NOT NULL DEFAULT 1 )",
]
_ROOTS = [
    (1, 0, 0, "", "root________"),
    (2, 1, 0, "menu", "menu________"),
    (3, 1, 1, "toolbar", "toolbar_____"),
    (4, 1, 2, "tags", "tags________"),
    (5, 1, 3, "unfiled", "unfiled_____"),
    (6, 1, 4, "mobile", "mobile______"),
]


def _make_places(path):
    c = sqlite3.connect(path)
    for stmt in _SCHEMA:
        c.execute(stmt)
    for rid, parent, pos, title, guid in _ROOTS:
        c.execute(
            "INSERT INTO moz_bookmarks(id,type,parent,position,title,guid) "
            "VALUES (?,2,?,?,?,?)",
            (rid, parent, pos, title, guid),
        )
    c.commit()
    c.close()


def _toolbar_bookmarks(path):
    c = sqlite3.connect(path)
    tid = c.execute(
        "SELECT id FROM moz_bookmarks WHERE guid='toolbar_____'"
    ).fetchone()[0]
    rows = c.execute(
        "SELECT b.title, p.url FROM moz_bookmarks b JOIN moz_places p ON b.fk=p.id "
        "WHERE b.parent=? AND b.type=1 ORDER BY b.position",
        (tid,),
    ).fetchall()
    c.close()
    return rows


def test_seeds_under_toolbar(tmp_path):
    db = str(tmp_path / "places.sqlite")
    _make_places(db)
    ok = seed_places_bookmarks(
        db,
        [
            Bookmark("browserleaks", "https://browserleaks.com/"),
            Bookmark("iphey", "https://iphey.com/"),
        ],
    )
    assert ok is True
    assert _toolbar_bookmarks(db) == [
        ("browserleaks", "https://browserleaks.com/"),
        ("iphey", "https://iphey.com/"),
    ]


def test_creates_one_origin_and_place_per_bookmark(tmp_path):
    db = str(tmp_path / "places.sqlite")
    _make_places(db)
    seed_places_bookmarks(db, [Bookmark("a", "https://a.example/")])
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM moz_origins").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM moz_places").fetchone()[0] == 1
    # foreign_count must be set so Firefox counts the bookmark reference.
    assert c.execute("SELECT foreign_count FROM moz_places").fetchone()[0] == 1
    c.close()


def test_noop_on_empty_list(tmp_path):
    db = str(tmp_path / "places.sqlite")
    _make_places(db)
    assert seed_places_bookmarks(db, []) is False
    assert _toolbar_bookmarks(db) == []


def test_missing_db_returns_false(tmp_path):
    assert seed_places_bookmarks(str(tmp_path / "nope.sqlite"), [Bookmark("a", "https://a")]) is False


def test_guids_are_unique_12_char(tmp_path):
    db = str(tmp_path / "places.sqlite")
    _make_places(db)
    seed_places_bookmarks(db, [Bookmark("a", "https://a.example/"), Bookmark("b", "https://b.example/")])
    c = sqlite3.connect(db)
    guids = [r[0] for r in c.execute("SELECT guid FROM moz_bookmarks WHERE type=1")]
    c.close()
    assert len(guids) == 2
    assert len(set(guids)) == 2
    assert all(len(g) == 12 for g in guids)
