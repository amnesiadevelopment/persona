"""A profile's assigned mTLS certificate must survive save/reload, and profiles
saved by an older version (no certificate key) must still load with no
certificate, so shipping the feature never breaks existing profiles.
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


def test_certificate_defaults_to_none():
    from src.models.profile import Profile

    assert Profile(name="p").certificate is None


def test_certificate_persists_across_reload(pm):
    pm.add_profile("c1", "", "windows", certificate="admin")
    assert pm.profiles["c1"].certificate == "admin"
    raw = json.load(open(os.environ["PERSONA_PROFILES_FILE"], encoding="utf-8"))
    assert raw["c1"]["certificate"] == "admin"
    from src.services.profile import manager as mgr
    pm2 = mgr.ProfileManager()
    assert pm2.profiles["c1"].certificate == "admin"


def test_update_changes_certificate(pm):
    pm.add_profile("c2", "", "windows", certificate="admin")
    pm.update_profile("c2", "c2", "", "windows", new_certificate="staging")
    from src.services.profile import manager as mgr
    pm2 = mgr.ProfileManager()
    assert pm2.profiles["c2"].certificate == "staging"


def test_update_can_clear_certificate(pm):
    pm.add_profile("c3", "", "windows", certificate="admin")
    pm.update_profile("c3", "c3", "", "windows", new_certificate="")
    from src.services.profile import manager as mgr
    pm2 = mgr.ProfileManager()
    assert pm2.profiles["c3"].certificate is None


def test_old_profile_without_certificate_loads_as_none(pm):
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
    assert pm2.profiles["old"].certificate is None
