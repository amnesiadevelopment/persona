import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.services.browser.engine_install as eng
import src.services.browser.invisible_launch as inv
from src.services.engine import firefox as ff

WIN_ASSET = "firefox-150.0.1-stealth-win-x86_64.zip"
FULL_ASSETS = [
    {"name": "checksums.txt"},
    {"name": "firefox-150.0.1-stealth-linux-arm64.tar.gz"},
    {"name": "firefox-150.0.1-stealth-linux-x86_64.tar.gz"},
    {"name": "firefox-150.0.1-stealth-macos-arm64.tar.gz"},
    {"name": "firefox-150.0.1-stealth-macos-x86_64.tar.gz"},
    {"name": WIN_ASSET},
]


def test_build_number():
    assert ff.build_number("firefox-15") == 15
    assert ff.build_number("firefox-8") == 8
    assert ff.build_number("usage-counter") == -1
    assert ff.build_number("firefox-15-beta") == -1
    assert ff.build_number("") == -1
    # #234: the newer engine package names the cache dir with an upstream+
    # timestamp suffix; build_number must parse the leading firefox-NN so the
    # downloaded build is recognised as installed.
    assert ff.build_number("firefox-18_151.0_20260724001829") == 18
    assert ff.build_number("firefox-18.151.0") == 18
    assert ff.build_number("firefox-180") == 180  # not firefox-18


def test_is_newer():
    assert ff.is_newer("firefox-16", "firefox-15") is True
    assert ff.is_newer("firefox-15", "firefox-15") is False
    assert ff.is_newer("firefox-14", "firefox-15") is False
    assert ff.is_newer("firefox-16", "") is True  # nothing installed
    assert ff.is_newer("", "firefox-15") is False  # no latest info
    assert ff.is_newer("usage-counter", "firefox-15") is False


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _serve(monkeypatch, payload):
    monkeypatch.setattr(ff, "_expected_asset", lambda: WIN_ASSET)
    monkeypatch.setattr(
        ff.urllib.request, "urlopen", lambda req, timeout=20: _Resp(payload)
    )


def test_fetch_latest_picks_highest_firefox_tag(monkeypatch):
    # pkg drives firefox-16, so firefox-16 is the highest compatible.
    import invisible_core.constants as consts

    monkeypatch.setattr(consts, "BINARY_VERSION", "firefox-16")
    _serve(
        monkeypatch,
        [
            {"tag_name": "usage-counter", "assets": [{"name": "launch.txt"}]},
            {"tag_name": "firefox-15", "assets": FULL_ASSETS},
            {"tag_name": "firefox-16", "assets": FULL_ASSETS},
        ],
    )
    assert ff.fetch_latest() == ("firefox-16", True)


def test_fetch_latest_skips_broken_versions(monkeypatch):
    # A version listed in BROKEN_VERSIONS must never be picked even when it's the
    # highest. Mock the set explicitly so the test doesn't depend on whatever the
    # currently-installed engine package happens to ship (it changes across
    # package versions — firefox-8 was broken in 0.3.0, not in 0.4.6).
    import invisible_core.constants as consts

    monkeypatch.setattr(consts, "BROKEN_VERSIONS", frozenset({"firefox-8"}))
    _serve(
        monkeypatch,
        [
            {"tag_name": "firefox-8", "assets": FULL_ASSETS},
            {"tag_name": "firefox-7", "assets": FULL_ASSETS},
        ],
    )
    assert ff.fetch_latest() == ("firefox-7", True)


def test_fetch_latest_skips_drafts_and_prereleases(monkeypatch):
    _serve(
        monkeypatch,
        [
            {"tag_name": "firefox-17", "draft": True, "assets": FULL_ASSETS},
            {"tag_name": "firefox-16", "prerelease": True, "assets": FULL_ASSETS},
            {"tag_name": "firefox-15", "assets": FULL_ASSETS},
        ],
    )
    assert ff.fetch_latest() == ("firefox-15", True)


def test_fetch_latest_incompatible_when_expected_asset_missing(monkeypatch):
    # An upstream-Firefox bump renames the assets → the installed package's
    # ARCHIVE_NAME no longer matches → needs a persona update, not a download.
    _serve(
        monkeypatch,
        [
            {
                "tag_name": "firefox-16",
                "assets": [
                    {"name": "checksums.txt"},
                    {"name": "firefox-151.0-stealth-win-x86_64.zip"},
                ],
            },
            {"tag_name": "firefox-15", "assets": FULL_ASSETS},
        ],
    )
    assert ff.fetch_latest() == ("firefox-16", False)


def test_fetch_latest_network_failure(monkeypatch):
    monkeypatch.setattr(ff, "_expected_asset", lambda: WIN_ASSET)

    def boom(req, timeout=20):
        raise OSError("no network")

    monkeypatch.setattr(ff.urllib.request, "urlopen", boom)
    assert ff.fetch_latest() == ("", False)


# --- active-build selection (invisible_launch) ---


def _fake_cache(monkeypatch, tmp_path, builds, binary_version="firefox-15"):
    """Create firefox-NN dirs under a fake cache root. `builds` is a list of
    (tag, has_binary, has_marker)."""
    import invisible_core.constants as consts
    import invisible_core.download as dl

    from invisible_playwright.constants import BINARY_ENTRY_REL

    monkeypatch.setattr(dl, "cache_root", lambda: tmp_path)
    monkeypatch.setattr(consts, "BINARY_VERSION", binary_version)
    entry_rel = BINARY_ENTRY_REL[sys.platform]
    for tag, has_binary, has_marker in builds:
        d = tmp_path / tag
        if has_binary:
            p = d / Path(entry_rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            # The entry executable is a THIN launcher (~700KB on every OS); the
            # engine's real weight is the shared core (libxul, hundreds of MB)
            # beside it. A whole build is recognised by that unpacked bulk, not
            # the entry's size — write a small entry plus a large core file so a
            # markerless pinned build reads as installed (an aborted-download
            # stub, which has no core, is exercised separately, #225).
            p.write_bytes(b"\0" * 4096)
            core = p.parent / "libxul.so"
            core.write_bytes(b"\0" * (inv._WHOLE_BUILD_BYTES + 1))
        else:
            d.mkdir(parents=True, exist_ok=True)
        if has_marker:
            (d / inv._INSTALL_MARKER).touch()


def test_active_build_pinned_only(monkeypatch, tmp_path):
    # the package-pinned build counts without a marker (ensure_binary installs
    # it markerless)
    _fake_cache(monkeypatch, tmp_path, [("firefox-15", True, False)])
    assert inv.installed_builds() == ["firefox-15"]
    assert inv.active_build() == "firefox-15"
    assert inv._binary_path_override() is None
    assert inv.is_invisible_installed() is True


def test_installed_build_in_suffixed_cache_dir_is_recognised(monkeypatch, tmp_path):
    # #234: the newer engine package names the cache dir with an upstream+
    # timestamp suffix (firefox-18_151.0_20260724001829) while BINARY_VERSION
    # stays the short 'firefox-18'. installed_builds must canonicalise the dir
    # name to 'firefox-18' so a fully-downloaded (marker) build is recognised —
    # not read as "not installed" and re-downloaded forever.
    import invisible_core.constants as consts
    import invisible_core.download as dl
    from pathlib import Path
    from invisible_playwright.constants import BINARY_ENTRY_REL

    monkeypatch.setattr(dl, "cache_root", lambda: tmp_path)
    monkeypatch.setattr(consts, "BINARY_VERSION", "firefox-18")
    d = tmp_path / "firefox-18_151.0_20260724001829"
    entry = d / Path(BINARY_ENTRY_REL[sys.platform])
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_bytes(b"\0" * 4096)  # thin launcher
    (d / inv._INSTALL_MARKER).touch()  # complete build

    assert inv.installed_builds() == ["firefox-18"]
    assert inv.active_build() == "firefox-18"
    assert inv.is_invisible_installed() is True


def test_pinned_build_with_stub_only_is_not_installed(monkeypatch, tmp_path):
    # #225: an aborted first download (common over Tor, esp. on Mac) can leave the
    # thin entry launcher present but the engine core unpacked. is_invisible_installed
    # must read False so the auto-download re-runs — otherwise FF is unlaunchable
    # forever.
    import invisible_core.constants as consts
    import invisible_core.download as dl
    from invisible_playwright.constants import BINARY_ENTRY_REL

    monkeypatch.setattr(dl, "cache_root", lambda: tmp_path)
    monkeypatch.setattr(consts, "BINARY_VERSION", "firefox-15")
    entry = tmp_path / "firefox-15" / Path(BINARY_ENTRY_REL[sys.platform])
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_bytes(b"\0" * 4096)  # thin launcher only, engine core missing

    # No COMPLETE build → not installed, so the auto-download re-runs. (active_build
    # still names BINARY_VERSION — that's simply the build the next fetch targets.)
    assert inv.installed_builds() == []
    assert inv.is_invisible_installed() is False


def test_whole_pinned_build_with_thin_entry_is_installed(monkeypatch, tmp_path):
    # The regression #225 REALLY caused: a fully-installed markerless pinned build
    # has a THIN entry launcher (~700KB) plus the large engine core. It must read
    # as installed — a size gate on the entry wrongly rejected it and re-downloaded
    # the engine on every start.
    import invisible_core.constants as consts
    import invisible_core.download as dl
    from invisible_playwright.constants import BINARY_ENTRY_REL

    monkeypatch.setattr(dl, "cache_root", lambda: tmp_path)
    monkeypatch.setattr(consts, "BINARY_VERSION", "firefox-15")
    d = tmp_path / "firefox-15"
    entry = d / Path(BINARY_ENTRY_REL[sys.platform])
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_bytes(b"\0" * 4096)  # thin launcher, like the real one
    (entry.parent / "libxul.so").write_bytes(b"\0" * (inv._WHOLE_BUILD_BYTES + 1))

    assert inv.installed_builds() == ["firefox-15"]
    assert inv.is_invisible_installed() is True


def test_stub_pinned_build_installed_once_marker_written(monkeypatch, tmp_path):
    # Even a stub-only dir counts if the completion marker is present (a real
    # install our resumable downloader finished and marked).
    import invisible_core.constants as consts
    import invisible_core.download as dl
    from invisible_playwright.constants import BINARY_ENTRY_REL

    monkeypatch.setattr(dl, "cache_root", lambda: tmp_path)
    monkeypatch.setattr(consts, "BINARY_VERSION", "firefox-15")
    d = tmp_path / "firefox-15"
    entry = d / Path(BINARY_ENTRY_REL[sys.platform])
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_bytes(b"\0" * 4096)
    (d / inv._INSTALL_MARKER).touch()

    assert inv.is_invisible_installed() is True


def test_active_build_prefers_newer_complete_build(monkeypatch, tmp_path):
    # A newer build is used ONLY when the bundled pkg can drive it — i.e. its
    # build number is <= the pkg's BINARY_VERSION. Here pkg is firefox-16 so both
    # 15 and 16 are drivable and the highest (16) wins.
    # firefox-15 is marked complete so it counts even though it isn't the pinned
    # build; firefox-16 (pinned + marked) is the highest drivable → active.
    _fake_cache(
        monkeypatch,
        tmp_path,
        [("firefox-15", True, True), ("firefox-16", True, True)],
        binary_version="firefox-16",
    )
    assert inv.installed_builds() == ["firefox-15", "firefox-16"]
    assert inv.active_build() == "firefox-16"
    # active == pkg build here, so the launcher resolves it itself (override None).
    override = inv._binary_path_override()
    assert override is None or "firefox-16" in override


def test_active_build_caps_at_pkg_binary_version(monkeypatch, tmp_path):
    # #405: the FF engine auto-updater downloaded firefox-19 (upstream 151.0) but
    # the bundled invisible_playwright is firefox-18 — a DIFFERENT juggler build
    # of the same upstream it CANNOT drive → every launch failed. installed_builds
    # must never surface a build newer than the pkg's BINARY_VERSION, so a
    # stranded firefox-19 install silently falls back to the drivable firefox-18.
    _fake_cache(
        monkeypatch,
        tmp_path,
        [("firefox-18", True, False), ("firefox-19", True, True)],
        binary_version="firefox-18",
    )
    assert inv.installed_builds() == ["firefox-18"]
    assert inv.active_build() == "firefox-18"
    # never point launches at the undrivable firefox-19
    override = inv._binary_path_override()
    assert override is None or "firefox-19" not in override


def test_fetch_latest_incompatible_when_build_exceeds_pkg(monkeypatch):
    # #405: a firefox-NN newer than the bundled pkg carries the SAME upstream
    # asset (both 151.0) so the old asset-only gate said "compatible" and it got
    # auto-installed → broke FF. fetch_latest must report it incompatible (needs a
    # persona update that ships the matching driver), even though the asset matches.
    #
    # PS-112 CHANGED THIS TEST'S PAYLOAD, DELIBERATELY. It used to serve
    # [firefox-19, firefox-18] with the pin at firefox-18 and assert
    # ("firefox-19", False). That payload is not a test of #405's invariant —
    # firefox-18 is present, drivable and ships the asset, so it is the PS-112
    # defect case (a drivable build passed over) with N-1 absent, and any
    # correct fix flips it to ("firefox-18", True). What the old assertion
    # encoded was the maximise-then-test ORDERING, not the invariant.
    #
    # #405 IS STILL HONOURED, AND STILL TESTED HERE: the invariant is "never
    # report a build the shipped driver cannot drive as compatible". Serving
    # firefox-19 ALONE tests exactly that, with nothing drivable to fall back
    # to — which is also the non-waivable control that the newer-but-undrivable
    # tag keeps being reported so the UI's "needs a newer persona" message
    # still fires. See test_fetch_latest_prefers_newest_drivable_over_newer_
    # undrivable for the case this payload used to occupy.
    import invisible_core.constants as consts

    monkeypatch.setattr(consts, "BINARY_VERSION", "firefox-18")
    _serve(monkeypatch, [{"tag_name": "firefox-19", "assets": FULL_ASSETS}])
    tag, compatible = ff.fetch_latest()
    assert tag == "firefox-19"
    assert compatible is False


def test_fetch_latest_prefers_newest_drivable_over_newer_undrivable(monkeypatch):
    # PS-112, THE DEFECT CASE. Upstream has moved above the driver pin, but a
    # drivable build exists BELOW it. The old code maximised over ALL releases
    # and applied the pin bound afterwards, so it returned ("firefox-20", False)
    # and app.py's `not self._engine2_compatible` gate refused to offer any
    # update at all — even though firefox-18 was present, drivable, and shipped
    # the asset. Measured on main before the fix: ("firefox-20", False).
    #
    # Asserted on the TUPLE fetch_latest returns, not on any helper being
    # called: reverting the selection change alone must turn this red.
    import invisible_core.constants as consts

    monkeypatch.setattr(consts, "BINARY_VERSION", "firefox-18")
    _serve(
        monkeypatch,
        [
            {"tag_name": "firefox-16", "assets": FULL_ASSETS},
            {"tag_name": "firefox-18", "assets": FULL_ASSETS},
            {"tag_name": "firefox-20", "assets": FULL_ASSETS},
        ],
    )
    assert ff.fetch_latest() == ("firefox-18", True)


def test_fetch_latest_reports_undrivable_tag_when_nothing_drivable(monkeypatch):
    # PS-112 AC3, NON-WAIVABLE CONTROL. When the ONLY release is above the pin
    # there is nothing to fall back to, and the pre-PS-112 behaviour must stand:
    # return the undrivable tag with compatible=False rather than ("", False).
    #
    # This is the case the obvious implementation gets wrong. Skipping
    # above-pin releases in-loop with a bare `continue` filters out the only
    # candidate, leaving best_tag empty and returning ("", False) — and BOTH
    # consumers guard on exactly that (`if tag:` in _check_engine2_async,
    # `if not tag: return` in _auto_update_engine2), so the operator-facing
    # "Firefox engine {tag} needs a newer persona" message is silenced and they
    # are told nothing at all. The tag being non-empty here is what keeps that
    # message reachable, so it is asserted explicitly.
    import invisible_core.constants as consts

    monkeypatch.setattr(consts, "BINARY_VERSION", "firefox-18")
    _serve(monkeypatch, [{"tag_name": "firefox-20", "assets": FULL_ASSETS}])
    tag, compatible = ff.fetch_latest()
    assert (tag, compatible) == ("firefox-20", False)
    assert tag, "empty tag silences the 'needs a newer persona' message"


def test_fetch_latest_picks_newest_when_every_release_is_drivable(monkeypatch):
    # PS-112 AC4, CONTROL A. Nothing upstream sits above the pin, so the
    # highest release is also the highest drivable one and must still be
    # chosen. Guards against the fix accidentally biasing DOWNWARD — e.g.
    # preferring a build strictly below the pin, or capping at N-1.
    import invisible_core.constants as consts

    monkeypatch.setattr(consts, "BINARY_VERSION", "firefox-18")
    _serve(
        monkeypatch,
        [
            {"tag_name": "firefox-16", "assets": FULL_ASSETS},
            {"tag_name": "firefox-18", "assets": FULL_ASSETS},
        ],
    )
    assert ff.fetch_latest() == ("firefox-18", True)


def test_fetch_latest_drivable_without_expected_asset_is_not_offered(monkeypatch):
    # PS-112 edge: the fallback must not launder a build that is drivable by
    # build number but does NOT ship this OS's asset. firefox-18 is under the
    # pin yet carries a renamed upstream asset, so it is not installable and
    # must not be announced as an update. Falls through to the report-only
    # path on the overall winner, exactly as an asset mismatch does today.
    import invisible_core.constants as consts

    monkeypatch.setattr(consts, "BINARY_VERSION", "firefox-18")
    _serve(
        monkeypatch,
        [
            {
                "tag_name": "firefox-18",
                "assets": [
                    {"name": "checksums.txt"},
                    {"name": "firefox-151.0-stealth-win-x86_64.zip"},
                ],
            },
            {"tag_name": "firefox-20", "assets": FULL_ASSETS},
        ],
    )
    assert ff.fetch_latest() == ("firefox-20", False)


def test_fetch_latest_logs_the_passed_over_undrivable_tag(monkeypatch, caplog):
    # PS-112 §5. Preferring the drivable build means the return value no longer
    # carries the fact that upstream has something newer. That information is
    # not dropped silently — it is logged to the "persona" logger from inside
    # the offering path. The return stays a 2-tuple: a `log=` parameter would
    # have to be threaded through both app.py consumers and breaks four
    # existing `lambda: (tag, compatible)` stubs in three other test files.
    import invisible_core.constants as consts

    monkeypatch.setattr(consts, "BINARY_VERSION", "firefox-18")
    _serve(
        monkeypatch,
        [
            {"tag_name": "firefox-18", "assets": FULL_ASSETS},
            {"tag_name": "firefox-20", "assets": FULL_ASSETS},
        ],
    )
    with caplog.at_level(logging.INFO, logger="persona"):
        assert ff.fetch_latest() == ("firefox-18", True)
    assert any(
        "firefox-20" in r.getMessage() and "firefox-18" in r.getMessage()
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]


def test_fetch_latest_does_not_log_when_nothing_was_passed_over(monkeypatch, caplog):
    # The PS-112 log line means "something newer exists that you can't drive".
    # When the chosen build IS the newest release there is nothing to report,
    # and firing it anyway would tell the operator to update persona for a
    # build that does not exist.
    import invisible_core.constants as consts

    monkeypatch.setattr(consts, "BINARY_VERSION", "firefox-18")
    _serve(
        monkeypatch,
        [
            {"tag_name": "firefox-16", "assets": FULL_ASSETS},
            {"tag_name": "firefox-18", "assets": FULL_ASSETS},
        ],
    )
    with caplog.at_level(logging.INFO, logger="persona"):
        assert ff.fetch_latest() == ("firefox-18", True)
    assert [
        r.getMessage() for r in caplog.records if "newer persona" in r.getMessage()
    ] == []


def test_active_build_ignores_unmarked_build(monkeypatch, tmp_path):
    # binary present but no completion marker = crashed mid-extract → the
    # half build must never become active
    _fake_cache(
        monkeypatch,
        tmp_path,
        [("firefox-15", True, False), ("firefox-16", True, False)],
    )
    assert inv.installed_builds() == ["firefox-15"]
    assert inv.active_build() == "firefox-15"
    assert inv._binary_path_override() is None


def test_active_build_ignores_broken_versions(monkeypatch, tmp_path):
    import invisible_core.constants as consts

    _fake_cache(
        monkeypatch,
        tmp_path,
        [("firefox-15", True, False), ("firefox-16", True, True)],
    )
    monkeypatch.setattr(consts, "BROKEN_VERSIONS", frozenset({"firefox-16"}))
    assert inv.installed_builds() == ["firefox-15"]
    assert inv.active_build() == "firefox-15"


def test_current_version_empty_when_not_installed(monkeypatch, tmp_path):
    _fake_cache(monkeypatch, tmp_path, [])
    assert inv.is_invisible_installed() is False
    assert ff.current_version() == ""


def _wire_checksummed_dl(monkeypatch, archive_bytes=b"data"):
    """A fake _resumable_download that serves a checksums.txt matching the
    archive it later writes, so the sha256 verify passes. Returns the asset
    name it checksums."""
    import hashlib
    import platform as _pyplatform

    from invisible_playwright.constants import ARCHIVE_NAME

    asset = ARCHIVE_NAME(sys.platform, _pyplatform.machine())
    digest = hashlib.sha256(archive_bytes).hexdigest()

    def fake_dl(url, path, progress=None, **kw):
        if url.endswith("checksums.txt"):
            Path(path).write_text(f"{digest}  {asset}\n", encoding="utf-8")
        else:
            Path(path).write_bytes(archive_bytes)
        return True

    monkeypatch.setattr(eng, "_resumable_download", fake_dl)
    return asset


def test_install_engine_build_marks_completion(monkeypatch, tmp_path):
    from invisible_playwright.constants import BINARY_ENTRY_REL

    # pkg drives firefox-16, so installing it becomes active (not capped out).
    _fake_cache(monkeypatch, tmp_path, [], binary_version="firefox-16")
    entry_rel = BINARY_ENTRY_REL[sys.platform]
    _wire_checksummed_dl(monkeypatch)

    def fake_extract(archive, dst, asset):
        p = Path(dst) / Path(entry_rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()

    monkeypatch.setattr(eng, "_extract_as", fake_extract)

    assert inv.install_engine_build("firefox-16") is True
    assert (tmp_path / "firefox-16" / inv._INSTALL_MARKER).exists()
    assert inv.active_build() == "firefox-16"


def test_install_engine_build_no_marker_when_extract_incomplete(
    monkeypatch, tmp_path
):
    _fake_cache(monkeypatch, tmp_path, [("firefox-15", True, False)])
    _wire_checksummed_dl(monkeypatch)
    # extraction produced no binary (bad archive) → not installed, no marker,
    # the previous build stays active
    monkeypatch.setattr(eng, "_extract_as", lambda a, d, n: None)

    assert inv.install_engine_build("firefox-16") is False
    assert not (tmp_path / "firefox-16" / inv._INSTALL_MARKER).exists()
    assert inv.active_build() == "firefox-15"


def test_install_engine_build_refuses_when_checksum_missing(monkeypatch, tmp_path):
    # The checksums.txt downloads fine but carries no line for our asset — an
    # unverifiable archive must NOT be installed (fail-closed, supply-chain).
    from invisible_playwright.constants import BINARY_ENTRY_REL

    _fake_cache(monkeypatch, tmp_path, [("firefox-15", True, False)])
    entry_rel = BINARY_ENTRY_REL[sys.platform]

    def fake_dl(url, path, progress=None, **kw):
        if url.endswith("checksums.txt"):
            # checksums for OTHER assets only — nothing for ours
            Path(path).write_text("deadbeef  some-other-asset.zip\n", encoding="utf-8")
        else:
            Path(path).write_bytes(b"data")
        return True

    def fake_extract(archive, dst, asset):
        p = Path(dst) / Path(entry_rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()

    monkeypatch.setattr(eng, "_resumable_download", fake_dl)
    monkeypatch.setattr(eng, "_extract_as", fake_extract)

    assert inv.install_engine_build("firefox-16") is False
    assert not (tmp_path / "firefox-16" / inv._INSTALL_MARKER).exists()
    assert inv.active_build() == "firefox-15"


def test_install_engine_build_refuses_on_checksum_mismatch(monkeypatch, tmp_path):
    # checksums.txt HAS our asset but the archive bytes don't match it (a MITM
    # swap or corrupt transfer that survived resume) — refuse the install.
    from invisible_playwright.constants import BINARY_ENTRY_REL

    _fake_cache(monkeypatch, tmp_path, [("firefox-15", True, False)])
    entry_rel = BINARY_ENTRY_REL[sys.platform]
    # checksum says the archive should hash to sha256(b"good"), but we serve
    # b"tampered" instead
    _wire_checksummed_dl(monkeypatch, archive_bytes=b"good")

    def fake_dl_tampered(url, path, progress=None, **kw):
        import hashlib
        import platform as _pyplatform

        from invisible_playwright.constants import ARCHIVE_NAME

        asset = ARCHIVE_NAME(sys.platform, _pyplatform.machine())
        if url.endswith("checksums.txt"):
            good = hashlib.sha256(b"good").hexdigest()
            Path(path).write_text(f"{good}  {asset}\n", encoding="utf-8")
        else:
            Path(path).write_bytes(b"tampered")
        return True

    monkeypatch.setattr(eng, "_resumable_download", fake_dl_tampered)

    def fake_extract(archive, dst, asset):
        p = Path(dst) / Path(entry_rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()

    monkeypatch.setattr(eng, "_extract_as", fake_extract)

    assert inv.install_engine_build("firefox-16") is False
    assert not (tmp_path / "firefox-16" / inv._INSTALL_MARKER).exists()
    assert inv.active_build() == "firefox-15"


# --- app wiring ---


class InlineThread:
    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


def test_engine2_update_available_gating(monkeypatch):
    import src.ui.app as app_mod

    monkeypatch.setattr(inv, "is_invisible_installed", lambda: True)
    monkeypatch.setattr(ff, "current_version", lambda: "firefox-15")

    stub = SimpleNamespace(_engine2_latest="firefox-16", _engine2_compatible=True)
    assert app_mod.App._engine2_update_available(stub) is True

    stub._engine2_compatible = False
    assert app_mod.App._engine2_update_available(stub) is False

    stub._engine2_compatible = True
    stub._engine2_latest = "firefox-15"
    assert app_mod.App._engine2_update_available(stub) is False

    stub._engine2_latest = "firefox-16"
    monkeypatch.setattr(inv, "is_invisible_installed", lambda: False)
    assert app_mod.App._engine2_update_available(stub) is False


def test_update_engine2_downloads_tag(monkeypatch):
    import src.ui.app as app_mod

    calls = {}

    def fake_download(tag, progress=None, log=None):
        calls["tag"] = tag
        return True

    monkeypatch.setattr(ff, "download_engine", fake_download)
    monkeypatch.setattr(app_mod.threading, "Thread", InlineThread)

    stub = SimpleNamespace(
        _engine2_busy=False,
        _engine2_status="",
        _engine2_latest="firefox-16",
        _engine2_start_t=0.0,
        _engine2_throttle=None,
        _engine2_pstate=None,
        _engine2_bar=SimpleNamespace(value=None),
        _engine2_detail=SimpleNamespace(value=""),
        _engine2_progress_cb=lambda d, t: None,
        _refresh_sidebar=lambda: None,
        _log=lambda m: None,
    )
    app_mod.App._update_engine2_async(stub)

    assert calls["tag"] == "firefox-16"
    assert stub._engine2_busy is False
    assert stub._engine2_status == ""


def test_check_engine2_incompatible_says_update_persona(monkeypatch):
    import src.ui.app as app_mod

    monkeypatch.setattr(ff, "fetch_latest", lambda timeout=20: ("firefox-16", False))
    monkeypatch.setattr(ff, "current_version", lambda: "firefox-15")
    monkeypatch.setattr(app_mod.threading, "Thread", InlineThread)

    logs = []
    stub = SimpleNamespace(
        _engine2_checking=False,
        _engine2_latest="",
        _engine2_compatible=True,
        _engine2_status="",
        _engine2_update_available=lambda: False,
        _refresh_engine_text=lambda *a: None,
        _log=logs.append,
    )
    app_mod.App._check_engine2_async(stub)

    assert stub._engine2_latest == "firefox-16"
    assert stub._engine2_compatible is False
    assert stub._engine2_checking is False
    assert stub._engine2_status == "update persona for the newest engine"
    assert any("newer persona" in m for m in logs)


def test_prune_removes_old_builds_including_superseded_pinned(monkeypatch, tmp_path):
    # #213: once a newer build is installed and active, the shipped pinned
    # firefox-15 (markerless BINARY_VERSION) is dead weight — launches and the
    # install check both resolve the newer build, so removing firefox-15
    # reclaims ~600MB and never triggers a re-download. Prune firefox-14
    # (marked, ours) AND firefox-15 (superseded pinned).
    #
    # PS-51 CHANGES THIS CASE, and the change is worth stating precisely —
    # INCLUDING the part that is a LIMIT rather than a win.
    #
    # Retention spares exactly ONE build below `keep`: the highest. Normally
    # that is the rollback target, because normally `keep` IS the active
    # build. THIS FIXTURE IS THE CASE WHERE THAT BREAKS DOWN, so do not read
    # the surviving firefox-15 below as "the way back":
    #
    #   installed_builds() -> [13, 14, 15]   firefox-16 capped out (#405)
    #   active_build()     -> firefox-15     <- what LAUNCHES
    #   rollback_target()  -> firefox-14     <- what a revert would go TO
    #   ...after this prune...
    #   survivors          -> [15, 16]
    #   rollback_target()  -> ""             <- the revert is now REFUSED
    #
    # `installed_builds` caps what it surfaces at BINARY_VERSION (#405 — a
    # build newer than the shipped driver can't be driven), so `keep`
    # (firefox-16) is ABOVE the cap while active_build() is the pinned
    # firefox-15. Retention measures "highest below `keep`", and with the cap
    # binding that is firefox-15 — the ACTIVE build, the one you roll back
    # FROM. The slot is spent on it, the genuine target firefox-14 is pruned,
    # and retention yields NO usable way back in this configuration.
    #
    # So the firefox-15 assertion below is protecting the LAUNCHING build, not
    # the undo path. The test still passes for #213's reason (13 and 14 are
    # reclaimed); what it must not be read as is proof that rollback survives
    # a prune when the cap binds. See
    # test_prune_with_keep_above_cap_leaves_no_rollback_target, which pins that
    # limit explicitly, and _prune_old_engine_builds' RETENTION docstring for
    # why it is left as-is (unreachable through the app; the obvious
    # alternative is measurably worse).
    #
    # In the REACHABLE configuration — `keep` at or below the cap, i.e. the
    # active build — this is the depth-1 policy doing its job and not a leak:
    # footprint stays two builds (retained + active), and everything below is
    # still reclaimed. #213's concern was a build sitting there for NOTHING;
    # there, it has a job — it is the way back.
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-13", True, True),   # old, ours → pruned
            ("firefox-14", True, True),   # old, ours → pruned
            ("firefox-15", True, False),  # superseded pinned → retained (PS-51)
            ("firefox-16", True, True),   # new active → kept
        ],
        binary_version="firefox-15",
    )
    logs = []
    inv._prune_old_engine_builds(keep="firefox-16", log=logs.append)
    assert not (tmp_path / "firefox-13").exists()
    assert not (tmp_path / "firefox-14").exists()
    assert (tmp_path / "firefox-15").exists(), (
        "retention spares the highest build below keep; with keep above the "
        "#405 cap that is active_build() itself, so this protects the "
        "LAUNCHING build — not the way back (PS-51, see the comment above)"
    )
    assert (tmp_path / "firefox-16").exists()
    assert any("firefox-13" in m for m in logs)
    assert any("firefox-14" in m for m in logs)


def test_prune_with_keep_above_cap_leaves_no_rollback_target(monkeypatch, tmp_path):
    """PS-51 LIMIT, pinned deliberately: when `keep` is above the #405
    visibility cap, retention does NOT leave a way back.

    Retention spares the highest build below `keep`. That is the rollback
    target only while `keep` is the active build. Here firefox-16 is installed
    but capped out of installed_builds (the shipped driver can't drive it), so
    active_build() is the pinned firefox-15 while `keep` is firefox-16 — and
    the retention slot is spent on the ACTIVE build instead of on a build below
    it. The genuine target firefox-14 is pruned and the revert gesture is
    refused afterwards.

    This is asserted rather than fixed. It is unreachable through the app
    (fetch_latest marks any build above the pin incompatible, and the reachable
    prune path takes `keep` from the already-capped list), and measuring
    retain_n below active_build() instead is worse — it deletes the launching
    build and still yields no target. See _prune_old_engine_builds' RETENTION
    docstring. If someone makes this state reachable, THIS test is the one that
    should start failing."""
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-14", True, True),   # the genuine rollback target
            ("firefox-15", True, False),  # shipped pinned == active_build()
            ("firefox-16", True, True),   # installed but capped out (#405)
        ],
        binary_version="firefox-15",
    )
    # Precondition: the cap binds, and a way back exists right now.
    assert eng.installed_builds() == ["firefox-14", "firefox-15"]
    assert eng.active_build() == "firefox-15"
    assert eng.rollback_target() == "firefox-14"

    inv._prune_old_engine_builds(keep="firefox-16", log=lambda m: None)

    # The spared build is the ACTIVE one, not the way back.
    assert (tmp_path / "firefox-15").exists()
    assert eng.active_build() == "firefox-15"
    assert not (tmp_path / "firefox-14").exists(), (
        "the rollback target was pruned — retention's slot went to the active "
        "build because `keep` is above the cap (PS-51, stated limit)"
    )
    assert eng.rollback_target() == "", (
        "with `keep` above the cap the revert is refused after the prune; if "
        "this now returns a build, retention has been changed and the "
        "RETENTION docstring's stated limit needs updating with it"
    )


def test_prune_superseded_builds_cleans_stale_pinned_at_startup(monkeypatch, tmp_path):
    # #213: an upgrade that happened on an earlier run left ~600MB of stale
    # builds behind (the old prune kept them). The startup housekeeping prune
    # reclaims them now that firefox-16 is active — without a fresh download.
    #
    # PS-51: depth-1 retention spares the highest build below the active one
    # (firefox-15, the rollback target). firefox-14 is the genuinely stale
    # build this test is about — two updates back, no way to reach it from the
    # UI, and nothing but dead weight. Housekeeping still reclaims it, which is
    # the #213 promise; what changed is that the reclaim floor is now one build
    # lower, not that it stopped happening.
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-14", True, True),   # two back, stale → reclaimed
            ("firefox-15", True, True),   # retained rollback target (PS-51)
            ("firefox-16", True, True),   # active
        ],
        binary_version="firefox-16",
    )
    logs = []
    inv.prune_superseded_builds(log=logs.append)
    assert not (tmp_path / "firefox-14").exists()
    assert (tmp_path / "firefox-15").exists(), (
        "the highest build below the active one is the retained rollback "
        "target — housekeeping must not reclaim the way back (PS-51)"
    )
    assert (tmp_path / "firefox-16").exists()
    assert any("firefox-14" in m for m in logs)


def test_prune_superseded_builds_keeps_sole_engine(monkeypatch, tmp_path):
    # Only the pinned build installed — nothing higher is active, so it must
    # NOT be pruned (that would delete the only engine and force a re-download).
    _fake_cache(
        monkeypatch,
        tmp_path,
        [("firefox-15", True, False)],
        binary_version="firefox-15",
    )
    inv.prune_superseded_builds()
    assert (tmp_path / "firefox-15").exists()


def test_prune_leaves_unmarked_half_downloads(monkeypatch, tmp_path):
    # A crashed mid-extract build at a NON-pinned version (binary, no marker)
    # is a half download, not ours to delete — leave it (a later download
    # resumes/overwrites it). Only the pinned version is safe to prune markerless.
    _fake_cache(
        monkeypatch,
        tmp_path,
        [("firefox-14", True, False), ("firefox-16", True, True)],
        binary_version="firefox-15",
    )
    inv._prune_old_engine_builds(keep="firefox-16")
    assert (tmp_path / "firefox-14").exists()


def test_prune_defers_while_a_profile_is_running(monkeypatch, tmp_path):
    # PS-14: pruning deletes whole build trees and keeps only the HIGHEST
    # build, so a profile still running on the PREVIOUS build has the tree it
    # is executing from deleted out from under it. The old code relied on
    # shutil.rmtree raising OSError "if the build is in use" — true only on
    # Windows; POSIX unlink happily succeeds, so on Linux/macOS the deletion
    # went through and Firefox lost every resource it had not yet lazily
    # loaded (omni.ja, component libs, locale files). Pruning must ASK.
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-14", True, True),   # would be pruned if we didn't defer
            ("firefox-15", True, True),   # the build the live profile is on
            ("firefox-16", True, True),   # the newly-installed active build
        ],
        binary_version="firefox-15",
    )
    monkeypatch.setattr(eng, "_in_use_provider", lambda: True)
    # Any rmtree at all is the defect — assert on the call, not just the
    # survival of the dir, so a "deleted then restored" impl can't pass.
    import shutil

    monkeypatch.setattr(
        shutil,
        "rmtree",
        lambda *a, **k: pytest.fail("pruned a build while a profile was running"),
    )
    logs = []
    inv._prune_old_engine_builds(keep="firefox-16", log=logs.append)

    assert (tmp_path / "firefox-14").exists()
    assert (tmp_path / "firefox-15").exists(), (
        "the build a running profile is executing from must survive the prune"
    )
    assert (tmp_path / "firefox-16").exists()
    # The deferral is logged, and says WHY — a silent skip reads as a bug when
    # disk isn't reclaimed.
    assert any("running" in m for m in logs), logs


def test_prune_proceeds_when_no_profile_is_running(monkeypatch, tmp_path):
    # The guard defers only while something is actually running: with the
    # provider reporting "none", pruning reclaims what it is allowed to.
    #
    # PS-51: firefox-15 is now the retained rollback target, so the build that
    # proves "the prune ran" is firefox-14. This test is about the in-use
    # GUARD, not the retention floor — the claim being defended is that a
    # provider reporting "none" lets the prune proceed and logs no deferral.
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-14", True, True),
            ("firefox-15", True, False),
            ("firefox-16", True, True),
        ],
        binary_version="firefox-15",
    )
    monkeypatch.setattr(eng, "_in_use_provider", lambda: False)
    logs = []
    inv._prune_old_engine_builds(keep="firefox-16", log=logs.append)

    assert not (tmp_path / "firefox-14").exists()
    assert (tmp_path / "firefox-15").exists(), (
        "retained rollback target (PS-51) — the prune ran, it just has a floor"
    )
    assert (tmp_path / "firefox-16").exists()
    assert not any("running" in m for m in logs), logs


def test_prune_startup_housekeeping_defers_while_running(monkeypatch, tmp_path):
    # prune_superseded_builds is the STARTUP prune (app.py's engine check calls
    # it unguarded); it delegates to _prune_old_engine_builds, so it must
    # inherit the deferral rather than need its own condition.
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-15", True, True),   # superseded, but possibly in use
            ("firefox-16", True, True),   # active
        ],
        binary_version="firefox-16",
    )
    monkeypatch.setattr(eng, "_in_use_provider", lambda: True)
    logs = []
    inv.prune_superseded_builds(log=logs.append)

    assert (tmp_path / "firefox-15").exists()
    assert (tmp_path / "firefox-16").exists()
    assert any("running" in m for m in logs), logs


def test_prune_with_no_provider_wired_behaves_exactly_as_before(monkeypatch, tmp_path):
    # A direct library call with no UI has no session state to consult. Unset
    # must mean "prune proceeds" — the guard is a safety net over the wired
    # production path, not a fail-closed brake that would silently stop
    # reclaiming ~320-600MB per stale build in every non-UI caller.
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-13", True, True),
            ("firefox-14", True, True),
            ("firefox-16", True, True),
        ],
        binary_version="firefox-15",
    )
    monkeypatch.setattr(eng, "_in_use_provider", None)
    inv._prune_old_engine_builds(keep="firefox-16")
    # PS-51: firefox-14 is now the retained rollback target, so firefox-13 is
    # what proves the prune actually ran. The claim under test is unchanged —
    # an unset provider must not fail closed and stop reclaiming disk.
    assert not (tmp_path / "firefox-13").exists()
    assert (tmp_path / "firefox-14").exists(), (
        "retained rollback target (PS-51) — the prune ran, it just has a floor"
    )


def test_prune_proceeds_when_the_provider_raises(monkeypatch, tmp_path):
    # A broken oracle must not wedge disk reclamation forever — degrade to
    # today's behaviour rather than never pruning again.
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-13", True, True),
            ("firefox-14", True, True),
            ("firefox-16", True, True),
        ],
        binary_version="firefox-15",
    )

    def boom():
        raise RuntimeError("launcher unavailable")

    monkeypatch.setattr(eng, "_in_use_provider", boom)
    logs = []
    inv._prune_old_engine_builds(keep="firefox-16", log=logs.append)
    # PS-51: firefox-14 is the retained rollback target, so firefox-13 is what
    # proves the prune proceeded despite the broken oracle.
    assert not (tmp_path / "firefox-13").exists()
    assert (tmp_path / "firefox-14").exists(), (
        "retained rollback target (PS-51) — the prune ran, it just has a floor"
    )
    # Failing open degrades back into the exact deletion this guard exists to
    # prevent, so it must not do so SILENTLY: the raise is diagnosable, and the
    # message carries the cause rather than just noting a failure.
    assert any("in-use check failed" in m for m in logs), logs
    assert any("launcher unavailable" in m for m in logs), logs


def test_set_in_use_provider_is_visible_to_the_prune_path(monkeypatch, tmp_path):
    # invisible_launch re-exports engine_install's names, and `from x import y`
    # binds by VALUE — so re-exporting the _in_use_provider VARIABLE would hand
    # out a stale copy the setter's rebind never reaches. Wiring through the
    # setter (the only supported route) must actually reach the prune that
    # reads engine_install's own global.
    _fake_cache(
        monkeypatch,
        tmp_path,
        [("firefox-14", True, True), ("firefox-16", True, True)],
        binary_version="firefox-15",
    )
    monkeypatch.setattr(eng, "_in_use_provider", None)
    inv.set_in_use_provider(lambda: True)
    try:
        logs = []
        inv._prune_old_engine_builds(keep="firefox-16", log=logs.append)
    finally:
        # module global — restore explicitly so the suite isn't polluted
        inv.set_in_use_provider(None)

    assert (tmp_path / "firefox-14").exists(), (
        "a provider wired via set_in_use_provider must reach the prune path"
    )
    assert any("running" in m for m in logs), logs
