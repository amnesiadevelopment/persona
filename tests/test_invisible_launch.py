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
    # A fresh profile opens at half the work area in PHYSICAL px. xulstore.json's
    # main-window size restores in device pixels (live-proven: a 1920x1068
    # physical window at 1.5 OS scale persisted and restored as "1920"/"1068"),
    # so the seed is the physical work area halved, NOT divided by the OS scale —
    # dividing would open the window a third too small on a HiDPI display.
    import json as _json

    monkeypatch.setattr(invisible_launch, "_work_area", lambda: (3840, 2088))
    monkeypatch.setattr(invisible_launch, "_system_dpr", lambda: 1.5)
    invisible_launch._seed_window_size(str(tmp_path))

    data = _json.loads((tmp_path / "xulstore.json").read_text(encoding="utf-8"))
    win = data["chrome://browser/content/browser.xhtml"]["main-window"]
    assert win["width"] == "1920"   # 3840 / 2
    assert win["height"] == "1044"  # 2088 / 2
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


def test_profile_prefs_uncloak_windows():
    # #142: the engine implements headless on Windows/macOS by making the
    # patched binary DWM-cloak its own windows (zoom.stealth.cloak_windows).
    # The bookmarks seeding runs the engine once headless in the SAME profile,
    # and Firefox persists that pref into prefs.js — so the next visible launch
    # inherited the cloak and opened every window invisible (live-proven:
    # MozillaWindowClass vis=True cloaked=1 for the whole session). The visible
    # launch must force the cloak off.
    prefs = _profile_prefs({})
    assert prefs["zoom.stealth.cloak_windows"] is False


def test_scrub_cloak_pref_removes_stale_true(tmp_path):
    # The cloak gate acts on the pref value ALREADY IN prefs.js when the
    # window is created — the launch's own prefs land too late to uncloak
    # (live-proven: a launch right after the headless init stayed cloaked
    # despite the False override; the NEXT launch, with False persisted, was
    # visible). So the stale true must be scrubbed from prefs.js before the
    # visible launch.
    prefs = tmp_path / "prefs.js"
    prefs.write_text(
        'user_pref("browser.startup.page", 3);\n'
        'user_pref("zoom.stealth.cloak_windows", true);\n'
        'user_pref("widget.windows.window_occlusion_tracking.enabled", false);\n',
        encoding="utf-8",
    )
    invisible_launch._scrub_headless_cloak_prefs(str(tmp_path))
    left = prefs.read_text(encoding="utf-8")
    assert "cloak_windows" not in left
    assert "window_occlusion_tracking" not in left
    assert "browser.startup.page" in left  # everything else untouched


def test_scrub_cloak_pref_tolerates_missing_profile(tmp_path):
    invisible_launch._scrub_headless_cloak_prefs(str(tmp_path / "nope"))
    invisible_launch._scrub_headless_cloak_prefs("")


def test_headless_init_prefs_keep_engine_cloak():
    # The one-time headless places init must stay invisible: its prefs must NOT
    # carry the uncloak override (the engine's setdefault would make an
    # explicit False win and flash a window at the user).
    from src.services.browser.invisible_launch import _NO_STARTUP_FETCH

    assert "zoom.stealth.cloak_windows" not in _NO_STARTUP_FETCH


def test_raise_profile_window_skips_cloaked_hwnd(monkeypatch):
    # A DWM-cloaked window passes IsWindowVisible but is invisible to the user;
    # raising it does nothing and must not count as success — keep polling for
    # a really-visible window instead.
    monkeypatch.setattr(invisible_launch._platform, "IS_WINDOWS", True)
    monkeypatch.setattr(invisible_launch, "_profile_firefox_pids", lambda d: {10})
    enums = iter([[(111, 10)], [(111, 10), (222, 10)]])
    monkeypatch.setattr(invisible_launch, "_visible_windows", lambda: next(enums))
    monkeypatch.setattr(
        invisible_launch, "_window_cloaked", lambda hwnd: hwnd == 111
    )
    raised = []
    monkeypatch.setattr(
        invisible_launch, "_bring_window_to_foreground", raised.append
    )
    assert (
        invisible_launch._raise_profile_window(r"C:\p", timeout=2, interval=0.01)
        is True
    )
    assert raised == [222]


def test_enter_on_worker_bounded_abandons_wedged_launch_and_retries(
    monkeypatch, tmp_path
):
    # #137: a proxied launch of the patched Firefox wedges nondeterministically
    # inside launch_persistent_context (live: ~half of fresh proxied launches
    # on Windows never reach the initial page attach), and killing the browser
    # does NOT make the blocked sync __enter__ raise — the driver keeps
    # waiting. So the enter runs on an abandonable worker thread: an overrun
    # attempt is abandoned, this profile's Firefox is killed + settled, and a
    # FRESH worker retries. The unbounded path hung the child forever — no
    # BROWSER_STARTED, no stop button.
    import threading
    import time as _time

    kills = []
    attempts = []

    class Ctx:
        pages = [object()]

    class Engine:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            attempts.append("enter")
            if len(attempts) == 1:
                threading.Event().wait(30)  # wedged, never returns
            return Ctx()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        invisible_launch, "_kill_profile_firefox",
        lambda d, known_pids=None: kills.append(d),
    )
    settles = []
    monkeypatch.setattr(
        invisible_launch, "_wait_profile_released",
        lambda d: settles.append(d) or True,
    )

    t0 = _time.monotonic()
    session = invisible_launch._enter_on_worker(
        Engine, {}, str(tmp_path), attempts=2, per_try=0.3
    )
    assert session is not None
    assert session.ctx is not None
    assert len(attempts) == 2
    # The wedged first attempt is abandoned: its Firefox killed and the profile
    # confirmed released before the retry (never relaunch over a dying one).
    assert str(tmp_path) in kills
    assert settles == [str(tmp_path)]
    assert _time.monotonic() - t0 < 10


def test_enter_on_worker_stop_event_aborts_without_retry(monkeypatch, tmp_path):
    # Pressing [stop] during a wedged launch aborts it (kill + settle) and does
    # NOT retry. per_try is large to prove the abort came from STOP, not the
    # overrun bound.
    import threading
    import time as _time

    attempts = []

    class Engine:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            attempts.append("enter")
            threading.Event().wait(30)
            raise RuntimeError("browser process exited")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        invisible_launch, "_kill_profile_firefox", lambda d, known_pids=None: None
    )
    monkeypatch.setattr(invisible_launch, "_wait_profile_released", lambda d: True)

    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    t0 = _time.monotonic()
    session = invisible_launch._enter_on_worker(
        Engine, {}, str(tmp_path), attempts=3, per_try=60, stop_event=stop
    )
    assert session is None
    assert attempts == ["enter"]  # no retry after a user cancel
    assert _time.monotonic() - t0 < 10


def test_worker_session_runs_ctx_calls_and_teardown_on_worker():
    # Playwright's sync ctx is thread-affine: add_init_script and __exit__ must
    # run on the thread that created ctx. _WorkerSession marshals both onto its
    # worker.
    import threading

    class Ctx:
        pages = [object()]

        def __init__(self):
            self.calls_thread = []

        def add_init_script(self, _s):
            self.calls_thread.append(threading.get_ident())
            return "added"

    class Engine:
        def __init__(self, **kw):
            self.exit_thread = None

        def __enter__(self):
            return self.ctx

        def __exit__(self, *a):
            self.exit_thread = threading.get_ident()
            return False

    eng = Engine()
    eng.ctx = Ctx()
    session = invisible_launch._enter_on_worker(
        lambda **kw: eng, {}, "d", attempts=1, per_try=5
    )
    assert session is not None
    worker_id = session._worker.ident
    assert session.run_on_worker(lambda: session.ctx.add_init_script("x")) == "added"
    assert eng.ctx.calls_thread == [worker_id]
    session.teardown()
    assert eng.exit_thread == worker_id  # teardown ran on the worker
    assert not session._worker.is_alive()
    # A second teardown (the stop path: stop_gracefully already tore down,
    # then _child tears down again) must return instead of waiting forever
    # for the dead worker to answer run_on_worker.
    session.teardown()


def test_child_stop_during_wedged_launch_emits_cancelled(monkeypatch, tmp_path):
    # #137 wiring: a launch wedged inside __enter__ that the user STOPs reports
    # LAUNCH_CANCELLED (the launcher tears the session down on it) and still
    # closes the pipe. The worker is abandoned after the overrun/stop; killing
    # the browser does not unblock the wedged sync enter.
    import os
    import sys
    import threading
    import types

    class FakeEngine:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            threading.Event().wait(30)  # wedged, never returns
            raise RuntimeError("browser process exited")

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)
    monkeypatch.setattr(
        invisible_launch, "_kill_profile_firefox", lambda d, known_pids=None: None
    )
    monkeypatch.setattr(invisible_launch, "_wait_profile_released", lambda d: True)

    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    r, w = os.pipe()
    invisible_launch._child(
        {"profile_dir": str(tmp_path), "profile_name": "t", "seed": 1},
        w,
        stop_event=stop,
    )
    out = os.read(r, 65536).decode()
    os.close(r)
    assert "LAUNCH_CANCELLED" in out
    assert "BROWSER_CLOSED" in out


def test_geo_shortcircuit_skips_egress_lookup_with_concrete_timezone(monkeypatch):
    # #137: with a proxy set, the engine's prepare_session_geo discovers the
    # egress IP THROUGH the proxy before every launch. persona always passes a
    # concrete timezone, so the lookup is pure latency and one more network
    # step for a proxied launch to wedge on — the installed shortcircuit must
    # resolve a concrete zone with NO network round-trip.
    pytest.importorskip("invisible_playwright")
    pytest.importorskip("invisible_core")
    import invisible_core._geo as core_geo
    from invisible_playwright import launcher as iplauncher

    def boom(*a, **k):
        raise AssertionError("egress lookup must not run for a concrete zone")

    monkeypatch.setattr(core_geo, "discover_egress_ip", boom)
    invisible_launch._install_geo_shortcircuit()
    geo = iplauncher.prepare_session_geo(
        "Europe/Berlin", {"server": "socks5://127.0.0.1:9"}
    )
    assert geo.timezone == "Europe/Berlin"
    assert geo.egress_ip is None


def test_visible_window_pids_built_on_shared_enumeration(monkeypatch):
    # The close-watch pid set and the #136 window raise share one enumeration:
    # (hwnd, pid) pairs, None staying a distinct no-verdict.
    monkeypatch.setattr(
        invisible_launch, "_visible_windows", lambda: [(1, 10), (2, 10), (3, 20)]
    )
    assert invisible_launch._visible_window_pids() == {10, 20}
    monkeypatch.setattr(invisible_launch, "_visible_windows", lambda: None)
    assert invisible_launch._visible_window_pids() is None


def test_raise_profile_window_raises_only_profile_hwnd(monkeypatch):
    # #136: the raise must target the window owned by THIS profile's Firefox
    # pid — never another profile's (or any other app's) window.
    monkeypatch.setattr(invisible_launch._platform, "IS_WINDOWS", True)
    monkeypatch.setattr(invisible_launch, "_profile_firefox_pids", lambda d: {10})
    monkeypatch.setattr(
        invisible_launch, "_visible_windows", lambda: [(111, 999), (222, 10)]
    )
    raised = []
    monkeypatch.setattr(
        invisible_launch, "_bring_window_to_foreground", raised.append
    )
    assert (
        invisible_launch._raise_profile_window(r"C:\p", timeout=2, interval=0.01)
        is True
    )
    assert raised == [222]


def test_raise_profile_window_waits_for_window_to_appear(monkeypatch):
    # The window can lag BROWSER_STARTED by a beat; poll until it exists
    # instead of giving up on the first empty enumeration.
    monkeypatch.setattr(invisible_launch._platform, "IS_WINDOWS", True)
    monkeypatch.setattr(invisible_launch, "_profile_firefox_pids", lambda d: {10})
    enums = iter([[], [(5, 999)], [(7, 10)]])
    monkeypatch.setattr(invisible_launch, "_visible_windows", lambda: next(enums))
    raised = []
    monkeypatch.setattr(
        invisible_launch, "_bring_window_to_foreground", raised.append
    )
    assert (
        invisible_launch._raise_profile_window(r"C:\p", timeout=2, interval=0.01)
        is True
    )
    assert raised == [7]


def test_raise_profile_window_noop_off_windows(monkeypatch):
    monkeypatch.setattr(invisible_launch._platform, "IS_WINDOWS", False)

    def boom(*a, **k):
        raise AssertionError("no window work off Windows")

    monkeypatch.setattr(invisible_launch, "_visible_windows", boom)
    monkeypatch.setattr(invisible_launch, "_profile_firefox_pids", boom)
    assert invisible_launch._raise_profile_window(r"C:\p") is False


def test_child_raises_profile_window_after_started_on_windows(
    monkeypatch, tmp_path
):
    # #136 wiring: the engine's window opens BEHIND persona on Windows (the
    # launch runs in persona's own process, which holds the foreground), so
    # once BROWSER_STARTED is reported the profile's window must be raised —
    # off the session thread.
    import os
    import sys
    import threading
    import types

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
    monkeypatch.setattr(invisible_launch._platform, "IS_WINDOWS", True)
    monkeypatch.setattr(
        invisible_launch, "_thread_close_watch", lambda *a, **k: None
    )
    monkeypatch.setattr(
        invisible_launch, "_kill_profile_firefox", lambda d, pids=None: None
    )

    raised = []
    raised_ev = threading.Event()

    def fake_raise(profile_dir):
        raised.append(profile_dir)
        raised_ev.set()

    monkeypatch.setattr(invisible_launch, "_raise_profile_window", fake_raise)

    stop = threading.Event()
    stop.set()
    r, w = os.pipe()
    invisible_launch._child(
        {"profile_dir": str(tmp_path), "profile_name": "t", "seed": 1},
        w,
        stop_event=stop,
    )
    out = os.read(r, 65536).decode()
    os.close(r)
    assert "BROWSER_STARTED" in out
    assert raised_ev.wait(5)
    assert raised == [str(tmp_path)]


def test_child_does_not_raise_window_off_windows(monkeypatch, tmp_path):
    import os
    import sys
    import threading
    import types

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
    monkeypatch.setattr(invisible_launch._platform, "IS_WINDOWS", False)
    monkeypatch.setattr(
        invisible_launch, "_thread_close_watch", lambda *a, **k: None
    )
    monkeypatch.setattr(
        invisible_launch, "_kill_profile_firefox", lambda d, pids=None: None
    )

    def boom(*a, **k):
        raise AssertionError("no window raise off Windows")

    monkeypatch.setattr(invisible_launch, "_raise_profile_window", boom)

    stop = threading.Event()
    stop.set()
    r, w = os.pipe()
    invisible_launch._child(
        {"profile_dir": str(tmp_path), "profile_name": "t", "seed": 1},
        w,
        stop_event=stop,
    )
    out = os.read(r, 65536).decode()
    os.close(r)
    assert "BROWSER_STARTED" in out


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
    monkeypatch.setattr(
        invisible_launch, "_win_firefox_command_lines", lambda: None
    )

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
        invisible_launch, "_win_firefox_command_lines", lambda: None
    )
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


def test_fork_close_watch_closes_on_pid_death(monkeypatch):
    import threading

    # #143: the Linux close signal is the profile's Firefox pid dying — the
    # window's X quits Firefox there. The pid is resolved once (pgrep), then
    # polled with the cheap liveness check.
    monkeypatch.setattr(invisible_launch, "_firefox_pid", lambda d: 4242)
    liveness = iter([True, True, False])
    monkeypatch.setattr(invisible_launch, "_pid_alive", lambda p: next(liveness))
    got = invisible_launch._fork_close_watch(
        "/p", threading.Event(), interval=0.0
    )
    assert got == {4242}
    assert next(liveness, "done") == "done"  # closed exactly on the dead poll


def test_fork_close_watch_gives_up_when_process_never_seen(monkeypatch):
    import threading
    import time as _time

    # A launch whose Firefox is never seen must not wedge the profile
    # "running" forever — give up after no_process_timeout so the child emits
    # BROWSER_CLOSED and tears down.
    monkeypatch.setattr(invisible_launch, "_firefox_pid", lambda d: None)
    t0 = _time.monotonic()
    got = invisible_launch._fork_close_watch(
        "/p", threading.Event(), no_process_timeout=0.05, interval=0.01
    )
    assert got is None
    assert _time.monotonic() - t0 < 5


def test_fork_close_watch_returns_tracked_pid_on_stop(monkeypatch):
    import threading

    # STOP: the SIGTERM handler's stop_gracefully sets `closed`; the watch
    # must hand back the tracked pid so the survivor force-kill has a target
    # even when the polite teardown failed.
    closed = threading.Event()
    monkeypatch.setattr(invisible_launch, "_firefox_pid", lambda d: 7)

    def alive(_p):
        closed.set()
        return True

    monkeypatch.setattr(invisible_launch, "_pid_alive", alive)
    got = invisible_launch._fork_close_watch("/p", closed, interval=0.0)
    assert got == {7}


def test_child_fork_path_teardown_fires_on_pid_exit(monkeypatch, tmp_path):
    # #143 wiring: the fork (Linux) path must NOT watch ctx.pages — the ctx
    # lives on _enter_with_timeout's launch thread, and the sync API's
    # dispatcher greenlet never pumps events for a poll from the child's own
    # thread, so ctx.pages is a frozen snapshot that never drops to 0: no
    # teardown, no BROWSER_CLOSED, no close in the log, card stuck "running".
    # The close signal is the pid watch; when it fires the child tears down,
    # force-kills the survivors with the tracked pids and emits BROWSER_CLOSED.
    import os
    import signal
    import sys
    import types

    class FakeCtx:
        @property
        def pages(self):
            raise AssertionError("fork path must not poll ctx.pages (#143)")

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

    watches, kills, exits = [], [], []
    monkeypatch.setattr(
        invisible_launch, "_fork_close_watch",
        lambda d, closed, **k: watches.append(d) or {10},
    )
    monkeypatch.setattr(
        invisible_launch, "_kill_profile_firefox",
        lambda d, pids=None: kills.append((d, pids)),
    )

    def no_thread_watch(*_a, **_k):
        raise AssertionError("thread close-watch on the fork path")

    monkeypatch.setattr(invisible_launch, "_thread_close_watch", no_thread_watch)
    monkeypatch.setattr(invisible_launch.os, "_exit", lambda code: exits.append(code))
    monkeypatch.setattr(invisible_launch, "_raise_profile_window", lambda *a, **k: None)

    old_term = signal.getsignal(signal.SIGTERM)
    r, w = os.pipe()
    try:
        invisible_launch._child(
            {"profile_dir": str(tmp_path), "profile_name": "t", "seed": 1}, w
        )
    finally:
        signal.signal(signal.SIGTERM, old_term)
    out = os.read(r, 65536).decode()
    os.close(r)

    assert watches == [str(tmp_path)]
    assert kills == [(str(tmp_path), {10})]
    assert exits == [0]
    assert "BROWSER_STARTED" in out
    assert "BROWSER_CLOSED" in out


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
    monkeypatch.setattr(
        invisible_launch, "_raise_profile_window", lambda *a, **k: None
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
    monkeypatch.setattr(
        invisible_launch, "_thread_close_watch", lambda *a, **k: None
    )
    monkeypatch.setattr(
        invisible_launch, "_kill_profile_firefox", lambda d, pids=None: None
    )
    monkeypatch.setattr(
        invisible_launch, "_raise_profile_window", lambda *a, **k: None
    )

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
    # The REAL close-watch runs here on purpose: with stop_event pre-set it
    # calls stop_gracefully (first teardown, worker exits) and _child then
    # tears down AGAIN — the second teardown must be a no-op, not a forever-
    # blocking run_on_worker on the dead worker (the STOP-path deadlock).
    monkeypatch.setattr(
        invisible_launch, "_kill_profile_firefox", lambda d, pids=None: None
    )
    monkeypatch.setattr(
        invisible_launch, "_raise_profile_window", lambda *a, **k: None
    )

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

    def fake_seed(profile_dir, bookmarks, seed, stop_event=None):
        calls.append("seed")
        seeded["args"] = (profile_dir, bookmarks, seed)

    monkeypatch.setattr(invisible_launch, "_seed_firefox_bookmarks", fake_seed)
    monkeypatch.setattr(
        invisible_launch, "_ensure_firefox_policies", lambda: None
    )
    monkeypatch.setattr(
        invisible_launch, "_thread_close_watch", lambda *a, **k: None
    )
    monkeypatch.setattr(
        invisible_launch, "_kill_profile_firefox", lambda d, pids=None: None
    )
    monkeypatch.setattr(
        invisible_launch, "_raise_profile_window", lambda *a, **k: None
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
        # Settled at least once (per bailed attempt + the final settle), and
        # only ever for THIS profile.
        assert settled and set(settled) == {str(tmp_path)}
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
        lambda d, s, stop_event=None: inits.append((d, s)) or False,
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


def test_seed_bookmarks_relaunch_keeps_exactly_one_copy(monkeypatch, tmp_path):
    # #144(a): the seed runs on EVERY launch; relaunching with the same set
    # must leave exactly one copy of each bookmark, never a duplicate row.
    from tests.test_firefox_bookmarks import _make_places, _toolbar_bookmarks

    db = str(tmp_path / "places.sqlite")
    _make_places(db)
    marks = [{"name": "a", "url": "https://a.example/"}]
    invisible_launch._seed_firefox_bookmarks(str(tmp_path), marks, 7)
    invisible_launch._seed_firefox_bookmarks(str(tmp_path), marks, 7)
    assert _toolbar_bookmarks(db) == [("a", "https://a.example/")]


def test_seed_bookmarks_edited_set_replaces_old(monkeypatch, tmp_path):
    # #144: Mars removed bookmarks in the profile editor and they came back —
    # the launch seed must reconcile the toolbar to the CURRENT set, deleting
    # rows that are no longer in it.
    from tests.test_firefox_bookmarks import _make_places, _toolbar_bookmarks

    db = str(tmp_path / "places.sqlite")
    _make_places(db)
    invisible_launch._seed_firefox_bookmarks(
        str(tmp_path),
        [
            {"name": "a", "url": "https://a.example/"},
            {"name": "b", "url": "https://b.example/"},
        ],
        7,
    )
    invisible_launch._seed_firefox_bookmarks(
        str(tmp_path), [{"name": "b", "url": "https://b.example/"}], 7
    )
    assert _toolbar_bookmarks(db) == [("b", "https://b.example/")]


def test_seed_bookmarks_empty_set_clears_toolbar(monkeypatch, tmp_path):
    # #144(b): ALL bookmarks removed in the profile editor → the next launch
    # clears the toolbar. No engine init for that — the db already exists.
    from tests.test_firefox_bookmarks import _make_places, _toolbar_bookmarks

    db = str(tmp_path / "places.sqlite")
    _make_places(db)
    invisible_launch._seed_firefox_bookmarks(
        str(tmp_path), [{"name": "a", "url": "https://a.example/"}], 7
    )

    def boom(*a, **k):
        raise AssertionError("no engine init when places.sqlite is ready")

    monkeypatch.setattr(invisible_launch, "_init_places_db", boom)
    invisible_launch._seed_firefox_bookmarks(str(tmp_path), [], 7)
    assert _toolbar_bookmarks(db) == []


def test_seed_bookmarks_empty_set_without_places_skips_init(monkeypatch, tmp_path):
    # No bookmarks and no places.sqlite yet: there is nothing to clear —
    # never burn a tens-of-seconds headless engine init to write nothing.
    def boom(*a, **k):
        raise AssertionError("no engine init for an empty set on a fresh profile")

    monkeypatch.setattr(invisible_launch, "_init_places_db", boom)
    invisible_launch._seed_firefox_bookmarks(str(tmp_path), [], 7)
    assert not (tmp_path / "places.sqlite").exists()


def test_child_starts_engine_exactly_once_when_already_seeded(
    monkeypatch, tmp_path
):
    # #132: the hot path (places.sqlite ready, bookmarks reconciled in-place)
    # must start the engine EXACTLY once — the visible launch. The double
    # start (headless init + real launch back-to-back) is what wedged the
    # patched Firefox in a half-destroyed webProgress: no BROWSER_STARTED,
    # close-watch dead, card stuck "running".
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
    monkeypatch.setattr(
        invisible_launch, "_thread_close_watch", lambda *a, **k: None
    )
    monkeypatch.setattr(
        invisible_launch, "_raise_profile_window", lambda *a, **k: None
    )

    marks = [{"name": "a", "url": "https://a/"}]
    _make_places(str(tmp_path / "places.sqlite"))

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
    monkeypatch.setattr(
        invisible_launch, "_thread_close_watch", lambda *a, **k: None
    )
    monkeypatch.setattr(
        invisible_launch, "_kill_profile_firefox", lambda d, pids=None: None
    )
    monkeypatch.setattr(
        invisible_launch, "_raise_profile_window", lambda *a, **k: None
    )

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


def test_profile_prefs_session_restore_owns_the_window():
    # #148: SessionStore only lets the restored session OWN the startup window
    # (overwriteTabs) when the cmdline URL equals nsIBrowserHandler.defaultArgs.
    # Playwright hardcodes an "about:blank" cmdline URL on every persistent
    # launch, and with browser.startup.page=3 defaultArgs is the homepage — so
    # every relaunch KEPT an extra blank tab next to the restored ones. Restore
    # must come from resume_session_once (re-armed via user.js each launch)
    # with startup.page=0, which keeps defaultArgs at "about:blank".
    prefs = _profile_prefs({"search_engine": "duckduckgo"})
    assert prefs["browser.startup.page"] == 0
    assert prefs["browser.sessionstore.resume_session_once"] is True
    assert prefs["browser.sessionstore.resume_from_crash"] is True
    # The homepage still feeds the Home button and the first-launch start page.
    assert "duckduckgo.com" in prefs["browser.startup.homepage"]


def test_has_saved_session(tmp_path):
    assert invisible_launch._has_saved_session(str(tmp_path)) is False
    assert invisible_launch._has_saved_session("") is False

    store = tmp_path / "sessionstore.jsonlz4"
    store.write_bytes(b"x")
    assert invisible_launch._has_saved_session(str(tmp_path)) is True

    store.unlink()
    backups = tmp_path / "sessionstore-backups"
    backups.mkdir()
    (backups / "recovery.jsonlz4").write_bytes(b"x")  # crashed-session backup
    assert invisible_launch._has_saved_session(str(tmp_path)) is True


def test_child_first_launch_swallows_cmdline_url_and_opens_start_page(
    monkeypatch, tmp_path
):
    # #148, fresh profile: the trailing -new-window flag must consume
    # Playwright's hardcoded "about:blank" cmdline URL (a positional URL makes
    # SessionStore keep an extra blank tab on every restore launch), and with
    # nothing to restore the lone about:blank tab is navigated to the chosen
    # start page so the window isn't empty.
    import os
    import sys
    import threading
    import types

    captured = {}
    gotos = []

    class FakePage:
        def goto(self, url, **kwargs):
            gotos.append((url, kwargs))

    class FakeCtx:
        pages = [FakePage()]

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
    monkeypatch.setattr(
        invisible_launch, "_thread_close_watch", lambda *a, **k: None
    )
    monkeypatch.setattr(
        invisible_launch, "_kill_profile_firefox", lambda d, pids=None: None
    )
    monkeypatch.setattr(
        invisible_launch, "_raise_profile_window", lambda *a, **k: None
    )

    stop = threading.Event()
    stop.set()
    r, w = os.pipe()
    invisible_launch._child(
        {
            "profile_dir": str(tmp_path),
            "profile_name": "t",
            "seed": 1,
            "search_engine": "duckduckgo",
        },
        w,
        stop_event=stop,
    )
    os.close(r)

    assert captured["extra_args"][-1] == "-new-window"
    assert len(gotos) == 1
    url, kwargs = gotos[0]
    assert "duckduckgo.com" in url
    assert kwargs.get("wait_until") == "commit"  # don't block on a full load


def test_child_restore_launch_leaves_restored_tabs_alone(monkeypatch, tmp_path):
    # #148, relaunch: with a saved session Firefox restores the user's tabs and
    # the juggler-attached initial page IS a restored tab — navigating it would
    # clobber the user's session. No page may be touched.
    import os
    import sys
    import threading
    import types

    gotos = []

    class FakePage:
        def goto(self, url, **kwargs):
            gotos.append(url)

    class FakeCtx:
        pages = [FakePage()]

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
    monkeypatch.setattr(
        invisible_launch, "_thread_close_watch", lambda *a, **k: None
    )
    monkeypatch.setattr(
        invisible_launch, "_kill_profile_firefox", lambda d, pids=None: None
    )
    monkeypatch.setattr(
        invisible_launch, "_raise_profile_window", lambda *a, **k: None
    )

    (tmp_path / "sessionstore.jsonlz4").write_bytes(b"x")

    stop = threading.Event()
    stop.set()
    r, w = os.pipe()
    invisible_launch._child(
        {"profile_dir": str(tmp_path), "profile_name": "t", "seed": 1},
        w,
        stop_event=stop,
    )
    os.close(r)

    assert gotos == []


# ---------------------------------------------------------------------------
# #149: the Windows pid scan must not depend on spawning PATH-searched tools.
# In the packaged flet app every powershell/taskkill spawn silently failed, so
# the close-watch never resolved the profile's pids and every X-close was only
# "detected" by the 60s give-up (live-proven: persona's own logs show every
# Firefox close landing at start+60..66s while STOPs resolve in 1s).
# ---------------------------------------------------------------------------


def test_profile_pids_ctypes_scan_scopes_to_profile(monkeypatch):
    monkeypatch.setattr(invisible_launch._platform, "IS_WINDOWS", True)
    monkeypatch.setattr(
        invisible_launch,
        "_win_firefox_command_lines",
        lambda: [
            (10, r"firefox.exe -no-remote -profile C:\p\dir\.invisible-profile"),
            (11, r"firefox.exe -contentproc -parentPid 10"),
            (12, r"firefox.exe -no-remote -profile C:\other\.invisible-profile"),
        ],
    )

    def boom(*a, **k):
        raise AssertionError("PowerShell must not be spawned")

    monkeypatch.setattr(invisible_launch.subprocess, "check_output", boom)
    got = invisible_launch._profile_firefox_pids(r"C:\p\dir\.invisible-profile")
    assert got == {10}


def test_profile_pids_ctypes_confident_empty_skips_powershell(monkeypatch):
    monkeypatch.setattr(invisible_launch._platform, "IS_WINDOWS", True)
    monkeypatch.setattr(
        invisible_launch, "_win_firefox_command_lines", lambda: []
    )

    def boom(*a, **k):
        raise AssertionError("PowerShell must not be spawned")

    monkeypatch.setattr(invisible_launch.subprocess, "check_output", boom)
    assert invisible_launch._profile_firefox_pids(r"C:\p\dir") == set()


def test_profile_pids_unreadable_cmdline_falls_back_to_powershell(monkeypatch):
    # A firefox.exe whose command line can't be read is "no verdict for that
    # process" — a confident empty can't be claimed, so PowerShell decides.
    monkeypatch.setattr(invisible_launch._platform, "IS_WINDOWS", True)
    monkeypatch.setattr(invisible_launch._platform, "no_window_kwargs", lambda: {})
    monkeypatch.setattr(
        invisible_launch, "_win_firefox_command_lines", lambda: [(10, None)]
    )
    monkeypatch.setattr(
        invisible_launch.subprocess, "check_output", lambda *a, **k: "10\n"
    )
    assert invisible_launch._profile_firefox_pids(r"C:\p\dir") == {10}


def test_profile_pids_ctypes_match_wins_despite_unreadable_sibling(monkeypatch):
    # Positive ctypes evidence is enough even when another process is
    # unreadable — no PowerShell needed.
    monkeypatch.setattr(invisible_launch._platform, "IS_WINDOWS", True)
    monkeypatch.setattr(
        invisible_launch,
        "_win_firefox_command_lines",
        lambda: [
            (10, r"firefox.exe -profile C:\p\dir\.invisible-profile"),
            (11, None),
        ],
    )

    def boom(*a, **k):
        raise AssertionError("PowerShell must not be spawned")

    monkeypatch.setattr(invisible_launch.subprocess, "check_output", boom)
    got = invisible_launch._profile_firefox_pids(r"C:\p\dir\.invisible-profile")
    assert got == {10}


def test_profile_pids_powershell_fallback_uses_absolute_path(monkeypatch):
    # The packaged app couldn't find PATH-searched tools; when the ctypes scan
    # has no verdict the fallback must invoke powershell.exe by absolute path.
    import os as _os

    monkeypatch.setattr(invisible_launch._platform, "IS_WINDOWS", True)
    monkeypatch.setattr(invisible_launch._platform, "no_window_kwargs", lambda: {})
    monkeypatch.setattr(
        invisible_launch, "_win_firefox_command_lines", lambda: None
    )
    argvs = []

    def fake_check_output(argv, **k):
        argvs.append(argv)
        return "7\n"

    monkeypatch.setattr(
        invisible_launch.subprocess, "check_output", fake_check_output
    )
    assert invisible_launch._profile_firefox_pids(r"C:\p\dir") == {7}
    exe = argvs[0][0]
    assert exe.lower().endswith("powershell.exe") or exe == "powershell"
    if exe.lower().endswith("powershell.exe"):
        assert _os.path.isabs(exe)


def test_force_kill_prefers_ctypes_tree_kill(monkeypatch):
    monkeypatch.setattr(invisible_launch._platform, "IS_WINDOWS", True)
    killed = []
    monkeypatch.setattr(
        invisible_launch,
        "_kill_process_tree_ctypes",
        lambda pid: killed.append(pid) or True,
    )

    def boom(*a, **k):
        raise AssertionError("taskkill must not be spawned when ctypes worked")

    monkeypatch.setattr(invisible_launch.subprocess, "run", boom)
    invisible_launch._force_kill_pid(5)
    assert killed == [5]


def test_force_kill_falls_back_to_taskkill_by_absolute_path(monkeypatch):
    import os as _os

    monkeypatch.setattr(invisible_launch._platform, "IS_WINDOWS", True)
    monkeypatch.setattr(invisible_launch._platform, "no_window_kwargs", lambda: {})
    monkeypatch.setattr(
        invisible_launch, "_kill_process_tree_ctypes", lambda pid: False
    )
    argvs = []
    monkeypatch.setattr(
        invisible_launch.subprocess, "run", lambda argv, **k: argvs.append(argv)
    )
    invisible_launch._force_kill_pid(5)
    assert argvs, "taskkill fallback must run"
    exe = argvs[0][0]
    assert exe.lower().endswith("taskkill.exe") or exe == "taskkill"
    if exe.lower().endswith("taskkill.exe"):
        assert _os.path.isabs(exe)
    assert "/PID" in argvs[0] and "5" in argvs[0]


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="exercises the real Toolhelp/PEB process scan",
)
def test_win_process_scan_reads_own_command_line():
    # Live check of the pure-ctypes layer: our own process must be visible and
    # its command line readable (same-user, like persona's Firefoxes).
    import os as _os

    cl = invisible_launch._win_process_command_line(_os.getpid())
    assert cl and "python" in cl.lower()
    entries = invisible_launch._win_process_entries()
    assert entries is not None
    assert any(pid == _os.getpid() for pid, _ppid, _name in entries)


# ---------------------------------------------------------------------------
# #150: a relaunch racing the previous launch of the SAME profile. The
# predecessor's cleanup (_kill_profile_firefox / the headless places init)
# used to fire into the successor's launch window and shoot down its Firefox
# — the intermittent silent no-op needing ~5 relaunches (live-proven in
# persona's logs: a stopped first-launch's 80s headless init overlapped two
# later launches, killing one mid-enter).
# ---------------------------------------------------------------------------


def test_launch_lock_is_per_profile_dir():
    a = invisible_launch._profile_launch_lock(r"C:\p\one")
    assert a is invisible_launch._profile_launch_lock(r"C:\p\one")
    if sys.platform == "win32":
        # normcase folds case only on Windows, where paths are case-insensitive
        assert a is invisible_launch._profile_launch_lock(r"C:\P\ONE".lower())
    assert a is not invisible_launch._profile_launch_lock(r"C:\p\two")


def test_child_waiting_on_prior_launch_cancels_on_stop(tmp_path):
    # While the previous _child of the same profile is still winding down, a
    # STOP during the wait must cancel cleanly (never a blind blocking
    # acquire — the #141 lesson: no uninterruptible waits in the stop path).
    import os
    import threading

    lock = invisible_launch._profile_launch_lock(str(tmp_path))
    assert lock.acquire(timeout=1)
    try:
        stop = threading.Event()
        threading.Timer(0.6, stop.set).start()
        r, w = os.pipe()
        invisible_launch._child(
            {"profile_dir": str(tmp_path), "profile_name": "t", "seed": 1},
            w,
            stop_event=stop,
        )
        out = os.read(r, 65536).decode()
        os.close(r)
        assert "LAUNCH_CANCELLED" in out
        assert "BROWSER_CLOSED" in out
    finally:
        lock.release()


def test_child_serializes_launches_of_same_profile(monkeypatch, tmp_path):
    # The second launch of a profile must not enter the engine while the
    # first is still inside its launch pipeline.
    import os
    import sys
    import threading
    import time
    import types

    constructed = []

    class FakeCtx:
        pages = [object()]

        def add_init_script(self, *_a, **_k):
            pass

    class FakeEngine:
        def __init__(self, **kwargs):
            constructed.append(time.monotonic())

        def __enter__(self):
            return FakeCtx()

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)
    monkeypatch.setattr(invisible_launch._platform, "IS_WINDOWS", False)
    monkeypatch.setattr(
        invisible_launch, "_thread_close_watch", lambda *a, **k: None
    )
    monkeypatch.setattr(
        invisible_launch, "_kill_profile_firefox", lambda d, pids=None: None
    )

    gate = threading.Event()
    in_seed = threading.Event()

    def slow_seed(profile_dir, bookmarks, seed, stop_event=None):
        in_seed.set()
        gate.wait(10)

    monkeypatch.setattr(invisible_launch, "_seed_firefox_bookmarks", slow_seed)

    cfg = {"profile_dir": str(tmp_path), "profile_name": "t", "seed": 1}

    def run_child():
        r, w = os.pipe()
        invisible_launch._child(dict(cfg), w, stop_event=threading.Event())
        os.close(r)

    t1 = threading.Thread(target=run_child, daemon=True)
    t1.start()
    assert in_seed.wait(5)
    in_seed.clear()
    t2 = threading.Thread(target=run_child, daemon=True)
    t2.start()
    time.sleep(0.5)
    # t1 is wedged in seeding and t2 must be waiting on the profile lock —
    # not inside its own seeding, and neither in the engine.
    assert not in_seed.is_set()
    assert constructed == []
    gate.set()
    t1.join(10)
    t2.join(10)
    assert not t1.is_alive() and not t2.is_alive()
    assert len(constructed) == 2


def test_child_passes_stop_event_to_bookmark_seeding(monkeypatch, tmp_path):
    # The headless places init runs inside the seeding; a STOP must reach it
    # or the first launch of a bookmarked profile is uncancellable for ~90s.
    import os
    import sys
    import threading
    import types

    class FakeCtx:
        pages = [object()]

        def add_init_script(self, *_a, **_k):
            pass

    class FakeEngine:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return FakeCtx()

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)
    monkeypatch.setattr(invisible_launch._platform, "IS_WINDOWS", False)
    monkeypatch.setattr(
        invisible_launch, "_thread_close_watch", lambda *a, **k: None
    )
    monkeypatch.setattr(
        invisible_launch, "_kill_profile_firefox", lambda d, pids=None: None
    )

    seen = {}

    def capture_seed(profile_dir, bookmarks, seed, stop_event=None):
        seen["stop_event"] = stop_event

    monkeypatch.setattr(invisible_launch, "_seed_firefox_bookmarks", capture_seed)

    stop = threading.Event()
    stop.set()
    r, w = os.pipe()
    invisible_launch._child(
        {"profile_dir": str(tmp_path), "profile_name": "t", "seed": 1},
        w,
        stop_event=stop,
    )
    os.close(r)
    assert seen["stop_event"] is stop


def test_init_places_db_cancels_on_stop(monkeypatch, tmp_path):
    # A STOP during the headless places init must abort it early — it used to
    # run its full ~90s deadline with the user staring at a dead card.
    import sys
    import threading
    import time
    import types

    class FakeEngine:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)
    from src.services.browser import firefox_bookmarks

    monkeypatch.setattr(firefox_bookmarks, "places_ready", lambda p: False)
    monkeypatch.setattr(
        invisible_launch, "_wait_profile_released", lambda d: True
    )
    monkeypatch.setattr(
        invisible_launch, "_kill_profile_firefox", lambda d, pids=None: None
    )
    monkeypatch.setattr(invisible_launch._platform, "IS_LINUX", True)

    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    t0 = time.monotonic()
    ok = invisible_launch._init_places_db(
        str(tmp_path), 1, timeout=30.0, close_grace=1.0, stop_event=stop
    )
    assert ok is False
    assert time.monotonic() - t0 < 10


def test_init_places_db_retries_wedged_enter(monkeypatch, tmp_path):
    # A wedged persistent-context enter (the #137 family — live: the driver
    # crashed and the init ate its whole 90s as dead air) must be bounded and
    # retried, not waited out.
    import sys
    import threading
    import time
    import types

    instances = []

    class FakeEngine:
        def __init__(self, **kwargs):
            instances.append(self)

        def __enter__(self):
            if len(instances) == 1:
                threading.Event().wait(30)  # wedged first attempt
            return self

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)
    from src.services.browser import firefox_bookmarks

    monkeypatch.setattr(firefox_bookmarks, "places_ready", lambda p: True)
    monkeypatch.setattr(
        invisible_launch, "_wait_profile_released", lambda d: True
    )
    monkeypatch.setattr(
        invisible_launch, "_kill_profile_firefox", lambda d, pids=None: None
    )
    monkeypatch.setattr(invisible_launch._platform, "IS_LINUX", True)

    t0 = time.monotonic()
    ok = invisible_launch._init_places_db(
        str(tmp_path), 1, timeout=20.0, close_grace=1.0, enter_timeout=0.4
    )
    assert ok is True
    assert len(instances) == 2
    assert time.monotonic() - t0 < 15
