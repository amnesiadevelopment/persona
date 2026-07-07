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
