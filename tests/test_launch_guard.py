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
    def __init__(self, name, engine="chromium", os_type="windows"):
        self.name = name
        self.engine = engine
        self.os_type = os_type


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


def test_firefox_launch_check_never_downloads(monkeypatch):
    # #119: the launch path must only CHECK for the Firefox engine — never call
    # ensure_invisible_installed, which downloads ~118MB over Tor and blocks the
    # launch for minutes. Downloading belongs to the sidebar job only.
    from src.services.browser import invisible_launch as il

    def _boom(*a, **k):
        raise AssertionError("launch path must not trigger the engine download")

    monkeypatch.setattr(il, "ensure_invisible_installed", _boom)
    monkeypatch.setattr(il, "is_invisible_installed", lambda: True)
    bl = _BL()
    browser_actions.launch_or_stop(
        "p1", _PM(_Profile("p1", engine="firefox")), bl, AppState(), lambda _: None
    )
    assert bl.started is True


def test_firefox_launch_blocked_when_engine_missing(monkeypatch):
    from src.services.browser import invisible_launch as il

    monkeypatch.setattr(il, "is_invisible_installed", lambda: False)
    bl = _BL()
    logs = []
    browser_actions.launch_or_stop(
        "p1", _PM(_Profile("p1", engine="firefox")), bl, AppState(), logs.append
    )
    assert bl.started is False
    assert any("engine" in m.lower() for m in logs)


def test_mobile_firefox_profile_does_not_require_firefox_engine(monkeypatch):
    # A legacy mobile profile stored engine="firefox" actually launches chromium
    # (effective_engine downgrades it), so the guard must NOT demand the Firefox
    # engine be installed — it should check chromium and launch.
    from src.services.browser import invisible_launch as il

    monkeypatch.setattr(il, "is_invisible_installed", lambda: False)  # FF absent
    monkeypatch.setattr(browser_actions.engine, "is_installed", lambda: True)  # chromium present
    bl = _BL()
    browser_actions.launch_or_stop(
        "p1",
        _PM(_Profile("p1", engine="firefox", os_type="android")),
        bl, AppState(), lambda _: None,
    )
    assert bl.started is True  # launched as chromium, not blocked on the FF engine
