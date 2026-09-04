"""PS-283 — a launch the geo gate will REFUSE must do no launch work.

The defect: ``spawn_browser``'s Chromium arm asked the fail-closed geo gate
(``_profile_timezone``) at the arg builder, ~320 lines after the earliest legal
position, so a launch the gate then refused had already:

  * built 11 ``.persona-*-ext`` fingerprint extension directories plus
    ``Default/Preferences`` and ``Default/Bookmarks`` — 24 files, ~433 KB;
  * written ``~/.local/share/applications/persona-<name>-<digest>.desktop``,
    a host-filesystem artifact OUTSIDE the profile perimeter;
  * started the mTLS terminator, which writes the operator's decrypted client
    key to disk and binds a loopback port.

The Firefox arm (``_spawn_invisible``) calls the SAME helper before any work and
leaves nothing. One function, one helper, two arms, opposite behaviour — the
asymmetry is what this suite pins, not the byte count.

Every test here drives the REAL ``spawn_browser`` and then WALKS the directories
(and counts real calls through a spy). None of them asserts call order on a
mock: a mock can only report the order the code asked for things, which is the
very thing that was wrong, and it would go green on a build that still wrote the
files. Falsification tests at the bottom re-create the pre-fix ordering and
assert this suite goes RED against it, so a green run means the fix is present
rather than that the assertions are vacuous.
"""

import os
import pathlib

import pytest

import src.services.browser.invisible_launch as il
import src.services.browser.process as process
from src.models.profile import Profile
from src.models.proxy import Proxy
from src.services.bookmark.store import Bookmark
from src.services.proxy.errors import (
    GeographyDisprovenError,
    GeographyUnknownError,
    TimezoneUnderivableError,
)

# ---------------------------------------------------------------------------
# The three refusal causes, as REAL Proxy records rather than duck types — the
# gate reads freshness bookkeeping (checked_at / last_check_ok) as well as geo,
# so a stand-in carrying only geography cannot reach two of these three states.
# ---------------------------------------------------------------------------

#: A country with no ``_COUNTRY_TZ`` row: checked fine, no derivable zone.
_UNDERIVABLE = dict(country_code="ZW", checked_at=9e9, last_check_ok=True)
#: Geography on file, but the most recent check FAILED — it is disproven.
_DISPROVEN = dict(
    country_code="DE", timezone="Europe/Berlin", checked_at=9e9,
    last_check_ok=False,
)
#: Never successfully checked: no geography at all.
_UNKNOWN: dict = {}
#: A proxy that launches: checked, with a derivable zone.
_GOOD = dict(
    country_code="DE", timezone="Europe/Berlin", checked_at=9e9,
    last_check_ok=True,
)

REFUSALS = [
    pytest.param(_UNDERIVABLE, TimezoneUnderivableError, id="timezone-underivable"),
    pytest.param(_DISPROVEN, GeographyDisprovenError, id="geography-disproven"),
    pytest.param(_UNKNOWN, GeographyUnknownError, id="geography-unknown"),
]


def _proxy(**geo) -> Proxy:
    return Proxy(name="p1", url="socks5://1.2.3.4:1080", **geo)


class _Bookmarks:
    """A non-empty selection: an empty one makes ``seed_bookmarks`` a no-op, so
    the residue this suite measures would be partly unreachable by construction
    (PS-11's degenerate-fixture shape)."""

    def resolve_selection(self, pool, names):
        return [Bookmark(name="News", url="https://example.com/")]


def _store_for(proxy):
    class _Store:
        def resolve(self, name):
            return "socks5://1.2.3.4:1080"

        def get(self, name):
            return proxy

    return _Store


class _Spawned:
    """Accepts attribute assignment, as Popen does and object() does not."""

    pid = 4242


@pytest.fixture
def launch_env(monkeypatch, tmp_path):
    """A real launch, with only the OUTSIDE WORLD stubbed.

    Stubbed: the engine process (Popen / the FF spawn), the proxy store and the
    bookmark pool. NOT stubbed: ``seed_profile_prefs``, ``seed_bookmarks``,
    ``write_window_entry``, the extension builders or the gate — those are the
    writers under measurement, and stubbing any of them (as the existing geo
    suites do for ``write_window_entry``) is exactly what let this defect hide.

    The home directory is isolated on every platform so ``_entry_dir()`` —
    ``expanduser("~/.local/share/applications")`` — resolves inside the sandbox
    rather than the runner's real home (the PS-267 trap).
    """
    home = tmp_path / "home"
    (home / ".local/share/applications").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("HOMEDRIVE", raising=False)
    monkeypatch.delenv("HOMEPATH", raising=False)

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(process, "DATA_DIR", str(data))
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)

    # The desktop entry is Linux-gated in the product; force it ON so the host
    # artifact is actually reachable on every CI platform. Without this the
    # host half of AC1/AC2 would pass vacuously off Linux.
    monkeypatch.setattr(
        process._platform, "supports_linux_desktop_integration", lambda: True
    )

    argv: list[list[str]] = []

    class _FakePopen:
        def __init__(self, args, **kwargs):
            argv.append(list(args))
            self.pid = 4242

    def _pin_platform(name: str) -> None:
        """Pin the OS the arg builder branches on, coherently.

        The Chromium arg builder is heavily platform-branched (``IS_LINUX``
        gates ``--appimage-extract-and-run`` and the SwiftShader block,
        ``not IS_LINUX`` gates ``.persona-search-ext``, ``IS_MACOS`` gates the
        keychain flags, ``supports_linux_desktop_integration`` gates
        ``--class=``), so an argv claim is only meaningful against a NAMED
        platform. Pinning all four together is what keeps the pinned argv
        coherent — forcing the desktop entry on while leaving ``IS_LINUX`` at
        the runner's real value describes a platform that does not exist.

        ``_host_display_scale`` reads real CoreGraphics on macOS and the real
        system DPI on Windows, so a HiDPI runner would append
        ``--force-device-scale-factor`` that no other runner emits; pin it to
        1.0. ``no_window_kwargs`` touches ``subprocess.CREATE_NO_WINDOW``,
        which only exists on Windows, so it must be neutralised whenever the
        Windows arm is pinned from another host.
        """
        monkeypatch.setattr(process._platform, "IS_LINUX", name == "linux")
        monkeypatch.setattr(process._platform, "IS_MACOS", name == "macos")
        monkeypatch.setattr(process._platform, "IS_WINDOWS", name == "windows")
        monkeypatch.setattr(
            process._platform,
            "supports_linux_desktop_integration",
            lambda: name == "linux",
        )
        monkeypatch.setattr(process, "_host_display_scale", lambda: 1.0)
        monkeypatch.setattr(process._platform, "no_window_kwargs", lambda: {})

    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(
        process, "popen_in_new_session", lambda args, **kw: _FakePopen(args, **kw)
    )
    monkeypatch.setattr(il, "is_invisible_installed", lambda: True)
    monkeypatch.setattr(il, "spawn", lambda cfg, **kw: _Spawned())

    class Env:
        data_dir = data
        apps_dir = home / ".local/share/applications"
        spawned = argv
        pin_platform = staticmethod(_pin_platform)

        @staticmethod
        def use(proxy):
            monkeypatch.setattr(process, "ProxyStore", _store_for(proxy))

        @staticmethod
        def profile_files(name) -> list[str]:
            """Relative paths, POSIX-separated on EVERY platform.

            ``relative_to`` yields an ``os.sep``-joined string, so on Windows a
            bare ``str()`` gives ``Default\\Preferences`` and every ``"…/…" in
            files`` assertion below is False for a reason that has nothing to do
            with the product — including the AC6 falsification control, which
            would then stop guarding anything there.
            """
            root = data / name
            if not root.exists():
                return []
            return sorted(
                p.relative_to(root).as_posix()
                for p in root.rglob("*")
                if p.is_file()
            )

        @staticmethod
        def desktop_entries() -> list[str]:
            return sorted(p.name for p in Env.apps_dir.glob("*.desktop"))

    return Env


# ---------------------------------------------------------------------------
# AC1 — a refused Chromium launch leaves NOTHING, in the profile or on the host
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("geo,exc", REFUSALS)
def test_a_refused_chromium_launch_writes_no_profile_files(launch_env, geo, exc):
    launch_env.use(_proxy(**geo))

    with pytest.raises(exc):
        process.spawn_browser(Profile(name="refused", proxy="p1"))

    residue = launch_env.profile_files("refused")
    assert residue == [], (
        "a launch the geo gate REFUSED still built the profile: "
        f"{len(residue)} files, e.g. {residue[:6]}. The gate must be asked "
        "before any launch work, as the Firefox arm already asks it."
    )


@pytest.mark.parametrize("geo,exc", REFUSALS)
def test_a_refused_chromium_launch_writes_no_host_desktop_entry(
    launch_env, geo, exc
):
    """The half a profile-directory assertion cannot see.

    ``write_window_entry`` writes to ``~/.local/share/applications``, which is
    OUTSIDE the profile perimeter — so it is not cleaned by a profile wipe and
    would survive a hoist that only got as far as the proxy block.
    """
    launch_env.use(_proxy(**geo))

    with pytest.raises(exc):
        process.spawn_browser(Profile(name="refused-host", proxy="p1"))

    assert launch_env.desktop_entries() == [], (
        "a refused launch wrote a host desktop entry outside the profile "
        f"perimeter: {launch_env.desktop_entries()}"
    )


def test_the_engine_is_never_spawned_on_a_refusal(launch_env):
    """The premise everything else rests on: these ARE refusals, not launches
    that happened to write nothing."""
    launch_env.use(_proxy(**_UNKNOWN))

    with pytest.raises(GeographyUnknownError):
        process.spawn_browser(Profile(name="never", proxy="p1"))

    assert launch_env.spawned == [], "the engine was launched despite the refusal"


# ---------------------------------------------------------------------------
# AC2 — parity: the two arms must leave the SAME footprint for the SAME refusal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("geo,exc", REFUSALS)
def test_both_engines_leave_the_same_footprint_for_the_same_refusal(
    launch_env, geo, exc
):
    """The property this ticket exists to establish.

    Asserted as EQUALITY between the arms rather than as "chromium writes
    nothing", so it keeps its meaning if the reference behaviour ever moves:
    one helper, one refusal, one footprint. Both arms are driven through the
    public ``spawn_browser`` entry point, so the engine split is the product's
    own (``effective_engine``) rather than the test's.
    """
    launch_env.use(_proxy(**geo))

    with pytest.raises(exc):
        process.spawn_browser(Profile(name="parity-chromium", proxy="p1"))
    with pytest.raises(exc):
        process.spawn_browser(
            Profile(name="parity-firefox", engine="firefox", proxy="p1")
        )

    chromium = launch_env.profile_files("parity-chromium")
    firefox = launch_env.profile_files("parity-firefox")
    assert chromium == firefox, (
        "the two arms of the SAME function, refusing on the SAME helper, left "
        f"different on-disk footprints: chromium={chromium[:6]} "
        f"({len(chromium)} files) vs firefox={firefox[:6]} "
        f"({len(firefox)} files)"
    )
    assert launch_env.desktop_entries() == [], (
        "the arms also disagree on the HOST: a desktop entry survives both "
        f"refusals: {launch_env.desktop_entries()}"
    )


# ---------------------------------------------------------------------------
# AC3 — the mTLS terminator must not be started for a launch that is refused
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("geo,exc", REFUSALS)
def test_the_cert_session_is_never_started_on_a_refused_launch(
    launch_env, monkeypatch, geo, exc
):
    """``_cert_session_for`` -> ``start_cert_session`` writes the operator's
    DECRYPTED client key to disk and binds a loopback port. It ran upstream of
    the gate, so a refused launch paid both costs.

    Asserted with a COUNTER on the call, not by "no exception was raised": on a
    host without ``cryptography`` the import inside the terminator raises before
    anything is written, so an exception-shaped assertion passes vacuously and
    would keep passing with the fix reverted.
    """
    entered = []

    def _spy(profile, profile_dir, upstream):
        entered.append(profile.name)
        return None

    monkeypatch.setattr(process, "_cert_session_for", _spy)
    launch_env.use(_proxy(**geo))

    with pytest.raises(exc):
        process.spawn_browser(
            Profile(name="cert-refused", proxy="p1", certificate="admin")
        )

    assert entered == [], (
        "the mTLS terminator path was entered for a launch the geo gate then "
        "refused — decrypted key material on disk and a bound port for a "
        "browser that never started"
    )


def test_the_cert_session_spy_does_fire_on_a_launch_that_proceeds(
    launch_env, monkeypatch
):
    """The control for the test above. Without it, `entered == []` would also
    be satisfied by a spy that can never fire (PS-11's "bound that cannot be
    crossed")."""
    entered = []

    def _spy(profile, profile_dir, upstream):
        entered.append(profile.name)
        return None

    monkeypatch.setattr(process, "_cert_session_for", _spy)
    launch_env.use(_proxy(**_GOOD))

    process.spawn_browser(
        Profile(name="cert-ok", proxy="p1", certificate="admin")
    )

    assert entered == ["cert-ok"], (
        "the spy never fires at all, so the refusal assertion proves nothing"
    )


# ---------------------------------------------------------------------------
# AC4 — the happy path is untouched
# ---------------------------------------------------------------------------


def _normalise(argv, profile_dir, data_dir):
    """Mask the machine-specific values, and make separators comparable.

    ``profile_dir`` reaches argv ``os.sep``-joined (``--user-data-dir=`` and
    every ``--load-extension=`` entry), so on Windows the extension list is
    ``<PROFILE>\\.persona-gpu-ext``. The pinned lists below are written with
    ``/``, which is a presentation choice, not a claim about the product — so
    normalise the separator here rather than pinning three copies of the same
    list that differ only in a slash.
    """
    out = [
        a.replace(str(profile_dir), "<PROFILE>").replace(str(data_dir), "<DATA>")
        for a in argv
    ]
    if os.sep != "/":
        out = [a.replace(os.sep, "/") for a in out]
    return out


#: The FULL argv a launched profile produced BEFORE the hoist, captured from
#: pristine ``main`` with a DE/Europe-Berlin proxy and tmp paths normalised —
#: one list per platform, because the Chromium arg builder branches on the OS
#: and an argv claim is only meaningful against a NAMED platform. Pinned as
#: whole LISTS, not as "contains --timezone": the claim is that the hoist
#: changed nothing about a launch that proceeds, and only full-list equality
#: can carry that claim.
#:
#: Re-baselined per platform by driving pristine ``main`` in a ``git worktree``
#: with the same four platform seams pinned that ``Env.pin_platform`` pins; all
#: three came back byte-identical to the post-hoist tree, which is the AC4
#: measurement itself.
_PRISTINE_ARGV = {
    "linux": [
        "<ENGINE>",
        "--appimage-extract-and-run",
        "--user-data-dir=<PROFILE>",
        "--fingerprint=<SEED>",
        "--fingerprint-platform=windows",
        "--fingerprint-brand=Chrome",
        "--lang=de-DE",
        "--accept-lang=de-DE,de",
        (
            "--load-extension=<PROFILE>/.persona-native-ext,"
            "<PROFILE>/.persona-locale-ext,<PROFILE>/.persona-voice-ext,"
            "<PROFILE>/.persona-stealth-ext,<PROFILE>/.persona-measuretext-ext,"
            "<PROFILE>/.persona-audio-ext,<PROFILE>/.persona-device-ext,"
            "<PROFILE>/.persona-webgl-ext,<PROFILE>/.persona-gpu-ext,"
            "<PROFILE>/.persona-canvas-ctx-ext,<PROFILE>/.persona-geo-ext"
        ),
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-search-engine-choice-screen",
        "--restore-last-session",
        "--hide-crash-restore-bubble",
        "--force-dark-mode",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
        "--use-gl=angle",
        "--use-angle=swiftshader",
        "--enable-unsafe-swiftshader",
        "--password-store=basic",
        "--use-mock-keychain",
        "--disable-threaded-animation",
        "--animation-duration-scale=0",
        "--wm-window-animations-disabled",
        "--disable-gpu-vsync",
        "--class=<WMCLASS>",
        "--timezone=Europe/Berlin",
        "--proxy-server=socks5://1.2.3.4:1080",
        "--dns-over-https-mode=off",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--dns-prefetch-disable",
        "--disable-quic",
        (
            "--disable-features=CalculateNativeWinOcclusion,VaapiVideoDecoder,"
            "VaapiVideoEncoder,DnsOverHttps,EnableQuic"
        ),
    ],
    # macOS: no AppImage flag, no SwiftShader block, no Wayland --class; the
    # keychain pair IS emitted (IS_MACOS), and .persona-search-ext IS built
    # (the `not IS_LINUX` branch).
    "macos": [
        "<ENGINE>",
        "--user-data-dir=<PROFILE>",
        "--fingerprint=<SEED>",
        "--fingerprint-platform=windows",
        "--fingerprint-brand=Chrome",
        "--lang=de-DE",
        "--accept-lang=de-DE,de",
        (
            "--load-extension=<PROFILE>/.persona-native-ext,"
            "<PROFILE>/.persona-locale-ext,<PROFILE>/.persona-voice-ext,"
            "<PROFILE>/.persona-stealth-ext,<PROFILE>/.persona-measuretext-ext,"
            "<PROFILE>/.persona-search-ext,<PROFILE>/.persona-audio-ext,"
            "<PROFILE>/.persona-device-ext,<PROFILE>/.persona-webgl-ext,"
            "<PROFILE>/.persona-gpu-ext,<PROFILE>/.persona-canvas-ctx-ext,"
            "<PROFILE>/.persona-geo-ext"
        ),
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-search-engine-choice-screen",
        "--restore-last-session",
        "--hide-crash-restore-bubble",
        "--force-dark-mode",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
        "--password-store=basic",
        "--use-mock-keychain",
        "--timezone=Europe/Berlin",
        "--proxy-server=socks5://1.2.3.4:1080",
        "--dns-over-https-mode=off",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--dns-prefetch-disable",
        "--disable-quic",
        "--disable-features=CalculateNativeWinOcclusion,DnsOverHttps,EnableQuic",
    ],
    # Windows: as macOS minus the keychain pair (that block is IS_MACOS-gated).
    "windows": [
        "<ENGINE>",
        "--user-data-dir=<PROFILE>",
        "--fingerprint=<SEED>",
        "--fingerprint-platform=windows",
        "--fingerprint-brand=Chrome",
        "--lang=de-DE",
        "--accept-lang=de-DE,de",
        (
            "--load-extension=<PROFILE>/.persona-native-ext,"
            "<PROFILE>/.persona-locale-ext,<PROFILE>/.persona-voice-ext,"
            "<PROFILE>/.persona-stealth-ext,<PROFILE>/.persona-measuretext-ext,"
            "<PROFILE>/.persona-search-ext,<PROFILE>/.persona-audio-ext,"
            "<PROFILE>/.persona-device-ext,<PROFILE>/.persona-webgl-ext,"
            "<PROFILE>/.persona-gpu-ext,<PROFILE>/.persona-canvas-ctx-ext,"
            "<PROFILE>/.persona-geo-ext"
        ),
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-search-engine-choice-screen",
        "--restore-last-session",
        "--hide-crash-restore-bubble",
        "--force-dark-mode",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
        "--timezone=Europe/Berlin",
        "--proxy-server=socks5://1.2.3.4:1080",
        "--dns-over-https-mode=off",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--dns-prefetch-disable",
        "--disable-quic",
        "--disable-features=CalculateNativeWinOcclusion,DnsOverHttps,EnableQuic",
    ],
}


@pytest.mark.parametrize("platform", sorted(_PRISTINE_ARGV))
def test_the_happy_path_argv_is_byte_identical_to_before_the_hoist(
    launch_env, platform
):
    """A launch that PROCEEDS must be unchanged, argument for argument.

    Run for EACH platform the product ships on, with the OS seams pinned — not
    for whichever OS the runner happens to be. An argv recorded on Linux and
    asserted unconditionally is coherent on no platform but the one it was
    recorded on, and it is the runner, not the product, that decides whether it
    passes.

    Two values are masked and neither is the point of this test: the engine
    path (an absolute install path) and the fingerprint seed / WM class (salted
    per install, so they legitimately differ between machines). Everything
    else — including order — is compared verbatim.
    """
    launch_env.pin_platform(platform)
    launch_env.use(_proxy(**_GOOD))

    process.spawn_browser(Profile(name="happy", proxy="p1"))

    assert len(launch_env.spawned) == 1, "the happy path did not launch"
    argv = _normalise(
        launch_env.spawned[0], launch_env.data_dir / "happy", launch_env.data_dir
    )
    argv[0] = "<ENGINE>"
    argv = [
        "<WMCLASS>" if a.startswith("--class=") else a for a in argv
    ]
    argv = [
        "--fingerprint=<SEED>" if a.startswith("--fingerprint=") else a
        for a in argv
    ]
    argv = ["--class=<WMCLASS>" if a == "<WMCLASS>" else a for a in argv]

    assert argv == _PRISTINE_ARGV[platform], (
        f"the hoist changed a launch that PROCEEDS on {platform}. This is meant "
        "to be a move of WHERE the gate is asked, not a change to what is "
        "launched."
    )


def test_a_launched_profile_still_gets_its_seeded_files_and_desktop_entry(
    launch_env,
):
    """The other half of "no behaviour change on the happy path": the writers
    the gate now runs AHEAD of must still run when the gate says yes."""
    launch_env.use(_proxy(**_GOOD))

    process.spawn_browser(Profile(name="still-seeded", proxy="p1"))

    files = launch_env.profile_files("still-seeded")
    assert "Default/Preferences" in files
    assert "Default/Bookmarks" in files
    assert any(f.startswith(".persona-gpu-ext/") for f in files)
    assert launch_env.desktop_entries(), (
        "the desktop entry is no longer written for a launch that proceeds"
    )


# ---------------------------------------------------------------------------
# AC7 — the refusal still reaches the operator, unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "geo,fragment",
    [
        (_UNDERIVABLE, "add a row for that country to _COUNTRY_TZ"),
        (_DISPROVEN, "Re-check the proxy to resolve it"),
        (_UNKNOWN, "Check the proxy to resolve it"),
    ],
    ids=["underivable", "disproven", "unknown"],
)
def test_the_refusal_message_still_reaches_the_operator(
    launch_env, monkeypatch, geo, fragment
):
    """PS-31's channel: ``spawn_browser`` raises, ``BrowserLauncher.start_thread``
    catches and calls ``log_callback("Error starting process: ...")``.

    The remedy sentence is asserted, not just the exception type: the three
    causes carry three DIFFERENT remedies (add a table row / re-check / check),
    and an operator handed the wrong one goes looking for the wrong thing.
    """
    from src.services.browser.launcher import BrowserLauncher

    launch_env.use(_proxy(**geo))
    lines: list[str] = []
    bl = BrowserLauncher()
    bl.start_thread(Profile(name="operator", proxy="p1"), lines.append)

    errors = [ln for ln in lines if ln.startswith("Error starting process:")]
    assert errors, f"the refusal never reached the operator; log was {lines}"
    assert fragment in errors[0], (
        f"the refusal reached the operator without its remedy: {errors[0]!r}"
    )
    assert bl.last_refusal("operator") is not None, (
        "the refusal did not land on the profile card either"
    )


# ---------------------------------------------------------------------------
# AC6 — falsification: this suite must go RED against the pre-fix ordering
# ---------------------------------------------------------------------------


def _spawn_with_the_gate_back_at_the_arg_builder(profile):
    """Re-create the PRE-FIX order without editing the product.

    Two edits, and between them they put the gate back where it was:

      1. the gate at the TOP of ``spawn_browser`` is neutralised — it answers
         without consulting the real policy, exactly as the pre-fix function
         did by not asking at all there;
      2. the real gate is re-armed at the engine spawn.

    Point 2 is LATER than the literal old call site (the arg builder, ~320
    lines down), and that is deliberate rather than sloppy: nothing between the
    old call site and the spawn writes to disk, so the two positions produce
    the SAME residue — and the residue is what is being falsified. The
    assertions below check the reproduction is faithful by size (the pre-fix
    footprint measured on ``main``: 24 files, incl. both seeded files and the
    full extension build) rather than merely non-empty, so a harness that only
    half-reproduced the defect would not pass for one that did.

    ⚠️ BOTH GEO GATES ARE NEUTRALISED, not just the timezone one, and that is
    what keeps this harness faithful rather than what weakens it. PS-240 added
    a SECOND fail-closed gate — the LOCALE half — hoisted to the same position
    for this suite's own stated reason. Leaving it armed would refuse at the top
    of ``spawn_browser`` and produce ZERO residue, so the harness would report a
    perfectly reproduced defect as un-reproducible and this file's falsification
    would fail for a reason that has nothing to do with the ordering it tests.
    At the PRE-FIX commit neither gate stood there, so neutralising both IS the
    pre-fix order; re-arming both at the spawn keeps the refusal itself real.
    No assertion below is relaxed — the residue is still measured by size, and
    a refusal is still required.
    """
    real_gate = process._profile_timezone
    real_locale_gate = process._profile_locale
    real_popen = process.popen_in_new_session

    def _neutralised(prof, proxy):
        return "Europe/Berlin"

    def _neutralised_locale(prof, proxy):
        return "de-DE"

    def _gate_at_the_spawn(args, **kwargs):
        proxy = process.ProxyStore().get(profile.proxy)
        real_gate(profile, proxy)
        real_locale_gate(profile, proxy)
        return real_popen(args, **kwargs)

    process._profile_timezone = _neutralised
    process._profile_locale = _neutralised_locale
    process.popen_in_new_session = _gate_at_the_spawn
    try:
        return process.spawn_browser(profile)
    finally:
        process._profile_timezone = real_gate
        process._profile_locale = real_locale_gate
        process.popen_in_new_session = real_popen


def test_falsification_the_residue_assertion_fails_against_the_old_ordering(
    launch_env,
):
    launch_env.use(_proxy(**_UNKNOWN))

    with pytest.raises(GeographyUnknownError):
        _spawn_with_the_gate_back_at_the_arg_builder(
            Profile(name="falsify", proxy="p1")
        )

    residue = launch_env.profile_files("falsify")
    assert len(residue) >= 20, (
        "the falsification harness did not reproduce the defect (saw "
        f"{len(residue)} files, expected the pre-fix ~24), so the AC1 "
        "assertions above may be passing for a reason other than the hoist"
    )
    assert "Default/Preferences" in residue and "Default/Bookmarks" in residue
    assert launch_env.desktop_entries(), (
        "the falsification harness did not reproduce the HOST artifact, so the "
        "desktop-entry assertions above may be vacuous"
    )


def test_falsification_the_parity_assertion_fails_against_the_old_ordering(
    launch_env,
):
    """AC2 specifically: under the old ordering the two arms DISAGREE, which is
    the whole finding. A parity assertion that could not detect that is not
    testing anything."""
    launch_env.use(_proxy(**_UNKNOWN))

    with pytest.raises(GeographyUnknownError):
        _spawn_with_the_gate_back_at_the_arg_builder(
            Profile(name="falsify-parity", proxy="p1")
        )
    with pytest.raises(GeographyUnknownError):
        process.spawn_browser(
            Profile(name="falsify-firefox", engine="firefox", proxy="p1")
        )

    chromium = launch_env.profile_files("falsify-parity")
    firefox = launch_env.profile_files("falsify-firefox")
    assert chromium != firefox, (
        "under the OLD ordering the two arms must differ; if they do not, the "
        "parity test above cannot have been measuring the asymmetry"
    )
    assert firefox == [], "the Firefox reference arm is supposed to write nothing"


def test_the_extension_builders_really_do_write_when_a_launch_proceeds(
    launch_env, tmp_path
):
    """One more anti-vacuity control, on the fixture rather than the fix: the
    residue this suite asserts is ABSENT after a refusal must be PRESENT after
    a launch, or the walk is looking at the wrong directory."""
    launch_env.use(_proxy(**_GOOD))

    process.spawn_browser(Profile(name="writes", proxy="p1"))

    files = launch_env.profile_files("writes")
    # ``profile_files`` yields POSIX-separated relative paths on every platform,
    # so this really does take the DIRECTORY component. It used to split an
    # ``os.sep``-joined string on "/", which on Windows is a no-op: the set then
    # held FILE paths, and 11 extension dirs x >=2 files each cleared ">= 10"
    # without measuring what the assertion claims — the anti-vacuity control
    # itself going vacuous (PS-11's "a green test is a claim about the
    # assertion"). Assert the shape as well as the count so it cannot recur.
    ext_dirs = {f.split("/")[0] for f in files if f.startswith(".persona-")}
    assert all("/" not in d and d.endswith("-ext") for d in ext_dirs), (
        "these are file paths, not extension directories — the split did not "
        f"take a directory component: {sorted(ext_dirs)}"
    )
    assert len(ext_dirs) >= 10, (
        f"expected the full extension build, saw {sorted(ext_dirs)}"
    )
    assert os.path.isdir(launch_env.data_dir / "writes")
    assert isinstance(launch_env.apps_dir, pathlib.Path)
