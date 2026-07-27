import json

import pytest

from src.core import settings


@pytest.fixture
def settings_path(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setenv("PERSONA_SETTINGS_FILE", str(path))
    return path


def test_corrupt_file_backed_up_before_defaults(settings_path):
    raw = "{not json"
    settings_path.write_text(raw, encoding="utf-8")

    assert settings.get("x", "d") == "d"

    backups = list(settings_path.parent.glob("settings.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == raw


def test_set_after_corruption_does_not_lose_backup(settings_path):
    raw = json.dumps({"onboarding_done": True})[:-1]  # truncated json
    settings_path.write_text(raw, encoding="utf-8")

    settings.set("theme", "dark")

    backups = list(settings_path.parent.glob("settings.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == raw
    assert json.loads(settings_path.read_text(encoding="utf-8")) == {
        "theme": "dark"
    }


def test_missing_file_creates_no_backup(settings_path):
    assert settings.get("x", "d") == "d"
    assert not list(settings_path.parent.glob("settings.json.corrupt-*"))


def test_non_dict_json_backed_up(settings_path):
    raw = json.dumps([1, 2, 3])
    settings_path.write_text(raw, encoding="utf-8")

    assert settings.get("x", "d") == "d"

    backups = list(settings_path.parent.glob("settings.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == raw


def test_transient_lock_does_not_read_as_absent(settings_path, monkeypatch):
    # #226: after a Windows update the relaunched persona can hit a momentary
    # lock on settings.json (the installer's /CLOSEAPPLICATIONS, an AV sweep, or
    # the relaunch purge still holding a handle). A single failed read must NOT
    # be treated as "no settings" — that read onboarding_done as False and
    # re-ran the full onboarding after every update. get() retries a locked file
    # and only then falls back, so the real value survives a transient lock.
    settings_path.write_text(json.dumps({"onboarding_done": True}), encoding="utf-8")

    real_open = open
    calls = {"n": 0}

    def flaky_open(path, *a, **kw):
        if str(path) == str(settings_path):
            calls["n"] += 1
            if calls["n"] <= 2:  # first reads hit the lock
                raise PermissionError("locked")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", flaky_open)

    assert settings.is_onboarding_done() is True
    assert calls["n"] >= 2  # it retried rather than giving up on the first fail
