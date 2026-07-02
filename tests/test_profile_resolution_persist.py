"""A profile's resolution must survive save/reload, and profiles saved by an
older version (no resolution key) must still load — defaulting to "auto" — so
an update never breaks existing profiles.
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


def test_resolution_persists_across_reload(pm):
    pm.add_profile("r1", "", "windows", resolution="1920x1080")
    assert pm.profiles["r1"].resolution == "1920x1080"
    raw = json.load(open(os.environ["PERSONA_PROFILES_FILE"]))
    assert raw["r1"]["resolution"] == "1920x1080"
    # a fresh manager reads it back rather than resetting to "auto"
    from src.services.profile import manager as mgr
    pm2 = mgr.ProfileManager()
    assert pm2.profiles["r1"].resolution == "1920x1080"


def test_old_profile_without_resolution_loads_as_auto(pm, tmp_path):
    # simulate a profiles.json written by a version predating the resolution
    # field: the key is simply absent.
    legacy = {
        "old": {
            "name": "old",
            "proxy": None,
            "os_type": "windows",
            "engine": "chromium",
            "search_engine": "duckduckgo",
            "bookmarks": [],
            "tags": [],
            "notes": "",
        }
    }
    with open(os.environ["PERSONA_PROFILES_FILE"], "w", encoding="utf-8") as f:
        json.dump(legacy, f)
    from src.services.profile import manager as mgr
    pm2 = mgr.ProfileManager()
    assert "old" in pm2.profiles  # loads without error
    assert pm2.profiles["old"].resolution == "auto"
