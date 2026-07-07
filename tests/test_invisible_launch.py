import inspect
import sys

import pytest

from src.services.browser import invisible_launch
from src.services.browser.invisible_launch import (
    InvisibleProcess,
    _child,
    _profile_prefs,
    _ps_single_quote,
    _remoting_name,
    _window_size_for,
    installed_version,
)


def test_remoting_name_is_dbus_valid():
    # The remoting name doubles as the Firefox DBus name; a dash or space makes
    # Firefox reject it and fall back to a shared default, which is what made a
    # second profile fail to launch. It must be [A-Za-z0-9_] only.
    import re

    name = _remoting_name("My Profile-2")
    assert re.fullmatch(r"persona_[A-Za-z0-9_]+", name)


def test_installed_version_is_firefox_version_not_build_tag():
    # The sidebar must show the real Firefox version (e.g. "150.0.1"), not the
    # engine's internal build tag ("firefox-13").
    v = installed_version()
    assert not v.startswith("firefox-")


def test_ps_single_quote_keeps_backslashes():
    # The WMI CommandLine match must compare against a real Windows path with
    # single backslashes. json.dumps escaped them to \\ so the -like filter never
    # matched and the close-watch never saw the profile (stuck-running bug).
    p = r"C:\Users\admin\.persona\FF test\.invisible-profile"
    q = _ps_single_quote("*" + p + "*")
    # single-quoted, backslashes untouched (no doubling)
    assert q == "'*" + p + "*'"
    assert "\\\\" not in q


def test_ps_single_quote_doubles_apostrophes():
    # A path with a single quote in it must be escaped for PowerShell by doubling.
    assert _ps_single_quote("a'b") == "'a''b'"


def test_window_never_exceeds_work_area():
    # A 4K pick on a 4K monitor (work area 2560x1392 CSS at 150% scale) must NOT
    # open a window at 3840x2160 — that overflows the screen. It's capped to the
    # work area so it fits with room for the taskbar/borders.
    cw, ch = _window_size_for(3840, 2160, (2560, 1392))
    assert cw <= 2560 and ch <= 1392
    assert cw < 3840 and ch < 2160  # actually shrunk, not passed through


def test_window_keeps_small_resolution_as_is():
    # A small pick that fits the monitor opens at exactly its size.
    cw, ch = _window_size_for(1366, 768, (2560, 1392))
    assert (cw, ch) == (1366, 768)


def test_window_falls_back_without_work_area():
    # No work-area reading (non-Windows / failure): cap at a common laptop size
    # so a huge pick can't open a window larger than a typical screen.
    cw, ch = _window_size_for(3840, 2160, (0, 0))
    assert cw <= 1280 and ch <= 800


def test_context_overrides_window_fills_monitor_regardless_of_pick():
    # The window ALWAYS fills the monitor's work area (Mars wants full-screen),
    # while the spoofed screen reports the chosen resolution. A 4K pick and a
    # small pick both open a full-screen window; only the fingerprint differs.
    from src.services.browser.invisible_launch import _context_overrides_for

    big = _context_overrides_for(3840, 2160, (2560, 1392))
    assert big["screen"] == {"width": 3840, "height": 2160}      # fingerprint = chosen
    assert big["viewport"] == {"width": 2560, "height": 1392}     # window = monitor

    small = _context_overrides_for(1366, 768, (2560, 1392))
    assert small["screen"] == {"width": 1366, "height": 768}      # fingerprint = chosen
    assert small["viewport"] == {"width": 2560, "height": 1392}    # window STILL = monitor


def test_context_overrides_fallback_without_work_area():
    # No work-area reading: the window falls back to the chosen size so at least
    # the picked resolution's window shows.
    from src.services.browser.invisible_launch import _context_overrides_for

    ov = _context_overrides_for(1920, 1080, (0, 0))
    assert ov["viewport"] == {"width": 1920, "height": 1080}


def test_outer_size_override_script_ties_outer_to_window():
    # outerWidth/outerHeight must track the real window (inner + chrome), NOT the
    # spoofed screen — otherwise outerWidth leaks the 4K screen on a physically
    # small window, an inner<outer==screen mismatch a scanner can catch.
    from src.services.browser.invisible_launch import _outer_size_override_script

    js = _outer_size_override_script()
    assert "outerWidth" in js and "outerHeight" in js
    assert "innerWidth" in js  # derives outer from the real inner size


def test_profile_prefs_force_dark_theme():
    prefs = _profile_prefs({"search_engine": "duckduckgo"})
    assert prefs["ui.systemUsesDarkTheme"] == 1


def test_profile_prefs_close_without_confirmation():
    # Closing the window with the X must not pop a "close N tabs?" dialog, which
    # would leave the profile shown as running until dismissed.
    prefs = _profile_prefs({"search_engine": "duckduckgo"})
    assert prefs["browser.tabs.warnOnClose"] is False
    assert prefs["browser.warnOnQuit"] is False


def test_profile_prefs_homepage_follows_search_engine():
    assert "duckduckgo.com" in _profile_prefs({"search_engine": "duckduckgo"})[
        "browser.startup.homepage"
    ]
    assert "google.com" in _profile_prefs({"search_engine": "google"})[
        "browser.startup.homepage"
    ]


def test_profile_prefs_skip_startup_network_fetch():
    # The remote-settings server is pointed at a data: URL so Firefox skips the
    # startup changeset poll that hangs a launch over Tor.
    prefs = _profile_prefs({})
    assert prefs["services.settings.server"].startswith("data:")


def test_child_accepts_stop_event_for_thread_path():
    # On Windows/macOS _child runs in a thread and is stopped via a stop_event
    # (SIGTERM is main-thread only). The signature must accept it.
    params = inspect.signature(_child).parameters
    assert "stop_event" in params


def test_close_watch_uses_profile_process_liveness():
    # #112: the Windows close-watch keys on whether THIS profile's Firefox
    # PROCESS is still alive (matched by "-profile <dir>" in the command line),
    # NOT on a window count. Closing the window with the X exits the whole
    # patched Firefox, so the profile's process disappears — a reliable signal.
    # Counting windows by title missed the "New Tab" page (Firefox owns its
    # title, no "[profile]" prefix), so the close was never seen. The
    # per-profile pid helper must exist.
    from src.services.browser import invisible_launch

    assert hasattr(invisible_launch, "_profile_firefox_pids")


def test_resolve_seed_uses_cfg_seed():
    # #118: the fingerprint seed comes from cfg (the profile's stable crc32
    # seed). hash(str) is salted per-process, so deriving the seed in the child
    # gave a DIFFERENT fingerprint every app restart.
    from src.services.browser.invisible_launch import _resolve_seed

    assert _resolve_seed({"seed": 12345, "profile_name": "x"}) == 12345
    assert _resolve_seed({"seed": "77", "profile_name": "x"}) == 77
    assert _resolve_seed({"seed": 0, "profile_name": "x"}) == 0


def test_resolve_seed_falls_back_to_name_hash():
    from src.services.browser.invisible_launch import _resolve_seed

    assert (
        _resolve_seed({"profile_name": "legacy"})
        == abs(hash("legacy")) % (2**31)
    )


def test_profile_pids_query_error_returns_none(monkeypatch):
    # #122: a transient PowerShell/WMI failure must be reported as "no verdict"
    # (None), not as an empty set — an empty set means the query SUCCEEDED and
    # the profile truly has no Firefox process. Collapsing the two is what let
    # the close-watch miss the process forever and wedge the card "running".
    monkeypatch.setattr(invisible_launch._platform, "IS_WINDOWS", True)

    def boom(*a, **k):
        raise RuntimeError("wmi hiccup")

    monkeypatch.setattr(invisible_launch.subprocess, "check_output", boom)
    assert invisible_launch._profile_firefox_pids(r"C:\p\dir") is None


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="exercises the real Windows PowerShell/WMI pid query path",
)
def test_profile_pids_success_with_no_match_is_confident_empty(monkeypatch):
    monkeypatch.setattr(invisible_launch._platform, "IS_WINDOWS", True)
    monkeypatch.setattr(
        invisible_launch.subprocess, "check_output", lambda *a, **k: ""
    )
    assert invisible_launch._profile_firefox_pids(r"C:\p\dir") == set()


def test_profile_pids_unqueryable_is_no_verdict(monkeypatch):
    # Non-Windows (and a missing profile_dir) can't run the WMI query at all —
    # that's "no verdict", not "confidently no process".
    monkeypatch.setattr(invisible_launch._platform, "IS_WINDOWS", False)
    assert invisible_launch._profile_firefox_pids(r"C:\p\dir") is None
    monkeypatch.setattr(invisible_launch._platform, "IS_WINDOWS", True)
    assert invisible_launch._profile_firefox_pids("") is None


def test_close_watch_closes_only_on_confident_process_exit(monkeypatch):
    import threading

    # #122(a): early query errors (None) during the launch window must not count
    # as "process gone". The watch caches the pid on the first sighting, stops
    # querying WMI (#122(b): a per-tick CommandLine scan per open profile burns
    # CPU), and closes only when the cached pid is confidently dead.
    queries = iter([None, None, {4242}])
    monkeypatch.setattr(
        invisible_launch, "_profile_firefox_pids", lambda d: next(queries)
    )
    monkeypatch.setattr(invisible_launch, "_firefox_pid", lambda d: None)
    liveness = iter([True, True, False])
    monkeypatch.setattr(invisible_launch, "_pid_alive", lambda p: next(liveness))

    stops = []
    invisible_launch._thread_close_watch(
        r"C:\p", threading.Event(), None, lambda: stops.append("stop"),
        no_process_timeout=60.0, interval=0.0,
    )
    assert stops == []                       # returned as "closed", not STOP
    assert next(queries, "done") == "done"   # no WMI polls after the pid is cached
    assert next(liveness, "done") == "done"  # closed exactly on the dead poll


def test_close_watch_grace_for_confident_empty_before_first_sighting(monkeypatch):
    import threading

    # #122(a): the process takes a moment to appear after launch; a confident
    # empty BEFORE the first sighting is the launch window, not a close.
    queries = iter([set(), set(), {7}])
    monkeypatch.setattr(
        invisible_launch, "_profile_firefox_pids", lambda d: next(queries)
    )
    monkeypatch.setattr(invisible_launch, "_firefox_pid", lambda d: None)
    liveness = iter([False])
    monkeypatch.setattr(invisible_launch, "_pid_alive", lambda p: next(liveness))

    invisible_launch._thread_close_watch(
        r"C:\p", threading.Event(), None, lambda: None,
        no_process_timeout=60.0, interval=0.0,
    )
    assert next(queries, "done") == "done"
    assert next(liveness, "done") == "done"


def test_close_watch_gives_up_when_process_never_seen(monkeypatch):
    import threading
    import time as _time

    # #122(a): BROWSER_STARTED with no process ever confidently seen (the query
    # keeps failing, or the launch half-died) must not wedge the profile
    # "running" forever — the watch gives up after no_process_timeout so the
    # caller emits BROWSER_CLOSED and tears down.
    monkeypatch.setattr(invisible_launch, "_profile_firefox_pids", lambda d: None)
    monkeypatch.setattr(invisible_launch, "_firefox_pid", lambda d: None)

    t0 = _time.monotonic()
    invisible_launch._thread_close_watch(
        r"C:\p", threading.Event(), None, lambda: None,
        no_process_timeout=0.05, interval=0.01,
    )
    assert _time.monotonic() - t0 < 5


def test_close_watch_stop_event_stops_gracefully(monkeypatch):
    import threading

    monkeypatch.setattr(invisible_launch, "_profile_firefox_pids", lambda d: {11})
    monkeypatch.setattr(invisible_launch, "_pid_alive", lambda p: True)
    ev = threading.Event()
    ev.set()
    stops = []
    invisible_launch._thread_close_watch(
        r"C:\p", threading.Event(), ev, lambda: stops.append("stop"),
        interval=0.0,
    )
    assert stops == ["stop"]


def test_concurrent_launch_overrides_do_not_cross():
    import threading
    import time as _time

    # #123: on the Windows/macOS thread path two overlapping launches share the
    # process; the override must ride on each launch's OWN engine class, so
    # neither launch can see the other's viewport/screen.
    class Engine:
        def _default_context_kwargs(self):
            return {"viewport": "engine", "locale": "engine"}

    results = {}
    barrier = threading.Barrier(2)

    def launch(name, res):
        cls = invisible_launch._with_context_overrides(Engine, {"viewport": res})
        inst = cls()
        barrier.wait()  # both launches are mid-flight at the same time
        _time.sleep(0.02)
        results[name] = inst._default_context_kwargs()

    t1 = threading.Thread(target=launch, args=("a", "1280x720"))
    t2 = threading.Thread(target=launch, args=("b", "3840x2160"))
    t1.start()
    t2.start()
    t1.join(5)
    t2.join(5)

    assert results["a"]["viewport"] == "1280x720"
    assert results["b"]["viewport"] == "3840x2160"
    assert results["a"]["locale"] == "engine"  # engine kwargs remain the base


def test_context_overrides_leave_engine_class_untouched():
    # #123: the override must not leak into the engine class — a later launch
    # WITHOUT a resolution pick (Auto) must get the engine's own kwargs.
    class Engine:
        def _default_context_kwargs(self):
            return {"viewport": "engine"}

    invisible_launch._with_context_overrides(Engine, {"viewport": "1x1"})
    assert Engine()._default_context_kwargs() == {"viewport": "engine"}


def test_child_thread_path_applies_overrides_and_reports_lifecycle(
    monkeypatch, tmp_path
):
    # Characterization for the thread path with a stub engine: the launch must
    # emit BROWSER_STARTED then BROWSER_CLOSED on stop, and the resolution
    # override must be live on THIS launch's engine when the context enters.
    import os
    import sys
    import threading
    import types

    seen = {}

    class FakeCtx:
        pages = [object()]

        def add_init_script(self, *_a, **_k):
            pass

    class FakeEngine:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def _default_context_kwargs(self):
            return {"viewport": "engine", "screen": "engine"}

        def __enter__(self):
            seen["ctx_kwargs"] = self._default_context_kwargs()
            return FakeCtx()

        def __exit__(self, *a):
            seen["exited"] = True
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)

    stop = threading.Event()
    stop.set()  # ask for STOP on the first watch tick
    r, w = os.pipe()
    invisible_launch._child(
        {
            "profile_dir": str(tmp_path),
            "profile_name": "t",
            "seed": 1,
            "resolution": [1920, 1080],
        },
        w,
        stop_event=stop,
    )
    out = os.read(r, 65536).decode()
    os.close(r)

    expected = invisible_launch._context_overrides_for(
        1920, 1080, invisible_launch._work_area()
    )
    assert seen["ctx_kwargs"]["viewport"] == expected["viewport"]
    assert seen["ctx_kwargs"]["screen"] == expected["screen"]
    assert seen["exited"] is True
    assert "BROWSER_STARTED" in out
    assert "BROWSER_CLOSED" in out


def test_init_does_not_seed_bookmarks_on_callers_thread(monkeypatch):
    # Seeding bookmarks can do a one-time headless engine init (a real Firefox
    # start, 10-30s). The handle is constructed on the caller's thread — on a
    # UI launch that's the Flet session thread — so seeding in __init__ froze
    # the whole app on every new Firefox profile. Constructing must not seed.
    monkeypatch.setattr(
        invisible_launch._platform, "needs_fork_launch", lambda: False
    )
    monkeypatch.setattr(
        invisible_launch, "_child", lambda cfg, wfd, stop_event=None: None
    )

    def boom(*a, **k):
        raise AssertionError("bookmark seeding must not run in the constructor")

    monkeypatch.setattr(invisible_launch, "_seed_firefox_bookmarks", boom)
    monkeypatch.setattr(invisible_launch, "_ensure_firefox_policies", boom)
    proc = InvisibleProcess(
        {
            "profile_name": "t",
            "profile_dir": "x",
            "bookmarks": [{"name": "a", "url": "https://a"}],
        }
    )
    proc.wait(timeout=5)


def test_child_seeds_bookmarks_before_engine_launch(monkeypatch, tmp_path):
    # The child (worker thread / forked process) does the seeding instead, and
    # it must happen BEFORE the engine opens the visible window so the first
    # real launch already shows the bookmarks. The seed passed through is the
    # profile's stable fingerprint seed (cfg["seed"]), not something derived
    # locally.
    import os
    import sys
    import threading
    import types

    calls = []
    seeded = {}

    class FakeCtx:
        pages = [object()]

        def add_init_script(self, *_a, **_k):
            pass

    class FakeEngine:
        def __init__(self, **kwargs):
            pass

        def _default_context_kwargs(self):
            return {}

        def __enter__(self):
            calls.append("enter")
            return FakeCtx()

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)

    def fake_seed(profile_dir, bookmarks, seed):
        calls.append("seed")
        seeded["args"] = (profile_dir, bookmarks, seed)

    monkeypatch.setattr(invisible_launch, "_seed_firefox_bookmarks", fake_seed)
    monkeypatch.setattr(
        invisible_launch, "_ensure_firefox_policies", lambda: None
    )

    stop = threading.Event()
    stop.set()
    r, w = os.pipe()
    marks = [{"name": "a", "url": "https://a"}]
    invisible_launch._child(
        {
            "profile_dir": str(tmp_path),
            "profile_name": "t",
            "seed": 42,
            "bookmarks": marks,
        },
        w,
        stop_event=stop,
    )
    os.close(r)

    assert "seed" in calls and "enter" in calls
    assert calls.index("seed") < calls.index("enter")
    assert seeded["args"] == (str(tmp_path), marks, 42)


def test_init_places_db_uses_profile_seed(monkeypatch, tmp_path):
    # The throwaway headless init must fingerprint with the SAME stable seed
    # the real launch uses (cfg["seed"]), not a locally derived one.
    import os
    import sys
    import types

    captured = {}

    class FakeEngine:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            places = os.path.join(str(tmp_path), "places.sqlite")
            open(places, "wb").close()
            return self

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)
    monkeypatch.setattr(invisible_launch.time, "sleep", lambda s: None)

    assert invisible_launch._init_places_db(str(tmp_path), 4242) is True
    assert captured["seed"] == 4242
    assert captured["headless"] is True


def test_child_thread_path_closes_pipe_on_normal_exit(monkeypatch, tmp_path):
    # After BROWSER_CLOSED the parent's monitor keeps reading until EOF; a
    # write end left open on a normal exit blocks readline forever — a
    # thread+fd leak per launch. The normal-exit path must close the pipe.
    import os
    import sys
    import threading
    import types

    import pytest

    class FakeCtx:
        pages = [object()]

        def add_init_script(self, *_a, **_k):
            pass

    class FakeEngine:
        def __init__(self, **kwargs):
            pass

        def _default_context_kwargs(self):
            return {}

        def __enter__(self):
            return FakeCtx()

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)

    stop = threading.Event()
    stop.set()
    r, w = os.pipe()
    invisible_launch._child(
        {"profile_dir": str(tmp_path), "profile_name": "t", "seed": 1},
        w,
        stop_event=stop,
    )

    with pytest.raises(OSError):
        os.fstat(w)  # write end must be closed, not left to GC
    data = os.read(r, 65536)
    os.close(r)
    assert b"BROWSER_CLOSED" in data


def test_non_fork_launch_uses_thread_not_reexec(monkeypatch):
    # The Win/Mac path must NOT re-exec sys.executable (in a flet bundle that's
    # the GUI launcher, not python — it just opens a second window). It must run
    # _child in a thread. Guard against a regression to subprocess.Popen.
    monkeypatch.setattr(invisible_launch._platform, "needs_fork_launch", lambda: False)

    def _boom(*a, **k):
        raise AssertionError("non-fork path must not spawn a subprocess")

    monkeypatch.setattr(invisible_launch.subprocess, "Popen", _boom)
    # _child does the heavy lifting; stub it so no real Firefox launches.
    monkeypatch.setattr(
        invisible_launch, "_child", lambda cfg, wfd, stop_event=None: None
    )
    proc = InvisibleProcess({"profile_name": "t"})
    assert hasattr(proc, "_thread")
    assert hasattr(proc, "_stop_event")
    proc.wait(timeout=5)
