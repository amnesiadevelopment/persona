import json
import os

import pytest

from src.models.profile import Profile
from src.services.profile.filter import filter_profiles


def test_profile_has_notes_default_empty():
    p = Profile(name="x")
    assert p.notes == ""
    assert "notes" in p.to_dict()


def test_filter_matches_notes():
    a = Profile(name="alpha", notes="warmup done, instagram")
    b = Profile(name="beta", notes="")
    assert filter_profiles([a, b], "instagram") == [a]
    assert filter_profiles([a, b], "nope") == []


@pytest.fixture
def pm(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_PROFILES_FILE", str(tmp_path / "p.json"))
    monkeypatch.setenv("PERSONA_DATA_DIR", str(tmp_path / "data"))
    # config reads env at import; reload the modules that cached the paths
    import importlib
    from src.core import config as cfg
    importlib.reload(cfg)
    from src.services.profile import manager as mgr
    importlib.reload(mgr)
    return mgr.ProfileManager()


def test_add_profile_with_notes_persists(pm):
    assert pm.add_profile("p1", "", "windows", notes="my note")
    assert pm.profiles["p1"].notes == "my note"
    # round-trip through the saved file
    raw = json.load(open(os.environ["PERSONA_PROFILES_FILE"]))
    assert raw["p1"]["notes"] == "my note"


def test_update_profile_notes(pm):
    pm.add_profile("p2", "", "windows")
    assert pm.update_profile("p2", "p2", "", "windows", new_notes="updated")
    assert pm.profiles["p2"].notes == "updated"


def test_notes_survive_reload(pm, tmp_path):
    pm.add_profile("p3", "", "windows", notes="persist me")
    # new manager instance loads from the same file
    from src.services.profile import manager as mgr
    pm2 = mgr.ProfileManager()
    assert pm2.profiles["p3"].notes == "persist me"
