"""Panic wipe: wipe_all_profiles() deletes every profile AND its data dir in one
pass, irreversibly. Gated in the UI behind a typed DELETE confirmation.
"""
import os

import pytest

from src.services.profile.manager import ProfileManager


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    import src.core.config as cfg
    import src.services.profile.manager as mod

    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    for m in (cfg, mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
    return ProfileManager()


def test_wipe_removes_all_profiles_and_returns_count(mgr):
    mgr.add_profile("a", "", "windows")
    mgr.add_profile("b", "", "windows")
    mgr.add_profile("c", "", "windows")
    # materialise a data dir for one profile
    os.makedirs(mgr._data_path("a"), exist_ok=True)
    with open(os.path.join(mgr._data_path("a"), "cookies.sqlite"), "w") as f:
        f.write("x")

    n = mgr.wipe_all_profiles()

    assert n == 3
    assert mgr.list_profiles() == []
    assert not os.path.exists(mgr._data_path("a")), "data dir must be deleted"


def test_wipe_persists_empty_profiles_file(mgr):
    mgr.add_profile("a", "", "windows")
    mgr.wipe_all_profiles()
    # a fresh manager (reloading from disk) sees no profiles
    fresh = ProfileManager()
    assert fresh.list_profiles() == []


def test_wipe_on_empty_is_a_noop_returning_zero(mgr):
    assert mgr.wipe_all_profiles() == 0
