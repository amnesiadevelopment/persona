import importlib

import pytest

from src.models.profile import Profile
from src.services.browser import camoufox_runner as R


def test_profile_engine_default_chromium():
    assert Profile(name="x").engine == "chromium"
    assert "engine" in Profile(name="x").to_dict()


def test_proxy_dict_socks():
    d = R._proxy_dict("socks5://u:p@1.2.3.4:1080")
    assert d == {"server": "socks5://1.2.3.4:1080", "username": "u", "password": "p"}


def test_proxy_dict_no_auth():
    d = R._proxy_dict("http://1.2.3.4:8080")
    assert d == {"server": "http://1.2.3.4:8080"}


def test_proxy_dict_empty_is_none():
    assert R._proxy_dict("") is None


@pytest.fixture
def pm(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_PROFILES_FILE", str(tmp_path / "p.json"))
    monkeypatch.setenv("PERSONA_DATA_DIR", str(tmp_path / "data"))
    from src.core import config as cfg
    importlib.reload(cfg)
    from src.services.profile import manager as mgr
    importlib.reload(mgr)
    return mgr.ProfileManager()


def test_add_profile_with_engine_persists(pm):
    assert pm.add_profile("p1", "", "windows", engine="camoufox")
    assert pm.profiles["p1"].engine == "camoufox"
    from src.services.profile import manager as mgr
    pm2 = mgr.ProfileManager()
    assert pm2.profiles["p1"].engine == "camoufox"


def test_update_profile_engine(pm):
    pm.add_profile("p2", "", "windows")
    assert pm.profiles["p2"].engine == "chromium"
    assert pm.update_profile("p2", "p2", "", "windows", new_engine="camoufox")
    assert pm.profiles["p2"].engine == "camoufox"
