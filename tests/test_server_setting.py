import pytest

from src.core import settings


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_SETTINGS_FILE", str(tmp_path / "settings.json"))
    yield


def test_server_disabled_by_default():
    assert settings.is_server_enabled() is False


def test_enable_server_persists():
    settings.set_server_enabled(True)
    assert settings.is_server_enabled() is True


def test_disable_server():
    settings.set_server_enabled(True)
    settings.set_server_enabled(False)
    assert settings.is_server_enabled() is False
