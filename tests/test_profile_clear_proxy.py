"""Deleting a proxy must drop its name from every profile that used it, so no
dangling proxy reference is left behind (a lingering name stranded the profile
page after a proxy was deleted)."""
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


def test_clear_proxy_drops_reference_from_matching_profiles(mgr):
    mgr.add_profile("uses-it-1", "PL", "windows")
    mgr.add_profile("uses-it-2", "PL", "windows")
    mgr.add_profile("other-proxy", "NL", "windows")
    mgr.add_profile("direct", "", "windows")

    changed = mgr.clear_proxy("PL")

    assert changed == 2
    assert mgr.profiles["uses-it-1"].proxy is None
    assert mgr.profiles["uses-it-2"].proxy is None
    assert mgr.profiles["other-proxy"].proxy == "NL"
    assert mgr.profiles["direct"].proxy is None


def test_clear_proxy_persists_across_reload(mgr, tmp_path, monkeypatch):
    mgr.add_profile("p", "PL", "windows")
    mgr.clear_proxy("PL")

    reloaded = ProfileManager()
    assert reloaded.profiles["p"].proxy is None


def test_clear_proxy_unused_changes_nothing(mgr):
    mgr.add_profile("p", "PL", "windows")
    assert mgr.clear_proxy("NL") == 0
    assert mgr.profiles["p"].proxy == "PL"
