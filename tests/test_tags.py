import pytest

from src.models.profile import Profile
from src.services.profile.filter import filter_profiles, all_tags
from src.services.profile.manager import ProfileManager


def _p(name, tags=None):
    return Profile(name=name, tags=tags or [])


# --- filter by tag ---


def test_filter_matches_tag():
    profiles = [_p("a", ["work"]), _p("b", ["personal"]), _p("c", ["work", "eu"])]
    out = filter_profiles(profiles, "work")
    assert {p.name for p in out} == {"a", "c"}


def test_filter_tag_case_insensitive():
    out = filter_profiles([_p("a", ["Work"])], "work")
    assert [p.name for p in out] == ["a"]


def test_all_tags_collects_unique_sorted():
    profiles = [_p("a", ["work", "eu"]), _p("b", ["personal", "work"])]
    assert all_tags(profiles) == ["eu", "personal", "work"]


def test_all_tags_empty():
    assert all_tags([_p("a")]) == []


def test_all_tags_dedups_case_insensitively():
    # audit5 LOW: "Work" and "work" must count as ONE tag — the chip cloud
    # counted them separately (case-sensitive) while filtering matched both
    # (case-insensitive), so a click filtered profiles the chip didn't count.
    profiles = [_p("a", ["Work"]), _p("b", ["work"]), _p("c", ["EU"])]
    tags = all_tags(profiles)
    # one canonical entry per case-insensitive tag
    assert len(tags) == 2
    assert {t.lower() for t in tags} == {"work", "eu"}


# --- update_profile carries tags ---


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    import src.core.config as cfg
    import src.services.profile.manager as mod

    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    for m in (cfg, mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
    return ProfileManager()


def test_update_profile_sets_tags(mgr):
    mgr.add_profile("p1", "", "windows")
    assert mgr.update_profile(
        "p1", "p1", "", "windows", new_tags=["work", "eu"]
    )
    assert mgr.profiles["p1"].tags == ["work", "eu"]


def test_update_profile_tags_none_keeps_existing(mgr):
    mgr.add_profile("p1", "", "windows", tags=["keep"])
    mgr.update_profile("p1", "p1", "", "windows")
    assert mgr.profiles["p1"].tags == ["keep"]


def test_remove_tag_is_case_insensitive(mgr):
    # audit5 LOW: removing a tag via a chip must strip every case variant, or a
    # "Work"/"work" mix leaves the other behind after the ✕.
    mgr.add_profile("a", "", "windows", tags=["Work", "eu"])
    mgr.add_profile("b", "", "windows", tags=["work"])
    changed = mgr.remove_tag("work")
    assert changed == 2
    assert mgr.profiles["a"].tags == ["eu"]
    assert mgr.profiles["b"].tags == []
