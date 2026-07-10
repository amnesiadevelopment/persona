import json

import pytest

from src.services.profile.manager import ProfileManager


@pytest.fixture
def make_mgr(tmp_path, monkeypatch):
    pf = tmp_path / "profiles.json"
    dd = tmp_path / "data"
    monkeypatch.setenv("PERSONA_PROFILES_FILE", str(pf))
    monkeypatch.setenv("PERSONA_DATA_DIR", str(dd))
    import src.core.config as cfg
    import src.services.profile.manager as mod

    monkeypatch.setattr(cfg, "PROFILES_FILE", str(pf))
    monkeypatch.setattr(cfg, "DATA_DIR", str(dd))
    monkeypatch.setattr(mod, "PROFILES_FILE", str(pf))
    monkeypatch.setattr(mod, "DATA_DIR", str(dd))

    def _make(data):
        pf.write_text(json.dumps(data), encoding="utf-8")
        return ProfileManager()

    return _make


def test_malformed_entry_is_skipped_not_fatal(make_mgr):
    # #120: one bad record must not zero out the whole store — the next save
    # would overwrite profiles.json and silently lose every good profile.
    mgr = make_mgr(
        {
            "good1": {"name": "good1", "proxy": None},
            "bad": None,  # not a dict — .get() raises
            "good2": {"name": "good2", "proxy": None},
        }
    )
    assert set(mgr.profiles) == {"good1", "good2"}


def test_all_good_entries_load(make_mgr):
    mgr = make_mgr(
        {
            "a": {"name": "a", "proxy": None},
            "b": {"name": "b", "proxy": None},
        }
    )
    assert set(mgr.profiles) == {"a", "b"}


def test_bookmarks_none_when_key_absent(make_mgr):
    # A profile saved before the bookmarks field existed loads as None so it
    # keeps getting the default bookmarks, not an empty toolbar.
    mgr = make_mgr({"old": {"name": "old", "proxy": None}})
    assert mgr.profiles["old"].bookmarks is None


def test_cleared_bookmarks_round_trip_stays_empty(make_mgr):
    # An explicitly emptied selection ([]) survives save/reload as [] — the
    # user cleared them and they must not resurrect as the defaults.
    mgr = make_mgr({"p": {"name": "p", "proxy": None, "bookmarks": []}})
    assert mgr.profiles["p"].bookmarks == []
