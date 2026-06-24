import json
import os

import pytest

from src.models.profile import Profile
from src.services.profile.manager import ProfileManager


def test_profile_defaults_cookie_import_status_none():
    p = Profile(name="a")
    assert p.cookie_import_status is None


def test_profile_to_dict_roundtrips_cookie_import_status():
    p = Profile(name="a", cookie_import_status="creep.json · 11 cookies")
    d = p.to_dict()
    assert d["cookie_import_status"] == "creep.json · 11 cookies"
    assert Profile(**d).cookie_import_status == "creep.json · 11 cookies"


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    pf = tmp_path / "profiles.json"
    dd = tmp_path / "data"
    monkeypatch.setenv("PERSONA_PROFILES_FILE", str(pf))
    monkeypatch.setenv("PERSONA_DATA_DIR", str(dd))
    # config reads env at import; patch the already-imported module values too
    import src.core.config as cfg
    import src.services.profile.manager as mod

    monkeypatch.setattr(cfg, "PROFILES_FILE", str(pf))
    monkeypatch.setattr(cfg, "DATA_DIR", str(dd))
    monkeypatch.setattr(mod, "PROFILES_FILE", str(pf))
    monkeypatch.setattr(mod, "DATA_DIR", str(dd))
    return ProfileManager()


def test_set_cookie_status_persists(mgr, tmp_path):
    mgr.add_profile("p1", "", "windows")
    assert mgr.set_cookie_status("p1", "creep.json · 11 cookies") is True
    assert mgr.profiles["p1"].cookie_import_status == "creep.json · 11 cookies"

    raw = json.loads((tmp_path / "profiles.json").read_text(encoding="utf-8"))
    assert raw["p1"]["cookie_import_status"] == "creep.json · 11 cookies"


def test_set_cookie_status_unknown_profile_returns_false(mgr):
    assert mgr.set_cookie_status("ghost", "x") is False


def test_cookie_status_survives_reload(mgr, tmp_path, monkeypatch):
    mgr.add_profile("p1", "", "windows")
    mgr.set_cookie_status("p1", "creep.json · 11 cookies")

    import src.services.profile.manager as mod

    fresh = mod.ProfileManager()
    assert fresh.profiles["p1"].cookie_import_status == "creep.json · 11 cookies"
