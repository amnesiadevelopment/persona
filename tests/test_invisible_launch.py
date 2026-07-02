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


def test_count_windows_for_pids_empty_without_pids():
    # No pids → no windows, and it never touches Win32 with an empty set.
    from src.services.browser.invisible_launch import _count_windows_for_pids

    assert _count_windows_for_pids(set()) == 0


def test_close_watch_uses_window_count_on_thread_path():
    # The Windows close-watch keys on the OS window count of THIS profile's
    # Firefox pids (a persistent-context Firefox keeps its process + a background
    # page alive after the last window closes, so ctx.pages/pid-alive can't
    # signal the close). Pure-ctypes helpers, no subprocess.
    from src.services.browser import invisible_launch

    assert hasattr(invisible_launch, "_count_windows_for_pids")
    assert hasattr(invisible_launch, "_firefox_pids_snapshot")


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
