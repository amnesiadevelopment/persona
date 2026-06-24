from src.ui.actions import browser as browser_actions
from src.ui.state import AppState


class _PM:
    def __init__(self, profile):
        self.profiles = {profile.name: profile} if profile else {}


class _BL:
    def __init__(self):
        self.started = False

    def is_running(self, name):
        return False

    def start_thread(self, *a, **k):
        self.started = True


class _Profile:
    def __init__(self, name):
        self.name = name


def test_launch_blocked_when_engine_missing(monkeypatch):
    monkeypatch.setattr(browser_actions.engine, "is_installed", lambda: False)
    bl = _BL()
    logs = []
    browser_actions.launch_or_stop(
        "p1", _PM(_Profile("p1")), bl, AppState(), logs.append
    )
    assert bl.started is False
    assert any("engine" in m.lower() for m in logs)


def test_launch_proceeds_when_engine_present(monkeypatch):
    monkeypatch.setattr(browser_actions.engine, "is_installed", lambda: True)
    bl = _BL()
    browser_actions.launch_or_stop(
        "p1", _PM(_Profile("p1")), bl, AppState(), lambda _: None
    )
    assert bl.started is True
