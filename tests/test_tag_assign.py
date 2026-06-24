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
    m = ProfileManager()
    m.add_profile("a", "", "windows")
    m.add_profile("b", "", "windows", tags=["work"])
    m.add_profile("c", "", "windows")
    return m


def test_assign_tag_adds_to_selected(mgr):
    mgr.assign_tag(["a", "c"], "eu")
    assert "eu" in mgr.profiles["a"].tags
    assert "eu" in mgr.profiles["c"].tags
    assert "eu" not in mgr.profiles["b"].tags


def test_assign_tag_no_duplicate(mgr):
    mgr.assign_tag(["b"], "work")  # already has it
    assert mgr.profiles["b"].tags.count("work") == 1


def test_assign_tag_persists(mgr, tmp_path):
    mgr.assign_tag(["a"], "eu")
    import src.services.profile.manager as mod

    fresh = mod.ProfileManager()
    assert "eu" in fresh.profiles["a"].tags


def test_remove_tag_from_all(mgr):
    mgr.assign_tag(["a", "c"], "eu")
    mgr.remove_tag("eu")
    assert all("eu" not in p.tags for p in mgr.profiles.values())


def test_assign_tag_ignores_unknown_profile(mgr):
    # must not crash on a name that doesn't exist
    mgr.assign_tag(["ghost", "a"], "eu")
    assert "eu" in mgr.profiles["a"].tags


def test_assign_empty_tag_is_noop(mgr):
    mgr.assign_tag(["a"], "   ")
    assert mgr.profiles["a"].tags == []
