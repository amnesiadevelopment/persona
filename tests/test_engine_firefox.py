import json
import logging
import subprocess
import sys
import time
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
    # The invariant is "never report a build the shipped driver cannot drive as
    # compatible". Serving firefox-19 ALONE tests exactly that, with nothing
    # drivable to fall back to.
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
    # PS-112 §5, the LOG-FILE half. The operator-facing channel is the third
    # return value (`capped_by`, covered by the fetch_latest_full tests below
    # and by the two consumer tests); this line is the session log file, so the
    # update trail reads in one place alongside the rest of it.
    #
    # It is NOT the mechanism the UI depends on, and must not be mistaken for
    # it: the "persona" logger's console handler is WARNING (src/core/logging.py)
    # and the sidebar is fed by AppUI._log, which this module cannot reach.
    # Round 1 of PS-112 relied on this line alone and the audit failed for
    # exactly that reason — a line in a file the operator has to go find does
    # not offset a false "up to date" in the interface.
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


def test_fetch_latest_full_names_the_capped_build(monkeypatch):
    # PS-112 ROUND 2. `capped_by` is the third return value and it is the
    # channel the operator-facing consumers actually read. Upstream [18, 20]
    # with the pin at firefox-18: firefox-18 is offered as drivable, and
    # firefox-20 is named as the build that was passed over.
    #
    # This is what makes the "capped" state expressible at all. Once
    # compatible is True the consumers' `not compatible` branch is unreachable,
    # and when the offered tag equals what is installed `is_newer` is False
    # too — so without this value they have nothing left to say and report
    # "up to date" while firefox-20 exists.
    import invisible_core.constants as consts

    monkeypatch.setattr(consts, "BINARY_VERSION", "firefox-18")
    _serve(
        monkeypatch,
        [
            {"tag_name": "firefox-18", "assets": FULL_ASSETS},
            {"tag_name": "firefox-20", "assets": FULL_ASSETS},
        ],
    )
    assert ff.fetch_latest_full() == ("firefox-18", True, "firefox-20")


def test_fetch_latest_full_capped_by_is_empty_when_nothing_passed_over(monkeypatch):
    # The other side of the distinction. `capped_by` means "a higher release
    # exists that you cannot drive". When the drivable winner IS the newest
    # release there is nothing to name, and reporting one would send the
    # operator off to update persona for a build that does not exist.
    #
    # Both no-cap shapes are covered, because they reach '' by different
    # routes: everything drivable (the offer path) and nothing drivable (the
    # report-only path, where the tag returned is already the newest release).
    import invisible_core.constants as consts

    monkeypatch.setattr(consts, "BINARY_VERSION", "firefox-18")

    _serve(
        monkeypatch,
        [
            {"tag_name": "firefox-16", "assets": FULL_ASSETS},
            {"tag_name": "firefox-18", "assets": FULL_ASSETS},
        ],
    )
    assert ff.fetch_latest_full() == ("firefox-18", True, "")

    # Nothing drivable: AC3's control. The tag IS the newest release, so there
    # is nothing passed over — the existing `not compatible` branch speaks here.
    _serve(monkeypatch, [{"tag_name": "firefox-20", "assets": FULL_ASSETS}])
    assert ff.fetch_latest_full() == ("firefox-20", False, "")


def test_fetch_latest_is_the_narrow_view_of_fetch_latest_full(monkeypatch):
    # The 2-tuple wrapper must stay exactly that — the first two values of the
    # 3-tuple, unchanged. Callers that only want the offer (and the test stubs
    # shaped `lambda: (tag, compatible)`) keep working, exactly as
    # updater.fetch_latest wraps updater.fetch_latest_full.
    import invisible_core.constants as consts

    monkeypatch.setattr(consts, "BINARY_VERSION", "firefox-18")
    _serve(
        monkeypatch,
        [
            {"tag_name": "firefox-18", "assets": FULL_ASSETS},
            {"tag_name": "firefox-20", "assets": FULL_ASSETS},
        ],
    )
    full = ff.fetch_latest_full()
    assert ff.fetch_latest() == full[:2] == ("firefox-18", True)
    # ...and the capped build is genuinely only visible through the wide view.
    assert full[2] == "firefox-20"


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

    monkeypatch.setattr(
        ff, "fetch_latest_full", lambda timeout=20: ("firefox-16", False, "")
    )
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


def test_check_engine2_capped_operator_at_max_drivable_is_still_told(monkeypatch):
    # PS-112 ROUND 2, THE BLOCKING REGRESSION. The operator is ALREADY ON the
    # highest drivable build and upstream sits above the pin: pin firefox-18,
    # upstream [18, 20], installed firefox-18. fetch_latest_full offers
    # firefox-18 with compatible=True and names firefox-20 as capped_by.
    #
    # This input reaches the SAME silence AC3 exists to prevent, by a route
    # AC3's own payload does not touch. Both older branches fall through:
    #   * _engine2_update_available() is False — the offered tag IS what is
    #     installed, so is_newer says no.
    #   * the `not compatible` branch is unreachable — compatible is True,
    #     which is precisely what the PS-112 fix achieves.
    # Before this round the row therefore went BLANK while firefox-20 existed.
    #
    # This is not a corner case: it is where the fix's own success path leads.
    # An operator on firefox-16 is correctly offered firefox-18, installs it,
    # and from that moment lands here on every check.
    #
    # Asserted on what the OPERATOR is told — the row status and the UI log
    # line — not on capped_by being read or any helper being called.
    import src.ui.app as app_mod

    monkeypatch.setattr(
        ff,
        "fetch_latest_full",
        lambda timeout=20: ("firefox-18", True, "firefox-20"),
    )
    monkeypatch.setattr(ff, "current_version", lambda: "firefox-18")
    monkeypatch.setattr(app_mod.threading, "Thread", InlineThread)

    logs = []
    stub = SimpleNamespace(
        _engine2_checking=False,
        _engine2_latest="",
        _engine2_compatible=False,
        _engine2_status="",
        # The operator is on the offered build: there is no update to advertise.
        _engine2_update_available=lambda: False,
        _refresh_engine_text=lambda *a: None,
        _log=logs.append,
    )
    app_mod.App._check_engine2_async(stub)

    assert stub._engine2_status == "update persona for the newest engine", (
        "operator at the max drivable build with upstream above the pin was "
        "told nothing — the row fell back to the bare version"
    )
    assert any("firefox-20" in m and "newer persona" in m for m in logs), (
        f"the passed-over build was never named to the operator: {logs}"
    )
    # And it must name the build they cannot drive, not the one they are on.
    assert not any("up to date" in m for m in logs), logs


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


# ---------------------------------------------------------------------------
# PS-221: pruning defers on WHICH build a running profile is executing from,
# not on whether anything is running at all.
#
# Every test below asserts on DIRECTORIES ON DISK, never that a helper was
# called: the claim under test is that a build tree survives or is reclaimed,
# and a test that checked the call would pass over an implementation that
# consulted the oracle and then deleted the build anyway.
#
# The dangerous direction is guarded harder than the useful one. Reclaiming
# less disk than possible costs disk; treating an UNRESOLVED running profile as
# "using no build" deletes a build out from under a live session — Firefox
# loads from its build dir all session long, and POSIX unlink does not refuse
# it. So four separate tests pin UNKNOWN → DEFER, one per producer.
# ---------------------------------------------------------------------------


class _PollingProc:
    """A stand-in for a live ``Popen``: ``poll()`` is None while it runs.

    Module-level so the survivor tests below can register a session on the REAL
    launcher without spawning a second real process — the survivor's liveness
    is what has to be real there, not ours.
    """

    def poll(self):
        return None


def _stamped(engine, build):
    """A minimal stand-in for a Profile carrying a launch stamp.

    Retained for the tests that assert on the PERSISTED record. The prune's
    narrowing no longer reads it — see `_session` below and the module note on
    why a persisted last-launch stamp cannot answer a liveness question.
    """
    return SimpleNamespace(last_launch_engine=engine, last_launch_build=build)


def _session(engine, build):
    """A LIVE session's (engine, build), the shape the launcher records at
    registration and the shape the prune's narrowing actually consumes.

    None (rather than a pair) is the launcher's "this running name has no
    session record" — an in-flight spawn.
    """
    return (engine, build)


def _wire_narrowing(monkeypatch, sessions):
    """Wire both oracles the way the app does: the boolean GATE from the set of
    running names, and the NARROWING join over the launcher's live session map.

    `sessions` is `{name: (engine, build) | None}` — exactly the shape
    `BrowserLauncher.running_session_builds()` returns, keyed by the running
    names. A None VALUE is a running name with no session record yet.

    Deliberately routed through the real `firefox_builds_in_use` rather than a
    hand-written set, because the UNKNOWN cases below are properties of that
    join — a test that supplied the set directly would be asserting on its own
    fixture and would stay green over the mutation in AC8.
    """
    from src.services.browser.launch_provenance import firefox_builds_in_use

    monkeypatch.setattr(eng, "_in_use_provider", lambda: len(sessions) > 0)
    monkeypatch.setattr(
        eng,
        "_in_use_builds_provider",
        lambda: firefox_builds_in_use(lambda: sessions),
    )


def test_prune_reclaims_a_build_no_running_profile_is_stamped_to(
    monkeypatch, tmp_path
):
    """THE SLICE, both halves in one fixture: while a profile runs, the build
    it is executing from survives and a build nothing is executing from goes.

    Before PS-221 the second half was false — the prune returned early on "is
    anything running?" and reclaimed nothing at all (measured at 64fe2aa:
    firefox-13 survived). An operator who kept any profile open never got a
    byte back, while each stale build is ~320-600MB.
    """
    # THE LIVE BUILD IS firefox-13, NOT the highest prunable one, on purpose.
    # Retention already spares the highest build below `keep` (firefox-15
    # here), so staking this test on that build would pass over an
    # implementation that ignores the stamp entirely — the assertion would be
    # satisfied by PS-51's floor rather than by this slice. firefox-13 is
    # spared by nothing except the narrowing, and firefox-14 (equally
    # prunable, nothing on it) is what proves the prune actually ran.
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-13", True, True),   # THE LIVE BUILD → must survive
            ("firefox-14", True, True),   # nothing is on it → reclaimed
            ("firefox-15", True, True),   # retained rollback target (PS-51)
            ("firefox-16", True, True),   # keep / active
        ],
        binary_version="firefox-16",
    )
    _wire_narrowing(
        monkeypatch,
        {"live-one": _session("firefox", "firefox-13")},
    )
    logs = []
    inv._prune_old_engine_builds(keep="firefox-16", log=logs.append)

    assert (tmp_path / "firefox-13").exists(), (
        "the build the running session is executing from must survive — "
        "it is the exact harm the wholesale guard existed to prevent"
    )
    assert not (tmp_path / "firefox-14").exists(), (
        "a build NO running profile is executing from must be reclaimed even "
        "while another profile runs — this is the whole slice, and it is the "
        "half that fails on main"
    )
    assert (tmp_path / "firefox-15").exists(), "retained rollback target (PS-51)"
    assert (tmp_path / "firefox-16").exists()


def test_prune_defers_when_a_running_sessions_build_is_none(monkeypatch, tmp_path):
    """UNKNOWN #1 — the session's build could not be read at launch.

    `engine_build_for` returns None on ANY read failure, deliberately: a build
    that names the wrong thing is worse than none, because the comparison it
    enables returns a confident false answer. So a session registered as
    (firefox, None) means "running, build not known", and the prune must defer
    wholesale. Reading it as "this session is on no build" would authorise
    deleting every build.
    A SECOND running session is resolvable, deliberately. With only the
    unresolved one, an implementation that silently SKIPPED it would return an
    EMPTY set and be caught by the separate empty-set backstop — so the test
    would stay green over an inverted UNKNOWN rule and prove nothing about it.
    With a resolved session beside it, skipping yields a NON-empty set, the
    backstop does not fire, and the only thing between the prune and a live
    build is the UNKNOWN rule itself. It is also the realistic shape: one
    session accounted for, one that is not.
    """
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-13", True, True),
            ("firefox-14", True, True),
            ("firefox-15", True, True),
            ("firefox-16", True, True),
        ],
        binary_version="firefox-15",
    )
    _wire_narrowing(
        monkeypatch,
        {
            "live-one": _session("firefox", None),
            "resolved-one": _session("firefox", "firefox-15"),
        },
    )
    logs = []
    inv._prune_old_engine_builds(keep="firefox-16", log=logs.append)

    assert (tmp_path / "firefox-13").exists(), (
        "a None build is UNKNOWN, not 'using no build' — the prune must defer"
    )
    assert (tmp_path / "firefox-15").exists()
    assert any("running" in m for m in logs), logs


def test_prune_defers_when_a_running_name_has_no_session_record(
    monkeypatch, tmp_path
):
    """UNKNOWN #2 — a running name the launcher holds no session record for.

    The populations the ticket named here were properties of the PERSISTED
    stamp: a profile whose last launch predates the record shipping, and one
    imported from an archive. Neither can reach this guard any more, because
    the guard no longer reads that record — which is a strengthening, not a
    gap: a profile that never launched under THIS process has no session entry
    at all, so it can only ever read as UNKNOWN and can never contribute a
    stale build. The shape that does reach it is a running name with no entry,
    and it must defer rather than be skipped.
    A SECOND running session is resolvable, deliberately. With only the
    unresolved one, an implementation that silently SKIPPED it would return an
    EMPTY set and be caught by the separate empty-set backstop — so the test
    would stay green over an inverted UNKNOWN rule and prove nothing about it.
    With a resolved session beside it, skipping yields a NON-empty set, the
    backstop does not fire, and the only thing between the prune and a live
    build is the UNKNOWN rule itself. It is also the realistic shape: one
    session accounted for, one that is not.
    """
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-13", True, True),
            ("firefox-14", True, True),
            ("firefox-15", True, True),
            ("firefox-16", True, True),
        ],
        binary_version="firefox-15",
    )
    # Running, but the launcher has no session record for it.
    _wire_narrowing(
        monkeypatch,
        {
            "legacy-one": None,
            "resolved-one": _session("firefox", "firefox-15"),
        },
    )
    logs = []
    inv._prune_old_engine_builds(keep="firefox-16", log=logs.append)

    assert (tmp_path / "firefox-13").exists(), (
        "a running name with no session record is UNKNOWN — the prune must "
        "defer, not reclaim"
    )
    assert (tmp_path / "firefox-15").exists()
    assert any("running" in m for m in logs), logs


def test_prune_defers_on_a_chromium_stamp_because_the_shapes_do_not_compare(
    monkeypatch, tmp_path
):
    """UNKNOWN #3 — a CHROMIUM session while the firefox prune runs.

    The two engines' build strings are deliberately not normalised: chromium
    reports a dotted version (151.0.8000.10), firefox a firefox-NN tag, and
    they cannot be compared or ordered. `build_number` answers -1 for the
    dotted shape, so the seductive bug is to drop it from the set and carry on
    — which silently asserts "no firefox build is in use" on the strength of a
    string that says nothing about firefox. It must collapse the answer to
    UNKNOWN instead.
    A SECOND running session is resolvable, deliberately. With only the
    unresolved one, an implementation that silently SKIPPED it would return an
    EMPTY set and be caught by the separate empty-set backstop — so the test
    would stay green over an inverted UNKNOWN rule and prove nothing about it.
    With a resolved session beside it, skipping yields a NON-empty set, the
    backstop does not fire, and the only thing between the prune and a live
    build is the UNKNOWN rule itself. It is also the realistic shape: one
    session accounted for, one that is not.
    """
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-13", True, True),
            ("firefox-14", True, True),
            ("firefox-15", True, True),
            ("firefox-16", True, True),
        ],
        binary_version="firefox-15",
    )
    _wire_narrowing(
        monkeypatch,
        {
            "chrome-one": _session("chromium", "151.0.8000.10"),
            "resolved-one": _session("firefox", "firefox-15"),
        },
    )
    logs = []
    inv._prune_old_engine_builds(keep="firefox-16", log=logs.append)

    assert (tmp_path / "firefox-13").exists(), (
        "a chromium build is not comparable to firefox-NN — dropping it and "
        "pruning would delete a firefox build on no evidence at all"
    )
    assert (tmp_path / "firefox-15").exists()
    assert any("running" in m for m in logs), logs


def test_prune_defers_for_a_launch_still_in_flight(monkeypatch, tmp_path):
    """UNKNOWN #4 — a profile in `_starting`.

    `running_profile_names()` unions `_starting` in by design (a spawn in
    flight counts as running so the UI shows it busy and a second launch is
    refused). Such a profile has NOT been registered yet, so the launcher holds
    no session record for it and `running_session_builds()` maps it to None.
    That is UNKNOWN BY CONSTRUCTION — there is no value to read, correct or
    otherwise — and it is a first-class defer case, not an edge to optimise
    away.

    THIS IS THE CASE THE PERSISTED STAMP GOT WRONG, and the reason this test is
    driven through the REAL launcher rather than a hand-made map. An earlier
    implementation resolved the name through `Profile.last_launch_build`, whose
    docstring claimed an in-flight launch "carries either no stamp or the
    PREVIOUS launch's" and therefore lands in a defer case. The first half was
    true and the second half was NOT: a profile that has launched before —
    i.e. the common case — carries the previous launch's build, which is a
    positive, resolvable value, so the join returned a confident answer about a
    build the in-flight launch may not be loading. It is asserted below that
    the previous launch's stamp does NOT rescue the reading, because nothing
    reads it.

    A SECOND running session is resolvable, deliberately. With only the
    unresolved one, an implementation that silently SKIPPED it would return an
    EMPTY set and be caught by the separate empty-set backstop — so the test
    would stay green over an inverted UNKNOWN rule and prove nothing about it.
    With a resolved session beside it, skipping yields a NON-empty set, the
    backstop does not fire, and the only thing between the prune and a live
    build is the UNKNOWN rule itself. It is also the realistic shape: one
    session accounted for, one that is not.
    """
    from src.services.browser.launcher import BrowserLauncher

    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-13", True, True),
            ("firefox-14", True, True),
            ("firefox-15", True, True),
            ("firefox-16", True, True),
        ],
        binary_version="firefox-15",
    )

    class _Proc:
        def poll(self):
            return None

    bl = BrowserLauncher()
    bl._starting.add("spawning-one")
    assert "spawning-one" in bl.running_profile_names(), (
        "precondition: an in-flight spawn counts as running"
    )
    # A SECOND profile, fully registered and resolvable — see the docstring.
    bl._active_sessions["resolved-one"] = _Proc()
    bl._session_build["resolved-one"] = ("firefox", "firefox-15")

    # THE HALF THE OLD IMPLEMENTATION GOT WRONG, pinned: the in-flight profile
    # HAS launched before, so its persisted stamp names the PREVIOUS launch's
    # build. If anything resolved the name through that record it would get a
    # confident "firefox-13" and spare the wrong build while pruning the rest.
    stale_store = {
        "spawning-one": _stamped("firefox", "firefox-13"),
        "resolved-one": _stamped("firefox", "firefox-15"),
    }
    assert stale_store["spawning-one"].last_launch_build == "firefox-13", (
        "precondition: the in-flight profile carries a PREVIOUS launch's stamp"
    )
    assert bl.running_session_builds()["spawning-one"] is None, (
        "an in-flight launch must have NO session record — this is what makes "
        "it UNKNOWN by construction rather than by a stamp that happens to be "
        "absent, and it is the claim the previous implementation got wrong"
    )

    from src.services.browser.launch_provenance import firefox_builds_in_use

    monkeypatch.setattr(eng, "_in_use_provider", lambda: True)
    monkeypatch.setattr(
        eng,
        "_in_use_builds_provider",
        lambda: firefox_builds_in_use(bl.running_session_builds),
    )
    logs = []
    inv._prune_old_engine_builds(keep="firefox-16", log=logs.append)

    assert (tmp_path / "firefox-13").exists(), (
        "a launch still in flight has no record of the build it is loading — "
        "the prune must defer, and must not fall back to the stamp from its "
        "PREVIOUS launch"
    )
    assert (tmp_path / "firefox-14").exists(), (
        "deferral is wholesale: nothing may be reclaimed while a running "
        "session cannot be accounted for"
    )
    assert (tmp_path / "firefox-15").exists()
    assert any("running" in m for m in logs), logs
    bl._active_sessions.clear()


def test_prune_defers_when_a_running_name_is_unaccounted_for(
    monkeypatch, tmp_path
):
    """UNKNOWN #5 — A SURVIVOR: a live browser this process never launched.

    A survivor is a browser a PREVIOUS persona left running. It is a real,
    probed-alive process executing out of a real build directory: ``is_running``
    reports it running and the UI paints its card as running. But
    ``scan_survivors`` populates ``_survivors`` ONLY with names that are in
    neither ``_active_sessions`` nor ``_starting``, so a survivor can never be a
    key in ``_running_names_locked()``.

    THAT IS WHY THIS TEST DRIVES THE REAL PATH RATHER THAN HAND-WRITING THE MAP.
    An earlier version of it supplied ``{"ghost-one": None, ...}`` directly and
    asserted the prune deferred. It did defer — but a real survivor cannot
    produce that map. It produced ``{}``: not an UNKNOWN, but NOTHING, so the
    join returned a confident set over the sessions it could see and the
    survivor's live build was deleted. The test was green over a fixture state
    the production wiring could not reach, while the state it CAN reach was
    unprotected. So this test now goes through ``SessionRegistry.record`` and
    ``scan_survivors`` with a REAL live process, and asserts on directories on
    disk.

    A SECOND, RESOLVABLE session runs beside it, deliberately, and on a
    DIFFERENT build — the ordinary shape after an engine bump, where the
    survivor predates the update and our own session is on the new build. With
    only the survivor, an implementation that silently SKIPPED it would return
    an EMPTY set and be caught by the separate empty-set backstop, so the test
    would stay green over an inverted UNKNOWN rule and prove nothing about it.
    With a resolved session beside it, skipping yields a NON-empty set, the
    backstop does not fire, and the only thing between the prune and a live
    build is the UNKNOWN rule itself.
    """
    psutil = pytest.importorskip("psutil")
    from src.services.browser.launcher import BrowserLauncher
    from src.services.browser.launch_provenance import firefox_builds_in_use
    from src.services.browser.session_registry import (
        SessionRecord,
        SessionRegistry,
    )

    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-13", True, True),   # THE SURVIVOR'S BUILD → must survive
            ("firefox-15", True, True),   # our own session's build
            ("firefox-16", True, True),   # keep
        ],
        binary_version="firefox-16",
    )

    # A REAL live process, probed by the REAL registry. A fabricated pid would
    # probe GONE and be dropped, and the survivor would never be adopted — the
    # test would then pass for the wrong reason.
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"]
    )
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                create_time = psutil.Process(proc.pid).create_time()
                break
            except Exception:
                time.sleep(0.05)
        else:  # pragma: no cover - the probe target refused to appear
            pytest.skip("could not probe the helper process")

        registry = SessionRegistry(tmp_path / "sessions.json")
        registry.record(
            SessionRecord(
                profile="ghost-one",
                pid=proc.pid,
                create_time=create_time,
                pgid=None,
                engine="firefox",
                started_at=time.time(),
                owner_pid=1,
            )
        )

        bl = BrowserLauncher(registry=registry)
        alive, _unknown = bl.scan_survivors()
        assert [r.profile for r in alive] == ["ghost-one"], (
            "precondition: the survivor must actually be adopted by the real "
            "scan — a test over an un-adopted survivor asserts nothing"
        )
        assert bl.is_running("ghost-one"), (
            "precondition: the launcher reports the survivor as RUNNING, which "
            "is exactly why a guard that cannot see it is dangerous"
        )

        # Our own session, on the NEWER build: the ordinary post-bump shape.
        bl._active_sessions["resolved-one"] = _PollingProc()
        bl._session_build["resolved-one"] = ("firefox", "firefox-15")

        assert "ghost-one" not in bl.running_profile_names(), (
            "precondition: the survivor is structurally absent from the "
            "running NAMES — the widening belongs to running_session_builds()"
        )

        monkeypatch.setattr(eng, "_in_use_provider", lambda: True)
        monkeypatch.setattr(
            eng,
            "_in_use_builds_provider",
            lambda: firefox_builds_in_use(bl.running_session_builds),
        )
        logs = []
        inv._prune_old_engine_builds(keep="firefox-16", log=logs.append)

        assert (tmp_path / "firefox-13").exists(), (
            "THE SURVIVOR'S LIVE BUILD: a running browser the session map "
            "cannot resolve is UNKNOWN, and UNKNOWN defers. A survivor that is "
            "merely ABSENT from the map is not an unknown — it is a licence to "
            "delete the build it is executing from"
        )
        assert (tmp_path / "firefox-15").exists(), (
            "deferral is wholesale: nothing may be reclaimed while a running "
            "session cannot be accounted for"
        )
        assert any("running" in m for m in logs), logs
        bl._active_sessions.clear()
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_running_session_builds_reports_a_survivor_as_unknown(
    monkeypatch, tmp_path
):
    """The launcher-level half of UNKNOWN #5, pinned at the seam itself.

    The prune test above proves the OUTCOME (a live build survives). This pins
    the MECHANISM, because the two can come apart: a future change that made
    the prune defer for some other reason would keep that test green while the
    survivor went back to being invisible. What is asserted here is the exact
    property the join's precondition rests on — that the map's key set is
    ``running_profile_names() | survivors``, and that a survivor's value is
    None rather than an invented build.
    """
    from src.services.browser.launcher import BrowserLauncher
    from src.services.browser.launch_provenance import firefox_builds_in_use
    from src.services.browser.session_registry import SessionRecord

    bl = BrowserLauncher()
    bl._survivors = {
        "ghost-one": SessionRecord(
            profile="ghost-one",
            pid=4242,
            create_time=1.0,
            pgid=None,
            engine="firefox",
            started_at=1.0,
            owner_pid=1,
        )
    }
    bl._active_sessions["resolved-one"] = _PollingProc()
    bl._session_build["resolved-one"] = ("firefox", "firefox-15")

    names = bl.running_profile_names()
    builds = bl.running_session_builds()

    assert "ghost-one" not in names, (
        "running_profile_names() is deliberately NOT widened — its callers "
        "(the running snapshot, the launch-refusal path) handle survivors "
        "themselves and would double-count"
    )
    assert builds["ghost-one"] is None, (
        "a survivor is UNKNOWN, and None is the honest value: SessionRecord "
        "carries the engine and no build, so there is nothing to read. "
        "Resolving one from active_build() would invent a confident wrong "
        "answer about a process that predates our startup"
    )
    assert builds["resolved-one"] == ("firefox", "firefox-15"), (
        "the widening must not disturb a session this run owns"
    )
    assert firefox_builds_in_use(lambda: builds) is None, (
        "and the join must collapse to UNKNOWN on it, not return the resolved "
        "session's build as an affirmative 'every other build is free'"
    )
    bl._active_sessions.clear()


def test_a_survivor_never_overwrites_a_session_this_run_owns(monkeypatch):
    """The widening is a `setdefault`, and that direction is load-bearing.

    ``scan_survivors`` already excludes names it finds in ``_active_sessions``,
    so the two sets should not overlap — but the scan is a point in time and
    the exclusion is enforced there, not here. If a name ever appeared in both,
    a survivor entry overwriting the session's resolved pair would turn a
    perfectly known build into an UNKNOWN and defer the prune forever. The
    resolved answer is strictly better information, so it wins.
    """
    from src.services.browser.launcher import BrowserLauncher
    from src.services.browser.session_registry import SessionRecord

    bl = BrowserLauncher()
    bl._active_sessions["both"] = _PollingProc()
    bl._session_build["both"] = ("firefox", "firefox-15")
    bl._survivors = {
        "both": SessionRecord(
            profile="both",
            pid=4242,
            create_time=1.0,
            pgid=None,
            engine="firefox",
            started_at=1.0,
            owner_pid=1,
        )
    }

    assert bl.running_session_builds()["both"] == ("firefox", "firefox-15"), (
        "a session THIS run owns is the better answer; a survivor entry must "
        "never downgrade it to UNKNOWN"
    )
    bl._active_sessions.clear()


def test_prune_defers_when_the_session_source_raises(monkeypatch, tmp_path):
    """AC5's second half, and the deliberate OPPOSITE of the in-use provider's
    fail-OPEN default two tests below.

    A broken in-use ORACLE must not wedge disk reclamation forever, so that one
    fails open. An unreadable SESSION SOURCE is not evidence that nothing is
    running, so this one fails CLOSED — and it costs nothing to do so, because
    deferring is exactly the pre-PS-221 behaviour. The two directions are not
    an inconsistency; they are the same rule (never delete a live build)
    applied to oracles whose failures mean different things.
    """
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-13", True, True),
            ("firefox-15", True, True),
            ("firefox-16", True, True),
        ],
        binary_version="firefox-15",
    )
    from src.services.browser.launch_provenance import firefox_builds_in_use

    def unreadable_launcher():
        raise OSError("the launcher could not be read")

    monkeypatch.setattr(eng, "_in_use_provider", lambda: True)
    monkeypatch.setattr(
        eng,
        "_in_use_builds_provider",
        lambda: firefox_builds_in_use(unreadable_launcher),
    )
    logs = []
    inv._prune_old_engine_builds(keep="firefox-16", log=logs.append)

    assert (tmp_path / "firefox-13").exists(), (
        "an unreadable session source must fail CLOSED — it is not evidence "
        "that nothing is running"
    )
    assert any("running" in m for m in logs), logs


def test_prune_defers_when_the_narrowing_provider_raises(monkeypatch, tmp_path):
    """The narrowing oracle itself raising is the same reading as the launcher
    being unreadable: cannot say which builds are live, so defer. Asserted
    separately because it is a different failure SITE — the guard lives in
    `_in_use_build_numbers`, not inside the join."""
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-13", True, True),
            ("firefox-15", True, True),
            ("firefox-16", True, True),
        ],
        binary_version="firefox-15",
    )

    def boom():
        raise RuntimeError("narrowing oracle unavailable")

    monkeypatch.setattr(eng, "_in_use_provider", lambda: True)
    monkeypatch.setattr(eng, "_in_use_builds_provider", boom)
    logs = []
    inv._prune_old_engine_builds(keep="firefox-16", log=logs.append)

    assert (tmp_path / "firefox-13").exists()
    # Failing closed still costs a prune cycle, so it must be diagnosable.
    assert any("in-use build check failed" in m for m in logs), logs
    assert any("narrowing oracle unavailable" in m for m in logs), logs


def test_prune_defers_wholesale_when_only_the_boolean_guard_is_wired(
    monkeypatch, tmp_path
):
    """An app that wires the GATE but not the NARROWING must behave exactly as
    it did before PS-221 — defer wholesale — rather than prune a live build.

    This is the half-wired case, and it is why the narrowing is a second
    provider that fails CLOSED rather than a richer return type on the first.
    """
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-13", True, True),
            ("firefox-15", True, True),
            ("firefox-16", True, True),
        ],
        binary_version="firefox-15",
    )
    monkeypatch.setattr(eng, "_in_use_provider", lambda: True)
    monkeypatch.setattr(eng, "_in_use_builds_provider", None)
    logs = []
    inv._prune_old_engine_builds(keep="firefox-16", log=logs.append)

    assert (tmp_path / "firefox-13").exists(), (
        "an unwired narrowing oracle must defer, not authorise a prune"
    )
    assert any("running" in m for m in logs), logs


def test_prune_defers_when_the_two_oracles_disagree(monkeypatch, tmp_path):
    """The gate says something IS running and the narrowing says NO build is in
    use. That pair is a contradiction, and an empty set is the single most
    dangerous shape the narrowing can return — it is an affirmative "every
    prunable build is free", i.e. a licence to delete all of them.

    NOT HYPOTHETICAL, and it is how this was found. `_in_use_builds_provider`
    is a module global, so any test (or any lane) that constructs an App leaks
    a REAL provider wired to a launcher with no browsers running. It answers an
    empty set perfectly truthfully; combined with a gate that says True it
    authorised pruning the live build. Caught only by the full suite —
    `test_prune_defers_while_a_profile_is_running` went red in a run where an
    earlier file had built an App, and passed in isolation.

    The rule is the UNKNOWN rule applied to a disagreement rather than to a
    missing stamp: when the two oracles are demonstrably looking at different
    state, we do not know, so we defer. Deferring costs one prune cycle.
    """
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-13", True, True),
            ("firefox-15", True, True),
            ("firefox-16", True, True),
        ],
        binary_version="firefox-15",
    )
    monkeypatch.setattr(eng, "_in_use_provider", lambda: True)
    monkeypatch.setattr(eng, "_in_use_builds_provider", lambda: set())
    logs = []
    inv._prune_old_engine_builds(keep="firefox-16", log=logs.append)

    assert (tmp_path / "firefox-13").exists(), (
        "a gate saying 'running' beside a narrowing saying 'no build in use' "
        "is a contradiction, not a licence to prune everything"
    )
    assert (tmp_path / "firefox-15").exists()
    assert any("no build could be attributed" in m for m in logs), logs


def test_prune_defers_when_every_in_use_tag_is_unparseable(monkeypatch, tmp_path):
    """The same claim reached by the other route: a provider that hands over
    only non-firefox-NN tags reduces to an empty set of build numbers, which is
    the identical "no build is in use" assertion. The emptiness must be checked
    on the RESULT, after parsing, not on the provider's raw answer."""
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-13", True, True),
            ("firefox-15", True, True),
            ("firefox-16", True, True),
        ],
        binary_version="firefox-15",
    )
    monkeypatch.setattr(eng, "_in_use_provider", lambda: True)
    monkeypatch.setattr(
        eng, "_in_use_builds_provider", lambda: {"151.0.8000.10", "not-a-build"}
    )
    logs = []
    inv._prune_old_engine_builds(keep="firefox-16", log=logs.append)

    assert (tmp_path / "firefox-13").exists(), (
        "tags that all fail to parse are no evidence at all — dropping them "
        "and pruning is the empty-set bug wearing a different hat"
    )
    assert any("no build could be attributed" in m for m in logs), logs


def test_prune_startup_housekeeping_narrows_to_the_live_build(
    monkeypatch, tmp_path
):
    """AC6: the STARTUP path gets the same narrowing.

    It is where the ~600MB actually accumulates — `_prune_old_engine_builds`
    runs only right after a fresh download, so a build that went stale on an
    earlier run sits until housekeeping reclaims it, and housekeeping deferred
    wholesale on any running profile. It inherits the narrowing by delegating;
    this pins that it really does, so a refactor that gave it its own guard
    fails here rather than silently re-deferring forever.
    """
    # Same fixture shape as the post-install test, and for the same reason:
    # the live build is firefox-13, which retention does NOT spare, so only the
    # narrowing can save it. firefox-14 is the reclaim that proves the prune ran.
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-13", True, True),   # THE LIVE BUILD → survives
            ("firefox-14", True, True),   # stale, nothing on it → reclaimed
            ("firefox-15", True, True),   # retained rollback target (PS-51)
            ("firefox-16", True, True),   # newest → keep
        ],
        binary_version="firefox-16",
    )
    _wire_narrowing(
        monkeypatch,
        {"live-one": _session("firefox", "firefox-13")},
    )
    logs = []
    inv.prune_superseded_builds(log=logs.append)

    assert not (tmp_path / "firefox-14").exists(), (
        "startup housekeeping must reclaim a build no running profile is on"
    )
    assert (tmp_path / "firefox-13").exists(), (
        "and must still spare the build the running profile is executing from"
    )
    assert (tmp_path / "firefox-15").exists(), "retained rollback target (PS-51)"
    assert (tmp_path / "firefox-16").exists()


def test_a_suffixed_stamp_still_spares_its_build(monkeypatch, tmp_path):
    """The engine package names its cache dir with an upstream+timestamp suffix
    (firefox-18_151.0_20260724001829) while the short tag stays firefox-18, and
    `active_build()` — which is what gets stamped — can return either shape.

    The join compares BUILD NUMBERS rather than strings precisely so the two
    shapes agree. A string comparison would fail to match the suffixed stamp
    against the short dir name and delete the live build, which is the failure
    this test exists to make loud.
    """
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-13", True, True),   # the live build, stamped SUFFIXED
            ("firefox-14", True, True),   # nothing on it → reclaimed
            ("firefox-15", True, True),   # retained rollback target (PS-51)
            ("firefox-16", True, True),   # keep
        ],
        binary_version="firefox-16",
    )
    _wire_narrowing(
        monkeypatch,
        {"live-one": _session("firefox", "firefox-13_151.0_20260724001829")},
    )
    inv._prune_old_engine_builds(keep="firefox-16", log=lambda m: None)

    assert (tmp_path / "firefox-13").exists(), (
        "a suffixed stamp names the same build as the short tag — comparing "
        "the strings instead of the build numbers would delete a live build"
    )
    assert not (tmp_path / "firefox-14").exists()


def test_two_running_profiles_on_different_builds_both_survive(
    monkeypatch, tmp_path
):
    """The answer is a SET, not a single build: profiles can legitimately be on
    different builds (one launched before an update, one after), and sparing
    only the first would delete the other's tree."""
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-12", True, True),   # nothing on it → reclaimed
            ("firefox-13", True, True),   # live
            ("firefox-14", True, True),   # live
            ("firefox-15", True, True),   # retained rollback target (PS-51)
            ("firefox-16", True, True),   # keep
        ],
        binary_version="firefox-15",
    )
    _wire_narrowing(
        monkeypatch,
        {
            "a": _session("firefox", "firefox-13"),
            "b": _session("firefox", "firefox-14"),
        },
    )
    inv._prune_old_engine_builds(keep="firefox-16", log=lambda m: None)

    assert (tmp_path / "firefox-13").exists()
    assert (tmp_path / "firefox-14").exists()
    assert not (tmp_path / "firefox-12").exists(), (
        "the prune still runs — it spares the live builds, not everything"
    )


def test_a_stopped_profiles_stale_stamp_does_not_spare_its_build(
    monkeypatch, tmp_path
):
    """The stamp is LAST-launch and nothing clears it on exit, so for a STOPPED
    profile it is stale. The live fact is the stamp INTERSECTED with the set of
    running names — a fix resting on the stamp alone would spare every build
    any profile had ever launched under and reclaim nothing over time.
    """
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-13", True, True),   # a STOPPED profile's stale stamp
            ("firefox-14", True, True),   # the RUNNING profile's build
            ("firefox-15", True, True),   # retained rollback target (PS-51)
            ("firefox-16", True, True),   # keep
        ],
        binary_version="firefox-16",
    )
    # "stopped-one" is NOT in the map at all: its session ended, so
    # _forget_session_facts dropped its build with it.
    _wire_narrowing(
        monkeypatch,
        {"live-one": _session("firefox", "firefox-14")},
    )
    inv._prune_old_engine_builds(keep="firefox-16", log=lambda m: None)

    assert not (tmp_path / "firefox-13").exists(), (
        "a stopped session's build is history, not a live fact — it dies with "
        "the session, so nothing spares the build it was on"
    )
    assert (tmp_path / "firefox-14").exists(), (
        "the RUNNING session's build is spared — so the reclaim above is the "
        "narrowing working, not it having been skipped wholesale"
    )


def test_a_running_profiles_stale_stamp_does_not_delete_the_build_it_is_on(
    monkeypatch, tmp_path
):
    """UNKNOWN #6 — THE REGRESSION THIS GUARD'S FIRST IMPLEMENTATION SHIPPED.

    The other five cases are all about a running profile that cannot be
    RESOLVED. This one is the opposite and is far more dangerous: the profile
    resolves perfectly, confidently, and to the WRONG BUILD.

    `Profile.last_launch_build` is a PERSISTED, LAST-launch stamp, and for a
    RUNNING profile it can name a different build than the one being executed.
    Two ordinary paths produce that, neither of them exotic:

    * the record hook fires AFTER the session is registered (`launcher.py`), so
      between those two points the profile is reported running while the record
      still names the PREVIOUS launch. EVERY launch passes through that window;
    * the hook's failure is swallowed so the browser still opens — which leaves
      the previous launch's build STANDING rather than clearing it.

    An implementation that read the stamp as authoritative therefore SPARED the
    stale build and DELETED the live one. That is strictly worse than the disk
    it reclaims, and it is the shape the ticket's constraint #4 was believed to
    rule out: intersecting with `running_profile_names()` does NOT repair it,
    because the value being intersected is stale for a RUNNING profile and not
    only for a stopped one.

    Driven through the REAL launcher, in the exact state that window produces:
    registered in `_active_sessions`, executing firefox-14, while the profile
    store still says firefox-11. Asserted on directories on disk.
    """
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-11", True, True),   # what the STALE STAMP names
            ("firefox-13", True, True),   # nothing is on it → reclaimable
            ("firefox-14", True, True),   # THE LIVE BUILD → must survive
            ("firefox-15", True, True),   # retained rollback target (PS-51)
            ("firefox-16", True, True),   # keep
        ],
        binary_version="firefox-16",
    )

    class _Proc:
        def poll(self):
            return None

    from src.services.browser.launcher import BrowserLauncher

    bl = BrowserLauncher()
    bl._active_sessions["live-one"] = _Proc()
    bl._session_build["live-one"] = ("firefox", "firefox-14")

    # The persisted record DISAGREES — it still names the previous launch.
    stale_store = {"live-one": _stamped("firefox", "firefox-11")}
    assert stale_store["live-one"].last_launch_build == "firefox-11", (
        "precondition: the running profile's persisted stamp names a DIFFERENT "
        "build than the session is executing from"
    )

    from src.services.browser.launch_provenance import firefox_builds_in_use

    monkeypatch.setattr(eng, "_in_use_provider", lambda: True)
    monkeypatch.setattr(
        eng,
        "_in_use_builds_provider",
        lambda: firefox_builds_in_use(bl.running_session_builds),
    )
    inv._prune_old_engine_builds(keep="firefox-16", log=lambda m: None)

    assert (tmp_path / "firefox-14").exists(), (
        "THE REGRESSION: the build the session is ACTUALLY executing from must "
        "survive. Reading the persisted stamp as authoritative deletes it — a "
        "live deletion on the ordinary launch path, reached without any "
        "failure at all"
    )
    assert not (tmp_path / "firefox-11").exists(), (
        "and the STALE build must NOT be spared — sparing it is the same bug "
        "seen from the other side, and a test that only checked the live build "
        "survived would pass over an implementation that deferred wholesale"
    )
    assert not (tmp_path / "firefox-13").exists(), (
        "the prune still runs: this is a narrowing that resolved, not a defer"
    )
    assert (tmp_path / "firefox-15").exists(), "retained rollback target (PS-51)"
    bl._active_sessions.clear()


def test_app_construction_wires_the_engine_prune_in_use_builds_guard(monkeypatch):
    """PS-221, same argument as the PS-14 wiring test above: the narrowing is
    only worth anything if production wires it, and nothing in the prune path
    can tell a wired app from an unwired one — an unwired app defers exactly as
    it did before, silently reclaiming nothing.

    And it must be the REAL join over the REAL launcher, not a stub — the
    wiring is exactly where the wrong SOURCE would be reintroduced. It is
    asserted below that the provider follows the launcher's live session map
    and NOT the profile store's persisted stamp, because those two disagree
    precisely in the window that deletes a live build.
    """
    import src.services.browser.engine_install as eng_mod
    import src.ui.app as app_mod
    from src.core.container import Container

    monkeypatch.setattr(eng_mod, "_in_use_builds_provider", None)

    app = app_mod.App(Container())

    assert eng_mod._in_use_builds_provider is not None, (
        "App construction must wire the engine-prune in-use BUILDS guard"
    )

    class _Proc:
        def poll(self):
            return None

    app.bl._active_sessions.clear()
    app.bl._session_build.clear()
    app.bl._starting.clear()
    app.pm.profiles.clear()
    assert eng_mod._in_use_builds_provider() == set(), "nothing running, no builds"

    # A registered session resolves to the build IT was launched with...
    app.bl._active_sessions["live-one"] = _Proc()
    app.bl._session_build["live-one"] = ("firefox", "firefox-15")
    assert eng_mod._in_use_builds_provider() == {"firefox-15"}, (
        "the wired provider must read the launcher's live session builds"
    )

    # ...AND IT MUST NOT BE THE PERSISTED STAMP. Give the same profile a
    # CONTRADICTORY last_launch_build: if the wiring were reading the store,
    # the answer would move to firefox-11 and the prune would spare a build
    # nothing is on while deleting the one this session is executing from.
    app.pm.profiles["live-one"] = SimpleNamespace(
        last_launch_engine="firefox", last_launch_build="firefox-11"
    )
    assert eng_mod._in_use_builds_provider() == {"firefox-15"}, (
        "the provider must follow the LIVE session build, not the persisted "
        "last-launch stamp — the two disagree exactly in the window that "
        "would delete a live build"
    )

    # ...and an in-flight launch collapses the whole answer to UNKNOWN.
    app.bl._starting.add("ghost")
    assert eng_mod._in_use_builds_provider() is None, (
        "one unresolved running session must make the whole answer UNKNOWN"
    )
    app.bl._active_sessions.clear()
    app.bl._session_build.clear()
    app.bl._starting.clear()
