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


def test_context_overrides_native_window_spoofed_screen():
    # #128: the chromium model — the chosen resolution lives ONLY in the
    # fingerprint (`screen`), the physical window is NATIVE (`no_viewport`), so
    # the content follows the real window: drag/resize/maximize with no skew.
    # A fixed viewport (any value) is what filled the whole 4K screen misscaled.
    from src.services.browser.invisible_launch import _context_overrides_for

    ov = _context_overrides_for(3840, 2160)
    assert ov["screen"] == {"width": 3840, "height": 2160}  # fingerprint = chosen
    assert ov["no_viewport"] is True                        # window = native
    assert "viewport" not in ov


def test_with_overrides_strips_engine_viewport_and_dsf():
    # The engine's own context kwargs carry a fixed viewport and a
    # device_scale_factor; either defeats no_viewport (and Playwright rejects
    # device_scale_factor with a null viewport). The overlay must strip both.
    class Engine:
        def _default_context_kwargs(self):
            return {
                "viewport": {"width": 1264, "height": 918},
                "device_scale_factor": 1.5,
                "locale": "engine",
            }

    cls = invisible_launch._with_context_overrides(
        Engine, invisible_launch._context_overrides_for(1920, 1080)
    )
    kw = cls()._default_context_kwargs()
    assert kw["no_viewport"] is True
    assert kw["screen"] == {"width": 1920, "height": 1080}
    assert "viewport" not in kw
    assert "device_scale_factor" not in kw
    assert kw["locale"] == "engine"  # engine kwargs remain the base


def test_window_size_seed_writes_half_work_area(monkeypatch, tmp_path):
    # A fresh profile opens at roughly half the work area (CSS px = physical
    # ÷ system dpr), a normal mid-size window like chromium's default.
    import json as _json

    monkeypatch.setattr(invisible_launch, "_work_area", lambda: (3840, 2088))
    monkeypatch.setattr(invisible_launch, "_system_dpr", lambda: 1.5)
    invisible_launch._seed_window_size(str(tmp_path))

    data = _json.loads((tmp_path / "xulstore.json").read_text(encoding="utf-8"))
    win = data["chrome://browser/content/browser.xhtml"]["main-window"]
    assert win["width"] == "1280"   # 3840 / 1.5 / 2
    assert win["height"] == "696"   # 2088 / 1.5 / 2
    assert win["sizemode"] == "normal"


def test_window_size_seed_keeps_existing_xulstore(monkeypatch, tmp_path):
    # Firefox persists the user's own window size in xulstore.json; a manual
    # resize must survive relaunches, so an existing file is never touched.
    (tmp_path / "xulstore.json").write_text('{"user": "sized"}', encoding="utf-8")
    monkeypatch.setattr(invisible_launch, "_work_area", lambda: (3840, 2088))
    monkeypatch.setattr(invisible_launch, "_system_dpr", lambda: 1.5)
    invisible_launch._seed_window_size(str(tmp_path))
    assert (tmp_path / "xulstore.json").read_text(encoding="utf-8") == (
        '{"user": "sized"}'
    )


def test_window_size_seed_noop_without_work_area(monkeypatch, tmp_path):
    # No work-area reading (non-Windows / failure): leave sizing to Firefox.
    monkeypatch.setattr(invisible_launch, "_work_area", lambda: (0, 0))
    invisible_launch._seed_window_size(str(tmp_path))
    assert not (tmp_path / "xulstore.json").exists()


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


def test_close_watch_closes_when_window_gone_but_processes_alive(monkeypatch):
    import threading

    # #129: a persistent-context Firefox is multi-process and does NOT exit
    # when the user X-closes the window — firefox.exe children (GPU/content/
    # socket) stay alive, so waiting for a pid to die never fires and the
    # profile stays "running". The close signal is the profile's VISIBLE
    # top-level window disappearing after it was seen.
    monkeypatch.setattr(
        invisible_launch, "_profile_firefox_pids", lambda d: {10, 11}
    )
    monkeypatch.setattr(invisible_launch, "_pid_alive", lambda p: True)
    visibility = iter([True, True, False])
    monkeypatch.setattr(
        invisible_launch, "_pids_have_visible_window", lambda pids: next(visibility)
    )

    stops = []
    invisible_launch._thread_close_watch(
        r"C:\p", threading.Event(), None, lambda: stops.append("stop"),
        interval=0.0,
    )
    assert stops == []                          # closed, not STOP
    assert next(visibility, "done") == "done"   # closed exactly on window-gone


def test_close_watch_waits_for_window_to_appear_first(monkeypatch):
    import threading

    # The window takes a moment to show after the pids appear; "no visible
    # window" during that launch gap is not a close — only gone-after-seen is.
    monkeypatch.setattr(invisible_launch, "_profile_firefox_pids", lambda d: {10})
    monkeypatch.setattr(invisible_launch, "_pid_alive", lambda p: True)
    visibility = iter([False, False, True, False])
    monkeypatch.setattr(
        invisible_launch, "_pids_have_visible_window", lambda pids: next(visibility)
    )
    invisible_launch._thread_close_watch(
        r"C:\p", threading.Event(), None, lambda: None, interval=0.0,
    )
    assert next(visibility, "done") == "done"


def test_close_watch_window_no_verdict_does_not_close(monkeypatch):
    import threading

    # None from the enumeration = "can't tell" (transient failure or a
    # platform without EnumWindows); acting on it would tear down a live
    # browser. Only a confident False after the window was seen closes.
    monkeypatch.setattr(invisible_launch, "_profile_firefox_pids", lambda d: {10})
    monkeypatch.setattr(invisible_launch, "_pid_alive", lambda p: True)
    visibility = iter([True, None, None, False])
    monkeypatch.setattr(
        invisible_launch, "_pids_have_visible_window", lambda pids: next(visibility)
    )
    invisible_launch._thread_close_watch(
        r"C:\p", threading.Event(), None, lambda: None, interval=0.0,
    )
    assert next(visibility, "done") == "done"


def test_close_watch_still_closes_on_all_pids_dead(monkeypatch):
    import threading

    # Process exit stays a valid close signal (a crash, or macOS where the
    # window enumeration always returns no-verdict).
    monkeypatch.setattr(
        invisible_launch, "_profile_firefox_pids", lambda d: {10, 11}
    )
    liveness = iter([True, False, False])  # tick1: alive (short-circuit); tick2: both dead
    monkeypatch.setattr(invisible_launch, "_pid_alive", lambda p: next(liveness))
    monkeypatch.setattr(
        invisible_launch, "_pids_have_visible_window", lambda pids: None
    )
    invisible_launch._thread_close_watch(
        r"C:\p", threading.Event(), None, lambda: None, interval=0.0,
    )
    assert next(liveness, "done") == "done"


def test_pids_have_visible_window_true_when_tracked_pid_owns_one(monkeypatch):
    # The decision is REAL set logic over the enumeration result: any tracked
    # pid owning a visible top-level window means the browser is still open.
    monkeypatch.setattr(
        invisible_launch, "_visible_window_pids", lambda: {10, 999}
    )
    assert invisible_launch._pids_have_visible_window({10, 11}) is True


def test_pids_have_visible_window_false_when_none_tracked(monkeypatch):
    monkeypatch.setattr(invisible_launch, "_visible_window_pids", lambda: {999})
    assert invisible_launch._pids_have_visible_window({10, 11}) is False


def test_pids_have_visible_window_no_verdict_on_enum_failure(monkeypatch):
    monkeypatch.setattr(invisible_launch, "_visible_window_pids", lambda: None)
    assert invisible_launch._pids_have_visible_window({10}) is None


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="exercises the real EnumWindows/ctypes enumeration path",
)
def test_visible_window_pids_enumerates_real_windows():
    pids = invisible_launch._visible_window_pids()
    assert pids is None or isinstance(pids, set)
    if isinstance(pids, set):
        assert all(isinstance(p, int) for p in pids)


def test_kill_profile_firefox_kills_exactly_profile_pids(monkeypatch):
    # #129: inv.__exit__ is a polite Playwright teardown; a persistent-context
    # multi-process Firefox survives it (parent + GPU/content/socket children —
    # 30 firefox.exe were left alive after the user closed the window). The
    # kill set is the union of the watch-tracked pids and a fresh resolve
    # (children spawn/exit over the window's lifetime), minus already-dead
    # pids — and nothing else: other profiles' Firefox must never be touched.
    monkeypatch.setattr(
        invisible_launch, "_profile_firefox_pids", lambda d: {10, 11}
    )
    monkeypatch.setattr(invisible_launch, "_pid_alive", lambda p: p != 11)
    killed = []
    monkeypatch.setattr(invisible_launch, "_force_kill_pid", killed.append)
    invisible_launch._kill_profile_firefox(r"C:\p", {11, 12})
    assert sorted(killed) == [10, 12]


def test_kill_profile_firefox_keeps_tracked_pids_on_failed_resolve(monkeypatch):
    # A no-verdict re-resolve (WMI hiccup) must not lose the tracked pids —
    # they were captured while the browser was alive and are the only handle
    # left on the survivors.
    monkeypatch.setattr(invisible_launch, "_profile_firefox_pids", lambda d: None)
    monkeypatch.setattr(invisible_launch, "_pid_alive", lambda p: True)
    killed = []
    monkeypatch.setattr(invisible_launch, "_force_kill_pid", killed.append)
    invisible_launch._kill_profile_firefox(r"C:\p", {10, 11})
    assert sorted(killed) == [10, 11]


def test_close_watch_returns_tracked_pids_for_kill(monkeypatch):
    import threading

    # #129: the kill set must be captured while the browser is still alive —
    # after inv.__exit__ some processes are already gone and can't be matched.
    # The watch already resolved this profile's pids; it hands them back on
    # close so the caller can kill the survivors.
    monkeypatch.setattr(
        invisible_launch, "_profile_firefox_pids", lambda d: {10, 11}
    )
    monkeypatch.setattr(invisible_launch, "_pid_alive", lambda p: True)
    visibility = iter([True, False])
    monkeypatch.setattr(
        invisible_launch, "_pids_have_visible_window", lambda pids: next(visibility)
    )
    got = invisible_launch._thread_close_watch(
        r"C:\p", threading.Event(), None, lambda: None, interval=0.0,
    )
    assert got == {10, 11}


def test_child_force_kills_profile_firefox_after_teardown(monkeypatch, tmp_path):
    # #129 wiring: after the close-watch decides and inv.__exit__ ran, the
    # profile's remaining firefox.exe are force-killed — AFTER the polite
    # teardown, with the profile dir and the watch's tracked pid set.
    import os
    import sys
    import threading
    import types

    calls = []

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
            calls.append("exit")
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)
    monkeypatch.setattr(
        invisible_launch, "_thread_close_watch", lambda *a, **k: {10, 11}
    )
    monkeypatch.setattr(
        invisible_launch,
        "_kill_profile_firefox",
        lambda d, pids: calls.append(("kill", d, pids)),
    )

    r, w = os.pipe()
    invisible_launch._child(
        {"profile_dir": str(tmp_path), "profile_name": "t", "seed": 1},
        w,
        stop_event=threading.Event(),
    )
    os.close(r)

    assert calls == ["exit", ("kill", str(tmp_path), {10, 11})]


def test_child_renders_at_system_dpr_keeps_spoofed_screen(monkeypatch, tmp_path):
    # #131: the pinned screen.dpr=1 feeds BOTH the JS spoof
    # (zoom.stealth.screen.dpr) and the render scale (layout.css.devPixelsPerPx
    # — invisible_core.prefs derives the two from one profile.screen.dpr), so
    # the window rendered at 1:1 physical px, ignoring the OS 150% scale
    # (content ~1/3 too small on 4K). The render pref alone must be re-pointed
    # at the real OS scale via extra_prefs (overlaid LAST in the engine), while
    # the pin keeps the fingerprint at the chosen resolution. The probe proved
    # window.devicePixelRatio then follows devPixelsPerPx (== the host scale),
    # so the render is readable AND matchMedia/devicePixelRatio agree.
    import os
    import sys
    import threading
    import types

    captured = {}

    class FakeCtx:
        pages = [object()]

        def add_init_script(self, *_a, **_k):
            pass

    class FakeEngine:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def _default_context_kwargs(self):
            return {}

        def __enter__(self):
            return FakeCtx()

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)
    monkeypatch.setattr(invisible_launch, "_work_area", lambda: (3840, 2088))
    monkeypatch.setattr(invisible_launch, "_system_dpr", lambda: 1.5)

    stop = threading.Event()
    stop.set()
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
    os.close(r)

    assert captured["pin"]["screen.width"] == 1920   # fingerprint = chosen
    assert captured["pin"]["screen.dpr"] == 1
    assert captured["extra_prefs"]["layout.css.devPixelsPerPx"] == "1.5"


def test_render_dpr_override_wins_over_engine_prefs():
    # #131 against the REAL engine pref pipeline: invisible_core.prefs sets
    # layout.css.devPixelsPerPx = str(profile.screen.dpr) and applies
    # extra_prefs LAST, so our render override must survive while the
    # C++-read JS spoof prefs keep the pinned identity.
    pytest.importorskip("invisible_core")
    from invisible_core._fpforge import generate_profile
    from invisible_core.prefs import translate_profile_to_prefs

    profile = generate_profile(
        1,
        pin={
            "screen.width": 1920,
            "screen.height": 1080,
            "screen.avail_width": 1920,
            "screen.avail_height": 1040,
            "screen.dpr": 1,
        },
    )
    prefs = translate_profile_to_prefs(
        profile, extra_prefs={"layout.css.devPixelsPerPx": "1.5"}
    )
    assert prefs["layout.css.devPixelsPerPx"] == "1.5"  # render at OS scale
    assert prefs["zoom.stealth.screen.width"] == 1920   # fingerprint = chosen


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
            return {"screen": "engine", "locale": "engine"}

    results = {}
    barrier = threading.Barrier(2)

    def launch(name, res):
        cls = invisible_launch._with_context_overrides(Engine, {"screen": res})
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

    assert results["a"]["screen"] == "1280x720"
    assert results["b"]["screen"] == "3840x2160"
    assert results["a"]["locale"] == "engine"  # engine kwargs remain the base


def test_context_overrides_leave_engine_class_untouched():
    # #123: the override must not leak into the engine class — a later launch
    # WITHOUT a resolution pick (Auto) must get the engine's own kwargs.
    class Engine:
        def _default_context_kwargs(self):
            return {"screen": "engine"}

    invisible_launch._with_context_overrides(Engine, {"screen": "1x1"})
    assert Engine()._default_context_kwargs() == {"screen": "engine"}


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
    monkeypatch.setattr(invisible_launch, "_work_area", lambda: (3840, 2088))
    monkeypatch.setattr(invisible_launch, "_system_dpr", lambda: 1.5)

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

    assert seen["ctx_kwargs"]["screen"] == {"width": 1920, "height": 1080}
    assert seen["ctx_kwargs"]["no_viewport"] is True
    assert "viewport" not in seen["ctx_kwargs"]  # engine's own viewport stripped
    assert (tmp_path / "xulstore.json").exists()  # initial window size seeded
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
    import sys
    import types

    from tests.test_firefox_bookmarks import _make_places

    captured = {}

    class FakeEngine:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            _make_places(str(tmp_path / "places.sqlite"))
            return self

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)
    monkeypatch.setattr(
        invisible_launch, "_wait_profile_released", lambda d: True
    )

    assert invisible_launch._init_places_db(str(tmp_path), 4242) is True
    assert captured["seed"] == 4242
    assert captured["headless"] is True


def test_init_places_db_waits_for_toolbar_root_not_a_fixed_sleep(
    monkeypatch, tmp_path
):
    # #132 (empty-toolbar half): the engine writes places.sqlite to disk long
    # before Places creates the bookmark roots — on a cold first start well past
    # the old fixed 6s sleep. Seeding then found no toolbar parent and silently
    # inserted nothing. The init must WAIT for the toolbar root (the actual
    # readiness signal) and report success only once it exists.
    import sqlite3
    import sys
    import threading
    import types

    from tests.test_firefox_bookmarks import _make_places

    db = str(tmp_path / "places.sqlite")

    class FakeEngine:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            # File appears first, roots only later — like a real cold start.
            sqlite3.connect(db).close()
            threading.Timer(0.4, _make_places, args=(db,)).start()
            return self

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)
    monkeypatch.setattr(
        invisible_launch, "_wait_profile_released", lambda d: True
    )

    assert invisible_launch._init_places_db(str(tmp_path), 1, timeout=10) is True
    from src.services.browser.firefox_bookmarks import places_ready

    assert places_ready(db) is True


def test_init_places_db_settles_profile_release_after_engine_exit(
    monkeypatch, tmp_path
):
    # #132: the engine's __exit__ is a polite Playwright teardown the
    # multi-process Firefox routinely survives; relaunching over the dying
    # instance wedges launch_persistent_context (half-destroyed webProgress).
    # The init must not return until the profile's Firefox is confirmed
    # released — kill survivors and wait, AFTER the polite exit.
    import sys
    import types

    from tests.test_firefox_bookmarks import _make_places

    calls = []

    class FakeEngine:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            _make_places(str(tmp_path / "places.sqlite"))
            return self

        def __exit__(self, *a):
            calls.append("exit")
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)
    monkeypatch.setattr(
        invisible_launch,
        "_wait_profile_released",
        lambda d: calls.append(("settle", d)) or True,
    )

    assert invisible_launch._init_places_db(str(tmp_path), 1) is True
    assert calls == ["exit", ("settle", str(tmp_path))]


def test_init_places_db_wipes_throwaway_session_state(monkeypatch, tmp_path):
    # #132 (the actual wedge): the REAL launch runs with browser.startup.page=3
    # and session-RESTORES the throwaway headless run's session — the restore
    # replaces the initial window juggler attached to (half-destroyed
    # webProgress, SimpleChannel "transport.sendMessage is not a function"
    # churn) and the first juggler command hangs forever: no BROWSER_STARTED,
    # dead close-watch, card stuck "running". Live-proven: wiping the init
    # run's session state unwedges the launch (process-level settling alone
    # did NOT). The init's session is throwaway by definition.
    import sys
    import types

    from tests.test_firefox_bookmarks import _make_places

    class FakeEngine:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            _make_places(str(tmp_path / "places.sqlite"))
            (tmp_path / "sessionstore.jsonlz4").write_bytes(b"x")
            (tmp_path / "sessionCheckpoints.json").write_text("{}")
            (tmp_path / "sessionstore-backups").mkdir()
            (tmp_path / "sessionstore-backups" / "recovery.jsonlz4").write_bytes(
                b"x"
            )
            return self

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)
    monkeypatch.setattr(
        invisible_launch, "_wait_profile_released", lambda d: True
    )

    assert invisible_launch._init_places_db(str(tmp_path), 1) is True
    assert not (tmp_path / "sessionstore.jsonlz4").exists()
    assert not (tmp_path / "sessionCheckpoints.json").exists()
    assert not (tmp_path / "sessionstore-backups").exists()
    assert (tmp_path / "places.sqlite").exists()  # the init's product stays


def test_init_places_db_skips_startup_network_fetch(monkeypatch, tmp_path):
    # The throwaway init must not block on Firefox's startup remote-settings
    # sync (over Tor that fetch stalls the init for minutes) — same
    # no-startup-fetch prefs the real launch uses.
    import sys
    import types

    from tests.test_firefox_bookmarks import _make_places

    captured = {}

    class FakeEngine:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            _make_places(str(tmp_path / "places.sqlite"))
            return self

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)
    monkeypatch.setattr(
        invisible_launch, "_wait_profile_released", lambda d: True
    )

    assert invisible_launch._init_places_db(str(tmp_path), 1) is True
    assert captured["extra_prefs"]["services.settings.server"].startswith("data:")


def test_init_places_db_hung_close_bounded_by_close_grace(monkeypatch, tmp_path):
    # Live-observed: with the database already ready, the headless run's polite
    # __exit__ sometimes hangs ~90s on Firefox shutdown blockers. Once the roots
    # exist the init must abandon the hung close after close_grace (the settle
    # kills the leftovers) instead of sitting out the whole init timeout.
    import sys
    import threading
    import time as _time
    import types

    from tests.test_firefox_bookmarks import _make_places

    release = threading.Event()
    settled = []

    class FakeEngine:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            _make_places(str(tmp_path / "places.sqlite"))
            return self

        def __exit__(self, *a):
            release.wait(30)  # a shutdown-blocker hang
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)
    monkeypatch.setattr(
        invisible_launch,
        "_wait_profile_released",
        lambda d: settled.append(d) or True,
    )

    try:
        t0 = _time.monotonic()
        ok = invisible_launch._init_places_db(
            str(tmp_path), 1, timeout=60, close_grace=0.3
        )
        assert ok is True
        assert _time.monotonic() - t0 < 5
        assert settled == [str(tmp_path)]
    finally:
        release.set()


def test_init_places_db_bounded_when_engine_wedges(monkeypatch, tmp_path):
    # A wedged headless init (the engine hanging in __enter__) must not hang
    # the launch forever — give up after the bound, settle (which kills the
    # wedged Firefox), and let the launch continue without bookmarks.
    import sys
    import threading
    import time as _time
    import types

    release = threading.Event()
    settled = []

    class FakeEngine:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            release.wait(30)
            return self

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)
    monkeypatch.setattr(
        invisible_launch,
        "_wait_profile_released",
        lambda d: settled.append(d) or True,
    )

    try:
        t0 = _time.monotonic()
        ok = invisible_launch._init_places_db(
            str(tmp_path), 1, timeout=0.3, close_grace=0.2
        )
        assert ok is False
        assert _time.monotonic() - t0 < 5
        assert settled == [str(tmp_path)]
    finally:
        release.set()


def test_wait_profile_released_confident_empty_needs_no_kill(monkeypatch):
    monkeypatch.setattr(invisible_launch, "_profile_firefox_pids", lambda d: set())

    def boom(*a, **k):
        raise AssertionError("nothing to kill on a released profile")

    monkeypatch.setattr(invisible_launch, "_kill_profile_firefox", boom)
    assert invisible_launch._wait_profile_released(r"C:\p") is True


def test_wait_profile_released_force_kills_survivors(monkeypatch):
    # Survivors of the polite teardown are force-killed, and the wait returns
    # only once the profile is CONFIRMED released — a poll, not a fixed sleep.
    state = {"pids": {10}}
    monkeypatch.setattr(
        invisible_launch, "_profile_firefox_pids", lambda d: set(state["pids"])
    )
    killed = []

    def kill(d):
        killed.append(d)
        state["pids"] = set()

    monkeypatch.setattr(invisible_launch, "_kill_profile_firefox", kill)
    assert (
        invisible_launch._wait_profile_released(r"C:\p", grace=0.0, timeout=5.0)
        is True
    )
    assert killed == [r"C:\p"]


def test_wait_profile_released_no_verdict_uses_single_pid_probe(monkeypatch):
    # No WMI verdict (non-Windows, or the query failed): the pgrep/single-pid
    # probe is the fallback; no process found means released, immediately —
    # never a 20s dead wait on the fork path.
    import time as _time

    monkeypatch.setattr(invisible_launch, "_profile_firefox_pids", lambda d: None)
    monkeypatch.setattr(invisible_launch, "_firefox_pid", lambda d: None)

    def boom(*a, **k):
        raise AssertionError("nothing to kill on a released profile")

    monkeypatch.setattr(invisible_launch, "_kill_profile_firefox", boom)
    t0 = _time.monotonic()
    assert invisible_launch._wait_profile_released(r"C:\p") is True
    assert _time.monotonic() - t0 < 2


def test_seed_bookmarks_reinits_when_toolbar_root_missing(monkeypatch, tmp_path):
    # ff-test11's state: a wedged init left places.sqlite WITHOUT the toolbar
    # root, so every launch skipped the init (file exists) and every seed
    # silently inserted nothing — bookmarks empty forever. Readiness is the
    # toolbar root; a rootless database must re-run the init (which lets the
    # engine finish creating the roots).
    import sqlite3

    from tests.test_firefox_bookmarks import _SCHEMA

    db = str(tmp_path / "places.sqlite")
    c = sqlite3.connect(db)
    for stmt in _SCHEMA:
        c.execute(stmt)
    c.commit()
    c.close()

    inits = []
    monkeypatch.setattr(
        invisible_launch,
        "_init_places_db",
        lambda d, s: inits.append((d, s)) or False,
    )
    invisible_launch._seed_firefox_bookmarks(
        str(tmp_path), [{"name": "a", "url": "https://a/"}], 7
    )
    assert inits == [(str(tmp_path), 7)]


def test_seed_bookmarks_ready_db_needs_no_engine_start(monkeypatch, tmp_path):
    # With a toolbar-rooted places.sqlite already on disk the seed writes
    # straight into it — no headless engine init.
    from tests.test_firefox_bookmarks import _make_places, _toolbar_bookmarks

    db = str(tmp_path / "places.sqlite")
    _make_places(db)

    def boom(*a, **k):
        raise AssertionError("no engine init when places.sqlite is ready")

    monkeypatch.setattr(invisible_launch, "_init_places_db", boom)
    invisible_launch._seed_firefox_bookmarks(
        str(tmp_path), [{"name": "a", "url": "https://a.example/"}], 7
    )
    assert _toolbar_bookmarks(db) == [("a", "https://a.example/")]


def test_child_starts_engine_exactly_once_when_already_seeded(
    monkeypatch, tmp_path
):
    # #132: the hot path (bookmarks already seeded, marker matches) must start
    # the engine EXACTLY once — the visible launch. The double start (headless
    # init + real launch back-to-back) is what wedged the patched Firefox in a
    # half-destroyed webProgress: no BROWSER_STARTED, close-watch dead, card
    # stuck "running".
    import os
    import sys
    import threading
    import types

    from tests.test_firefox_bookmarks import _make_places

    starts = []

    class FakeCtx:
        pages = [object()]

        def add_init_script(self, *_a, **_k):
            pass

    class FakeEngine:
        def __init__(self, **kwargs):
            starts.append(kwargs.get("headless"))

        def _default_context_kwargs(self):
            return {}

        def __enter__(self):
            return FakeCtx()

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)
    monkeypatch.setattr(
        invisible_launch, "_kill_profile_firefox", lambda d, pids=None: None
    )

    marks = [{"name": "a", "url": "https://a/"}]
    _make_places(str(tmp_path / "places.sqlite"))
    (tmp_path / ".persona-bookmarks-sig").write_text(
        invisible_launch._bookmarks_sig(marks), encoding="utf-8"
    )

    stop = threading.Event()
    stop.set()
    r, w = os.pipe()
    invisible_launch._child(
        {
            "profile_dir": str(tmp_path),
            "profile_name": "t",
            "seed": 1,
            "bookmarks": marks,
        },
        w,
        stop_event=stop,
    )
    out = os.read(r, 65536).decode()
    os.close(r)

    assert starts == [False]  # ONE engine start, the visible one
    assert "BROWSER_STARTED" in out


def test_profile_prefs_bookmarks_toolbar_always_visible():
    # Seeded bookmarks live on the toolbar; without this pref Firefox only
    # shows the toolbar on the new-tab page, so the seed looks "missing".
    prefs = _profile_prefs({})
    assert prefs["browser.toolbars.bookmarks.visibility"] == "always"


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
