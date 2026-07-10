import asyncio
import threading
from types import SimpleNamespace

from src.ui.app import App


class FakePage:
    """Stands in for ft.Page: records run_task marshaling and update calls,
    and drives the scheduled coroutine to completion like the session loop
    would."""

    def __init__(self):
        self.run_task_handlers = []
        self.update_calls = 0

    def run_task(self, handler, *args, **kwargs):
        self.run_task_handlers.append(handler)
        return asyncio.run(handler(*args, **kwargs))

    def update(self):
        self.update_calls += 1


class BrokenPage(FakePage):
    def run_task(self, handler, *args, **kwargs):
        raise RuntimeError("session is gone")


class FailingUpdatePage(FakePage):
    def update(self):
        raise RuntimeError("update blew up")


def make_app(page):
    app = App.__new__(App)
    app.page = page
    app.state = SimpleNamespace(_ui_update_lock=threading.Lock())
    app._ui_ready = threading.Event()
    app._ui_ready.set()
    app._ui_backlog = []
    app._ui_backlog_lock = threading.Lock()
    return app


def make_unready_app(page):
    """The session loop hasn't serviced a task yet — the first window is
    still building."""
    app = make_app(page)
    app._ui_ready.clear()
    return app


# --- _ui ---

def test_ui_without_page_calls_fn_directly():
    app = make_app(None)
    called = []
    app._ui(lambda: called.append(1))
    assert called == [1]


def test_ui_without_page_swallows_fn_error():
    app = make_app(None)

    def boom():
        raise RuntimeError("boom")

    app._ui(boom)  # must not raise


def test_ui_marshals_through_run_task_off_loop():
    page = FakePage()
    app = make_app(page)
    called = []
    app._ui(lambda: called.append(1))
    assert called == [1]
    assert len(page.run_task_handlers) == 1


def test_ui_on_session_loop_calls_fn_inline():
    page = FakePage()
    app = make_app(page)
    called = []

    async def main():
        app._ui(lambda: called.append(1))

    asyncio.run(main())
    assert called == [1]
    assert page.run_task_handlers == []


def test_ui_marshaled_fn_error_is_swallowed():
    page = FakePage()
    app = make_app(page)

    def boom():
        raise RuntimeError("boom")

    app._ui(boom)  # must not raise
    assert len(page.run_task_handlers) == 1


def test_ui_run_task_failure_is_swallowed():
    page = BrokenPage()
    app = make_app(page)
    called = []
    app._ui(lambda: called.append(1))  # must not raise
    assert called == []  # dropped, not run off-thread


def test_ui_from_worker_thread_marshals():
    page = FakePage()
    app = make_app(page)
    called = []
    t = threading.Thread(target=lambda: app._ui(lambda: called.append(1)))
    t.start()
    t.join()
    assert called == [1]
    assert len(page.run_task_handlers) == 1


# --- #124: marshaling before the session loop services tasks ---

class NotReadyPage(FakePage):
    """A page whose session loop is still building the first window: run_task
    accepts the handler but nothing ever services it — the #124 first-launch
    state."""

    def run_task(self, handler, *args, **kwargs):
        self.run_task_handlers.append(handler)  # accepted, never serviced


def test_ui_before_session_ready_defers_without_marshaling():
    page = NotReadyPage()
    app = make_unready_app(page)
    called = []
    app._ui(lambda: called.append(1))  # must neither block nor hit run_task
    assert called == []
    assert page.run_task_handlers == []


def test_ui_from_worker_thread_before_ready_defers():
    page = NotReadyPage()
    app = make_unready_app(page)
    called = []
    t = threading.Thread(target=lambda: app._ui(lambda: called.append(1)))
    t.start()
    t.join(5)
    assert not t.is_alive()
    assert called == []
    assert page.run_task_handlers == []


def test_session_ready_flushes_deferred_ui_in_order(monkeypatch):
    from src.ui import app as app_mod

    app = make_unready_app(FakePage())
    calls = []
    app._ui(lambda: calls.append("a"))
    app._ui(lambda: calls.append("b"))
    assert calls == []
    monkeypatch.setattr(app_mod.app_settings, "is_onboarding_done", lambda: False)
    asyncio.run(app._on_session_ready())
    assert calls == ["a", "b"]
    assert app._ui_ready.is_set()


def test_session_ready_flush_swallows_errors(monkeypatch):
    from src.ui import app as app_mod

    app = make_unready_app(FakePage())
    calls = []

    def boom():
        raise RuntimeError("boom")

    app._ui(boom)
    app._ui(lambda: calls.append(1))
    monkeypatch.setattr(app_mod.app_settings, "is_onboarding_done", lambda: False)
    asyncio.run(app._on_session_ready())  # must not raise
    assert calls == [1]


def test_session_ready_starts_engine_bootstraps(monkeypatch):
    # #124: the bootstrap threads stream progress/update marshals the moment
    # they start; kicked off inside _main they raced the first window build
    # and froze the app on a fresh install. They start from the ready hook.
    from src.ui import app as app_mod

    app = make_unready_app(FakePage())
    starts = []
    app._check_engine_async = lambda: starts.append("chromium")
    app._ensure_engine2_async = lambda: starts.append("firefox")
    monkeypatch.setattr(app_mod.app_settings, "is_onboarding_done", lambda: True)
    asyncio.run(app._on_session_ready())
    assert starts == ["chromium", "firefox"]


def test_session_ready_leaves_bootstraps_to_onboarding(monkeypatch):
    # During onboarding the engine download is user-driven (on_finish kicks
    # it); the ready hook must not start a second one underneath the dialog.
    from src.ui import app as app_mod

    app = make_unready_app(FakePage())
    starts = []
    app._check_engine_async = lambda: starts.append("chromium")
    app._ensure_engine2_async = lambda: starts.append("firefox")
    monkeypatch.setattr(app_mod.app_settings, "is_onboarding_done", lambda: False)
    asyncio.run(app._on_session_ready())
    assert starts == []


# --- _finish_startup: reveal + splash gates on real loading ---

def test_finish_startup_reveals_window_and_loads_behind_the_splash(monkeypatch):
    # The window ships hidden (hide_window_on_start): the reveal must be the
    # first thing the startup task does, with the splash already the page's
    # first frame. And the splash must cover the real loading work — profile
    # cards, engine text, the live running snapshot — so the swapped-in UI is
    # finished instead of popping those in after the splash is gone.
    from src.ui import app as app_mod

    events = []

    class Page:
        def __init__(self):
            self.window = SimpleNamespace(visible=False)
            self.services = []
            self.controls = ["splash"]

        def update(self):
            events.append(("update", self.window.visible))

        def add(self, c):
            events.append(("add", c))

        def run_task(self, h, *a, **k):
            events.append(("task", getattr(h, "__name__", "")))

    page = Page()
    app = App.__new__(App)
    app.page = page
    app.pm = SimpleNamespace()
    app._change_page = lambda delta: None
    app.state = SimpleNamespace(_last_running_snapshot=None)
    app.bl = SimpleNamespace(running_profile_names=lambda: {"p1"})
    app._splash = SimpleNamespace(stop=lambda: events.append("splash_stop"))
    monkeypatch.setattr(app_mod, "build_ui_refs", lambda **kw: "refs")
    app._build_root_layout = lambda refs: "root"
    app._render_active_page = lambda: events.append("render")
    app._refresh_profiles = lambda: events.append("profiles")
    app._refresh_engine_text = lambda: events.append("engines")
    app._show_onboarding = lambda: None
    app._check_app_update_async = lambda: None
    app._check_engines_periodic = lambda: None
    app._start_server_if_enabled = lambda: None
    app._reconcile_started = True

    async def ready():
        events.append("session_ready")

    app._on_session_ready = ready
    monkeypatch.setattr(app_mod.splash_mod, "MIN_SECONDS", 0)
    monkeypatch.setattr(app_mod.app_settings, "is_onboarding_done", lambda: True)

    asyncio.run(app._finish_startup())

    # the hidden window is revealed before anything else runs
    assert page.window.visible is True
    assert events[0] == ("update", True)
    # the data the first screen shows loads BEFORE the root swap…
    reveal = events.index(("add", "root"))
    assert events.index("profiles") < reveal
    assert events.index("engines") < reveal
    assert events.index("splash_stop") < reveal
    # …including the live-status snapshot the reconcile loop diffs against
    assert app.state._last_running_snapshot == {"p1"}
    assert "session_ready" in events


# --- _safe_update ---

def test_safe_update_marshals_page_update():
    page = FakePage()
    app = make_app(page)
    app._safe_update()
    assert page.update_calls == 1
    assert len(page.run_task_handlers) == 1


def test_safe_update_without_page_is_noop():
    app = make_app(None)
    app._safe_update()  # must not raise


def test_safe_update_swallows_update_error():
    page = FailingUpdatePage()
    app = make_app(page)
    app._safe_update()  # must not raise
    assert len(page.run_task_handlers) == 1


def test_safe_update_holds_ui_update_lock():
    class LockCheckingPage(FakePage):
        def __init__(self, app_ref):
            super().__init__()
            self.app_ref = app_ref
            self.lock_held = None

        def update(self):
            super().update()
            self.lock_held = not self.app_ref.state._ui_update_lock.acquire(
                blocking=False
            )

    app = make_app(None)
    page = LockCheckingPage(app)
    app.page = page
    app._safe_update()
    assert page.lock_held is True


# --- _apply_update (self-update refusal recovery) ---

def test_apply_update_clears_staged_when_installer_deleted(monkeypatch, tmp_path):
    # When apply_and_restart refuses a corrupt installer it DELETES the staged
    # file and returns. _apply_update must then clear _update_staged so the
    # periodic checker (gated on `not self._update_staged`) can re-download.
    from src.ui import app as app_mod

    app = make_app(None)
    app._update_staged = str(tmp_path / "gone.exe")   # never created → "deleted"
    app._app_update_status = "ready"
    app._refresh_sidebar = lambda: None
    app._log = lambda *_: None
    monkeypatch.setattr(app_mod.app_update, "apply_and_restart", lambda *a, **k: False)

    app._apply_update(app._update_staged)
    assert app._update_staged == ""          # cleared → re-download possible
    assert app._app_update_status == ""


def test_apply_update_keeps_staged_when_file_still_present(monkeypatch, tmp_path):
    # If the installer is still on disk (relaunch failed, not a verify refusal),
    # keep the "ready" state so the restart button still works.
    from src.ui import app as app_mod

    staged = tmp_path / "setup.exe"
    staged.write_bytes(b"MZ")
    app = make_app(None)
    app._update_staged = str(staged)
    app._app_update_status = "ready"
    app._refresh_sidebar = lambda: None
    app._log = lambda *_: None
    monkeypatch.setattr(app_mod.app_update, "apply_and_restart", lambda *a, **k: False)

    app._apply_update(str(staged))
    assert app._app_update_status == "ready"   # still offer restart
