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


def _names(mgr):
    return [p.name for p in mgr.list_profiles()]


def test_rename_keeps_list_position(mgr):
    mgr.add_profile("a", "", "windows")
    mgr.add_profile("b", "", "windows")
    mgr.add_profile("c", "", "windows")

    mgr.update_profile("b", "b2", "", "windows")

    assert _names(mgr) == ["a", "b2", "c"]


def test_rename_first_keeps_position(mgr):
    mgr.add_profile("a", "", "windows")
    mgr.add_profile("b", "", "windows")

    mgr.update_profile("a", "a2", "", "windows")

    assert _names(mgr) == ["a2", "b"]


def test_update_without_rename_keeps_position(mgr):
    mgr.add_profile("a", "", "windows")
    mgr.add_profile("b", "", "windows")
    mgr.add_profile("c", "", "windows")

    mgr.update_profile("b", "b", "1.2.3.4:8080", "linux")

    assert _names(mgr) == ["a", "b", "c"]


def test_failed_dir_rename_leaves_profile_unchanged(mgr, monkeypatch):
    # #15: a data-dir rename can fail (Windows lock while the browser runs). The
    # fields must be mutated AFTER the rename, so a failure leaves the profile
    # fully intact — no half-applied name/proxy/os and no memory/disk divergence.
    import pathlib

    mgr.add_profile("a", "1.1.1.1:1", "windows")

    def boom(self, target):
        raise OSError("dir locked")

    monkeypatch.setattr(pathlib.Path, "rename", boom)
    ok = mgr.update_profile("a", "a2", "2.2.2.2:2", "linux")

    assert ok is False
    # nothing changed: still keyed by "a", original name/proxy/os intact
    assert _names(mgr) == ["a"]
    p = mgr.profiles.get("a")
    assert p.name == "a"
    assert p.os_type == "windows"
    assert (p.proxy or "") == "1.1.1.1:1"
