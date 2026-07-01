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
