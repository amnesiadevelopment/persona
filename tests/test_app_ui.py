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


# --- _finish_startup: splash gates on real loading ---

def test_finish_startup_loads_behind_the_splash(monkeypatch):
    # The splash must cover the real loading work — profile cards, engine
    # text, the live running snapshot — so the swapped-in UI is finished
    # instead of popping those in after the splash is gone.
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
    app._show_startup_notice = lambda: None
    app._check_app_update_async = lambda: None
    app._check_engines_periodic = lambda: None
    app._auto_update_engine2_async = lambda: None
    app._start_server_if_enabled = lambda: None
    app._reconcile_started = True

    async def ready():
        events.append("session_ready")

    app._on_session_ready = ready
    monkeypatch.setattr(app_mod.splash_mod, "MIN_SECONDS", 0)
    monkeypatch.setattr(app_mod.app_settings, "is_onboarding_done", lambda: True)

    asyncio.run(app._finish_startup())

    # _finish_startup's SUCCESS path doesn't touch visibility — _main already
    # revealed the window once the splash was up (the fixture leaves it at the
    # False sentinel here because this test drives _finish_startup directly).
    assert page.window.visible is False  # untouched sentinel from the fixture
    # the data the first screen shows loads BEFORE the root swap…
    swap = events.index(("add", "root"))
    assert events.index("profiles") < swap
    assert events.index("engines") < swap
    assert events.index("splash_stop") < swap
    # …including the live-status snapshot the reconcile loop diffs against
    assert app.state._last_running_snapshot == {"p1"}
    assert "session_ready" in events


def test_finish_startup_failure_paints_a_readable_error(monkeypatch):
    # A startup failure must not leave the splash sweeping forever over a broken
    # app: any exception while building the first screen swaps in a readable
    # error instead of raising out of the task. And because the window starts
    # HIDDEN (hide_window_on_start), the error path must FORCE the window visible
    # first — a hidden error screen with a live process would be the invisible
    # zombie the old ban feared.
    from src.ui import app as app_mod

    events = []

    class Page:
        def __init__(self):
            self.window = SimpleNamespace(visible=False)
            self.services = []
            self.controls = ["splash"]

        def update(self):
            events.append("update")

        def add(self, c):
            events.append(("add", c))

        def run_task(self, h, *a, **k):
            pass

    page = Page()
    app = App.__new__(App)
    app.page = page
    app.pm = SimpleNamespace()
    app._change_page = lambda delta: None
    app._splash = SimpleNamespace(stop=lambda: events.append("splash_stop"))

    def boom(**kw):
        raise RuntimeError("torn extraction")

    monkeypatch.setattr(app_mod, "build_ui_refs", boom)

    asyncio.run(app._finish_startup())  # must not raise

    assert "splash_stop" in events
    # the window was forced visible before the error was painted
    assert page.window.visible is True
    added = [ev[1] for ev in events if isinstance(ev, tuple) and ev[0] == "add"]
    assert added and "torn extraction" in added[0].value


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


def _make_engine2_app(monkeypatch, *, installed, current, latest, compatible):
    """An App stub wired so _auto_update_engine2_async can run: fake the
    engine module + record whether the download path was triggered."""
    from src.services.browser import invisible_launch as inv
    from src.services.engine import firefox as ff

    monkeypatch.setattr(inv, "is_invisible_installed", lambda: installed)
    monkeypatch.setattr(ff, "fetch_latest", lambda: (latest, compatible))
    monkeypatch.setattr(ff, "current_version", lambda: current)

    app = make_app(None)
    app._engine2_busy = False
    app._engine2_latest = ""
    app._engine2_compatible = True
    app._engine2_status = ""
    logs = []
    app._log = lambda m: logs.append(m)
    app._refresh_engine_text = lambda *a, **k: None
    downloaded = []
    app._update_engine2_async = lambda: downloaded.append(app._engine2_latest)
    return app, logs, downloaded


def test_auto_update_engine2_downloads_when_stale(monkeypatch):
    # A stale engine (firefox-13 installed, firefox-15 available + compatible)
    # must auto-download without a click — the flat-emoji upgrade path (#183).
    app, logs, downloaded = _make_engine2_app(
        monkeypatch, installed=True, current="firefox-13",
        latest="firefox-15", compatible=True,
    )
    app._auto_update_engine2()  # synchronous check path
    assert downloaded == ["firefox-15"]


def test_auto_update_engine2_noop_when_current(monkeypatch):
    # Already on the newest build → no download, a reassuring log line.
    app, logs, downloaded = _make_engine2_app(
        monkeypatch, installed=True, current="firefox-15",
        latest="firefox-15", compatible=True,
    )
    app._auto_update_engine2()
    assert downloaded == []
    assert any("up to date" in m for m in logs)


def test_engine2_click_claims_busy_before_worker_and_second_click_is_noop(monkeypatch):
    # #234: clicking the engine row when it's not installed must claim busy
    # SYNCHRONOUSLY, so a second click during the gap before the worker spawns
    # can't start a second download from scratch.
    app, logs, downloaded = _make_engine2_app(
        monkeypatch, installed=False, current="", latest="firefox-18",
        compatible=True,
    )
    ensure_calls = []
    app._ensure_engine2_async = lambda: ensure_calls.append(1)
    app._engine2_checking = False

    app._on_engine2_click()
    assert app._engine2_busy is True, "busy must be set synchronously on the click"
    assert ensure_calls == [1]

    # a second click while busy must do nothing (no second download)
    app._on_engine2_click()
    assert ensure_calls == [1], "a second click during download must be a no-op"


# --- _show_startup_notice (#214/#215) ---

def _make_notice_app(monkeypatch, *, onboarded, last_version, current="2.5.2"):
    from src.ui import app as app_mod

    app = make_app(FakePage())
    calls = {"onboarding": 0, "changelog": None, "recorded": None}
    app._show_onboarding = lambda: calls.__setitem__("onboarding", calls["onboarding"] + 1)
    monkeypatch.setattr(app_mod.app_settings, "is_onboarding_done", lambda: onboarded)
    monkeypatch.setattr(app_mod.app_settings, "last_seen_version", lambda: last_version)
    monkeypatch.setattr(
        app_mod.app_settings, "set_last_seen_version",
        lambda v: calls.__setitem__("recorded", v),
    )
    monkeypatch.setattr(app_mod.app_update, "APP_VERSION", current)
    # capture the changelog dialog without building flet controls
    import src.ui.dialogs.changelog as cl
    monkeypatch.setattr(
        cl, "open_changelog_dialog",
        lambda page, version, notes, on_dismiss=None: calls.__setitem__(
            "changelog", (version, tuple(notes))
        ),
    )
    return app, calls


def test_startup_notice_first_install_shows_onboarding(monkeypatch):
    app, calls = _make_notice_app(monkeypatch, onboarded=False, last_version="")
    app._show_startup_notice()
    assert calls["onboarding"] == 1
    assert calls["changelog"] is None
    assert calls["recorded"] == "2.5.2"  # version recorded regardless


def test_startup_notice_after_update_shows_changelog(monkeypatch):
    app, calls = _make_notice_app(
        monkeypatch, onboarded=True, last_version="2.5.1", current="2.5.2"
    )
    app._show_startup_notice()
    assert calls["onboarding"] == 0
    assert calls["changelog"] is not None
    version, notes = calls["changelog"]
    assert version == "2.5.2"
    assert len(notes) > 0  # 2.5.2 has changelog entries
    assert calls["recorded"] == "2.5.2"


def test_startup_notice_same_version_shows_nothing(monkeypatch):
    app, calls = _make_notice_app(
        monkeypatch, onboarded=True, last_version="2.5.2", current="2.5.2"
    )
    app._show_startup_notice()
    assert calls["onboarding"] == 0
    assert calls["changelog"] is None
    assert calls["recorded"] == "2.5.2"


def test_startup_notice_update_to_version_without_notes_shows_nothing(monkeypatch):
    # An update whose version has no changelog entry must not pop an empty
    # dialog — record the version and move on.
    app, calls = _make_notice_app(
        monkeypatch, onboarded=True, last_version="2.5.1", current="9.9.9"
    )
    app._show_startup_notice()
    assert calls["changelog"] is None
    assert calls["recorded"] == "9.9.9"
