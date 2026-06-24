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


def test_deleted_default_does_not_resurrect(store_path):
    store = BookmarkStore(path=store_path)
    store.delete("cookie-viewer")
    reloaded = BookmarkStore(path=store_path)
    assert "cookie-viewer" not in reloaded.bookmark_names()
