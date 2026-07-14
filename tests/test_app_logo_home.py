import asyncio
import threading
from types import SimpleNamespace

import flet as ft

from src.ui import app as app_mod
from src.ui.app import App


def _walk(control):
    yield control
    for attr in ("content", "controls", "actions"):
        v = getattr(control, attr, None)
        if v is None:
            continue
        children = v if isinstance(v, list) else [v]
        for child in children:
            if isinstance(child, ft.BaseControl):
                yield from _walk(child)


class FakePage:
    """Stands in for ft.Page with an overlay list; run_task drives the
    scheduled coroutine to completion like the session loop would."""

    def __init__(self):
        self.overlay = []
        self.update_calls = 0
        self.run_task_handlers = []

    def run_task(self, handler, *args, **kwargs):
        self.run_task_handlers.append(handler)
        return asyncio.run(handler(*args, **kwargs))

    def update(self):
        self.update_calls += 1


class FakeFlash:
    def __init__(self):
        self.control = ft.Container()
        self.played_while_on_overlay = None
        self.page = None
        self.resets = 0

    def reset(self):
        self.resets += 1

    async def play(self, cancelled=None):
        self.played_while_on_overlay = (
            self.page is not None and self.control in self.page.overlay
        )


class BoomFlash(FakeFlash):
    async def play(self, cancelled=None):
        await super().play(cancelled)
        raise RuntimeError("boom")


def make_app(page, active_page="network"):
    app = App.__new__(App)
    app.page = page
    app.state = SimpleNamespace(_ui_update_lock=threading.Lock())
    app._ui_ready = threading.Event()
    app._ui_ready.set()
    app._ui_backlog = []
    app._ui_backlog_lock = threading.Lock()
    app._active_page = active_page
    app._sidebar_host = ft.Container()
    app._scan_flash = None
    app._scan_gen = 0
    calls = []
    app._build_sidebar = lambda: ft.Text("sidebar")
    app._render_active_page = lambda: calls.append("render")
    app._refresh_profiles = lambda: calls.append("refresh")
    return app, calls


def use_flash(monkeypatch, page, flash_cls=FakeFlash):
    made = []

    def factory():
        flash = flash_cls()
        flash.page = page
        made.append(flash)
        return flash

    monkeypatch.setattr(app_mod.splash_mod, "ScanFlash", factory)
    return made


def test_logo_click_goes_home_and_refreshes(monkeypatch):
    page = FakePage()
    app, calls = make_app(page, active_page="network")
    use_flash(monkeypatch, page)
    app._on_logo_click()
    assert app._active_page == "profiles"
    assert calls == ["render", "refresh"]
    assert isinstance(app._sidebar_host.content, ft.Text)


def test_logo_click_refreshes_even_when_already_home(monkeypatch):
    page = FakePage()
    app, calls = make_app(page, active_page="profiles")
    use_flash(monkeypatch, page)
    app._on_logo_click()
    assert app._active_page == "profiles"
    assert "refresh" in calls


def test_logo_click_shows_one_reusable_flash(monkeypatch):
    page = FakePage()
    app, _calls = make_app(page)
    made = use_flash(monkeypatch, page)
    app._on_logo_click()
    assert len(made) == 1
    # the flash plays while pinned on the overlay...
    assert made[0].played_while_on_overlay is True
    # ...and STAYS on the overlay, reused for the next click (no re-add churn)
    assert page.overlay == [made[0].control]


def test_rapid_clicks_reuse_one_flash_and_restart_it(monkeypatch):
    # spamming the logo must NOT stack beams: one ScanFlash ever, each click
    # resets it to the top and bumps the generation token.
    page = FakePage()
    app, _calls = make_app(page)
    made = use_flash(monkeypatch, page)
    app._on_logo_click()
    app._on_logo_click()
    app._on_logo_click()
    assert len(made) == 1                 # only one flash object, ever
    assert len(page.overlay) == 1         # only one beam on the overlay
    assert made[0].resets == 3            # each click re-homed it to the top
    assert app._scan_gen == 3             # generation bumped per click


def test_flash_failure_does_not_break_click(monkeypatch):
    page = FakePage()
    app, _calls = make_app(page)
    made = use_flash(monkeypatch, page, BoomFlash)
    app._on_logo_click()  # a raising play() must not propagate
    assert made[0].played_while_on_overlay is True


def test_logo_click_without_page_still_goes_home(monkeypatch):
    app, calls = make_app(None, active_page="tags")
    app._on_logo_click()
    assert app._active_page == "profiles"
    assert calls == ["render", "refresh"]


def test_build_sidebar_wires_logo_click():
    app = App.__new__(App)
    app._active_page = "profiles"
    app._navigate = lambda k: None
    app.refs = SimpleNamespace(
        log_toggle_btn=ft.TextButton(content=ft.Text("log")),
        log_column=ft.Column(),
    )
    app.h = SimpleNamespace(open_log_fullscreen=lambda: None)
    app._build_engines_panel = lambda: ft.Text("engines")
    app._build_version_panel = lambda: ft.Text("version")
    fired = []
    app._on_logo_click = lambda: fired.append(1)
    sidebar = app._build_sidebar()
    header = next(
        c for c in _walk(sidebar) if isinstance(c, ft.GestureDetector)
    )
    header.content.on_click(None)
    assert fired == [1]
