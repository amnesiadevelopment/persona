"""PS-51: the replaced engine build is RETAINED, so a bad update can be undone
from the machine.

Before this, a successful update deleted the build it replaced
(`_prune_old_engine_builds` pruned EVERY build below the newest one), so the
failure mode "the copy worked, the result is bad" had no answer: going back
needed a download of a build upstream may not even still publish.

These tests pin the promises that make the undo REAL rather than nominal:

  * exactly one previous build survives a prune (the retention policy), and the
    footprint stays bounded — the slot is REUSED, never accumulated;
  * the build an operator reverted to is prune-immune, and it is what launches;
  * the automatic update does not walk them back onto the build they rejected;
  * a profile opened under the NEWER build still opens under the older one —
    Firefox's own downgrade protection is cleared, and the prefs.js guard that
    reads the same file still fires (the ordering trap).
"""

import sys
import types

import pytest

from src.services.browser import engine_install as ei


MARKER = ei._INSTALL_MARKER


def _fake_engine_pkg(monkeypatch, root, pinned="firefox-20", broken=()):
    """Install a stub `invisible_playwright` whose cache_root is `root`.

    The real package is not a test dependency (it ships the engine itself), and
    every function under test reaches it through these three names only."""
    entry = "firefox"
    pkg = types.ModuleType("invisible_playwright")
    constants = types.ModuleType("invisible_playwright.constants")
    constants.BINARY_ENTRY_REL = {sys.platform: entry}
    constants.BINARY_VERSION = pinned
    constants.BROKEN_VERSIONS = tuple(broken)
    download = types.ModuleType("invisible_playwright.download")
    download.cache_root = lambda: root
    download.cache_dir_for_version = lambda v: root / v
    pkg.constants = constants
    pkg.download = download
    for name, mod in (
        ("invisible_playwright", pkg),
        ("invisible_playwright.constants", constants),
        ("invisible_playwright.download", download),
    ):
        monkeypatch.setitem(sys.modules, name, mod)
    return entry


def _make_build(root, tag, entry="firefox", marker=True):
    """A COMPLETE build on disk: the entry executable plus our completion
    marker (which is what `installed_builds` requires of any non-pinned dir)."""
    d = root / tag
    d.mkdir(parents=True, exist_ok=True)
    (d / entry).write_text("#!/bin/sh\n")
    if marker:
        (d / MARKER).touch()
    return d


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """An engine cache with no pin set and no profile running."""
    root = tmp_path / "cache"
    root.mkdir()
    entry = _fake_engine_pkg(monkeypatch, root)
    monkeypatch.setenv("PERSONA_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(ei, "_in_use_provider", None)
    return types.SimpleNamespace(root=root, entry=entry)


# --- retention: the previous build survives ---------------------------------


def test_prune_retains_the_previous_build(cache):
    # THE CORE PROMISE. firefox-20 has just been installed over firefox-19.
    # Before PS-51 the prune deleted firefox-19 outright and the operator had
    # nothing to go back to.
    for tag in ("firefox-18", "firefox-19", "firefox-20"):
        _make_build(cache.root, tag, cache.entry)

    ei._prune_old_engine_builds(keep="firefox-20")

    assert (cache.root / "firefox-20").is_dir(), "the new build must stay"
    assert (cache.root / "firefox-19").is_dir(), (
        "the build the update REPLACED must be retained — it is the only copy "
        "of the version that worked, and deleting it is what made a bad update "
        "impossible to undo"
    )
    assert not (cache.root / "firefox-18").exists(), (
        "retention is depth ONE: anything below the retained build is still "
        "reclaimed, so the footprint stays bounded"
    )


def test_retention_slot_is_reused_not_accumulated(cache):
    # The disk answer: each update's retained build REPLACES the last one, so
    # repeated updates never grow past 2 builds. A policy that kept every
    # previous build would pass the test above and still fill the disk.
    for tag in ("firefox-18", "firefox-19"):
        _make_build(cache.root, tag, cache.entry)
    ei._prune_old_engine_builds(keep="firefox-19")

    _make_build(cache.root, "firefox-20", cache.entry)
    ei._prune_old_engine_builds(keep="firefox-20")

    live = sorted(d.name for d in cache.root.iterdir() if d.is_dir())
    assert live == ["firefox-19", "firefox-20"], (
        f"at most the active build + ONE retained previous, got {live}"
    )


def test_prune_leaves_a_half_finished_download_alone(cache):
    # Unchanged pre-existing behaviour, re-pinned because the retention branch
    # sits right next to it: a markerless dir at a non-pinned version is an
    # interrupted extract, not a build, and must survive for a later resume.
    _make_build(cache.root, "firefox-19", cache.entry, marker=False)
    _make_build(cache.root, "firefox-20", cache.entry)

    ei._prune_old_engine_builds(keep="firefox-20")

    assert (cache.root / "firefox-19").is_dir()


def test_retention_slot_is_not_spent_on_an_incomplete_build(cache):
    # A half-extracted dir cannot be launched, so it must not consume the one
    # retention slot: the highest COMPLETE build below `keep` is what is kept.
    _make_build(cache.root, "firefox-17", cache.entry)
    _make_build(cache.root, "firefox-18", cache.entry, marker=False)
    _make_build(cache.root, "firefox-20", cache.entry)

    ei._prune_old_engine_builds(keep="firefox-20")

    assert (cache.root / "firefox-17").is_dir(), (
        "the retained build must be one that can actually be launched"
    )


# --- rollback_target: is the gesture offerable at all? ----------------------


def test_rollback_target_is_the_retained_build(cache):
    for tag in ("firefox-19", "firefox-20"):
        _make_build(cache.root, tag, cache.entry)
    assert ei.rollback_target() == "firefox-19"


def test_rollback_target_empty_with_only_one_build(cache):
    _make_build(cache.root, "firefox-20", cache.entry)
    assert ei.rollback_target() == "", (
        "with nothing retained the gesture must not be offered — a revert that "
        "cannot work is worse than no button"
    )


# --- the pin: which build actually launches --------------------------------


def test_active_build_honours_the_pin(cache):
    from src.core import settings

    for tag in ("firefox-19", "firefox-20"):
        _make_build(cache.root, tag, cache.entry)
    assert ei.active_build() == "firefox-20"

    settings.set_engine_build_pin("firefox-19")
    assert ei.active_build() == "firefox-19", (
        "the revert IS this inversion — going back is expressed by changing "
        "which installed build launches, not by moving bytes"
    )


def test_pin_naming_a_missing_build_is_ignored(cache):
    from src.core import settings

    _make_build(cache.root, "firefox-20", cache.entry)
    settings.set_engine_build_pin("firefox-19")  # never installed / hand-deleted
    assert ei.active_build() == "firefox-20", (
        "honouring a pin with no tree on disk would resolve every launch to a "
        "path that does not exist"
    )


def test_pinned_build_is_prune_immune(cache):
    from src.core import settings

    for tag in ("firefox-19", "firefox-20"):
        _make_build(cache.root, tag, cache.entry)
    settings.set_engine_build_pin("firefox-19")

    # A third build arrives. Retention alone would now keep firefox-20 (the
    # highest below keep) and drop firefox-19 — the very build the operator is
    # deliberately running.
    _make_build(cache.root, "firefox-21", cache.entry)
    ei._prune_old_engine_builds(keep="firefox-21")

    assert (cache.root / "firefox-19").is_dir(), (
        "pruning the pinned build would delete the tree launches resolve to"
    )
    assert ei.active_build() == "firefox-19"


def test_prune_superseded_measures_from_the_newest_not_the_pin(cache):
    from src.core import settings

    for tag in ("firefox-19", "firefox-20"):
        _make_build(cache.root, tag, cache.entry)
    settings.set_engine_build_pin("firefox-19")

    ei.prune_superseded_builds()

    assert (cache.root / "firefox-20").is_dir(), (
        "keep must be the HIGHEST installed build; passing the pinned (lower) "
        "build would invert the prune into deleting everything ABOVE it — "
        "destroying the newest build the moment an operator went back one"
    )


# --- reverting ---------------------------------------------------------------


def test_revert_pins_the_retained_build(cache):
    for tag in ("firefox-19", "firefox-20"):
        _make_build(cache.root, tag, cache.entry)

    assert ei.revert_to_previous_build() == "firefox-19"
    assert ei.active_build() == "firefox-19"
    assert (cache.root / "firefox-20").is_dir(), "the revert moves no bytes"


def test_revert_refused_while_a_profile_is_running(cache, monkeypatch):
    for tag in ("firefox-19", "firefox-20"):
        _make_build(cache.root, tag, cache.entry)
    monkeypatch.setattr(ei, "_in_use_provider", lambda: True)

    logs = []
    assert ei.revert_to_previous_build(log=logs.append) == ""
    assert ei.active_build() == "firefox-20", "nothing may change on a refusal"
    assert any("close your running profiles" in m for m in logs), logs


def test_revert_refused_with_nothing_retained(cache):
    _make_build(cache.root, "firefox-20", cache.entry)
    logs = []
    assert ei.revert_to_previous_build(log=logs.append) == ""
    assert any("nothing to go back to" in m for m in logs), logs


def test_resume_clears_the_pin_and_goes_forward_again(cache):
    for tag in ("firefox-19", "firefox-20"):
        _make_build(cache.root, tag, cache.entry)
    ei.revert_to_previous_build()
    assert ei.active_build() == "firefox-19"

    ei.resume_engine_updates()

    assert ei.pinned_build() == ""
    assert ei.active_build() == "firefox-20", (
        "resuming is as instant as the revert: the build reverted FROM is still "
        "on disk, so going forward re-downloads nothing"
    )


# --- Q1: the PROFILES. What happens to a profile opened under the newer build?


def _profile_last_opened_by(tmp_path, build, prefs=True):
    """A profile Firefox has already run under `build` — compatibility.ini is
    Firefox's own record of that, and is what both migrations key off."""
    prof = tmp_path / "profile"
    prof.mkdir(exist_ok=True)
    (prof / "compatibility.ini").write_text(
        "[Compatibility]\n"
        "LastVersion=151.0_x/x\n"
        f"LastPlatformDir=/cache/{build}\n"
        f"LastAppDir=/cache/{build}/browser\n"
    )
    if prefs:
        (prof / "prefs.js").write_text('user_pref("x", 1);\n')
    (prof / "cookies.sqlite").write_bytes(b"USER-COOKIES")
    return prof


def test_downgrade_clears_firefox_own_refusal(tmp_path):
    # Firefox refuses to open a profile last used by a NEWER version and puts up
    # a modal nobody can click (the launcher drives it over juggler). Without
    # clearing this, "going back" produces a profile that never opens — moving
    # binaries alone would be a trap.
    from src.services.browser import invisible_launch as inv

    prof = _profile_last_opened_by(tmp_path, "firefox-20")

    lines = inv._migrate_profile_for_engine_build(str(prof), "/cache/firefox-19")

    assert not (prof / "compatibility.ini").exists(), (
        "the downgrade guard must be cleared or the older build refuses to "
        "open the profile at all"
    )
    assert any("ENGINE_BUILD_REVERTED" in ln for ln in lines), lines
    assert (prof / "cookies.sqlite").read_bytes() == b"USER-COOKIES", (
        "user data is never touched — only DERIVED files are removed"
    )


def test_downgrade_still_drops_the_incompatible_prefs(tmp_path):
    # THE ORDERING TRAP. Both migrations key off compatibility.ini and the
    # downgrade guard DELETES it. If it ran first, the prefs check would read
    # "Firefox never opened this profile" and leave the prefs.js that makes the
    # engine SIGSEGV — trading a refusal for a crash, which is strictly worse
    # than not reverting at all.
    from src.services.browser import invisible_launch as inv

    prof = _profile_last_opened_by(tmp_path, "firefox-20")

    lines = inv._migrate_profile_for_engine_build(str(prof), "/cache/firefox-19")

    # prefs.js is REWRITTEN, not left absent: the reset is immediately followed
    # by the warmup chrome prefs (#242, so the window doesn't open light). What
    # must be gone is the STALE content the other build wrote — asserting the
    # file's absence would assert the opposite of the intended behaviour.
    body = (prof / "prefs.js").read_text()
    assert 'user_pref("x", 1)' not in body, (
        "prefs.js is not compatible ACROSS builds in either direction; the "
        "downgrade path must still drop what the newer build wrote"
    )
    assert "toolbar-theme" in body, "the warmup chrome prefs are re-applied"
    assert any("ENGINE_BUILD_CHANGED" in ln for ln in lines), lines


def test_forward_update_does_not_clear_the_guard(tmp_path):
    # Firefox's downgrade protection does not fire going forward, so clearing it
    # there would be a behaviour change bought for nothing. The forward path is
    # the one that is live-proven today — leave it exactly as it was.
    from src.services.browser import invisible_launch as inv

    prof = _profile_last_opened_by(tmp_path, "firefox-19")

    lines = inv._migrate_profile_for_engine_build(str(prof), "/cache/firefox-20")

    assert (prof / "compatibility.ini").exists(), (
        "a forward update must not touch compatibility.ini"
    )
    # Same rewrite as the downgrade path: the stale value dies, the file itself
    # comes back carrying the warmup chrome prefs.
    body = (prof / "prefs.js").read_text()
    assert 'user_pref("x", 1)' not in body, "the forward prefs reset still runs"
    assert not any("ENGINE_BUILD_REVERTED" in ln for ln in lines), lines


def test_same_build_is_a_no_op(tmp_path):
    from src.services.browser import invisible_launch as inv

    prof = _profile_last_opened_by(tmp_path, "firefox-19")

    lines = inv._migrate_profile_for_engine_build(str(prof), "/cache/firefox-19")

    assert lines == []
    assert (prof / "prefs.js").exists(), "an unchanged build keeps its prefs"
    assert (prof / "compatibility.ini").exists()


def test_profile_firefox_never_opened_is_untouched(tmp_path):
    # No compatibility.ini means Firefox has never run this profile: there is no
    # prior version to refuse against and only persona's freshly-seeded prefs.
    from src.services.browser import invisible_launch as inv

    prof = tmp_path / "fresh"
    prof.mkdir()
    (prof / "prefs.js").write_text('user_pref("x", 1);\n')

    assert inv._migrate_profile_for_engine_build(str(prof), "/cache/firefox-19") == []
    assert (prof / "prefs.js").exists()


# --- the automatic update must not undo the operator's revert ---------------


def _engine2_app(monkeypatch, *, current, latest, pin):
    """The App stub _auto_update_engine2 runs against, wired the same way
    tests/test_app_ui.py does it, plus a pin."""
    from src.services.browser import invisible_launch as inv
    from src.services.engine import firefox as ff
    from tests.test_app_ui import make_app

    monkeypatch.setattr(inv, "is_invisible_installed", lambda: True)
    monkeypatch.setattr(inv, "pinned_build", lambda: pin)
    monkeypatch.setattr(ff, "fetch_latest", lambda: (latest, True))
    monkeypatch.setattr(ff, "current_version", lambda: current)
    # Pruning is a disk operation with no bearing on the decision under test.
    monkeypatch.setattr(inv, "prune_superseded_builds", lambda **k: None)

    app = make_app(None)
    app._engine2_busy = False
    app._engine2_latest = ""
    app._engine2_compatible = True
    app._engine2_status = ""
    logs = []
    app._log = logs.append
    app._refresh_engine_text = lambda *a, **k: None
    downloaded = []
    app._update_engine2_async = lambda: downloaded.append(app._engine2_latest)
    return app, logs, downloaded


def test_auto_update_is_held_off_while_pinned(monkeypatch):
    # THE DIFFERENCE BETWEEN A REAL UNDO AND A NOMINAL ONE. The operator went
    # back to firefox-19 because firefox-20 was bad for them. The unattended
    # updater runs at every startup — without this guard it reinstalls
    # firefox-20 and the revert lasts only until the next launch.
    app, logs, downloaded = _engine2_app(
        monkeypatch, current="firefox-19", latest="firefox-20", pin="firefox-19"
    )

    app._auto_update_engine2()

    assert downloaded == [], (
        "a pinned engine must not be auto-updated — that would walk the "
        "operator straight back onto the build they rejected"
    )
    assert any("pinned to firefox-19" in m for m in logs), logs


def test_auto_update_resumes_once_the_pin_is_cleared(monkeypatch):
    # The pin is the ONLY thing holding the update off: clearing it must restore
    # exactly today's behaviour, or "resume updates" would be a dead end.
    app, logs, downloaded = _engine2_app(
        monkeypatch, current="firefox-19", latest="firefox-20", pin=""
    )

    app._auto_update_engine2()

    assert downloaded == ["firefox-20"]
