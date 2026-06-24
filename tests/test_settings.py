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
    data = json.loads(path.read_text())
    assert data["a"] == 1
    assert data["b"] == [1, 2]


def test_corrupt_file_treated_as_empty(tmp_path, monkeypatch):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    monkeypatch.setenv("PERSONA_SETTINGS_FILE", str(path))
    assert settings.get("x", "d") == "d"
