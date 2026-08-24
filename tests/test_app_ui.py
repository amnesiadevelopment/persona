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
    # App.__init__ sets both (app.py:149-150) and this stub goes through
    # __new__, so without them any code reading them raises AttributeError
    # here while working fine in the real app — the stub must not be a
    # weaker object than the one it stands in for. Individual tests still
    # override them; these are only the idle defaults.
    app._update_in_progress = False
    app._update_staged = ""
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


def _make_engine2_app(
    monkeypatch, *, installed, current, latest, compatible, capped_by=""
):
    """An App stub wired so _auto_update_engine2_async can run: fake the
    engine module + record whether the download path was triggered.

    Stubs fetch_latest_full (the 3-tuple the consumers read), not fetch_latest:
    PS-112 gave the consumers a third value, `capped_by`, naming a higher
    release the driver pin passed over. It defaults to '' — no cap — so every
    existing case reads exactly as it did before.
    """
    from src.services.browser import invisible_launch as inv
    from src.services.engine import firefox as ff

    monkeypatch.setattr(inv, "is_invisible_installed", lambda: installed)
    monkeypatch.setattr(
        ff, "fetch_latest_full", lambda: (latest, compatible, capped_by)
    )
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


def test_auto_update_engine2_capped_never_says_up_to_date(monkeypatch):
    # PS-112 ROUND 2, THE BLOCKING REGRESSION, startup half. The operator is
    # ALREADY ON the highest drivable build and upstream sits above the pin:
    # pin firefox-18, upstream [18, 20], installed firefox-18. fetch_latest_full
    # offers firefox-18 with compatible=True and names firefox-20 as capped_by.
    #
    # `tag` now means "the newest build you can DRIVE", not "the newest build
    # that exists", so `is_newer(tag, current)` is False here and the old code
    # took the no-op branch and affirmatively logged "Firefox engine is up to
    # date (firefox-18)". That sentence is FALSE: firefox-20 exists, the
    # operator simply cannot drive it until they update the app. Telling them
    # they are current is worse than saying nothing, because it closes the
    # question.
    #
    # Asserted on what the OPERATOR is told, not on capped_by being read.
    app, logs, downloaded = _make_engine2_app(
        monkeypatch, installed=True, current="firefox-18",
        latest="firefox-18", compatible=True, capped_by="firefox-20",
    )
    app._auto_update_engine2()

    # Still no download — firefox-20 is undrivable and firefox-18 is installed.
    assert downloaded == []
    assert not any("up to date" in m for m in logs), (
        f"told 'up to date' while firefox-20 exists upstream: {logs}"
    )
    assert any("firefox-20" in m and "newer persona" in m for m in logs), (
        f"the passed-over build was never named to the operator: {logs}"
    )
    assert app._engine2_status == "update persona for the newest engine"


def test_auto_update_engine2_uncapped_still_says_up_to_date(monkeypatch):
    # The other side of the distinction above: when NOTHING was passed over
    # (capped_by == ''), the reassuring line is the truth and must still fire.
    # Without this, "never say up to date" could be satisfied by never saying
    # anything — which would regress the #183 no-op path instead of fixing it.
    app, logs, downloaded = _make_engine2_app(
        monkeypatch, installed=True, current="firefox-18",
        latest="firefox-18", compatible=True, capped_by="",
    )
    app._auto_update_engine2()

    assert downloaded == []
    assert any("up to date" in m for m in logs), logs
    assert app._engine2_status == ""


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

def _make_notice_app(monkeypatch, *, onboarded, last_version, current="2.5.2",
                     has_profiles=False):
    from src.ui import app as app_mod
    from types import SimpleNamespace

    app = make_app(FakePage())
    # The onboarding decision now also consults whether the user already has
    # profiles (existing data = not a first run). Control it explicitly.
    app.pm = SimpleNamespace(
        list_profiles=lambda: ([object()] if has_profiles else [])
    )
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
    # audit5 #2: the version must NOT be recorded at onboarding-open — that would
    # mark first-run "seen" before the user finishes, and an early quit would
    # never re-show onboarding (nor bootstrap the engine). on_finish() records it.
    assert calls["recorded"] is None


def test_onboarding_on_finish_records_version(monkeypatch):
    # audit5 #2: the version is recorded when onboarding COMPLETES (on_finish),
    # not at dialog-open. Capture the on_finish the app hands to Onboarding, run
    # it, and assert it both marks the flag and records the version.
    from src.ui import app as app_mod

    app = make_app(FakePage())
    app._onboarding_open = True
    app._engine_busy = False
    app._pending_update = None
    for m in ("_check_engine_async", "_ensure_engine2_async",
              "_refresh_engine_text", "_safe_update"):
        setattr(app, m, lambda *a, **k: None)

    monkeypatch.setattr(app_mod.engine, "is_installed", lambda: True)

    recorded = {"version": None, "done": False}
    monkeypatch.setattr(
        app_mod.app_settings, "mark_onboarding_done",
        lambda: recorded.__setitem__("done", True),
    )
    monkeypatch.setattr(
        app_mod.app_settings, "set_last_seen_version",
        lambda v: recorded.__setitem__("version", v),
    )
    monkeypatch.setattr(app_mod.app_update, "APP_VERSION", "3.1.4")

    captured = {}

    class FakeOnboarding:
        def __init__(self, page, on_finish, **kw):
            captured["on_finish"] = on_finish

        def open(self):
            pass

    monkeypatch.setattr(app_mod, "Onboarding", FakeOnboarding)

    app._show_onboarding()
    # simulate the user finishing the welcome
    captured["on_finish"]()

    assert recorded["done"] is True
    assert recorded["version"] == "3.1.4"


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


def test_startup_notice_update_with_no_notes_at_or_below_shows_nothing(monkeypatch):
    # An update to a version with nothing recorded AT OR BELOW it must not pop an
    # empty dialog — record the version and move on. (audit5 #5 changed notes_for
    # to fall back to nearest-older, so the empty case is now a version below the
    # whole changelog, not merely one without an exact entry.)
    app, calls = _make_notice_app(
        monkeypatch, onboarded=True, last_version="0.0.0", current="0.0.1"
    )
    app._show_startup_notice()
    assert calls["changelog"] is None
    assert calls["recorded"] == "0.0.1"


def test_startup_notice_patch_version_shows_nearest_older_notes(monkeypatch):
    # audit5 #5: a version with no exact changelog entry (a patch release) now
    # shows the nearest-older notes instead of silently skipping the dialog.
    app, calls = _make_notice_app(
        monkeypatch, onboarded=True, last_version="2.8.0", current="2.99.0"
    )
    app._show_startup_notice()
    assert calls["changelog"] is not None
    version, notes = calls["changelog"]
    assert version == "2.99.0"
    assert len(notes) > 0
    assert calls["recorded"] == "2.99.0"


# --- _profile_dir (PS-127) ---
#
# This call site used to be `os.path.join(os.getcwd(), DATA_DIR, name)`. That
# join was load-bearing under a RELATIVE PERSONA_DATA_DIR — the shape
# .env.example ships — and inert otherwise, so the call site could not tell
# whether it was compensating for _under_home returning an override verbatim.
# _under_home now guarantees DATA_DIR is absolute, so the join is gone. These
# assert the VALUE is unchanged, which is the claim that matters; reasoning that
# the join "was redundant" is exactly the step PS-125 got wrong.

def _profile_dir_with_data_dir(monkeypatch, data_dir, name="acme"):
    """Call App._profile_dir with DATA_DIR patched. _profile_dir imports the
    constant inside the function body, so the patch is read at call time."""
    import src.core.config as cfg

    monkeypatch.setattr(cfg, "DATA_DIR", data_dir)
    app = App.__new__(App)
    return App._profile_dir(app, name)


def test_profile_dir_joins_absolute_data_dir(monkeypatch, tmp_path):
    data_dir = str(tmp_path / "persona_data")
    assert _profile_dir_with_data_dir(monkeypatch, data_dir) == str(
        tmp_path / "persona_data" / "acme"
    )


def test_profile_dir_is_absolute_and_ignores_cwd(monkeypatch, tmp_path):
    """The old form injected getcwd(); the new one must not, so the result is
    stable when the working directory moves (main.py's _ensure_valid_cwd can
    relocate the process mid-run after a self-update re-exec)."""
    data_dir = str(tmp_path / "persona_data")
    first = tmp_path / "first"
    first.mkdir()
    monkeypatch.chdir(first)
    before = _profile_dir_with_data_dir(monkeypatch, data_dir)

    second = tmp_path / "second"
    second.mkdir()
    monkeypatch.chdir(second)
    after = _profile_dir_with_data_dir(monkeypatch, data_dir)

    import os

    assert os.path.isabs(before)
    assert before == after == str(tmp_path / "persona_data" / "acme")


# --- the app-update rollback row: a refused revert must be VISIBLE ----------
#
# These mirror the Firefox engine row's tests (test_engine_rollback.py:568-620),
# which are the encoded memory of the lesson _on_app_rollback's own docstring
# quotes: the gesture is a rename, so it finishes in milliseconds with no
# progress bar, and a refusal that reaches only the log is indistinguishable
# from a dead button. _log is not a surface here — the sidebar log panel does
# not render at all while collapsed.


def _rollback_app(monkeypatch, *, went, retained, boom=False):
    """An App stub for the app-rollback CLICK, with the service decision
    stubbed. `retained` is what rollback_target() reports AFTER the attempt,
    which is how the handler tells the two refusals apart."""
    from src.ui import app as app_mod

    def _revert(**k):
        if boom:
            raise OSError("permission denied")
        return went

    monkeypatch.setattr(app_mod.app_update, "revert_to_previous_build", _revert)
    monkeypatch.setattr(app_mod.app_update, "rollback_target", lambda: retained)

    app = make_app(None)
    app._app_rollback_status = ""
    app._log = lambda *a, **k: None
    app._refresh_sidebar = lambda *a, **k: None
    return app


def test_a_refused_app_revert_says_so_on_the_row(monkeypatch):
    # The retained bundle is still there after the attempt, so it was the
    # RENAME that was refused — /Applications not being writable by this user
    # is the ordinary case, and it is likelier on the rollback path than the
    # update path, because the update may have run with different privileges.
    # The OS error is in the log, so the row points at it.
    app = _rollback_app(monkeypatch, went="", retained="/Applications/p.app.bak")

    app._on_app_rollback()

    assert app._app_rollback_status == "couldn't go back — see the log", (
        app._app_rollback_status
    )


def test_an_app_revert_with_nothing_retained_says_that_instead(monkeypatch):
    # The two refusals are not interchangeable. Sending someone to the log to
    # read an error when there was simply never a retained bundle points them
    # at something that will not explain anything.
    app = _rollback_app(monkeypatch, went="", retained="")

    app._on_app_rollback()

    assert app._app_rollback_status == "nothing to go back to", (
        app._app_rollback_status
    )


def test_an_app_revert_that_raises_still_says_something(monkeypatch):
    # The service is documented to answer "" rather than raise, but the
    # handler must not depend on that: an exception escaping it would leave
    # the row silent, which is the exact defect these tests exist to prevent.
    app = _rollback_app(monkeypatch, went="", retained="", boom=True)

    app._on_app_rollback()

    assert app._app_rollback_status == "couldn't go back — see the log", (
        app._app_rollback_status
    )


def test_a_successful_app_revert_replaces_any_stale_complaint(monkeypatch):
    # A refusal from an earlier attempt must not outlive the attempt that
    # succeeded. Unlike the engine row, this one does not clear to "": nothing
    # restarts persona for the operator, so the one thing they must still do
    # is the thing the row says.
    app = _rollback_app(
        monkeypatch, went="/Applications/p.app", retained="/Applications/p.app.bak"
    )
    app._app_rollback_status = "couldn't go back — see the log"

    app._on_app_rollback()

    assert app._app_rollback_status == "restart to run the previous version", (
        app._app_rollback_status
    )


# --- the render rule: nothing retained -> render nothing at all -------------


def _row_app(monkeypatch, *, retained, boom=False):
    from src.ui import app as app_mod

    def _target():
        if boom:
            raise OSError("install location unreadable")
        return retained

    monkeypatch.setattr(app_mod.app_update, "rollback_target", _target)
    return make_app(None)


def test_app_rollback_row_renders_nothing_when_nothing_is_retained(monkeypatch):
    # A revert with no retained bundle cannot work, and a button that cannot
    # work is worse than no button: it promises the machine can undo something
    # it cannot.
    app = _row_app(monkeypatch, retained="")

    assert app._app_rollback_row() is None


def test_app_rollback_row_renders_nothing_when_the_target_is_unreadable(monkeypatch):
    # The panel must still render if the install location cannot be resolved.
    app = _row_app(monkeypatch, retained="", boom=True)

    assert app._app_rollback_row() is None


def test_app_rollback_row_is_offered_when_a_bundle_is_retained(monkeypatch):
    # The positive control for the two above: without it, an always-None
    # implementation would pass them both.
    app = _row_app(monkeypatch, retained="/Applications/p.app.bak")

    assert app._app_rollback_row() is not None


# --- the status must REACH THE PANEL, not just be assigned to a field -------
#
# This is the test that would have caught the defect these were written for.
# The original implementation set nothing; but an implementation that sets
# _app_rollback_status and never renders it would pass every assertion above
# while the operator still sees nothing move. The only way to tell those apart
# is to build the panel and read the text out of it.


def _panel_texts(app, monkeypatch):
    """Build the version panel and collect every string it renders."""
    from src.ui import app as app_mod

    monkeypatch.setattr(
        app_mod.app_settings, "is_auto_update_enabled", lambda: False
    )
    app._app_latest = ""
    app._app_update_status = ""
    app._update_staged = ""
    panel = app._build_version_panel()

    found: list[str] = []

    def walk(c):
        v = getattr(c, "value", None)
        if isinstance(v, str):
            found.append(v)
        for attr in ("content", "controls"):
            child = getattr(c, attr, None)
            if child is None:
                continue
            for k in (child if isinstance(child, list) else [child]):
                walk(k)

    walk(panel)
    return found


def test_a_refused_app_revert_is_rendered_in_the_version_panel(monkeypatch):
    # The whole point: after a refused click the operator must SEE the reason
    # in the panel they clicked in. _log does not count — the sidebar log
    # panel renders only while expanded.
    app = _rollback_app(monkeypatch, went="", retained="/Applications/p.app.bak")
    app._app_rollback_row = lambda: None

    app._on_app_rollback()

    assert "couldn't go back — see the log" in _panel_texts(app, monkeypatch)


def test_the_nothing_retained_refusal_is_rendered_even_though_the_row_is_not(
    monkeypatch,
):
    # The case the separation exists for. When nothing is retained the row
    # itself renders NOTHING AT ALL, so a status hung off the row would
    # disappear in exactly the case it is reporting on. It must survive the
    # row's absence.
    app = _rollback_app(monkeypatch, went="", retained="")
    app._app_rollback_row = lambda: None

    app._on_app_rollback()

    texts = _panel_texts(app, monkeypatch)
    assert "nothing to go back to" in texts
    assert "go back to the previous version" not in texts  # the row is gone


def test_a_quiet_panel_renders_no_rollback_status_line(monkeypatch):
    # The negative control: with no attempt made, the panel carries no status
    # line at all, so the assertions above are reporting the click rather than
    # a line that is always present.
    app = _rollback_app(monkeypatch, went="", retained="")
    app._app_rollback_row = lambda: None

    assert _panel_texts(app, monkeypatch) == [
        "persona v" + __import__(
            "src.ui.app", fromlist=["app_update"]
        ).app_update.APP_VERSION,
        "[ auto-update: off ]",
    ]


# --- the gesture is REFUSED while an update is pending ----------------------
#
# Two distinct problems share one guard, and the tests below cover them
# separately because they fail in different ways.
#
#   (a) A concurrent-rename race on the very bundle this ticket exists to
#       protect. On macOS the install runs in a daemon thread spawned AFTER
#       the dialog is popped (_offer_install -> on_install), so the sidebar
#       stays interactive through a sha256 re-verify, a checksum fetch,
#       `hdiutil attach` and `ditto`. A click in that window renames
#       app -> app.reverting and app.bak -> app while _apply_macos is
#       mid-`ditto` INTO app.
#   (b) Two contradictory instructions in one panel: _update_staged survives a
#       revert untouched, so "[ restart to update ]" and "restart to run the
#       previous version" render together.
#
# Note the axis: _update_in_progress alone does NOT cover the install, because
# its `finally` clears it when the DOWNLOAD thread ends — long before the
# operator clicks "install now". _update_staged is what spans the install
# itself, which is why both flags appear and why the staged cases below are
# the load-bearing ones rather than the redundant ones.


def test_the_app_rollback_row_is_not_offered_while_an_update_is_staged(monkeypatch):
    # The (a) race, closed at the surface the operator touches: a retained
    # bundle IS present, so the row would otherwise render and be clickable
    # for the whole of the install.
    app = _row_app(monkeypatch, retained="/Applications/p.app.bak")
    app._update_in_progress = False
    app._update_staged = "/tmp/persona-3.0.0.dmg"

    assert app._app_rollback_row() is None


def test_the_app_rollback_row_is_not_offered_while_a_download_is_running(monkeypatch):
    # The other half of the same axis — the download window, which is the one
    # _update_in_progress does cover.
    app = _row_app(monkeypatch, retained="/Applications/p.app.bak")
    app._update_in_progress = True
    app._update_staged = ""

    assert app._app_rollback_row() is None


def test_the_app_rollback_row_returns_when_no_update_is_pending(monkeypatch):
    # The positive control for the two above: without it, the guard could be
    # `return None` unconditionally and both would still pass. This is the
    # same row the retention feature exists to offer, so it must survive.
    app = _row_app(monkeypatch, retained="/Applications/p.app.bak")
    app._update_in_progress = False
    app._update_staged = ""

    assert app._app_rollback_row() is not None


def test_a_click_while_an_update_is_staged_does_not_reach_the_service(monkeypatch):
    # The guard the race actually turns on. The row can go stale — it is built
    # BEFORE the update arrives — so refusing to render is not enough on its
    # own; the handler must refuse the click that the stale row still carries.
    # Asserting the service was NOT called is the one assertion that can show
    # the rename never started.
    calls = []
    app = _rollback_app(
        monkeypatch, went="/Applications/p.app", retained="/Applications/p.app.bak"
    )
    from src.ui import app as app_mod

    monkeypatch.setattr(
        app_mod.app_update,
        "revert_to_previous_build",
        lambda **k: calls.append(1) or "/Applications/p.app",
    )
    app._update_in_progress = False
    app._update_staged = "/tmp/persona-3.0.0.dmg"

    app._on_app_rollback()

    assert calls == [], "the rename must not start while an update is in flight"


def test_a_click_while_a_download_is_running_does_not_reach_the_service(monkeypatch):
    calls = []
    app = _rollback_app(
        monkeypatch, went="/Applications/p.app", retained="/Applications/p.app.bak"
    )
    from src.ui import app as app_mod

    monkeypatch.setattr(
        app_mod.app_update,
        "revert_to_previous_build",
        lambda **k: calls.append(1) or "/Applications/p.app",
    )
    app._update_in_progress = True
    app._update_staged = ""

    app._on_app_rollback()

    assert calls == []


def test_a_click_with_no_update_pending_still_reaches_the_service(monkeypatch):
    # The positive control for the two above — a guard that refused everything
    # would pass them both while breaking the feature outright.
    calls = []
    app = _rollback_app(
        monkeypatch, went="/Applications/p.app", retained="/Applications/p.app.bak"
    )
    from src.ui import app as app_mod

    monkeypatch.setattr(
        app_mod.app_update,
        "revert_to_previous_build",
        lambda **k: calls.append(1) or "/Applications/p.app",
    )
    app._update_in_progress = False
    app._update_staged = ""

    app._on_app_rollback()

    assert calls == [1]


def test_a_refused_click_says_why_rather_than_swallowing_it(monkeypatch):
    # A silent return would reintroduce the exact dead-button defect this
    # whole group exists to prevent: the stale row is still clickable, and
    # nothing moving with no explanation is indistinguishable from a broken
    # button. This is the one place the engine sibling is deliberately NOT
    # mirrored — its panel has a busy indicator, this one does not.
    app = _rollback_app(
        monkeypatch, went="/Applications/p.app", retained="/Applications/p.app.bak"
    )
    app._update_in_progress = False
    app._update_staged = "/tmp/persona-3.0.0.dmg"

    app._on_app_rollback()

    assert app._app_rollback_status == "can't go back while an update is pending", (
        app._app_rollback_status
    )


def test_the_panel_never_shows_both_restart_instructions_at_once(monkeypatch):
    # Problem (b), asserted on the RENDERED PANEL rather than on the flags,
    # because the defect was that the operator reads two opposite instructions
    # in one box. _update_staged survives a revert untouched, so this is the
    # state that actually occurred: a successful revert with an update still
    # staged rendered "[ restart to update ]" and "restart to run the previous
    # version" together, with no way to tell which wins.
    app = _rollback_app(
        monkeypatch, went="/Applications/p.app", retained="/Applications/p.app.bak"
    )
    app._update_in_progress = False
    app._update_staged = "/tmp/persona-3.0.0.dmg"
    app._app_rollback_status = "restart to run the previous version"

    from src.ui import app as app_mod

    monkeypatch.setattr(
        app_mod.app_settings, "is_auto_update_enabled", lambda: False
    )
    app._app_latest = ""
    app._app_update_status = ""
    panel = app._build_version_panel()

    found: list[str] = []

    def walk(c):
        v = getattr(c, "value", None)
        if isinstance(v, str):
            found.append(v)
        for attr in ("content", "controls"):
            child = getattr(c, attr, None)
            if child is None:
                continue
            for k in (child if isinstance(child, list) else [child]):
                walk(k)

    walk(panel)

    # the staged update's own instruction is the one that stands...
    assert "[ restart to update ]" in found
    # ...and the gesture that would contradict it is not offered beside it
    assert "go back to the previous version" not in found, found
