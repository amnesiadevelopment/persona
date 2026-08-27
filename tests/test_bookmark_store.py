from src.models.bookmark import Bookmark
from src.services.bookmark.store import BookmarkStore


def _store(tmp_path):
    return BookmarkStore(path=str(tmp_path / "bookmarks.json"))


def test_save_is_atomic_no_temp_left_and_valid(tmp_path):
    # #14: the store must write atomically (temp + os.replace) so a crash
    # mid-save can't leave a half-written file that silently overwrites every
    # bookmark on the next load. A completed save leaves exactly one JSON file
    # (no stray .new temp) and it round-trips.
    import json
    import pathlib

    s = _store(tmp_path)
    s.add("leaks", "https://browserleaks.com")
    files = list(pathlib.Path(tmp_path).iterdir())
    assert [f.name for f in files] == ["bookmarks.json"], files
    data = json.loads((tmp_path / "bookmarks.json").read_text(encoding="utf-8"))
    assert data["bookmarks"]["leaks"]["url"] == "https://browserleaks.com"


def test_add_and_list(tmp_path):
    s = _store(tmp_path)
    assert s.add("leaks", "https://browserleaks.com") is True
    assert "leaks" in s.bookmark_names()
    assert Bookmark("leaks", "https://browserleaks.com") in s.list_bookmarks()


def test_add_duplicate_rejected(tmp_path):
    s = _store(tmp_path)
    s.add("leaks", "https://browserleaks.com")
    assert s.add("leaks", "https://other.com") is False


def test_add_empty_rejected(tmp_path):
    s = _store(tmp_path)
    assert s.add("", "https://x.com") is False


def test_update(tmp_path):
    s = _store(tmp_path)
    s.add("leaks", "https://browserleaks.com")
    assert s.update("leaks", "leaks2", "https://creepjs.com") is True
    assert s.get("leaks") is None
    assert s.get("leaks2").url == "https://creepjs.com"


def test_delete(tmp_path):
    s = _store(tmp_path)
    s.add("leaks", "https://browserleaks.com")
    assert s.delete("leaks") is True
    assert "leaks" not in s.bookmark_names()


def test_persistence(tmp_path):
    path = str(tmp_path / "bookmarks.json")
    s1 = BookmarkStore(path=path)
    s1.add("leaks", "https://browserleaks.com")
    s2 = BookmarkStore(path=path)
    assert s2.get("leaks").url == "https://browserleaks.com"


# --- pools ---


def test_add_pool_keeps_only_existing_bookmarks(tmp_path):
    s = _store(tmp_path)
    s.add("a", "https://a.com")
    s.add("b", "https://b.com")
    assert s.add_pool("verify", ["a", "b", "ghost"]) is True
    assert s.get_pool("verify").bookmark_names == ["a", "b"]


def test_add_pool_duplicate_rejected(tmp_path):
    s = _store(tmp_path)
    s.add_pool("verify", [])
    assert s.add_pool("verify", []) is False


def test_update_pool(tmp_path):
    s = _store(tmp_path)
    s.add("a", "https://a.com")
    s.add("b", "https://b.com")
    s.add_pool("verify", ["a"])
    assert s.update_pool("verify", "tools", ["a", "b"]) is True
    assert s.get_pool("verify") is None
    assert s.get_pool("tools").bookmark_names == ["a", "b"]


def test_delete_pool(tmp_path):
    s = _store(tmp_path)
    s.add_pool("verify", [])
    assert s.delete_pool("verify") is True
    assert s.pool_names() == []


def test_pools_persist(tmp_path):
    path = str(tmp_path / "bookmarks.json")
    s1 = BookmarkStore(path=path)
    s1.add("a", "https://a.com")
    s1.add_pool("verify", ["a"])
    s2 = BookmarkStore(path=path)
    assert s2.get_pool("verify").bookmark_names == ["a"]


def test_deleting_bookmark_removes_it_from_pools(tmp_path):
    s = _store(tmp_path)
    s.add("a", "https://a.com")
    s.add("b", "https://b.com")
    s.add_pool("verify", ["a", "b"])
    s.delete("a")
    assert s.get_pool("verify").bookmark_names == ["b"]


def test_renaming_bookmark_updates_pools(tmp_path):
    s = _store(tmp_path)
    s.add("a", "https://a.com")
    s.add_pool("verify", ["a"])
    s.update("a", "alpha", "https://a.com")
    assert s.get_pool("verify").bookmark_names == ["alpha"]


# --- resolution ---


def test_resolve_pool_then_extra_bookmarks_deduped(tmp_path):
    s = _store(tmp_path)
    s.add("a", "https://a.com")
    s.add("b", "https://b.com")
    s.add("c", "https://c.com")
    s.add_pool("verify", ["a", "b"])
    result = s.resolve_selection("verify", ["b", "c"])
    assert [bm.name for bm in result] == ["a", "b", "c"]


def test_resolve_only_bookmarks(tmp_path):
    s = _store(tmp_path)
    s.add("a", "https://a.com")
    result = s.resolve_selection(None, ["a"])
    assert [bm.name for bm in result] == ["a"]


def test_resolve_only_pool(tmp_path):
    s = _store(tmp_path)
    s.add("a", "https://a.com")
    s.add_pool("verify", ["a"])
    result = s.resolve_selection("verify", None)
    assert [bm.name for bm in result] == ["a"]


def test_resolve_empty_falls_back_to_stock_defaults(tmp_path):
    # No pool and no picks → the profile gets the stock default bookmarks that
    # exist in the store (so the toolbar isn't empty), not an empty list.
    from src.services.bookmark.store import DEFAULT_BOOKMARKS

    s = _store(tmp_path)
    names = {b.name for b in s.resolve_selection(None, None)}
    assert names == {n for n in DEFAULT_BOOKMARKS if n in s.bookmarks}


def test_resolve_skips_deleted_names(tmp_path):
    s = _store(tmp_path)
    s.add("a", "https://a.com")
    result = s.resolve_selection(None, ["a", "ghost"])
    assert [bm.name for bm in result] == ["a"]


def test_resolve_unknown_pool_falls_back_to_defaults(tmp_path):
    # audit5 #4: a profile pointing at a pool that was deleted (a stale, truthy
    # but unknown pool_name) must NOT open with an empty toolbar. With no
    # individual picks it falls through to the stock defaults, exactly like a
    # None pool — the old `not pool_name` guard returned [] instead.
    from src.services.bookmark.store import DEFAULT_BOOKMARKS

    s = _store(tmp_path)
    names = {b.name for b in s.resolve_selection("deleted-pool", None)}
    assert names == {n for n in DEFAULT_BOOKMARKS if n in s.bookmarks}
    assert names, "expected the stock defaults, not an empty toolbar"


def test_resolve_unknown_pool_honors_explicit_empty(tmp_path):
    # But a profile that was deliberately configured to an empty set
    # (bookmark_names == []) stays empty even with a stale pool — the user's
    # explicit choice wins; defaults only fill a never-configured profile.
    s = _store(tmp_path)
    s.add("a", "https://a.com")
    result = s.resolve_selection("deleted-pool", [])
    assert result == []


def test_one_malformed_bookmark_is_skipped_not_fatal(tmp_path):
    import json
    path = tmp_path / "bookmarks.json"
    path.write_text(json.dumps({
        "defaults_seeded": True,
        "bookmarks": {
            "good": {"name": "good", "url": "https://ok.example"},
            "bad": {"name": "bad"},  # missing url
        },
        "pools": {},
    }), encoding="utf-8")
    s = BookmarkStore(path=str(path))
    assert s.get("good").url == "https://ok.example"
    assert s.get("bad") is None


def test_corrupt_bookmarks_file_quarantined_not_overwritten(tmp_path):
    import pathlib
    path = tmp_path / "bookmarks.json"
    path.write_text("{ not valid json", encoding="utf-8")
    s = BookmarkStore(path=str(path))
    # An unreadable file holds the user's bookmarks; the next save must not
    # overwrite it with the empty/defaults store — it's quarantined instead.
    s.add("leaks", "https://browserleaks.com")
    backups = list(pathlib.Path(tmp_path).glob("bookmarks.json.corrupt-*"))
    assert backups, "corrupt bookmarks.json must be quarantined"
    assert backups[0].read_text(encoding="utf-8") == "{ not valid json"


def test_save_blocked_when_quarantine_fails(tmp_path, monkeypatch):
    import pathlib
    path = tmp_path / "bookmarks.json"
    path.write_text("{ broken", encoding="utf-8")

    def boom(*a, **k):
        raise OSError("cannot rename")

    monkeypatch.setattr(pathlib.Path, "rename", boom)
    s = BookmarkStore(path=str(path))
    s.add("leaks", "https://browserleaks.com")
    assert path.read_text(encoding="utf-8") == "{ broken"
