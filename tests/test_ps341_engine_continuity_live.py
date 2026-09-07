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
a build change in both directions with its derived state intact — live-confirmed
for the search engine, bookmarks and cookies, and confirmed on disk (with
nothing removing the file) for the theme and dark mode; see the split below,
which is load-bearing. Chromium's own downgrade handling never fires. The
position now sits beside the Chromium arm in ``process.py`` (where the zero-hit
grep that raised the question would find it); these tests are what keep it
HONEST as the tree moves.

⚠️ ONE THING DOES MOVE, AND IT IS NOT A MIGRATION PROBLEM. The WebGL
vendor/renderer pair a page reads CHANGES across a build change on the WINDOWS
arm — the only arm where the ENGINE authors it
(``gpu_ext.ENGINE_AUTHORED_IDENTITY_ARMS`` is ``frozenset({"windows"})``, and
that is where persona's own GPU layer deliberately stands down). Measured
across 8 seeds: 8/8 moved, and the two builds' card pools DO NOT INTERSECT AT
ALL (148 answers Intel integrated parts, 152 answers NVIDIA RTX parts) — while
being STABLE within a build, which is what makes the move attributable to the
build change and to nothing else. No profile migration could repair it: the
value comes from a table compiled into the engine BINARY, so rewriting the
profile directory cannot change it. It is recorded, not fixed, because the fix
is a decision about WHO AUTHORS that pair on those arms — ``gpu_ext.py``'s
question, not this launch path's.

⛔ MACOS IS THE CONTRAST, NOT A SECOND INSTANCE. ``macos`` is NOT in that
constant: ``engine_authors_identity_for_engine_platform("macos")`` is ``False``,
so persona writes the pair itself from its own ``MAC_GPUS`` table there. A table
in persona's Python is not a table in the engine binary, so this pair should be
STABLE across a build change on macos — for exactly the reason it is unstable on
windows. ⚠️ NOT MEASURED: ``scripts/ps341_gpu_seeds.py:60`` defaults to
``platform="windows"`` and both call sites take the default, so all 8 seeds here
are windows. That is an argument from the mechanism, and the macos arm is an
unmeasured cell needing its own reading.

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

The derived-state readings are of TWO DIFFERENT STRENGTHS, and this file keeps
them apart deliberately rather than under one banner. A ``Default/Preferences``
that survives on disk but is IGNORED by the engine is the same outcome for the
operator, and only a live read tells those two apart — so:

  * **LIVE, from a RUNNING BROWSER on the older build:** the chosen search
    engine (``chrome://settings/searchEngines``), the seeded bookmarks
    (``chrome://bookmarks``) and the cookie jar (served, not merely on disk).
  * **ON DISK ONLY:** the Classic theme and dark mode (``color_scheme2``). Both
    are byte-identical across the build change and nothing renames, resets or
    removes the file that holds them — which is exactly what the question this
    ticket asks needs, since ``seed_profile_prefs`` keys on that file EXISTING
    and would never re-seed the operator's choice if it went away. It is NOT
    the stronger claim that the engine still HONOURS those two values; that
    reading was not taken, and ``test_seeded_preferences_survive_the_downgrade
    _on_disk`` says so in its own name. Do not promote it without measuring it.

⚠️ ONE CAPTURED NUMBER IS UNEXPLAINED AND IS LEFT OPEN ON PURPOSE.
``page.dark`` — ``matchMedia('(prefers-color-scheme: dark)').matches`` — reads
**False on BOTH legs**, though the profile is seeded ``color_scheme2: 2`` and
launched with ``--force-dark-mode``. The obvious explanation ("the flag is
UI-level and does not drive ``prefers-color-scheme``") was CHECKED AND IS
FALSE: on stock chromium 152 headless, the flag alone, the seeded pref alone,
and both together all give ``dark=true``, against a fresh-profile negative
control that correctly gives ``false``
(``scripts/ps341_dark_control.py`` → ``control-dark-mode.json``). Two variables
separate that control from these legs — stock vs packaged fingerprint engine,
headless vs headful-under-Xvfb — so the cause is NOT KNOWN. ⛔ Nothing here
turns on it: the value is the same on both builds, so it does not move across a
build change and is not a continuity fact. It is recorded so the next reader
inherits the open question *and* the ruled-out answer.

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
#: The follow-up control for the one unexplained number in the reading
#: (``page.dark``). Unlike the two above it needs NO engine and NO display —
#: stock chromium headless — so it is cheap to re-run.
CONTROL_DARK = REPO / "readings" / "ps341-2026-09-07" / "control-dark-mode.json"


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
    record_after = reading["positive_control"]["record_after"]
    assert record_after.endswith(after_old) and after_old, (
        f"Last Version after the downgrade is {after_old!r}, which is not the "
        f"build that actually ran ({record_after!r}). Anchored at the END "
        "rather than matched as a substring: the updater's record carries a "
        "`personium-` prefix that Chromium's own stamp does not, so equality "
        "is wrong here — but a bare `in` would also accept a version that "
        "merely appears somewhere inside the tag."
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

    ⚠️ SCOPE, AND THE NAME MEANS IT: this is an **on-disk** assertion. It
    establishes that the file and its three values are byte-identical across the
    build change — which is precisely what the once-only guard turns on, since
    that guard keys on the file EXISTING. It does NOT establish that the engine
    still HONOURS those values. For the search engine that stronger reading was
    taken separately and live
    (``test_the_running_browser_still_reports_the_chosen_search_engine``); for
    the theme and dark mode it was NOT taken, and no claim beyond this one is
    supported anywhere in this change. Do not cite this test for a live one.
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
    per build. This is the fact that makes the move un-migratable.

    ⭐ AND THE OTHER HALF: macos is asserted to be OUTSIDE that set. The
    recorded position previously named ``windows / macos`` as the engine-authored
    arms, which is false — and false in the direction that INVERTS the position's
    own mechanism, since on macos the pair comes from persona's ``MAC_GPUS``
    table in Python rather than from the engine binary. Pinning both members
    means the prose and the constant cannot drift apart in either direction:
    widening the constant to include macos reddens this, and so does narrowing
    it away from windows.
    """
    from src.services.browser.gpu_ext import (
        ENGINE_AUTHORED_IDENTITY_ARMS,
        engine_authors_identity_for_engine_platform,
    )

    assert engine_authors_identity_for_engine_platform("windows"), (
        "persona now authors the WebGL pair on the windows arm. If that is "
        "deliberate, PS-341's recorded finding needs re-reading: the pair would "
        "no longer be engine-authored and would no longer move with the build."
    )
    assert not engine_authors_identity_for_engine_platform("macos"), (
        "the ENGINE now authors the WebGL pair on macos too. PS-341's recorded "
        "position states the OPPOSITE as its contrast case (persona authors it "
        "there from MAC_GPUS, so the pair should be stable across a build "
        "change) — that paragraph is now wrong and must be re-measured."
    )
    assert set(ENGINE_AUTHORED_IDENTITY_ARMS) == {"windows"}, (
        "the engine-authored arm set changed. PS-341's recorded position, its "
        "test docstring and EVIDENCE.md all name windows as the ONLY such arm "
        f"and macos as the contrast; got {set(ENGINE_AUTHORED_IDENTITY_ARMS)}. "
        "Re-measure before restating the prose."
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


def test_the_recorded_position_does_not_overclaim_a_live_theme_reading():
    """⭐ The position outlives every other artifact here, so it must say what
    was measured and no more.

    Theme and dark mode were read **on disk only**; the search engine, bookmarks
    and cookies were read **live**. An earlier draft of the position put all
    five under one "read from the RUNNING browser" banner, which is exactly the
    failure PS-11 names — a claim that got STRONGER as it travelled from the
    evidence file into the prose. This pins the split, on the artifact a future
    reader will act on without re-opening the evidence.

    It is deliberately a text assertion rather than a data one: the defect this
    guards against was never in the reading, it was in the sentence about it.

    ⚠️ ITS BOUND, STATED RATHER THAN IMPLIED (and measured, not assumed). This
    catches the DELETION of the split and the RETURN of the exact rejected
    sentence. It does NOT catch a NEW overclaim written alongside a surviving
    split — verified by mutation: appending "(and also confirmed live, all five
    read from the RUNNING browser)" to the on-disk bullet leaves this **green**.
    A general "this prose does not overclaim" assertion is not available to a
    string check, so do not read a pass here as one. What it buys is that the
    specific regression which already happened once cannot happen silently
    twice; the rest is a human reading the position. Recorded because a guard
    whose limits are unstated gets inherited as stronger than it is.
    """
    src = (REPO / "src" / "services" / "browser" / "process.py").read_text(
        encoding="utf-8"
    )
    head, _, tail = src.partition("PS-341")
    assert tail, "the PS-341 position is gone from process.py"
    position = tail[:6000]

    assert "ON DISK ONLY" in position, (
        "the recorded position no longer distinguishes the on-disk readings "
        "(theme, dark mode) from the live ones. If a live theme reading has "
        "since been TAKEN, update this test with it — do not simply delete the "
        "distinction, which is how the overclaim got in the first time."
    )
    assert "LIVE" in position
    # The exact sentence the audit rejected must not come back.
    assert "DERIVED STATE SURVIVES INTACT, read from the RUNNING browser" not in src


def test_the_position_does_not_name_macos_as_an_engine_authored_arm():
    """⭐ The SECOND overclaim in this position, and the same shape as the first.

    The WebGL paragraph named ``windows / macos`` as "the arms where the ENGINE
    authors it", citing ``ENGINE_AUTHORED_IDENTITY_ARMS`` by name — and that
    constant is ``frozenset({"windows"})``. Two arms under one "measured,
    engine-authored" banner when one qualifies, exactly as an earlier round had
    five readings under one "read from the RUNNING browser" banner when three
    did.

    ⚠️ IT IS WORSE THAN A MIS-CITATION, WHICH IS WHY IT GETS ITS OWN GUARD: it
    INVERTS the paragraph's own mechanism on that arm. The argument is "the value
    comes from a table in the engine BINARY, so no profile migration could repair
    it"; on macos the value comes from a table in persona's PYTHON
    (``MAC_GPUS``), which a build change does not touch — so the pair should be
    STABLE there for the very reason it moves on windows.

    Two halves are pinned, and the second is the one that makes this more than a
    string check: the rejected spelling must not return, AND the arm must still
    be disclosed as UNMEASURED. Every seed in the reading is windows
    (``ps341_gpu_seeds.py``'s ``platform`` defaults to ``"windows"`` and both
    call sites take the default), so a future reader must not inherit the macos
    contrast as a measurement.

    ⚠️ SAME BOUND AS ITS SIBLING ABOVE, and for the same reason: a string check
    catches the return of a spelling, never "this prose is honest". If the macos
    arm is ever actually MEASURED, update this test with the reading rather than
    deleting the assertion — deleting it is how the first overclaim survived.
    """
    src = (REPO / "src" / "services" / "browser" / "process.py").read_text(
        encoding="utf-8"
    )
    head, _, tail = src.partition("PS-341")
    assert tail, "the PS-341 position is gone from process.py"
    position = tail[:8000]

    # The rejected spellings: macos named as an arm the ENGINE authors.
    for rejected in ("windows /\n    # macos", "windows / macos", "windows and macos"):
        assert rejected not in position, (
            f"the recorded position again names {rejected!r} as the "
            "engine-authored arms. ENGINE_AUTHORED_IDENTITY_ARMS is "
            "frozenset({'windows'}) — on macos persona authors the pair itself "
            "from MAC_GPUS, so naming it here inverts the paragraph's own "
            "un-migratable mechanism."
        )

    # And the arm must stay disclosed as UNMEASURED, not quietly upgraded.
    assert "MACOS IS THE CONTRAST" in position, (
        "the position no longer states macos as the CONTRAST case. That "
        "paragraph is what stops the next reader re-deriving 'both arms are "
        "engine-authored' from the WebGL finding alone."
    )
    # ⚠️ SCOPED TO THE MACOS PARAGRAPH, NOT TO THE WHOLE POSITION. A bare
    # `"not measured" in position` PASSES FOR THE WRONG REASON: the theme /
    # dark-mode paragraph 40 lines above already contains "which was not
    # measured\n#  here", so the assertion was satisfied by an unrelated
    # sentence and stayed GREEN when this disclosure was deleted. Measured, not
    # reasoned — that mutation is recorded in this ticket's rework comment.
    # Narrow the window to the macos paragraph itself so the assertion is about
    # the claim it is named after.
    _, _, macos_para = position.partition("MACOS IS THE CONTRAST")
    macos_para = macos_para[:1600]
    assert "ARGUMENT, NOT A READING" in macos_para, (
        "the macos paragraph no longer marks itself as an ARGUMENT from the "
        "mechanism rather than a reading. All 8 seeds in the reading are "
        "windows, so an undisclosed macos claim is an un-measured arm asserted "
        "inside a measured finding — the exact defect this guard exists for."
    )
    assert "was NOT\n    # measured" in macos_para or "NOT measured" in macos_para, (
        "the macos paragraph no longer discloses that the arm was never "
        "measured here. If it has since BEEN measured, put the reading in and "
        "update this test — do not delete the disclosure."
    )


def test_the_unexplained_dark_reading_is_recorded_with_its_ruled_out_answer():
    """``page.dark`` is False on both legs and nobody knows why.

    That is a fine thing to ship — it does not move across the build change, so
    it is not a continuity fact and the position does not rest on it. What is
    NOT fine is leaving it bare in a committed reading, where the next person to
    grep it re-derives the same plausible-and-wrong explanation. Both halves are
    pinned: the number, and the control that already falsified the obvious
    answer.
    """
    reading_path = READING
    if not reading_path.exists():
        pytest.skip(f"PS-341 evidence not present at {reading_path}")
    with reading_path.open(encoding="utf-8") as f:
        data = json.load(f)

    dark = {leg: data[leg]["live"]["page"]["dark"]
            for leg in ("leg_N", "leg_N_minus_1")}
    assert dark["leg_N"] == dark["leg_N_minus_1"], (
        f"page.dark now DIFFERS across the build change ({dark}) — that would "
        "make it a genuine Level-2 continuity finding rather than the "
        "unexplained-but-stable reading the position records, and the position "
        "would need redoing"
    )

    src = (REPO / "src" / "services" / "browser" / "process.py").read_text(
        encoding="utf-8"
    )
    assert "prefers-color-scheme" in src, (
        "the position no longer reconciles the page.dark reading; an "
        "unexplained number in a committed reading is a trap for the next "
        "reader"
    )


def test_the_dark_control_actually_falsifies_the_obvious_explanation():
    """The control is only worth citing if it CAN fail — and its own negative
    control is what makes that true.

    A fresh profile with no flag must read ``dark=false``. If it does not, every
    other arm reading ``true`` means nothing (the probe would simply be unable
    to observe False), and the "ruled out" claim in the position collapses.
    """
    control = CONTROL_DARK
    if not control.exists():
        pytest.skip(f"PS-341 dark-mode control not present at {control}")
    with control.open(encoding="utf-8") as f:
        data = json.load(f)

    arms = data["arms"]
    for label, arm in arms.items():
        assert not arm["unreadable"], (
            f"{label} produced no reading — an unobtained result must never be "
            f"scored as an observation ({arm.get('stderr_tail')!r})"
        )

    assert arms["A_fresh_no_flag"]["dark"] is False, (
        "THE NEGATIVE CONTROL FAILED: a fresh profile with no flag reports dark "
        "mode, so this probe cannot distinguish 'dark is on' from 'the probe "
        "always says dark'. Nothing else in this control means anything."
    )
    assert arms["B_fresh_force_dark"]["dark"] is True, (
        "--force-dark-mode no longer drives prefers-color-scheme on a stock "
        "engine. That was the explanation the position records as FALSIFIED — "
        "if it has become true, the position's reconciliation is now wrong and "
        "must be rewritten rather than left standing."
    )
    assert arms["C_seeded_no_flag"]["dark"] is True
    assert arms["D_seeded_force_dark"]["dark"] is True, (
        "persona's own combination (seeded color_scheme2 + --force-dark-mode) "
        "no longer drives it on a stock engine"
    )
