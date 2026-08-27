"""A profile's chosen search engine must survive save/reload, and profiles
saved by an older version (no search_engine key) must still load, defaulting to
DuckDuckGo, so an update never breaks existing profiles.
"""
import json
import os

import pytest


@pytest.fixture
def pm(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_PROFILES_FILE", str(tmp_path / "p.json"))
    monkeypatch.setenv("PERSONA_DATA_DIR", str(tmp_path / "data"))
    import importlib

    from src.core import config as cfg
    importlib.reload(cfg)
    from src.services.profile import manager as mgr
    importlib.reload(mgr)
    return mgr.ProfileManager()


def test_search_engine_persists_across_reload(pm):
    pm.add_profile("s1", "", "windows", search_engine="google")
    assert pm.profiles["s1"].search_engine == "google"
    raw = json.load(open(os.environ["PERSONA_PROFILES_FILE"], encoding="utf-8"))
    assert raw["s1"]["search_engine"] == "google"
    from src.services.profile import manager as mgr
    pm2 = mgr.ProfileManager()
    assert pm2.profiles["s1"].search_engine == "google"


def test_update_changes_search_engine(pm):
    pm.add_profile("s2", "", "windows", search_engine="duckduckgo")
    pm.update_profile("s2", "s2", "", "windows", new_search_engine="brave")
    from src.services.profile import manager as mgr
    pm2 = mgr.ProfileManager()
    assert pm2.profiles["s2"].search_engine == "brave"


def test_old_profile_without_search_engine_loads_as_duckduckgo(pm):
    legacy = {
        "old": {
            "name": "old",
            "proxy": None,
            "os_type": "windows",
            "engine": "chromium",
            "resolution": "auto",
            "bookmarks": [],
            "tags": [],
            "notes": "",
        }
    }
    with open(os.environ["PERSONA_PROFILES_FILE"], "w", encoding="utf-8") as f:
        json.dump(legacy, f)
    from src.services.profile import manager as mgr
    pm2 = mgr.ProfileManager()
    assert "old" in pm2.profiles
    assert pm2.profiles["old"].search_engine == "duckduckgo"
