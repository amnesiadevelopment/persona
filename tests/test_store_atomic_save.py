"""Profile and proxy stores must save atomically — no partial file, no leftover
temp — so a crash mid-save can't lose everything.
"""
import os

import pytest


@pytest.fixture
def pm(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_PROFILES_FILE", str(tmp_path / "p.json"))
    monkeypatch.setenv("PERSONA_DATA_DIR", str(tmp_path / "data"))
    import importlib

    from src.core import config as cfg
    importlib.reload(cfg)
    from src.services.profile import manager as mgr
    importlib.reload(mgr)
    return mgr.ProfileManager(), tmp_path


def test_profiles_save_leaves_no_temp(pm):
    manager, tmp_path = pm
    manager.add_profile("a", "", "windows")
    manager.add_profile("b", "", "macos")
    files = os.listdir(tmp_path)
    assert "p.json" in files
    assert not any(f.endswith(".new") for f in files)


def test_proxy_save_leaves_no_temp(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_PROXIES_FILE", str(tmp_path / "px.json"))
    import importlib

    from src.core import config as cfg
    importlib.reload(cfg)
    from src.services.proxy import store as st
    importlib.reload(st)
    s = st.ProxyStore()
    s.add("mob", "socks5://u:p@h:1")
    files = os.listdir(tmp_path)
    assert "px.json" in files
    assert not any(f.endswith(".new") for f in files)
