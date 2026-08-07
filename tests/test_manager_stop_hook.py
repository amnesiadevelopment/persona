"""delete_profile and wipe_all_profiles must stop a running browser (via the
stop hook the app wires to launcher.stop_profile) BEFORE rmtree'ing the data
dir — otherwise the dir is deleted out from under a live engine."""
import pytest


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    import src.core.config as cfg
    import src.services.profile.manager as mod
    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    for m in (cfg, mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
    from src.services.profile.manager import ProfileManager
    return ProfileManager()


def test_delete_calls_stop_hook_before_rmtree(mgr):
    stopped = []
    mgr.set_stop_hook(stopped.append)
    mgr.add_profile("a", "", "windows")
    mgr.delete_profile("a")
    assert stopped == ["a"]
    assert "a" not in mgr.profiles


def test_wipe_calls_stop_hook_for_each(mgr):
    stopped = []
    mgr.set_stop_hook(stopped.append)
    mgr.add_profile("a", "", "windows")
    mgr.add_profile("b", "", "windows")
    n = mgr.wipe_all_profiles()
    assert n == 2
    assert set(stopped) == {"a", "b"}


def test_no_hook_is_safe(mgr):
    mgr.add_profile("a", "", "windows")
    assert mgr.delete_profile("a") is True  # no hook set → no crash
