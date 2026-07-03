import inspect

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
    # open a window at 3840x2160 вЂ” that overflows the screen. It's capped to the
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


def test_context_overrides_decouple_screen_from_window():
    # The spoofed screen must stay at the CHOSEN resolution (what the user
    # picked, e.g. 4K) while the real window (viewport) is shrunk to fit the
    # monitor. Coupling them is the #102 bug: picking 4K opened a 4K window that
    # overflowed the monitor, and shrinking the window shrank the fingerprint.
    from src.services.browser.invisible_launch import _context_overrides_for

    ov = _context_overrides_for(3840, 2160, (2560, 1392))
    assert ov["screen"] == {"width": 3840, "height": 2160}       # fingerprint = chosen
    assert ov["viewport"]["width"] <= 2560                        # window fits monitor
    assert ov["viewport"]["width"] < 3840


def test_context_overrides_small_pick_window_equals_screen():
    # A small pick that already fits opens the window at its size and the screen
    # matches it вЂ” no shrink, no decoupling needed.
    from src.services.browser.invisible_launch import _context_overrides_for

    ov = _context_overrides_for(1366, 768, (2560, 1392))
    assert ov["screen"] == {"width": 1366, "height": 768}
    assert ov["viewport"]["width"] <= 1366


def test_outer_size_override_script_ties_outer_to_window():
    # outerWidth/outerHeight must track the real window (inner + chrome), NOT the
    # spoofed screen вЂ” otherwise outerWidth leaks the 4K screen on a physically
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
    # patched Firefox, so the profile's process disappears вЂ” a reliable signal.
    # Counting windows by title missed the "New Tab" page (Firefox owns its
    # title, no "[profile]" prefix), so the close was never seen. The
    # per-profile pid helper must exist.
    from src.services.browser import invisible_launch

    assert hasattr(invisible_launch, "_profile_firefox_pids")


def test_non_fork_launch_uses_thread_not_reexec(monkeypatch):
    # The Win/Mac path must NOT re-exec sys.executable (in a flet bundle that's
    # the GUI launcher, not python вЂ” it just opens a second window). It must run
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
