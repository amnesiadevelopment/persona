"""audit5 #4: deleting or renaming a bookmark pool must not leave a dangling
Profile.bookmark_pool — mirror of clear_proxy for the proxy subsystem. A stale
pool name made resolve_selection return [] (empty toolbar), not the defaults."""
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


def test_clear_bookmark_pool_drops_reference(mgr):
    mgr.add_profile("uses-1", "", "windows", bookmark_pool="Work")
    mgr.add_profile("uses-2", "", "windows", bookmark_pool="Work")
    mgr.add_profile("other", "", "windows", bookmark_pool="Play")
    mgr.add_profile("none", "", "windows")

    changed = mgr.clear_bookmark_pool("Work")

    assert changed == 2
    assert mgr.profiles["uses-1"].bookmark_pool is None
    assert mgr.profiles["uses-2"].bookmark_pool is None
    assert mgr.profiles["other"].bookmark_pool == "Play"
    # persisted
    assert ProfileManager().profiles["uses-1"].bookmark_pool is None


def test_clear_bookmark_pool_none_matching_is_zero(mgr):
    mgr.add_profile("a", "", "windows", bookmark_pool="Play")
    assert mgr.clear_bookmark_pool("Work") == 0


def test_rename_bookmark_pool_propagates(mgr):
    mgr.add_profile("uses-1", "", "windows", bookmark_pool="Work")
    mgr.add_profile("uses-2", "", "windows", bookmark_pool="Work")
    mgr.add_profile("other", "", "windows", bookmark_pool="Play")

    changed = mgr.rename_bookmark_pool("Work", "Job")

    assert changed == 2
    assert mgr.profiles["uses-1"].bookmark_pool == "Job"
    assert mgr.profiles["uses-2"].bookmark_pool == "Job"
    assert mgr.profiles["other"].bookmark_pool == "Play"
    assert ProfileManager().profiles["uses-1"].bookmark_pool == "Job"


def test_rename_bookmark_pool_noop_when_same_name(mgr):
    mgr.add_profile("a", "", "windows", bookmark_pool="Work")
    assert mgr.rename_bookmark_pool("Work", "Work") == 0
    assert mgr.profiles["a"].bookmark_pool == "Work"


def test_set_notes_persists_under_lock(mgr):
    # audit5 LOW: the inline notes edit must go through the manager lock, not
    # mutate the Profile + save_profiles() directly (the one write escaping the
    # lock discipline).
    mgr.add_profile("a", "", "windows")
    assert mgr.set_notes("a", "primary account") is True
    assert mgr.profiles["a"].notes == "primary account"
    from src.services.profile.manager import ProfileManager
    assert ProfileManager().profiles["a"].notes == "primary account"


def test_set_notes_noop_when_unchanged(mgr):
    mgr.add_profile("a", "", "windows", )
    mgr.set_notes("a", "x")
    assert mgr.set_notes("a", "x") is False


def test_set_notes_unknown_profile_false(mgr):
    assert mgr.set_notes("ghost", "x") is False
