"""A proxied profile with NO geography must refuse to launch — on both engines.

The defect this pins: `_proxy_timezone` had a third branch that, when a proxy
carried neither `timezone` nor `country_code`, returned `_host_timezone()` — the
OPERATOR'S REAL ZONE, declared inside the tunnel. That is a real-location
disclosure on exactly the vector the proxy exists to close, and it fired for
every proxy that has never had a successful check (`mark_check_failed` writes
`checked_at` + `last_check_ok=False` and no geo at all).

The governing rule: when no geography is available the answer is STOP — not a
host-derived value, not a coarser value, not a quieter value. A persona that
will not launch has disclosed nothing.

These tests bind to the MECHANISM (the spawn spy records nothing / the raise
happens), not to prose, so removing the guard turns them red.
"""

import os

import pytest

import src.services.browser.invisible_launch as il
import src.services.browser.launch_policy as launch_policy
import src.services.browser.process as process
from src.models.profile import Profile
from src.services.proxy.errors import GeographyUnknownError


class _Spawned:
    """Stand-in for the spawned handle (accepts attribute assignment, as Popen
    does and a bare object() does not)."""


class _Bookmarks:
    def resolve_selection(self, pool, names):
        return []


class _GeolessProxy:
    """A proxy that RESOLVES fine but has never been successfully checked, so it
    carries no geography — the reachable population of the defect."""

    timezone = ""
    country_code = ""
    lat = None
    lon = None


class _CheckedProxy:
    """The same proxy AFTER a successful check populated geo (mark_checked)."""

    timezone = "Europe/Warsaw"
    country_code = "PL"
    lat = None
    lon = None


class _StoreWithGeolessProxy:
    def resolve(self, name):
        return "socks5://1.2.3.4:1080"

    def get(self, name):
        return _GeolessProxy()


class _StoreWithCheckedProxy:
    def resolve(self, name):
        return "socks5://1.2.3.4:1080"

    def get(self, name):
        return _CheckedProxy()


def _host_zone_is_distinctive(monkeypatch):
    """Patch the host zone to a value the assertions would catch if the removed
    fallback were somehow still reached.

    Patch on launch_policy, not process: `_proxy_timezone` lives there and
    resolves `_host_timezone` in its OWN namespace, so a patch on the process
    re-export alias is silently bypassed (the real host zone would be read).
    """
    monkeypatch.setattr(launch_policy, "_host_timezone", lambda: "Europe/Kyiv")


# ---------------------------------------------------------------------------
# The policy function itself
# ---------------------------------------------------------------------------


def test_proxy_timezone_refuses_when_no_geography_is_available(monkeypatch):
    """No geography => STOP. Not the host zone, not UTC, not any string."""
    _host_zone_is_distinctive(monkeypatch)
    with pytest.raises(GeographyUnknownError):
        launch_policy._proxy_timezone(_GeolessProxy())


def test_the_unknown_is_unrepresentable_as_a_zone(monkeypatch):
    """The refusal must not be expressible as a timezone STRING.

    A sentinel string ("", "UTC", "unknown") could be shipped to an engine by a
    caller that forgot to check it. An exception cannot be mistaken for a zone,
    which is why the unknown is signalled this way.
    """
    _host_zone_is_distinctive(monkeypatch)
    try:
        result = launch_policy._proxy_timezone(_GeolessProxy())
    except GeographyUnknownError:
        return  # correct: unrepresentable
    pytest.fail(
        f"_proxy_timezone returned {result!r} instead of refusing; a string "
        "result can be shipped to an engine as if it were a real zone"
    )


# ---------------------------------------------------------------------------
# AC6: BOTH engines refuse. A fix covering one engine is what this forbids.
# ---------------------------------------------------------------------------


def test_firefox_refuses_to_launch_when_geography_is_unknown(monkeypatch, tmp_path):
    """Firefox path (_spawn_invisible). The spawn spy must record NOTHING."""
    spawned = []
    monkeypatch.setattr(il, "is_invisible_installed", lambda: True)
    monkeypatch.setattr(
        il, "spawn", lambda cfg, **kw: spawned.append(cfg) or _Spawned()
    )
    monkeypatch.setattr(process, "ProxyStore", _StoreWithGeolessProxy)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    _host_zone_is_distinctive(monkeypatch)

    profile = Profile(name="tz-firefox", engine="firefox", proxy="p1")
    with pytest.raises(GeographyUnknownError):
        process._spawn_invisible(profile, str(tmp_path))

    assert spawned == [], (
        "Firefox must NOT spawn a profile whose geography is unknown — the old "
        "behaviour shipped the operator's real host zone inside the tunnel"
    )


def test_chromium_refuses_to_launch_when_geography_is_unknown(monkeypatch, tmp_path):
    """Chromium path (spawn_browser arg builder). Popen must never be reached."""
    spawned = []

    class _FakePopen:
        def __init__(self, args, **kwargs):
            spawned.append(args)
            self.pid = os.getpid()

    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", _StoreWithGeolessProxy)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "write_window_entry", lambda name: None)
    monkeypatch.setattr(process._platform, "IS_LINUX", False)
    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    _host_zone_is_distinctive(monkeypatch)

    profile = Profile(name="tz-chromium", proxy="p1")
    with pytest.raises(GeographyUnknownError):
        process.spawn_browser(profile)

    assert spawned == [], (
        "Chromium must NOT spawn a profile whose geography is unknown — the old "
        "behaviour shipped --timezone=<operator's real zone> inside the tunnel"
    )


def test_neither_engine_ever_emits_the_host_zone_for_a_geoless_proxy(
    monkeypatch, tmp_path
):
    """The leak itself, asserted directly: whatever happens, the host zone must
    not reach an engine. Belt-and-braces over the two tests above — those assert
    'nothing spawned', this asserts 'and specifically not THAT value'."""
    emitted = []

    class _FakePopen:
        def __init__(self, args, **kwargs):
            emitted.extend(args)
            self.pid = os.getpid()

    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", _StoreWithGeolessProxy)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "write_window_entry", lambda name: None)
    monkeypatch.setattr(process._platform, "IS_LINUX", False)
    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(il, "is_invisible_installed", lambda: True)
    monkeypatch.setattr(
        il, "spawn", lambda cfg, **kw: emitted.append(cfg) or _Spawned()
    )
    _host_zone_is_distinctive(monkeypatch)

    with pytest.raises(GeographyUnknownError):
        process.spawn_browser(Profile(name="c", proxy="p1"))
    with pytest.raises(GeographyUnknownError):
        process._spawn_invisible(
            Profile(name="f", engine="firefox", proxy="p1"), str(tmp_path)
        )

    assert not any("Europe/Kyiv" in str(item) for item in emitted), (
        f"the operator's host zone reached an engine: {emitted!r}"
    )


# ---------------------------------------------------------------------------
# AC7: the refusal REACHES THE OPERATOR — a visible stop, not a silent one.
# ---------------------------------------------------------------------------


def test_the_refusal_is_reported_to_the_operator(monkeypatch):
    """launcher.start_thread catches the raise and reports it via log_callback,
    so the operator sees WHY no browser appeared instead of a dead click."""
    import src.services.browser.launcher as launcher_mod
    from src.services.browser.launcher import BrowserLauncher

    def _refuse(profile):
        raise GeographyUnknownError(
            "Profile 'p' has proxy 'p1' assigned but its geography could not be "
            "established. Check the proxy to resolve it."
        )

    monkeypatch.setattr(launcher_mod, "spawn_browser", _refuse)

    messages = []
    stopped = []
    BrowserLauncher().start_thread(
        Profile(name="geoless", proxy="p1"),
        messages.append,
        on_stop=lambda: stopped.append(True),
    )

    # start_thread runs the spawn inline before handing off to its monitor
    # threads, so the failure has already been reported by the time it returns.
    joined = " | ".join(messages)
    assert "geography" in joined.lower(), (
        f"the operator was never told why the launch stopped: {messages!r}"
    )
    assert "check the proxy" in joined.lower(), (
        f"the message must say what RESOLVES it, not just that it failed: {messages!r}"
    )
    assert stopped, "the UI must be released from its loading state"


# ---------------------------------------------------------------------------
# AC8: the escape hatch. The refusal is a stop, not a dead end.
# ---------------------------------------------------------------------------


def test_after_a_successful_check_the_same_profile_launches_with_the_exit_zone(
    monkeypatch, tmp_path
):
    """Once a check populates geo, the profile launches and declares the EXIT's
    zone — never the host's. This is what makes the refusal escapable."""
    captured = {}

    class _FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            self.pid = os.getpid()

    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", _StoreWithCheckedProxy)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "write_window_entry", lambda name: None)
    monkeypatch.setattr(process._platform, "IS_LINUX", False)
    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    _host_zone_is_distinctive(monkeypatch)

    process.spawn_browser(Profile(name="checked", proxy="p1"))

    assert "--timezone=Europe/Warsaw" in captured["args"], (
        f"a checked proxy must declare the exit zone: {captured['args']!r}"
    )
    assert not any("Europe/Kyiv" in a for a in captured["args"]), (
        "the host zone must never appear once geo is known"
    )


def test_firefox_also_launches_once_geography_is_known(monkeypatch, tmp_path):
    """The escape hatch holds on the Firefox path too."""
    captured = []
    monkeypatch.setattr(il, "is_invisible_installed", lambda: True)
    monkeypatch.setattr(
        il, "spawn", lambda cfg, **kw: captured.append(cfg) or _Spawned()
    )
    monkeypatch.setattr(process, "ProxyStore", _StoreWithCheckedProxy)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    _host_zone_is_distinctive(monkeypatch)

    process._spawn_invisible(
        Profile(name="checked-ff", engine="firefox", proxy="p1"), str(tmp_path)
    )

    assert captured[0]["timezone"] == "Europe/Warsaw"


# ---------------------------------------------------------------------------
# AC5 / AC4: the paths that must NOT change.
# ---------------------------------------------------------------------------


def test_a_direct_profile_is_untouched_by_the_refusal(monkeypatch, tmp_path):
    """No proxy => no geography question to answer => _timezone_for("US").
    A direct profile must keep launching exactly as before."""
    captured = {}

    class _FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            self.pid = os.getpid()

    class _StoreNoProxy:
        def resolve(self, name):
            return ""

        def get(self, name):
            return None

    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", _StoreNoProxy)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "write_window_entry", lambda name: None)
    monkeypatch.setattr(process._platform, "IS_LINUX", False)
    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    _host_zone_is_distinctive(monkeypatch)

    process.spawn_browser(Profile(name="direct"))

    assert "--timezone=America/New_York" in captured["args"]


def test_the_two_correct_branches_still_answer(monkeypatch):
    """Branches 1 and 2 were already correct and must stay correct — the fix
    removes ONLY the host-derived third branch."""
    _host_zone_is_distinctive(monkeypatch)

    class _P:
        def __init__(self, timezone="", country_code=""):
            self.timezone = timezone
            self.country_code = country_code

    assert launch_policy._proxy_timezone(_P(timezone="Asia/Tokyo")) == "Asia/Tokyo"
    assert launch_policy._proxy_timezone(_P(country_code="DE")) == "Europe/Berlin"
    # An explicit zone still wins over a country code.
    assert (
        launch_policy._proxy_timezone(_P(timezone="Asia/Tokyo", country_code="DE"))
        == "Asia/Tokyo"
    )
