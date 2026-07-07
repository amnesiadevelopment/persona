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
