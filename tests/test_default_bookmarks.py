import pytest

from src.services.bookmark.store import BookmarkStore


@pytest.fixture
def store_path(tmp_path):
    return str(tmp_path / "bookmarks.json")


def test_fresh_store_seeds_cookie_viewer(store_path):
    store = BookmarkStore(path=store_path)
    names = store.bookmark_names()
    assert "cookie-viewer" in names
    assert store.bookmarks["cookie-viewer"].url == "https://httpbingo.org/cookies"


def test_seed_persists_to_disk(store_path):
    BookmarkStore(path=store_path)
    reloaded = BookmarkStore(path=store_path)
    assert "cookie-viewer" in reloaded.bookmark_names()


def test_fresh_store_seeds_anti_detect_testers(store_path):
    store = BookmarkStore(path=store_path)
    names = store.bookmark_names()
    for tester in ("browserleaks", "pixelscan", "iphey", "browserscan"):
        assert tester in names
    assert store.bookmarks["browserleaks"].url == "https://browserleaks.com/"
    assert store.bookmarks["pixelscan"].url == "https://pixelscan.net/"
    assert store.bookmarks["iphey"].url == "https://iphey.com/"
    assert store.bookmarks["browserscan"].url == "https://browserscan.net/"


def test_deleted_default_does_not_resurrect(store_path):
    store = BookmarkStore(path=store_path)
    store.delete("cookie-viewer")
    reloaded = BookmarkStore(path=store_path)
    assert "cookie-viewer" not in reloaded.bookmark_names()


def test_empty_selection_defaults_to_stock_bookmarks(store_path):
    # A profile with no pool and no picked bookmarks still opens with the stock
    # testers on the toolbar, not an empty bar.
    store = BookmarkStore(path=store_path)
    resolved = store.resolve_selection(None, [])
    names = [b.name for b in resolved]
    for tester in ("browserleaks", "pixelscan", "iphey", "browserscan"):
        assert tester in names


def test_explicit_selection_is_honored_not_defaulted(store_path):
    store = BookmarkStore(path=store_path)
    resolved = store.resolve_selection(None, ["iphey"])
    assert [b.name for b in resolved] == ["iphey"]


def test_existing_store_gets_missing_testers_once(store_path, tmp_path):
    import json

    # An old store (pre-testers) with no defaults_seeded marker and only the two
    # cookie bookmarks — as an upgrading user would have.
    with open(store_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "bookmarks": {
                    "cookie-viewer": {
                        "name": "cookie-viewer",
                        "url": "https://httpbingo.org/cookies",
                    }
                },
                "pools": {},
            },
            f,
        )
    store = BookmarkStore(path=store_path)
    for tester in ("browserleaks", "pixelscan", "iphey", "browserscan"):
        assert tester in store.bookmark_names()
    # migration is one-shot: after it runs, deleting a tester must not bring it
    # back on the next load.
    store.delete("pixelscan")
    reloaded = BookmarkStore(path=store_path)
    assert "pixelscan" not in reloaded.bookmark_names()
