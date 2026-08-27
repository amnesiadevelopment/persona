import json

import pytest

from src.core import settings


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_SETTINGS_FILE", str(tmp_path / "settings.json"))
    yield


def test_onboarding_not_done_by_default():
    assert settings.is_onboarding_done() is False


def test_mark_onboarding_done_persists():
    settings.mark_onboarding_done()
    assert settings.is_onboarding_done() is True


def test_mark_survives_reload(tmp_path):
    settings.mark_onboarding_done()
    # a fresh read (no in-memory cache) still sees it
    assert settings.is_onboarding_done() is True


def test_get_returns_default_when_absent():
    assert settings.get("nope", "fallback") == "fallback"


def test_set_then_get():
    settings.set("theme", "dark")
    assert settings.get("theme") == "dark"


def test_set_writes_valid_json(tmp_path, monkeypatch):
    path = tmp_path / "s.json"
    monkeypatch.setenv("PERSONA_SETTINGS_FILE", str(path))
    settings.set("a", 1)
    settings.set("b", [1, 2])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["a"] == 1
    assert data["b"] == [1, 2]


def test_corrupt_file_treated_as_empty(tmp_path, monkeypatch):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("PERSONA_SETTINGS_FILE", str(path))
    assert settings.get("x", "d") == "d"


def test_set_never_loses_existing_keys_when_read_transiently_fails(
    tmp_path, monkeypatch
):
    # #214: after a Windows auto-update the "Welcome" onboarding re-appeared —
    # onboarding_done was being LOST. If a startup set() (server_enabled /
    # auto_update) runs while _load() transiently can't read an existing file
    # (a permission blip / race at relaunch), the old code overwrote the file
    # with just the one key, dropping onboarding_done. A set() must NEVER
    # clobber a settings file that exists on disk but couldn't be read this
    # instant — it must back off rather than persist a partial.
    path = tmp_path / "settings.json"
    monkeypatch.setenv("PERSONA_SETTINGS_FILE", str(path))
    settings.mark_onboarding_done()
    assert settings.is_onboarding_done() is True

    # Simulate a transient read failure on the NEXT read of the settings file
    # (it's still there and valid on disk), then a set() during that window.
    import builtins

    real_open = builtins.open
    state = {"fail_next": True}

    def flaky_open(p, *a, **k):
        if str(p) == str(path) and state["fail_next"]:
            state["fail_next"] = False
            raise OSError("transient read failure")
        return real_open(p, *a, **k)

    monkeypatch.setattr(builtins, "open", flaky_open)
    settings.set_server_enabled(True)  # its _read fails → must back off, not clobber
    monkeypatch.setattr(builtins, "open", real_open)

    # onboarding_done must survive — the transient read must not have wiped it.
    assert settings.is_onboarding_done() is True


def test_settings_path_follows_persona_home(tmp_path, monkeypatch):
    # audit5 #3: settings.json must live under PERSONA_HOME like every other data
    # file, not a hardcoded ~/.persona — otherwise a portable/isolated layout
    # splits onboarding/changelog state off from the rest of the data.
    import os

    from src.core import config

    monkeypatch.delenv("PERSONA_SETTINGS_FILE", raising=False)
    home = str(tmp_path / "portable-home")
    monkeypatch.setattr(config, "PERSONA_HOME", home)
    assert settings._path() == os.path.join(home, "settings.json")


def test_explicit_settings_file_env_still_wins(tmp_path, monkeypatch):
    from src.core import config

    monkeypatch.setattr(config, "PERSONA_HOME", str(tmp_path / "home"))
    explicit = str(tmp_path / "explicit.json")
    monkeypatch.setenv("PERSONA_SETTINGS_FILE", explicit)
    assert settings._path() == explicit


def test_last_seen_version_absent_by_default():
    assert settings.last_seen_version() == ""


def test_last_seen_version_persists():
    settings.set_last_seen_version("2.5.1")
    assert settings.last_seen_version() == "2.5.1"
