"""What a CHROMIUM ENGINE BUILD CHANGE does to an ALREADY-EXISTING profile.

PS-341. Read this file's reason for existing before changing it.

THE QUESTION, and why it had to be MEASURED
-------------------------------------------
``services/browser/process.py``'s Chromium arm has ZERO build-awareness: it
computes ``profile_dir`` and hands it straight to the engine as
``--user-data-dir`` without ever asking which build wrote it. The Firefox arm
runs a FOUR-PART migration on every launch
(``invisible_launch._migrate_profile_for_engine_build``), and its own docstring
says why: *"a profile seeded on firefox-18 makes firefox-19 SIGSEGV on startup
(live-proven — the launch dies as TargetClosedError before the window paints)"*.
It drops ``prefs.js``, removes ``compatibility.ini`` so an OLDER build will open
the profile at all, and invalidates the addon startup cache on a revert.

⛔ THE PARITY QUESTION IS NOT "WHY IS CHROMIUM MISSING FIREFOX'S GUARD". It is
**"does Chromium EXHIBIT THE BEHAVIOUR that guard defends against?"** Only the
second is a defect. Nothing in the tree stated an answer either way, so the
Chromium arm had no recorded position at all — which is what this ticket fixed,
and this file is the live half of that record.

THE ANSWER: **no migration is owed on this engine.** A Chromium profile survives
a build change in both directions with its derived state intact, and Chromium's
own downgrade handling never fires. The position now sits beside the Chromium
arm in ``process.py`` (where the zero-hit grep that raised the question would
find it); these tests are what keep it HONEST as the tree moves.

⚠️ ONE THING DOES MOVE, AND IT IS NOT A MIGRATION PROBLEM. The WebGL
vendor/renderer pair a page reads CHANGES across a build change on the arms
where the ENGINE authors it (``gpu_ext.ENGINE_AUTHORED_IDENTITY_ARMS`` — windows
and macos, where persona's own GPU layer deliberately stands down). Measured
across 8 seeds: 8/8 moved, and the two builds' card pools DO NOT INTERSECT AT
ALL (148 answers Intel integrated parts, 152 answers NVIDIA RTX parts) — while
being STABLE within a build, which is what makes the move attributable to the
build change and to nothing else. No profile migration could repair it: the
value comes from a table compiled into the engine BINARY, so rewriting the
profile directory cannot change it. It is recorded, not fixed, because the fix
is a decision about WHO AUTHORS that pair on those arms — ``gpu_ext.py``'s
question, not this launch path's.

WHY THESE TESTS LAUNCH A REAL BROWSER
-------------------------------------
Because the claim is about WHAT HAPPENS TO A PROFILE, and no cheaper oracle can
reach it. A unit test over ``_launch_args``, or one asserting that a migration
function was (not) called, asserts the INTENTION — the half that was never in
doubt — and would pass identically whether Chromium ate the profile or left it
untouched. That is this project's own recurring failure mode (PS-11: *tests that
assert on what was written, not on what happens*), and the sibling live suites
``test_ps312_ff_geolocation_live`` and ``test_verify_chromium_timezone_live``
open with the same paragraph for the same reason.

The derived-state readings in particular are taken from a **RUNNING BROWSER**,
never from the JSON on disk: a ``Default/Preferences`` that survives on disk but
is IGNORED by the engine is the same outcome for the operator, and only a live
read tells the two apart.

THE POSITIVE CONTROL IS NOT OPTIONAL
------------------------------------
"The profile survived" is ambiguous between *Chromium tolerated the downgrade*
and *my probe never actually changed the build* — and the second reads exactly
like the first. So ``test_the_build_really_moved`` asserts the change on three
independent axes (the version RECORD, the binary's sha256, and the running
page's own ``navigator.userAgent`` major), and every other test in this file is
gated on that evidence file existing. Without it the null means nothing.

TWO FALSE POSITIVES WERE CAUGHT BY CONTROLS, AND BOTH ARE RECORDED HERE because
each looked like a headline finding:

  1. **"The downgrade lost the operator's cookie jar."** It did not. Chromium's
     cookie store is flushed by a task that OUTLIVES process exit — polling the
     SQLite file, the row was absent at +2s/+5s/+10s and present at +20s — so a
     probe that restarted too soon read an empty jar. The control reproduced the
     same "loss" with NO BUILD CHANGE AT ALL, on both builds.
  2. **"The older build refuses to open the profile."** It does not. Leaked
     browser process trees from earlier runs exhausted the container's 2048-PID
     cgroup budget, and the next launch died with ``pthread_create: Resource
     temporarily unavailable`` and exit 133 — which reads exactly like an engine
     refusing a profile.

Both are why the measurement harness reaps process GROUPS and settles before
reading, and why a difference is only reported when the same difference does
NOT appear within a single build.

SKIPPED, NEVER SILENTLY PASSED, wherever the engine, a display or the recorded
evidence is missing. An absent engine must not read as a clean bill of health —
that is the whole point of ``tests/KNOWN_SKIPS.md``.
"""

from __future__ import annotations

import json
import os
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

#: The measurement's own output, committed beside this file. These tests re-read
#: it rather than re-running a multi-minute two-build download on every suite
#: run — but they are NOT a substitute for it: the reading is reproducible with
#: ``scripts/ps341_run.py`` and ``scripts/ps341_gpu_seeds.py``, and the file
#: records the exact builds, digests and venue it came from.
READING = REPO / "readings" / "ps341-2026-09-07" / "reading.json"
GPU_SEEDS = REPO / "readings" / "ps341-2026-09-07" / "gpu_seeds.json"


def _load(path: pathlib.Path):
    if not path.exists():
        pytest.skip(f"PS-341 evidence not present at {path}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def reading():
    return _load(READING)


@pytest.fixture(scope="module")
def gpu_seeds():
    return _load(GPU_SEEDS)


# --------------------------------------------------------------------------
# THE POSITIVE CONTROL — everything below is meaningless without it
# --------------------------------------------------------------------------


def test_the_build_really_moved(reading):
    """Three independent axes, because one is not enough.

    A no-op probe can satisfy none of these: the version RECORD is what the
    updater writes, the sha256 is the BYTES on disk, and the UA major is what
    the RUNNING PROCESS told a page. Only a real build change moves all three.
    """
    pc = reading["positive_control"]
    assert pc["record_moved"], (
        "the version record did not move, so nothing below is a fact about a "
        f"build change: {pc['record_before']} -> {pc['record_after']}"
    )
    assert pc["sha_moved"], (
        "the engine binary's sha256 did not move — the same bytes were on disk "
        "for both legs, so no build change was actually performed"
    )
    assert pc["ua_moved"], (
        "the RUNNING browser reported the same userAgent major on both legs "
        f"({pc['ua_major_before']} vs {pc['ua_major_after']}). The record and "
        "the bytes can both move while the process a page talks to does not — "
        "this is the axis that proves the page saw the other build."
    )
    assert pc["ua_major_before"] != pc["ua_major_after"]


def test_the_build_was_moved_by_the_shipping_gesture(reading):
    """Not by hand-swapping files — the GESTURE is the thing under test.

    ``ui/app.py``'s rollback button calls ``revert_to_previous_build``, and a
    hand swap would measure a state the shipping product may never produce (it
    re-downloads against the RECORDED digest, rewrites the one-slot
    version.txt, and sets the update pin — any of which could matter).
    """
    rev = reading["revert"]
    assert rev["ok"], f"the revert gesture refused: {rev['message']}"
    assert rev["version_txt_after"] == reading["positive_control"]["record_after"]
    assert rev["pinned_build_after"], (
        "the revert must leave a pin, or the hourly unattended check re-installs "
        "the build just rejected within the hour"
    )


# --------------------------------------------------------------------------
# Q1 — does the profile OPEN at all?
# --------------------------------------------------------------------------


def test_the_profile_opens_on_the_older_build(reading):
    """The Firefox analogue is a SIGSEGV before the window paints. It does not
    happen here — and the failure mode, not merely a category, is recorded."""
    older = reading["leg_N_minus_1"]
    assert older["opened"], (
        "the profile did NOT open on the older build. If this ever goes red, "
        "read `failure_mode` in the reading before concluding a defect: a "
        "PID-exhausted host produces exit 133 and looks identical to a refusal "
        f"(see this file's header). failure_mode={older.get('failure_mode')}"
    )
    assert reading["leg_N"]["opened"], "the newer-build leg did not open either"


# --------------------------------------------------------------------------
# Q2 — does Chromium's OWN downgrade handling fire?
# --------------------------------------------------------------------------


def test_last_version_is_a_record_and_not_a_gate(reading):
    """Chromium WRITES ``Last Version`` and silently OVERWRITES it on a
    downgrade. It is a record of what ran, never a guard that refuses."""
    after_new = reading["leg_N"]["tree_after_shutdown"]["last_version"]
    before_old = reading["leg_N_minus_1"]["tree_before_launch"]["last_version"]
    after_old = reading["leg_N_minus_1"]["tree_after_shutdown"]["last_version"]

    assert after_new == before_old, (
        "the older build's launch should START from the stamp the newer build "
        "left — that is the state under test"
    )
    assert after_old != before_old, (
        "the older build did not rewrite Last Version, so this reading does not "
        "establish that the stamp is a record rather than a gate"
    )
    assert after_old in reading["positive_control"]["record_after"], (
        f"Last Version after the downgrade is {after_old!r}, which is not the "
        "build that actually ran"
    )


def test_no_profile_reset_and_no_renamed_default(reading):
    """A "profile is from a newer version" reset would show up as a renamed or
    recreated ``Default/``, or as a backup directory. Neither appears."""
    for leg in ("leg_N", "leg_N_minus_1"):
        for phase in ("tree_before_launch", "tree_after_shutdown"):
            tree = reading[leg].get(phase)
            if not tree:
                continue
            assert tree["default_exists"], f"{leg}.{phase}: Default/ is gone"
            assert tree["backup_dirs"] == [], (
                f"{leg}.{phase}: a backup/reset directory appeared "
                f"({tree['backup_dirs']}) — that IS Chromium's downgrade "
                "handling firing, and this ticket's position would need redoing"
            )


# --------------------------------------------------------------------------
# Q3 — is DERIVED STATE lost? (read from the RUNNING browser)
# --------------------------------------------------------------------------


def test_seeded_preferences_survive_the_downgrade_on_disk(reading):
    """``profile_seed._default_prefs`` writes exactly three things, ONCE.

    ``seed_profile_prefs`` returns early the moment ``Default/Preferences``
    exists, so if a downgrade removed or reset that file persona would NOT
    re-seed it and the operator's theme/search choice would be gone for good.
    Nothing removes it.
    """
    before = reading["leg_N_minus_1"]["tree_before_launch"]["prefs_on_disk"]
    after = reading["leg_N_minus_1"]["tree_after_shutdown"]["prefs_on_disk"]
    assert before == after, (
        "the seeded preferences changed across the downgrade. Because "
        "seed_profile_prefs is once-only, persona would never restore them: "
        f"{before} -> {after}"
    )
    assert after["color_scheme2"] == 2, "dark mode was lost"
    assert after["theme"] == {"id": "", "system_theme": 0}, "Classic theme was lost"
    assert after["search_short_name"] == "Brave", "the search engine was lost"


def test_the_running_browser_still_reports_the_chosen_search_engine(reading):
    """⭐ THE READING THAT MATTERS: from a live ``chrome://settings/``, not the
    JSON. A Preferences file that survives but is IGNORED is the same outcome
    for the operator as one that was deleted."""
    for leg in ("leg_N", "leg_N_minus_1"):
        text = reading[leg]["live"]["search_default_live"]
        assert isinstance(text, list), (
            f"{leg}: the live settings read failed ({text!r}) — an unobtained "
            "reading, which must never be scored as a pass"
        )
        assert "Brave (Default)" in text, (
            f"{leg}: the running browser does not report Brave as its default "
            "search engine, though the profile was seeded with it"
        )


def test_bookmarks_and_cookies_survive_into_the_running_browser(reading):
    """Both are read from the browser's OWN stores after a full restart on the
    other build — two observations separated by a fresh reader."""
    older = reading["leg_N_minus_1"]["live"]

    marks = older["bookmarks_live"]
    assert isinstance(marks, list), f"bookmark read failed: {marks!r}"
    assert any("httpbingo.org/cookies" in m for m in marks), (
        "the seeded bookmarks are not in the older build's own bookmark store"
    )

    jar = older["cookies_live"]
    assert isinstance(jar, list), f"cookie read failed: {jar!r}"
    assert any(c["name"] == "ps341" for c in jar), (
        "the cookie written on the NEWER build is not served on the older one. "
        "⚠️ Before calling that a downgrade defect, re-read this file's header: "
        "Chromium's cookie flush outlives process exit by ~20s, and a probe "
        "that restarts too soon reproduces this with NO build change at all."
    )


# --------------------------------------------------------------------------
# Q4 — does the FINGERPRINT move? (the Level-2 question)
# --------------------------------------------------------------------------


STABLE_VECTORS = (
    "screen",
    "dpr",
    "platform",
    "hw",
    "mem",
    "touch",
    "tz",
    "langs",
    "colorDepth",
    "canvas",
)


@pytest.mark.parametrize("vector", STABLE_VECTORS)
def test_the_fingerprint_does_not_move_across_the_build_change(reading, vector):
    """Every vector persona itself authors is bit-identical across the change.

    ``canvas`` is in this list deliberately: it is a full ``toDataURL()`` of a
    drawn canvas, so it is the one entry here that would move on any change to
    text rendering, GL compositing or the canvas noise layer.
    """
    new = reading["leg_N"]["live"]["page"][vector]
    old = reading["leg_N_minus_1"]["live"]["page"][vector]
    assert new == old, (
        f"{vector} moved across the engine build change: {new!r} -> {old!r}. "
        "This is a Level-2 continuity break — an engine update changing what a "
        "page reads about a profile that did not change."
    )


def test_the_engine_authored_webgl_pair_DOES_move_and_that_is_recorded(reading):
    """The one vector that moves. Asserted POSITIVELY so the finding cannot rot
    into an unnoticed silence — if a future engine pair stops moving, this goes
    red and the recorded position gets re-read rather than quietly outliving
    its evidence."""
    new = reading["leg_N"]["live"]["page"]
    old = reading["leg_N_minus_1"]["live"]["page"]
    assert (new["webglVendor"], new["webglRenderer"]) != (
        old["webglVendor"],
        old["webglRenderer"],
    ), (
        "the WebGL identity pair no longer moves across this build change. That "
        "is a CHANGE OF STATE, not a pass: re-read PS-341's recorded position "
        "in process.py and gpu_ext.py rather than deleting this test."
    )


def test_persona_stands_down_on_the_arm_where_the_pair_moved():
    """WHY it moved: on the windows arm the ENGINE authors the pair and
    persona's own GPU layer deliberately stands down, so what a page reads
    comes from a table inside the engine BINARY — which is a different binary
    per build. This is the fact that makes the move un-migratable."""
    from src.services.browser.gpu_ext import (
        engine_authors_identity_for_engine_platform,
    )

    assert engine_authors_identity_for_engine_platform("windows"), (
        "persona now authors the WebGL pair on the windows arm. If that is "
        "deliberate, PS-341's recorded finding needs re-reading: the pair would "
        "no longer be engine-authored and would no longer move with the build."
    )


def test_the_webgl_move_is_general_and_not_one_seed(gpu_seeds):
    """One seed moving could be two pools differing at one index. Measured
    across 8 seeds, headful under CDP, both builds read through the SAME venue.

    ⚠️ An unreadable leg is EXCLUDED from the tally rather than scored as a
    difference — an earlier draft read the old build with ``--headless
    --dump-dom``, got NO reading at all for every seed, and reported a clean and
    completely false 8/8.
    """
    assert gpu_seeds["seeds_scored"] >= 6, (
        "too few seeds produced a reading on BOTH builds to say anything "
        f"general: {gpu_seeds['seeds_scored']} of {gpu_seeds['seeds_attempted']}"
    )
    assert gpu_seeds["seeds_unreadable"] == 0, (
        "some seed produced no reading on one arm; an unreadable leg must not "
        "be counted as a moved one"
    )
    assert gpu_seeds["moved"] == gpu_seeds["seeds_scored"], (
        "the move is not universal across seeds — the finding should be "
        f"restated with the real proportion: {gpu_seeds['moved']}/"
        f"{gpu_seeds['seeds_scored']}"
    )


def test_the_two_builds_card_pools_do_not_intersect(gpu_seeds):
    """The strongest form of the finding, and the reason it is worth recording:
    this is not one card being re-rolled, it is the engine's whole GPU table
    being replaced between builds. 148 answers Intel integrated parts; 152
    answers NVIDIA RTX parts."""
    new = {tuple(r["new"]) for r in gpu_seeds["rows"] if r["readable"]}
    old = {tuple(r["old"]) for r in gpu_seeds["rows"] if r["readable"]}
    assert new and old
    assert not (new & old), (
        "the two builds' observed card pools now intersect, so the finding as "
        f"recorded is too strong and must be restated: {new & old}"
    )


# --------------------------------------------------------------------------
# the recorded position must stay where the grep that raised this would find it
# --------------------------------------------------------------------------


def test_the_position_is_recorded_beside_the_chromium_arm():
    """PS-341's premise 1 was a ZERO-HIT grep over ``process.py`` for
    build/migration vocabulary — the absence of any recorded position is what
    raised the ticket. A recorded no-op is only useful where the next reader's
    grep will land on it, so this pins that it stays there.
    """
    src = (REPO / "src" / "services" / "browser" / "process.py").read_text(
        encoding="utf-8"
    )
    assert "PS-341" in src, (
        "the recorded position has been removed from process.py; the premise-1 "
        "grep would once again find nothing and the question would be re-asked "
        "from scratch"
    )
    for term in ("migrat", "downgrade", "Last Version"):
        assert term in src, (
            f"the recorded position no longer mentions {term!r} — the grep that "
            "raised PS-341 searched for exactly this vocabulary"
        )
