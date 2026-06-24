import pytest

from src.core import settings
from src.ui.app import App


class FakeServer:
    def __init__(self):
        self.running = False
        self.starts = 0
        self.stops = 0

    def start(self):
        self.running = True
        self.starts += 1

    def stop(self):
        self.running = False
        self.stops += 1

    @property
    def is_running(self):
        return self.running


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_SETTINGS_FILE", str(tmp_path / "s.json"))
    monkeypatch.setenv("PERSONA_PROFILES_FILE", str(tmp_path / "p.json"))
    monkeypatch.setenv("PERSONA_PROXIES_FILE", str(tmp_path / "x.json"))
    monkeypatch.setenv("PERSONA_BOOKMARKS_FILE", str(tmp_path / "b.json"))
    monkeypatch.setenv("PERSONA_DATA_DIR", str(tmp_path / "data"))
    yield


def _app(server):
    a = App(api_server=server)
    # avoid touching a real Flet page
    a._render_active_page = lambda: None
    a._safe_update = lambda: None
    a._log = lambda *_: None
    return a


def test_server_off_means_not_running():
    s = FakeServer()
    a = _app(s)
    assert a._server_running() is False


def test_set_server_starts_and_persists():
    s = FakeServer()
    a = _app(s)
    a._set_server(True)
    assert s.running is True
    assert settings.is_server_enabled() is True
    assert a._server_running() is True


def test_set_server_stops_and_persists():
    s = FakeServer()
    a = _app(s)
    a._set_server(True)
    a._set_server(False)
    assert s.running is False
    assert settings.is_server_enabled() is False


def test_idempotent_enable_does_not_double_start():
    s = FakeServer()
    a = _app(s)
    a._set_server(True)
    a._set_server(True)
    assert s.starts == 1


def test_autostart_when_flag_set():
    settings.set_server_enabled(True)
    s = FakeServer()
    a = _app(s)
    a._start_server_if_enabled()
    assert s.running is True


def test_no_autostart_when_flag_unset():
    s = FakeServer()
    a = _app(s)
    a._start_server_if_enabled()
    assert s.running is False
