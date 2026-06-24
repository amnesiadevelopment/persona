import json

from src.models.bookmark import Bookmark
from src.services.browser.bookmarks_seed import (
    build_bookmarks_doc,
    seed_bookmarks,
)


def test_doc_has_chromium_structure():
    doc = build_bookmarks_doc([Bookmark("leaks", "https://browserleaks.com")])
    assert doc["version"] == 1
    bar = doc["roots"]["bookmark_bar"]
    assert bar["type"] == "folder"
    child = bar["children"][0]
    assert child["type"] == "url"
    assert child["name"] == "leaks"
    assert child["url"] == "https://browserleaks.com"


def test_doc_preserves_order_and_unique_ids():
    doc = build_bookmarks_doc(
        [Bookmark("a", "https://a.com"), Bookmark("b", "https://b.com")]
    )
    children = doc["roots"]["bookmark_bar"]["children"]
    assert [c["name"] for c in children] == ["a", "b"]
    ids = [c["id"] for c in children]
    guids = [c["guid"] for c in children]
    assert len(set(ids)) == 2
    assert len(set(guids)) == 2


def test_guid_is_deterministic():
    d1 = build_bookmarks_doc([Bookmark("leaks", "https://x.com")])
    d2 = build_bookmarks_doc([Bookmark("leaks", "https://x.com")])
    g1 = d1["roots"]["bookmark_bar"]["children"][0]["guid"]
    g2 = d2["roots"]["bookmark_bar"]["children"][0]["guid"]
    assert g1 == g2


def test_no_checksum_key():
    doc = build_bookmarks_doc([Bookmark("a", "https://a.com")])
    assert "checksum" not in doc


def test_seed_writes_file(tmp_path):
    ok = seed_bookmarks(str(tmp_path), [Bookmark("leaks", "https://browserleaks.com")])
    assert ok is True
    doc = json.loads((tmp_path / "Default" / "Bookmarks").read_text())
    assert doc["roots"]["bookmark_bar"]["children"][0]["name"] == "leaks"


def test_seed_noop_on_empty(tmp_path):
    assert seed_bookmarks(str(tmp_path), []) is False
    assert not (tmp_path / "Default" / "Bookmarks").exists()


def test_seed_skips_existing(tmp_path):
    default = tmp_path / "Default"
    default.mkdir(parents=True)
    existing = default / "Bookmarks"
    existing.write_text('{"existing":true}')
    assert seed_bookmarks(str(tmp_path), [Bookmark("a", "https://a.com")]) is False
    assert json.loads(existing.read_text()) == {"existing": True}
