import importlib
import os
import sys


def _reload_config(monkeypatch, **env):
    for k in [
        "PERSONA_HOME", "PERSONA_PROFILES_FILE", "PERSONA_PROXIES_FILE",
        "PERSONA_BOOKMARKS_FILE", "PERSONA_DATA_DIR", "PERSONA_LOG_DIR",
        "PERSONA_ENGINE_DIR",
    ]:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import src.core.config as cfg
    return importlib.reload(cfg)


def test_defaults_under_persona_home(monkeypatch, tmp_path):
    cfg = _reload_config(monkeypatch, PERSONA_HOME=str(tmp_path))
    assert cfg.PROFILES_FILE == os.path.join(str(tmp_path), "profiles.json")
    assert cfg.PROXIES_FILE == os.path.join(str(tmp_path), "proxies.json")
    assert cfg.BOOKMARKS_FILE == os.path.join(str(tmp_path), "bookmarks.json")
    assert cfg.DATA_DIR == os.path.join(str(tmp_path), "persona_data")
    assert cfg.LOG_DIR == os.path.join(str(tmp_path), "logs")
    assert cfg.ENGINE_DIR == os.path.join(str(tmp_path), "engine")


def test_home_is_created(monkeypatch, tmp_path):
    home = tmp_path / "fresh"
    _reload_config(monkeypatch, PERSONA_HOME=str(home))
    assert home.is_dir()


def test_explicit_file_override_wins(monkeypatch, tmp_path):
    cfg = _reload_config(
        monkeypatch,
        PERSONA_HOME=str(tmp_path),
        PERSONA_PROFILES_FILE="/custom/p.json",
    )
    assert cfg.PROFILES_FILE == "/custom/p.json"
    # others still under home
    assert cfg.DATA_DIR == os.path.join(str(tmp_path), "persona_data")


def test_default_home_is_dot_persona(monkeypatch):
    cfg = _reload_config(monkeypatch)
    assert cfg.PERSONA_HOME == os.path.expanduser("~/.persona")
