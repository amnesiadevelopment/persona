import sqlite3

from src.models.bookmark import Bookmark
from src.services.browser.firefox_bookmarks import (
    places_ready,
    sync_places_bookmarks,
)

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
    ok = sync_places_bookmarks(
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
    sync_places_bookmarks(db, [Bookmark("a", "https://a.example/")])
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM moz_origins").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM moz_places").fetchone()[0] == 1
    # foreign_count must be set so Firefox counts the bookmark reference.
    assert c.execute("SELECT foreign_count FROM moz_places").fetchone()[0] == 1
    c.close()


def test_same_set_twice_keeps_exactly_one_copy(tmp_path):
    # #144(a): re-syncing the same set on every launch must be idempotent —
    # the blind append duplicated the whole set per launch.
    db = str(tmp_path / "places.sqlite")
    marks = [
        Bookmark("a", "https://a.example/"),
        Bookmark("b", "https://b.example/"),
    ]
    _make_places(db)
    sync_places_bookmarks(db, marks)
    sync_places_bookmarks(db, marks)
    assert _toolbar_bookmarks(db) == [
        ("a", "https://a.example/"),
        ("b", "https://b.example/"),
    ]
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM moz_places").fetchone()[0] == 2
    assert c.execute("SELECT COUNT(*) FROM moz_origins").fetchone()[0] == 2
    c.close()


def test_sync_to_different_set_replaces_old(tmp_path):
    # #144(b): the toolbar must equal EXACTLY the desired set — bookmarks the
    # user removed in the profile editor must disappear, not pile up.
    db = str(tmp_path / "places.sqlite")
    _make_places(db)
    sync_places_bookmarks(
        db,
        [
            Bookmark("a", "https://a.example/"),
            Bookmark("b", "https://b.example/"),
            Bookmark("c", "https://c.example/"),
        ],
    )
    sync_places_bookmarks(db, [Bookmark("b", "https://b.example/")])
    assert _toolbar_bookmarks(db) == [("b", "https://b.example/")]


def test_sync_to_empty_clears_toolbar(tmp_path):
    # #144(b): removing ALL bookmarks in the profile editor must clear the
    # toolbar (and not leave orphaned unvisited places rows behind).
    db = str(tmp_path / "places.sqlite")
    _make_places(db)
    sync_places_bookmarks(
        db,
        [Bookmark("a", "https://a.example/"), Bookmark("b", "https://b.example/")],
    )
    assert sync_places_bookmarks(db, []) is True
    assert _toolbar_bookmarks(db) == []
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM moz_places").fetchone()[0] == 0
    c.close()


def test_sync_dedupes_preexisting_duplicates(tmp_path):
    # A profile that already carries duplicates from the blind-append era must
    # come out with exactly one row per desired bookmark.
    db = str(tmp_path / "places.sqlite")
    marks = [Bookmark("a", "https://a.example/")]
    _make_places(db)
    sync_places_bookmarks(db, marks)
    c = sqlite3.connect(db)
    tid = c.execute(
        "SELECT id FROM moz_bookmarks WHERE guid='toolbar_____'"
    ).fetchone()[0]
    fk = c.execute("SELECT fk FROM moz_bookmarks WHERE type=1").fetchone()[0]
    c.execute(
        "INSERT INTO moz_bookmarks(type,fk,parent,position,title,dateAdded,"
        "lastModified,guid,syncStatus,syncChangeCounter) "
        "VALUES (1,?,?,1,'a',0,0,'dupdupdupdu1',0,1)",
        (fk, tid),
    )
    c.execute("UPDATE moz_places SET foreign_count=2 WHERE id=?", (fk,))
    c.commit()
    c.close()
    sync_places_bookmarks(db, marks)
    assert _toolbar_bookmarks(db) == [("a", "https://a.example/")]


def test_sync_renames_kept_bookmark(tmp_path):
    # Same url, new name in the profile editor → the row is renamed in place,
    # not duplicated.
    db = str(tmp_path / "places.sqlite")
    _make_places(db)
    sync_places_bookmarks(db, [Bookmark("old", "https://a.example/")])
    sync_places_bookmarks(db, [Bookmark("new", "https://a.example/")])
    assert _toolbar_bookmarks(db) == [("new", "https://a.example/")]


def test_sync_positions_follow_desired_order(tmp_path):
    # Firefox orders the toolbar by `position`; after any reconcile the rows
    # must sit at contiguous positions in the desired order.
    db = str(tmp_path / "places.sqlite")
    _make_places(db)
    sync_places_bookmarks(
        db,
        [
            Bookmark("a", "https://a.example/"),
            Bookmark("b", "https://b.example/"),
            Bookmark("c", "https://c.example/"),
        ],
    )
    sync_places_bookmarks(
        db,
        [Bookmark("c", "https://c.example/"), Bookmark("a", "https://a.example/")],
    )
    assert _toolbar_bookmarks(db) == [
        ("c", "https://c.example/"),
        ("a", "https://a.example/"),
    ]
    c = sqlite3.connect(db)
    positions = [
        r[0]
        for r in c.execute(
            "SELECT position FROM moz_bookmarks WHERE type=1 ORDER BY position"
        )
    ]
    c.close()
    assert positions == [0, 1]


def test_sync_keeps_visited_place_row(tmp_path):
    # A place the user has actually visited is history, not our seed artifact —
    # deleting its bookmark must not delete the history row.
    db = str(tmp_path / "places.sqlite")
    _make_places(db)
    sync_places_bookmarks(db, [Bookmark("a", "https://a.example/")])
    c = sqlite3.connect(db)
    c.execute("UPDATE moz_places SET visit_count=3")
    c.commit()
    c.close()
    sync_places_bookmarks(db, [])
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM moz_places").fetchone()[0] == 1
    assert c.execute("SELECT foreign_count FROM moz_places").fetchone()[0] == 0
    c.close()


def test_empty_set_on_fresh_db_is_true_noop(tmp_path):
    db = str(tmp_path / "places.sqlite")
    _make_places(db)
    assert sync_places_bookmarks(db, []) is True
    assert _toolbar_bookmarks(db) == []


def test_missing_db_returns_false(tmp_path):
    assert sync_places_bookmarks(str(tmp_path / "nope.sqlite"), [Bookmark("a", "https://a")]) is False


def test_places_ready_false_when_db_missing(tmp_path):
    assert places_ready(str(tmp_path / "places.sqlite")) is False


def test_places_ready_false_before_toolbar_root_exists(tmp_path):
    # A wedged/slow headless init can leave places.sqlite on disk BEFORE
    # Places has written the bookmark roots. Seeding such a database silently
    # inserts nothing (no toolbar parent) — the empty-toolbar bug — so the
    # readiness signal is the toolbar root, not the file's existence.
    db = str(tmp_path / "places.sqlite")
    c = sqlite3.connect(db)
    for stmt in _SCHEMA:
        c.execute(stmt)
    c.commit()
    c.close()
    assert places_ready(db) is False


def test_places_ready_true_with_toolbar_root(tmp_path):
    db = str(tmp_path / "places.sqlite")
    _make_places(db)
    assert places_ready(db) is True


def test_places_ready_false_on_non_places_db(tmp_path):
    # A corrupt or foreign sqlite file (no moz_bookmarks at all) is not ready.
    db = str(tmp_path / "places.sqlite")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE t (x)")
    c.commit()
    c.close()
    assert places_ready(db) is False


def test_guids_are_unique_12_char(tmp_path):
    db = str(tmp_path / "places.sqlite")
    _make_places(db)
    sync_places_bookmarks(db, [Bookmark("a", "https://a.example/"), Bookmark("b", "https://b.example/")])
    c = sqlite3.connect(db)
    guids = [r[0] for r in c.execute("SELECT guid FROM moz_bookmarks WHERE type=1")]
    c.close()
    assert len(guids) == 2
    assert len(set(guids)) == 2
    assert all(len(g) == 12 for g in guids)
