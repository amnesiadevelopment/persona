"""APIServer runs the FastAPI app in a daemon thread. Cover start/stop/is_running
without binding a real socket by faking uvicorn's Server."""
import threading

import pytest

from src.api.server import APIServer


class _FakeUvicornServer:
    instances = []

    def __init__(self, config):
        self.config = config
        self.should_exit = False
        self._exit = threading.Event()
        _FakeUvicornServer.instances.append(self)

    def run(self):
        # Block until stop() flips should_exit, mimicking uvicorn's loop.
        while not self.should_exit:
            self._exit.wait(0.01)


@pytest.fixture
def fake_uvicorn(monkeypatch):
    _FakeUvicornServer.instances.clear()
    import src.api.server as srv
    monkeypatch.setattr(srv.uvicorn, "Server", _FakeUvicornServer)
    monkeypatch.setattr(srv.uvicorn, "Config", lambda **kw: kw)
    return srv


def test_not_running_before_start(fake_uvicorn):
    s = APIServer(app=object())
    assert s.is_running is False


def test_start_runs_and_stop_exits(fake_uvicorn):
    s = APIServer(app=object())
    s.start()
    assert s.is_running is True
    s.stop()
    # the fake server observes should_exit and its run() loop ends
    _FakeUvicornServer.instances[-1]._thread_join = None
    # give the daemon thread a beat to exit
    for _ in range(200):
        if not s.is_running:
            break
        threading.Event().wait(0.01)
    assert s.is_running is False


def test_stop_before_start_is_safe(fake_uvicorn):
    s = APIServer(app=object())
    s.stop()  # must not raise when never started
    assert s.is_running is False
