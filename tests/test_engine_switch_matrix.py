"""The engine's COMMAND-LINE SWITCH surface, reconciled as a matrix — including
the switches nobody ever wired.

The switch twin of ``test_engine_masking_matrix.py``, and it exists for exactly
the reason that file's header gives for itself: *"nobody could tell an
INTENTIONAL absence from an UNNOTICED one."* That file asks the question of
masking VECTORS across two engines. This one asks it of the thirteen
command-line SWITCHES patch ``000-add-fingerprint-switches.patch`` declares, and
it reuses that file's five-position vocabulary rather than minting a second one.

THE DEFECT THIS FILE ADDRESSES IS NOT THAT A SWITCH IS UNPASSED. It is that
THREE COLUMNS HAD NEVER BEEN READ AGAINST EACH OTHER:

* patch ``000`` DECLARES a switch (registers the command-line name),
* a later patch CONSUMES it (some C++ actually reads it), and
* ``spawn_browser`` PASSES it at launch.

Nothing in the tree reconciled those three, so a switch could be declared and
wired to nothing, or honoured by the engine and never sent, and no instrument
anywhere would say so. Both states existed, and both still do.

⭐ THE COST WAS PAID BEFORE THIS FILE EXISTED, and that is the argument for it.
PS-356 was row 5 of this table: ``--fingerprint-brand-version`` was declared,
was honoured by patch 002, and was not passed — so ``sec-ch-ua`` reported
Chromium 144 while the reduced UA reported 152. pixelscan called it *"masking
detected"* — a positive identification that this is a masking tool. **A human
found it by running four checkers by hand on 2026-09-06.** The reconciliation
below finds that row mechanically, with no browser, no engine build and no
checker.

THE FOURTH COLUMN, AND WHY THERE IS ONE. The obvious contract reads three
columns keyed by CONSTANT NAME. That contract would have manufactured a false
alarm, and did: ``kFingerprintTimezone`` registers the flag string
``"timezone"``, NOT ``"fingerprint-timezone"``. An audit that assumed the flag
name follows the constant name concluded timezone was leaking, when in fact
``018-timezone.patch`` honours it and ``spawn_browser`` passes ``--timezone=``
today. So every row below carries the REGISTERED FLAG STRING read out of the
patch, and the PASSED column is matched against THAT — never against a name
derived from the constant.

⛔ "WIRE EVERYTHING" IS THE WRONG SHAPE, AND ONE ROW PROVES IT.
``--disable-spoofing`` is consumed by SEVEN patches and is correctly never
passed. ⚠️ IT IS A VALUE-KEYED SELECTIVE DISABLE, NOT A BOOLEAN KILL SWITCH —
the BARE flag is inert through every consumer, and the harmful form is
``--disable-spoofing=canvas,gpu,audio,font,clientrects``, whose substring-
matched tokens switch off audio noise, font masking, GPU-info spoofing, canvas,
client rects, measureText and WebGL readPixels. A contract demanding every
declared switch be passed would demand a security regression on its very first
row. Hence a RECORDED POSITION per row, not a wiring campaign.

WHERE EACH COLUMN READS, and each choice is load-bearing:

* DECLARED — parsed from the ``+`` lines of ``000`` ONLY. The file patches an
  ungoogled header that already held three ``kFingerprintingCanvas*Noise``
  constants; those arrive as CONTEXT lines, and a parser that read the whole
  hunk would silently claim three switches this patch does not declare.
* CONSUMED — the other fifteen patches, ⭐ ALSO ``+`` LINES ONLY, held to the
  identical standard for the identical reason. Context lines are code a patch
  merely sits NEXT TO (often code an earlier patch in the same series
  authored), and removed lines are the OPPOSITE of a consumption. This is not
  theoretical: reading whole patch text credited ``015`` as an eighth consumer
  of ``kDisableSpoofing``, which it carries on context lines only, quoting the
  guard ``012`` wrote. Matched by constant name OR by the literal flag string,
  because a patch could plausibly hard-code ``"fingerprint-screen-width"``
  without naming the constant. Neither alone is sufficient and the pair is
  cheap.
* PASSED — ⭐ read off the argv A REAL LAUNCH PRODUCES, through the existing
  spawn harness, and NOT by grepping ``src/``. This is the same rule the masking
  matrix states for its own Chromium column ("a sentinel that reads below the
  layer it guards is silently worthless"), and here it is not a stylistic
  preference — it defeats two distinct traps that a grep walks straight into:

  1. ``grep -rl -- "--fingerprint-brand" src/`` matches ``__pycache__/*.pyc``,
     which is gitignored and absent from a clean checkout but present on any
     working container (88 of them under ``src/``). A row could then read
     PASSED on the strength of stale BYTECODE after its source line was
     deleted — which is precisely the deletion this file's falsification arm
     requires to turn it RED.
  2. This very file's sibling change adds a PROSE COMMENT to ``process.py``
     recording why ``--disable-spoofing`` is never passed. That comment contains
     the literal string ``--disable-spoofing``. A grep-based column would read
     the recorded reason for NOT passing a switch as evidence that it IS
     passed — inverting the one row the file most needs to get right.

  argv has neither problem: a comment is not an argument, and bytecode is not a
  launch.

  ⭐ MEASURED, NOT ARGUED — AND THE GREP IS WORSE THAN ANYBODY EXPECTED. This
  file's falsification arm 2 deleted the ``--fingerprint-brand=Chrome`` line
  from ``spawn_browser`` and re-ran every candidate column. The argv column went
  RED. THREE SEPARATE GREP FORMULATIONS ALL STAYED GREEN:

  * ``grep -rl -- "--fingerprint-brand" src/`` — still 4 hits, two of them
    ``.pyc`` bytecode still carrying the deleted literal.
  * the same search restricted to ``git ls-files`` (the remedy proposed for
    trap 1) — still 3 hits.
  * source-only, tracked-only, with an exact flag-boundary regex — STILL 2
    hits: ``src/core/strings.py:6`` names the flag in a comment explaining what
    it is for, and ``src/services/verify/chromium_tier.py:451`` carries it as
    an EXPECTED-ARGV constant in a verifier.

  So the bytecode was never the real problem, and excluding it does not fix
  this: the flag string legitimately appears in source that does not launch
  anything. ⛔ NO GREP OVER ``src/`` CAN SERVE AS THE PASSED COLUMN, because
  "this string appears somewhere" and "a launch emits this argument" are
  different claims, and only the second is the one this census makes.

⚠️ NO BROWSER WAS EXECUTED AND NO ENGINE WAS BUILT. What every cell establishes
is a STATIC reconciliation of the shipped patch set against the launch path.
That a switch is PASSED is not evidence it had an EFFECT — four switches in this
very table are proof of the difference, since they are accepted and discarded in
silence. Runtime deadness is INHERITED from PS-301 and PS-344 and is cited as
theirs, never re-claimed here.

⚠️ PS-301'S NUMBERS ARE A ``144.0.7559.132`` READING. That report says so
itself. PS-344 re-measured the same three switches on the PUBLISHED
``152.0.7977.75`` binary and reproduced the result, so the screen-trio cells
below rest on the 152 reading and cite the 144 one only as its predecessor.

THE SPLIT THIS FILE PINS IS 7 FULLY-WIRED / 2 CONSUMED-NOT-PASSED / 4
DECLARED-NEVER-CONSUMED, AND IT WAS 4 / 4 / 4 IN THOSE SAME THREE BUCKETS WHEN
THE WORK WAS COMMISSIONED. ⚠️ THE BUCKETS ARE NAMED RATHER THAN ORDERED, on
purpose: on a file whose whole value proposition is that the numbers are
DERIVED rather than written down, the one number written by hand must not
depend on a reader guessing which bucket a position in the sequence refers to
(the first version of this docstring said "6 / 4 / 2" while the code's own
bucket order said "6 / 2 / 4", and both were defensible). THREE rows have now
moved since commissioning, all by the same route — a human finding one row by
hand and a ticket closing it:

* ``--fingerprint-brand-version`` (PS-356, commit 5014250) — consumed-not-passed
  became fully wired.
* ``--fingerprint-hardware-concurrency`` (PS-354, commit eb64ad9) —
  consumed-not-passed became fully wired, to give the ServiceWorker realm an
  author.
* ``--fingerprint-device-memory`` (PS-392) — the third row, and the only one
  that arrived by DECLARATION rather than by wiring an existing declaration:
  the switch did not exist in ``000`` at all, so this move took the total from
  twelve declared to thirteen and the fully-wired bucket from 6 to 7.

That drift IS the argument for pinning the census as data rather than writing
the table into a document: the document would now be wrong, and nothing would
have said so.

* Add a switch to ``000`` and neither consume nor position it -> the census
  tests below fail, forcing the question to be answered out loud.
* Establish a position on purpose -> change that cell from ``NOT_ESTABLISHED``
  in the same commit. That edit IS the record that the question was answered.

The matrix, one row per declared switch:

| flag                             | consumed by      | passed | position          |
|----------------------------------|------------------|--------|-------------------|
| fingerprint                      | 10 patches       | YES    | COVERED           |
| fingerprint-brand                | 002              | YES    | COVERED           |
| fingerprint-brand-version        | 002              | YES    | COVERED (PS-356)  |
| fingerprint-device-memory        | 005              | YES    | COVERED (PS-392)  |
| fingerprint-hardware-concurrency | 005              | YES    | COVERED (PS-354)  |
| fingerprint-platform             | 002,006,011      | YES    | COVERED           |
| timezone                         | 018              | YES    | COVERED           |
| disable-spoofing                 | 7 patches        | NO     | NOT_COVERED_RECORDED |
| fingerprint-platform-version     | 002              | NO     | ⭐ NOT_ESTABLISHED |
| fingerprint-screen-width         | <NOBODY>         | NO     | COVERED_ELSEWHERE |
| fingerprint-screen-height        | <NOBODY>         | NO     | COVERED_ELSEWHERE |
| fingerprint-device-scale-factor  | <NOBODY>         | NO     | COVERED_ELSEWHERE |
| fingerprint-location             | <NOBODY>         | NO     | ⭐ NOT_ESTABLISHED |
"""

import re
from pathlib import Path

import pytest

from src.models.profile import Profile
from tests.test_process import _spawn_chromium_args

# The repo, anchored to THIS FILE rather than to the process CWD — the same
# convention the masking matrix states and for the same reason: a bare relative
# path is green from the repo root and FileNotFoundError from anywhere else.
REPO_ROOT = Path(__file__).resolve().parents[1]

PATCH_DIR = REPO_ROOT / "engine" / "patches" / "fingerprint"
DECLARING_PATCH = PATCH_DIR / "000-add-fingerprint-switches.patch"

# --- positions ---------------------------------------------------------------
#
# ⛔ IMPORTED, NOT REDEFINED. These are the same five strings
# test_engine_masking_matrix.py defines, and importing them is the mechanism
# that keeps one vocabulary rather than two: a rename there is a red test here,
# instead of two files quietly meaning different things by the same word.
from tests.test_engine_masking_matrix import (  # noqa: E402
    COVERED,
    COVERED_ELSEWHERE,
    NOT_APPLICABLE,
    NOT_COVERED_RECORDED,
    NOT_ESTABLISHED,
    _collapse,
)

POSITIONS = {
    COVERED,
    COVERED_ELSEWHERE,
    NOT_COVERED_RECORDED,
    NOT_APPLICABLE,
    NOT_ESTABLISHED,
}


# --- the census: read the tree, never a hardcoded list -----------------------


def declared_switches():
    """``{constant: flag_string}`` for every switch patch ``000`` DECLARES.

    ⛔ ``+`` LINES ONLY. The patch edits an ungoogled file that already declares
    ``kFingerprintingCanvasNoise``, ``kFingerprintingCanvasMeasureTextNoise``
    and ``kFingerprintingCanvasImageDataNoise``; those appear in the hunk as
    CONTEXT lines (leading space). A parser that read the whole hunk would
    report fifteen switches and credit this patch with three it does not
    declare — and would then demand a position on each, which is a census
    lying about its own subject.

    ``+++ b/path`` header lines also begin with ``+`` and are excluded by the
    regex, which requires a C++ ``const char k...[] = "..."`` declaration.
    """
    text = DECLARING_PATCH.read_text(encoding="utf-8")
    found = {}
    for line in text.splitlines():
        if not line.startswith("+"):
            continue
        m = re.search(r'const char (k\w+)\[\]\s*=\s*"([^"]+)"', line)
        if m:
            found[m.group(1)] = m.group(2)
    return found


def consumer_patches(constant, flag):
    """The other patches that READ this switch, as sorted numeric prefixes.

    ⛔ ``+`` LINES ONLY, held to EXACTLY the standard ``declared_switches``
    is held to, and for the identical reason. A unified diff carries three
    kinds of line, and only the added ones are what the patch DOES:

    * a CONTEXT line (leading space) is code the patch merely sits next to —
      often code an EARLIER patch in the same series authored, quoted so the
      hunk can be located. Counting it credits this patch with reading a
      switch it does not read.
    * a REMOVED line (leading ``-``) is the opposite of a consumption: a patch
      that DELETES a read would be counted as performing one, moving a row in
      exactly the wrong direction and silently.

    ⭐ THE CONTEXT HAZARD IS NOT THEORETICAL — IT FIRED, and a code review
    caught it. ``015-canvas-measure-text.patch`` adds NO ``kDisableSpoofing``
    line: both of its occurrences are context lines quoting the ``if`` that
    ``012`` authored. The first version of this census read whole patch text
    and credited 015 as an eighth consumer of a switch it never reads. The
    counter-example is pinned by
    ``test_a_context_only_patch_is_not_counted_as_a_consumer`` so it cannot
    evaporate.

    ⚠️ ``+++ b/path`` header lines also begin with ``+``; they are excluded
    explicitly rather than left to the matchers, because a patch whose
    FILENAME happened to contain a flag literal would otherwise self-consume.

    Matched by CONSTANT NAME **or** by the quoted FLAG STRING. Neither alone is
    sufficient: the constant is how every consumer in the tree happens to read
    it today, and the literal is how a future patch might, so a census keyed on
    only one of them has a blind spot for the other. The pair costs one extra
    regex.

    ``000`` itself is excluded — a declaration is not a consumption, and
    including the declaring file would make every row look consumed.
    """
    by_constant = re.compile(rf"\b{re.escape(constant)}\b")
    by_literal = re.compile(rf'"{re.escape(flag)}"')
    out = []
    for patch in sorted(PATCH_DIR.glob("*.patch")):
        if patch.name == DECLARING_PATCH.name:
            continue
        added = "\n".join(
            line
            for line in patch.read_text(encoding="utf-8").splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        if by_constant.search(added) or by_literal.search(added):
            out.append(patch.name.split("-")[0])
    return sorted(out)


def launch_switches(monkeypatch, tmp_path):
    """The fingerprint switches a REAL Chromium launch puts on the command line.

    ⭐ THIS IS THE PASSED COLUMN, and it is read from argv rather than from the
    source ON PURPOSE — see this module's docstring for the two traps a
    ``grep src/`` walks into (gitignored bytecode, and the prose comment in
    ``process.py`` that names ``--disable-spoofing`` in order to record why it
    is never passed).

    Returns the set of flag NAMES with the leading ``--`` and any ``=value``
    stripped, so a row matches on the switch rather than on this profile's
    particular seed.
    """
    captured = _spawn_chromium_args(
        monkeypatch, tmp_path, Profile(name="switch-census"), linux=True
    )
    names = set()
    for arg in captured["args"]:
        if not arg.startswith("--"):
            continue
        names.add(arg[2:].split("=", 1)[0])
    return names


# --- the matrix --------------------------------------------------------------
#
# One entry per DECLARED switch, keyed by the FLAG STRING the patch registers
# (never by the constant name — see the timezone trap in the docstring).
# ``(position, note)``; the note is the cell's evidence, and for a
# NOT_COVERED_RECORDED cell it quotes the in-tree reason so a test can re-read
# it at its source.

MATRIX = {
    # --- the six that are declared, consumed AND passed ----------------------
    "fingerprint": (
        COVERED,
        "The seed itself, and the most consumed switch in the set: patches "
        "002, 003, 005, 006, 011, 012, 013, 014, 015 and 016 all read it. "
        "Passed unconditionally as --fingerprint=<profile.fingerprint_seed>. "
        "PS-301 measured it live: canvas and WebGL digests all move with the "
        "seed.",
    ),
    "fingerprint-brand": (
        COVERED,
        "Consumed by 002. Passed UNCONDITIONALLY as --fingerprint-brand=Chrome "
        "— and it is also the GATE for fingerprint-brand-version below, which "
        "002 reads only inside `if (brand == \"chrome\")` after a ToLowerASCII. "
        "process.py carries that warning at the line itself.",
    ),
    "fingerprint-brand-version": (
        COVERED,
        "⭐ THIS ROW WAS 'consumed but never passed' WHEN THIS CENSUS WAS "
        "COMMISSIONED, and it is the reason the census exists. Declared by "
        "000, honoured by 002, and unpassed — so sec-ch-ua reported Chromium "
        "144 (the engine's hardcoded fallback table) while the reduced UA "
        "reported 152. pixelscan: 'inconsistent + masking detected'; "
        "browserscan: -5%, 'different browser version'; creepjs: version lie. "
        "A HUMAN found it by running four checkers by hand on 2026-09-06; "
        "PS-356 (commit 5014250) closed it. Now passed CONDITIONALLY — the "
        "flag is emitted only when the installed engine's version could be "
        "read, and the skip is a logged degraded state rather than a "
        "hardcoded fallback.",
    ),
    "fingerprint-hardware-concurrency": (
        COVERED,
        "⭐ ALSO 'consumed but never passed' WHEN THIS CENSUS WAS COMMISSIONED, "
        "closed by PS-354 (commit eb64ad9) in the days between. Consumed by "
        "005. It is now passed because it is the SERVICE WORKER REALM'S ONLY "
        "AUTHOR: a ServiceWorkerGlobalScope is never constructed by the page, "
        "so worker_wrap's chaining has no constructor to intercept and an MV3 "
        "content script does not run there — the realm fell through to the "
        "engine's seed fallback or to the HOST. The value is the page realm's "
        "own pick, resolved through the same generation-filtered pool, so "
        "page and engine agree by construction.",
    ),
    "fingerprint-platform": (
        COVERED,
        "Consumed by 002, 006 and 011 — the widest consumer set after the seed "
        "itself. Passed unconditionally as --fingerprint-platform=<engine "
        "platform>, the same string the device preset and the GPU pool arm "
        "resolve from, because one machine reporting three answers is itself "
        "the tell.",
    ),
    "fingerprint-device-memory": (
        COVERED,
        "⭐ ADDED BY THE PIXELSCAN PORT'S SLICE 2 (PS-392), and it is the only "
        "row here whose consumer had to be BUILT rather than found. 005 did "
        "not merely lack a read site for deviceMemory — it hardcoded "
        "`return 8;`, so the engine answered 8 for every profile and no seed "
        "could move it. The switch read replaces that constant, on the "
        "018-timezone model, and is passed unconditionally. "
        "⛔ IT IS ALSO A JS DELETION, and that half is what makes it "
        "measurable: device_ext.py declared navigator.deviceMemory in BOTH "
        "the page realm and the worker-realm twin inside applyHwPatch, and "
        "both were deleted in the same change. Moving a spoof into the engine "
        "while leaving the getter changes nothing a checker can see — both "
        "authors stay live — and a native value in the page beside a stale JS "
        "override in the worker is the exact disagreement checkers look for. "
        "⚠️ THE VALUE IS SPEC-BOUNDED AND THE CAP IS NOT A DISGUISE. The "
        "Device Memory API reports RAM rounded DOWN to a power of two and "
        "clamped to [0.25, 8], so an 8 GB and a 16 GB machine BOTH report 8. "
        "persona's pool RAM axis is {8, 16}, so every profile legitimately "
        "reports 8 — measured over 5000 seeds. That is NOT lost entropy to be "
        "'restored': a per-seed value would contradict the profile's own "
        "claimed RAM and publish a figure no capped browser produces. The "
        "launcher passes an already-legal value AND the patch re-clamps, so "
        "no path can publish an illegal one. "
        "⚠️ MOBILE TAKES ITS DEVICE PRESET'S VALUE, not the desktop pool's — "
        "the argv list is built outside the mobile/desktop branch, and an "
        "iPhone reporting desktop RAM in the ServiceWorker realm while its own "
        "JS says 4 would be the same realm disagreement one lane over.",
    ),
    "timezone": (
        COVERED,
        "⚠️ THE NAMING TRAP, and the reason this matrix carries a REGISTERED "
        "FLAG STRING column at all. The constant is kFingerprintTimezone but "
        "the flag it registers is `timezone`, NOT `fingerprint-timezone`. An "
        "audit keyed on constant names concluded this switch was leaking the "
        "host zone; in fact 018-timezone.patch honours it in three places and "
        "process.py passes --timezone=<zone> today. The host reading that "
        "raised the alarm was almost certainly Chromium silently discarding "
        "an unregistered `--fingerprint-timezone`. "
        "⛔ DO NOT 'fix' either half to match the other: persona passes "
        "--timezone today, so renaming the constant or the string silently "
        "breaks a working spoof.",
    ),
    # --- consumed by the engine, and DELIBERATELY never passed ---------------
    "disable-spoofing": (
        NOT_COVERED_RECORDED,
        "⛔ THE ROW THAT MAKES 'wire everything' THE WRONG SHAPE. Consumed by "
        "SEVEN patches — 003, 006, 011, 012, 013, 014, 016. "
        "⚠️ IT IS A VALUE-KEYED SELECTIVE DISABLE, NOT A BOOLEAN KILL SWITCH, "
        "and the first version of this cell got that wrong: every consumer "
        "tests the switch's VALUE for a token, never its mere presence. The "
        "dominant shape is `HasSwitch(kFingerprint) && (!HasSwitch(kDisable"
        "Spoofing) || GetSwitchValueASCII(kDisableSpoofing).find(\"canvas\") "
        "== npos)`, through which the BARE flag is INERT — HasSwitch is true "
        "so the first arm is false, then \"\".find(...) is npos so the second "
        "is true, and the spoofing still applies. 011 is the only genuine "
        "`return \"\"` and it inverts the test (`find(\"gpu\") != npos`), "
        "which an empty value fails identically. "
        "⛔ THE HARMFUL FORM IS THE VALUED ONE: "
        "--disable-spoofing=canvas,gpu,audio,font,clientrects, whose tokens "
        "are matched by substring, one per masking family — audio (003), font "
        "(006), gpu (011), canvas (012, 013, 016: getImageData, toDataURL, "
        "measureText, WebGL readPixels), clientrects (014). That comma list is "
        "upstream's kill switch for the whole masking layer, present so a "
        "developer can A/B the patched engine against stock. Its absence is "
        "therefore the FINISHED state of this row, not a gap — and the reason "
        "is recorded in process.py beside the flags it sits among, where the "
        "question gets asked. "
        "⚠️ 015 IS NOT A CONSUMER: it carries kDisableSpoofing on CONTEXT "
        "lines only, quoting the guard 012 authored, which is why this row "
        "reads seven and not eight. Pinned by "
        "test_a_context_only_patch_is_not_counted_as_a_consumer.",
    ),
    # --- the three the engine ACCEPTS AND DISCARDS ---------------------------
    #
    # All three are COVERED_ELSEWHERE rather than NOT_COVERED_RECORDED: the
    # VALUE is spoofed, just not by this switch. A cell claiming the vector is
    # uncovered would be false, and one claiming the SWITCH works would be the
    # exact error PS-344 measured.
    "fingerprint-screen-width": (
        COVERED_ELSEWHERE,
        "DECLARED, FORWARDED TO THE RENDERER, AND READ BY NOBODY — consumed by "
        "zero patches. Measured twice, on two engines: PS-301 on a trial "
        "144.0.7559.132 build (--fingerprint-screen-width=2560 -> screen.width "
        "= 800 headless / 1920 under the harness's Xvfb, i.e. the DISPLAY "
        "showing through), then PS-344 on the PUBLISHED 152.0.7977.75 binary "
        "(asked 2560, observed 800). The same run read "
        "--fingerprint-hardware-concurrency=6 -> 6, which is the positive "
        "control proving the mechanism works and these switches specifically "
        "do not. "
        "⭐ THE ROUTE THAT DOES COVER IT is persona's JS layer: "
        "device_ext.py's applyScreenPatch pins screen.width from the profile's "
        "own resolution pool. So the vector is covered and the SWITCH is "
        "inert. "
        "⚠️ WIRING THE C++ READ-SIDE IS A JUDGEMENT, NOT A CHORE, and it is "
        "deliberately NOT taken here: it would create a SECOND author for one "
        "value, which is PS-2's 'one declared machine, one authority' "
        "territory. Native authorship is the more detectable-resistant route "
        "(no JS descriptor for a scanner to see) but only if the JS one is "
        "retired in the same motion, and that needs an engine build this "
        "ticket does not do.",
    ),
    "fingerprint-screen-height": (
        COVERED_ELSEWHERE,
        "The screen-width row's twin in every respect and for the same "
        "reasons. Consumed by zero patches. PS-301: "
        "--fingerprint-screen-height=1440 -> screen.height = 600 / 1080. "
        "PS-344 reproduced it on the published 152 binary (asked 1440, "
        "observed 600). Covered instead by device_ext.py, which pins "
        "screen.height and availHeight from the profile's resolution pool "
        "(availHeight subtracting a typical taskbar inset). Same one-authority "
        "judgement, same deferral.",
    ),
    "fingerprint-device-scale-factor": (
        COVERED_ELSEWHERE,
        "Consumed by zero patches. PS-301: --fingerprint-device-scale-factor=2 "
        "-> devicePixelRatio = 1. PS-344 reproduced it on the published 152 "
        "binary (asked 2, observed 1). Covered instead by device_ext.py, which "
        "pins DPR and answers the matchMedia dppx / device-width probes "
        "consistently — and which states the stake in its own words: the "
        "host's real DPR leaking through makes a scanner read screen.width * "
        "dpr as a resolution no monitor has. That sentence is re-read out of "
        "the tree by a test below.",
    ),
    # --- the two honest unknowns ---------------------------------------------
    "fingerprint-location": (
        NOT_ESTABLISHED,
        "⭐ THE HONEST UNKNOWN OF THE SCREEN GROUP, and it is kept separate "
        "from its three neighbours on PS-301's OWN INSISTENCE. It is declared "
        "by 000, consumed by zero patches and never passed — statically "
        "identical to the screen trio — but PS-301 refused to call it dead in "
        "as many words: 'is *not* claimed as dead here — I did not measure it' "
        "and 'No geolocation probe was run — the static reading is a lead, not "
        "a verdict.' PS-344 did not probe it either. "
        "⛔ AND THE STATIC READING IS NOT ENOUGH TO CLOSE IT, because the "
        "adjacent geolocation question already has a recorded answer running "
        "the OTHER way: PS-312 measured that Chromium's geo route yields no "
        "coordinates at all, and persona covers geography with "
        "build_geo_extension on proxied profiles. So this cell needs a "
        "geolocation READING to say whether the switch is inert, redundant, or "
        "the better authority — and this file will not mint a position the "
        "tree does not hold.",
    ),
    "fingerprint-platform-version": (
        NOT_ESTABLISHED,
        "⭐ THE SECOND OPEN CELL, and structurally the most interesting row in "
        "the table: it is the ONLY switch that is CONSUMED by the engine and "
        "still not passed — the exact shape PS-356 and PS-354 both were before "
        "they were closed. 002's GetPlatformVersion() reads it FIRST ('custom "
        "version takes priority') and otherwise falls back to a seed-indexed "
        "table baked into the patch: {19.0.0, 15.0.0} for windows, {6.14.0, "
        "6.8.0} for linux, ten 15.x entries for macOS. So the value IS "
        "authored today, from the seed, by the engine. "
        "WHY THIS IS NOT SIMPLY 'PASS IT': the engine's fallback is already "
        "seed-derived and self-consistent, so passing a value is only an "
        "improvement if persona has a BETTER one — and persona has no "
        "os_version concept at all (grep: no os_version/osVersion anywhere in "
        "src/). Establishing this cell means deciding whether the platform "
        "version should become a profile-level fact, and then whether the "
        "baked table's values are even plausible for the OS each claims. "
        "⚠️ NEITHER HALF IS MEASURED HERE, and PS-356's row is the warning "
        "against guessing: that one looked equally harmless until a checker "
        "read it. A reading of what a page actually receives in "
        "navigator.userAgentData.platformVersion, against what the profile "
        "claims, is what closes this cell.",
    ),
}

# The recorded reasons, and WHERE they live, as (path, verbatim quote). A
# NOT_COVERED_RECORDED cell is only honest while its reason still exists, so
# ``test_recorded_reasons_still_in_tree`` re-reads each file — deleting the
# reason turns this suite red rather than silently downgrading a recorded
# decision to an unexplained absence.
#
# ⚠️ ONE SOURCE LINE PER QUOTE, deliberately, and the masking matrix records
# why: these reasons live in COMMENT blocks, so every line a reason wraps onto
# begins with a ``#``. ``_collapse`` normalises whitespace but does NOT strip
# comment markers, so a quote spanning two comment lines can never match
# however faithfully it is copied.
RECORDED_REASON_SOURCES = {
    "disable-spoofing": (
        "src/services/browser/process.py",
        "bare --disable-spoofing is inert across all seven patches.",
    ),
}

# ⚠️ THE SECOND CLAUSE OF THE SAME RECORDED REASON, pinned SEPARATELY because
# the two halves fail differently and a single quote cannot catch both.
#
# The first version of this cell described --disable-spoofing as a boolean kill
# switch that each consumer reads "as an EARLY RETURN that stands the patch
# down". That is FALSE, and a code review caught it: seven of the eight
# candidate consumers are value-keyed guards, and the BARE flag is inert
# through every one of them (trace it: HasSwitch true -> `!HasSwitch` false;
# "".find("canvas") -> npos -> `== npos` true -> the `||` is true -> the
# spoofing still applies). 011 is the only genuine `return ""` and it inverts
# the test, which an empty value fails identically.
#
# ⛔ SO THE PROHIBITION IS ON THE **VALUED** FORM, and that is the half most
# worth guarding: a reader who believes the danger is a boolean flag will not
# think to check for `--disable-spoofing=canvas`, which is the argument that
# actually causes harm. A quote pinned only on the inertness sentence would let
# the valued-form warning be deleted while the suite stayed green.
RECORDED_REASON_SECOND_CLAUSES = {
    "disable-spoofing": (
        "src/services/browser/process.py",
        "The prohibition is on the VALUED form; the bare form is inert.",
    ),
}
# The evidence the COVERED_ELSEWHERE screen cells rest on, re-read for the same
# anti-rot purpose. Two different kinds of claim, kept apart:
#
#   * the ROUTE that covers the vector instead (device_ext.py), and
#   * the MEASUREMENT that established the switch is inert (a reading).
#
# Collapsing them would let a deleted measurement hide behind a surviving route.
SCREEN_ROUTE_SOURCE = (
    "src/services/browser/device_ext.py",
    "devicePixelRatio must agree with the spoofed screen",
)

# PS-344 is quoted rather than PS-301 because it is the reading on the PUBLISHED
# 152 binary. PS-301's 144 reading is cited too, so a future agent can see the
# defect predates the shipped engine rather than arriving with it.
SWITCH_DEADNESS_READINGS = {
    "published-152": (
        "readings/ps344-2026-09-07/REPORT.md",
        "The first three are declared by patch 000, forwarded on the command "
        "line, and never read.",
    ),
    "trial-144": (
        "readings/ps301-2026-09-05/REPORT.md",
        "So the mechanism works; these three specific switches are not wired "
        "to anything.",
    ),
}

# The two quotes that hold the ``fingerprint-location`` cell OPEN. This cell's
# honesty depends on a REFUSAL staying in the tree, which is an unusual thing to
# guard: if PS-301's disclaimers were deleted, a future reader would see three
# dead siblings and one identical-looking row and quite reasonably close it by
# analogy. These are re-read so that closing it requires a reading rather than
# the disappearance of the sentence saying no reading exists.
LOCATION_REFUSAL_SOURCES = {
    "not-claimed-dead": (
        "readings/ps301-2026-09-05/REPORT.md",
        "is *not* claimed as dead here — I did not measure it",
    ),
    "no-probe-run": (
        "readings/ps301-2026-09-05/REPORT.md",
        "No geolocation probe was run — the static reading is a lead, not a "
        "verdict.",
    ),
}


# --- the census cannot drift from the tree -----------------------------------


def test_the_matrix_covers_every_declared_switch():
    # THE DRIFT-PROOFING, and AC4. The declared set is READ FROM THE PATCH, not
    # from a hardcoded list of thirteen — so adding a switch to 000 and leaving it
    # unpositioned fails HERE, which is the whole point of the file. The
    # symmetric half matters too: a switch REMOVED from 000 while a cell
    # survives would leave the matrix asserting a position on something that no
    # longer exists.
    declared = set(declared_switches().values())
    assert declared == set(MATRIX), (
        "the switch matrix and 000-add-fingerprint-switches.patch disagree. "
        f"declared but unpositioned: {sorted(declared - set(MATRIX))}; "
        f"positioned but no longer declared: {sorted(set(MATRIX) - declared)}. "
        "A switch may be added to the patch, but not without a position."
    )


def test_the_declaration_parse_excludes_ungoogled_context_constants():
    # The ``+``-only rule, asserted rather than trusted — and this is the
    # sharpest control in the file, because the patch happens to carry a
    # context line in EXACTLY the shape the regex matches:
    #
    #      const char kFingerprintingCanvasImageDataNoise[] = "fingerprinting-…";
    #
    # A single leading space is the only thing distinguishing it from the thirteen
    # ``+`` lines below it. Drop the ``+`` test and the parser credits patch 000
    # with a switch it does not declare, then demands a position on it — a
    # census lying about its own subject.
    #
    # Both arms are asserted: the context constant is ABSENT from the parse, and
    # PRESENT in the file in that exact form. The second is the positive
    # control; the first alone would pass identically against an empty file, a
    # missing file or a regex that matched nothing at all.
    declared = declared_switches()
    lines = DECLARING_PATCH.read_text(encoding="utf-8").splitlines()

    context_declarations = [
        line
        for line in lines
        if line.startswith(" ")
        and re.search(r'const char (k\w+)\[\]\s*=\s*"([^"]+)"', line)
    ]
    assert context_declarations, (
        "000 no longer carries a CONTEXT line in declaration form. The "
        "``+``-only rule is still correct, but the counter-example proving it "
        "necessary has evaporated — do not read this test's silence as "
        "evidence the rule is unneeded."
    )
    for line in context_declarations:
        constant = re.search(r"const char (k\w+)\[\]", line).group(1)
        assert constant not in declared, (
            f"{constant} was parsed as DECLARED by 000, but it arrives as an "
            "ungoogled CONTEXT line — the patch does not declare it. The "
            "parser is reading the whole hunk instead of the added lines."
        )
    # The specific constant, named, so a future reader can see what the control
    # actually is rather than trusting the loop above found something.
    assert any(
        "kFingerprintingCanvasImageDataNoise" in line for line in context_declarations
    )
    assert len(declared) == 13, (
        f"000 declares {len(declared)} switches, not 13. If that is a real "
        "change, test_the_matrix_covers_every_declared_switch names which one "
        "moved and this count follows it — update both together.\n"
        "⭐ WENT 12 -> 13 AT PS-392: fingerprint-device-memory was added so "
        "deviceMemory could move off its JS descriptor into the engine."
    )


def test_the_registered_flag_string_is_read_not_derived():
    # ⚠️ THE TIMEZONE TRAP, pinned. A contract that derived the flag name from
    # the constant name would read kFingerprintTimezone as
    # "fingerprint-timezone" and report a working switch as broken — which is
    # exactly the false alarm this project already raised once. The flag string
    # is READ from the declaration, and this test pins the one row where the two
    # genuinely disagree so a future "tidy-up" cannot quietly align them.
    declared = declared_switches()
    assert declared["kFingerprintTimezone"] == "timezone"
    assert "fingerprint-timezone" not in declared.values()
    # And the naming convention DOES hold for the other eleven, which is what
    # makes the exception dangerous rather than obvious.
    for constant, flag in declared.items():
        if constant == "kFingerprintTimezone":
            continue
        expected = re.sub(r"(?<!^)(?=[A-Z])", "-", constant[1:]).lower()
        assert flag == expected, (
            f"{constant} registers {flag!r}, which follows neither the "
            "convention nor the one known exception. A second naming "
            "exception needs its own row in the docstring before this "
            "assertion is relaxed."
        )


def test_the_consumer_column_is_read_from_the_other_patches():
    # The CONSUMED column, with its own positive and negative controls in the
    # same assertion block. Four rows in this table have ZERO consumers, and
    # four zeros over a sixteen-patch set are indistinguishable from a bad
    # pathspec without a positive control — which is this project's recorded
    # failure shape twice over (the PS-299 probe that printed "81/81 hunks, 0
    # rejects, ✅" against an empty directory, and ps301_repro.sh, which had no
    # non-zero exit path at all).
    declared = declared_switches()
    consumers = {
        flag: consumer_patches(constant, flag) for constant, flag in declared.items()
    }

    # POSITIVE: the switches that are read, are seen to be read.
    assert consumers["timezone"] == ["018"]
    assert consumers["fingerprint-platform"] == ["002", "006", "011"]
    assert consumers["fingerprint-brand"] == ["002"]
    assert consumers["fingerprint-brand-version"] == ["002"]
    assert consumers["fingerprint-hardware-concurrency"] == ["005"]
    assert consumers["disable-spoofing"] == [
        "003",
        "006",
        "011",
        "012",
        "013",
        "014",
        "016",
    ], (
        "the consumer set for --disable-spoofing has moved. ⚠️ SEVEN, NOT "
        "EIGHT: 015 carries kDisableSpoofing on CONTEXT lines only (it edits "
        "code inside the guard 012 authored) and is NOT a consumer. If 015 is "
        f"back in this list ({consumers['disable-spoofing']}), the ``+``-only "
        "rule in consumer_patches has been dropped."
    )

    # NEGATIVE: the four that are read by nobody. Only trustworthy because the
    # positives above fired in the same call.
    for flag in (
        "fingerprint-screen-width",
        "fingerprint-screen-height",
        "fingerprint-device-scale-factor",
        "fingerprint-location",
    ):
        assert consumers[flag] == [], (
            f"{flag} now has a consumer ({consumers[flag]}). That is a real "
            "change in the engine's behaviour: re-read its cell, because a "
            "COVERED_ELSEWHERE position resting on 'no patch reads this' no "
            "longer holds."
        )

    # NEGATIVE CONTROL: a switch that does not exist is consumed by nobody, and
    # a matcher that returned patches for it would be matching noise.
    assert consumer_patches("kFingerprintNope", "fingerprint-nope") == []


def test_a_context_only_patch_is_not_counted_as_a_consumer():
    # ⭐ THE COUNTER-EXAMPLE THAT PROVES THE ``+``-ONLY RULE NECESSARY ON THE
    # CONSUMER COLUMN, pinned in the same shape
    # test_the_declaration_parse_excludes_ungoogled_context_constants pins it
    # on the DECLARED column — because the first version of this file held the
    # two columns to DIFFERENT standards and never said why.
    #
    # The declared parse was scrupulously ``+``-only, with a whole test
    # explaining that reading the full hunk makes "a census lying about its own
    # subject". The consumer column read whole patch text — context and removed
    # lines included — with no guard at all. A code review found it FIRING:
    # 015-canvas-measure-text.patch adds no kDisableSpoofing line, but quotes
    # the guard 012 authored on two CONTEXT lines, and was credited as an
    # eighth consumer of a switch it does not read. That inflated 8 was then
    # asserted in four places and leaned on rhetorically ("the most consumed
    # switch").
    #
    # BOTH ARMS ARE ASSERTED, and the second is the positive control: the
    # constant IS in the file, on context lines, in exactly the shape that
    # would fool a whole-text matcher. Without it this test would pass
    # identically against a 015 that no longer mentions the switch at all — and
    # a future reader would take its silence as evidence the rule is unneeded.
    patch = PATCH_DIR / "015-canvas-measure-text.patch"
    lines = patch.read_text(encoding="utf-8").splitlines()

    context_reads = [
        line
        for line in lines
        if line.startswith(" ") and "kDisableSpoofing" in line
    ]
    added_reads = [
        line
        for line in lines
        if line.startswith("+")
        and not line.startswith("+++")
        and "kDisableSpoofing" in line
    ]
    assert context_reads, (
        "015 no longer carries kDisableSpoofing on a CONTEXT line. The "
        "``+``-only rule on consumer_patches is still correct, but the "
        "counter-example proving it necessary has evaporated — do not read "
        "this test's silence as evidence the rule is unneeded."
    )
    assert not added_reads, (
        "015 now ADDS a read of kDisableSpoofing, so it is a genuine consumer "
        "and this test's premise is gone. Add it back to the consumer list in "
        "test_the_consumer_column_is_read_from_the_other_patches and correct "
        "the SEVEN figure in this file's docstring, the disable-spoofing cell "
        "and process.py's recorded reason — together."
    )

    assert "015" not in consumer_patches("kDisableSpoofing", "disable-spoofing"), (
        "015 is being counted as a consumer of --disable-spoofing on the "
        "strength of CONTEXT lines. consumer_patches is reading whole patch "
        "text instead of added lines."
    )

    # ⛔ AND THE CORRECTION DOES NOT MOVE THE HEADLINE, which is worth pinning
    # rather than assuming: dropping 015 leaves --disable-spoofing consumed by
    # seven patches and still unpassed, so the row does not change bucket and
    # the 7 / 2 / 4 split survives the methodology fix. A census whose headline
    # moved under a correction to its own method would be a different
    # conversation entirely.
    assert consumer_patches("kDisableSpoofing", "disable-spoofing") != []


def test_the_passed_column_is_read_off_a_real_launch(monkeypatch, tmp_path):
    # ⭐ THE PASSED COLUMN, and the reason it is argv rather than a grep.
    #
    # Seven switches, exactly. The negative half of this assertion is the
    # load-bearing one — and it is trustworthy ONLY because the positive half
    # is in the same set comparison, taken from the same launch.
    #
    # ⭐ WENT SIX -> SEVEN AT PS-392: fingerprint-device-memory. Note this
    # column is read off a REAL launch's argv, so it is the assertion that
    # actually proves the switch is passed rather than merely declared — the
    # distinction that matters here, because persona ships four switches that
    # are declared and propagated and read by nothing.
    #
    # ⛔ THE TRAP THIS AVOIDS, stated because a future reader WILL be tempted to
    # simplify this into a grep: `grep -rl -- "--disable-spoofing" src/` matches
    # today, because process.py carries a prose comment recording WHY that
    # switch is never passed. A grep-based column would read the recorded reason
    # for not passing a switch as proof that it IS passed, inverting the single
    # most important row in this file. argv cannot be fooled that way: a comment
    # is not an argument.
    passed = launch_switches(monkeypatch, tmp_path)
    actually_passed = {flag for flag in MATRIX if flag in passed}
    assert actually_passed == {
        "fingerprint",
        "fingerprint-platform",
        "fingerprint-brand",
        "fingerprint-brand-version",
        "fingerprint-device-memory",
        "fingerprint-hardware-concurrency",
        "timezone",
    }, (
        "the set of fingerprint switches a launch passes has changed. If a "
        "switch was ADDED, its cell must move to COVERED in the same commit; "
        "if one was REMOVED, its cell is now claiming a route the launch no "
        "longer takes."
    )

    # ⛔ THE ROW THAT MUST NEVER MOVE. Asserted separately from the set above so
    # it reads as the standing prohibition it is rather than as one absence
    # among five.
    assert "disable-spoofing" not in passed, (
        "--disable-spoofing is on the command line. ⚠️ Read the mechanism "
        "before assuming this is harmless: the switch is a VALUE-KEYED "
        "selective disable, so a BARE --disable-spoofing is inert through all "
        "seven consumers (003, 006, 011, 012, 013, 014, 016) — but "
        "--disable-spoofing=canvas,gpu,audio,font,clientrects stands those "
        "patches down one masking family per token. launch_switches strips "
        "=value deliberately, so this assertion catches BOTH forms. Do not "
        "pass it."
    )


def test_the_grep_shortcut_this_file_refuses_would_be_wrong_today():
    # The refusal above, DEMONSTRATED rather than merely asserted. A future
    # agent reading `launch_switches` will wonder whether spawning a launch is
    # over-engineering for "does this string appear in src/". This test answers
    # by showing the shortcut giving the WRONG answer on the live tree.
    #
    # It is deliberately coupled to nothing: if the process.py comment is one
    # day reworded so the literal no longer appears, this test fails and its
    # own premise is what needs re-reading — which is the correct outcome, since
    # at that point the shortcut's counter-example has evaporated and the
    # docstring claiming one would be stale.
    source = (REPO_ROOT / "src/services/browser/process.py").read_text(
        encoding="utf-8"
    )
    assert "--disable-spoofing" in source, (
        "process.py no longer names --disable-spoofing anywhere, so the "
        "counter-example this file's docstring cites is gone. Re-read that "
        "docstring: either the recorded reason was deleted (a real problem, "
        "and test_recorded_reasons_still_in_tree will say so) or it was "
        "reworded (harmless, but the claim about greps needs restating)."
    )
    assert MATRIX["disable-spoofing"][0] == NOT_COVERED_RECORDED, (
        "the switch a grep would mis-read as passed is no longer the switch "
        "this file says is deliberately unpassed."
    )


def test_the_split_is_reproduced_as_data(monkeypatch, tmp_path):
    # AC1: the split, DERIVED from the three columns rather than written down —
    # 7 FULLY-WIRED / 2 CONSUMED-NOT-PASSED / 4 DECLARED-NEVER-CONSUMED, named
    # rather than ordered so the headline cannot be misread off a bare
    # sequence. This is what the ticket commissioned — it read 4 / 4 / 4 in the
    # same three buckets when the work was scoped, and THREE rows have moved
    # since (PS-356 and PS-354, both closed by a human finding one row by
    # hand; then PS-392, which DECLARED a new switch and so moved the total
    # from twelve to thirteen). That drift is the argument for pinning the
    # census as data.
    #
    # ⚠️ THE SPLIT SURVIVED A CORRECTION TO THE CENSUS'S OWN METHOD. A code
    # review found consumer_patches reading whole patch text, which credited
    # 015 as a consumer of --disable-spoofing on context lines alone. Fixing it
    # to ``+``-only drops that row's count from 8 to 7 and moves NOTHING here:
    # the switch is still consumed and still unpassed, so it stays in the
    # second bucket and these three assertions are unchanged. A census whose
    # headline had moved under a fix to its own method would be a different
    # conversation, so this is stated rather than assumed.
    declared = declared_switches()
    passed = launch_switches(monkeypatch, tmp_path)

    fully_wired, consumed_not_passed, declared_not_consumed = [], [], []
    for constant, flag in declared.items():
        has_consumer = bool(consumer_patches(constant, flag))
        is_passed = flag in passed
        if has_consumer and is_passed:
            fully_wired.append(flag)
        elif has_consumer:
            consumed_not_passed.append(flag)
        else:
            declared_not_consumed.append(flag)

    assert sorted(fully_wired) == sorted(
        [
            "fingerprint",
            "fingerprint-brand",
            "fingerprint-brand-version",
            "fingerprint-device-memory",
            "fingerprint-hardware-concurrency",
            "fingerprint-platform",
            "timezone",
        ]
    )
    assert sorted(consumed_not_passed) == sorted(
        [
            "disable-spoofing",
            "fingerprint-platform-version",
        ]
    )
    assert sorted(declared_not_consumed) == sorted(
        [
            "fingerprint-device-scale-factor",
            "fingerprint-location",
            "fingerprint-screen-height",
            "fingerprint-screen-width",
        ]
    ), (
        "the set of switches DECLARED BY 000 AND READ BY NO PATCH has changed: "
        f"{sorted(declared_not_consumed)}. A switch entering this bucket is "
        "declared and wired to nothing — the engine will accept and discard it "
        "in silence, so it needs a position, not a launch flag. A switch "
        "leaving it means somebody wrote the C++ read-side and its cell should "
        "no longer read COVERED_ELSEWHERE."
    )
    # The three buckets partition the THIRTEEN — no row may fall through, and a
    # row counted twice would let the arithmetic look right while a switch went
    # unexamined. Went 12 -> 13 at PS-392 (fingerprint-device-memory, which
    # enters the fully_wired bucket: declared, consumed by 005, passed).
    assert (
        len(fully_wired) + len(consumed_not_passed) + len(declared_not_consumed)
    ) == len(declared) == 13

    # ⛔ A PASSED SWITCH THAT NOBODY CONSUMES IS THE WORST CELL IN THE TABLE and
    # must never appear silently: it is a flag the engine accepts and discards
    # while the launch claims it as coverage — the precise state PS-344
    # measured for the screen trio. Nothing is in that state today (all four
    # unconsumed switches are also unpassed), and this asserts it stays that
    # way.
    assert not [f for f in declared_not_consumed if f in passed], (
        "a switch with NO consumer in the patch set is being passed at launch. "
        "The engine will accept and discard it in silence, and the launch will "
        "look like it is spoofing something it is not."
    )


# --- anti-rot: the recorded reasons and readings must still exist ------------


def test_recorded_reasons_still_in_tree():
    # A NOT_COVERED_RECORDED cell quotes a reason the TREE holds. If that
    # sentence is deleted or reworded, the cell silently degrades into an
    # unexplained absence — the exact confusion this matrix exists to remove.
    # Compared with whitespace COLLAPSED on both sides (the masking matrix's
    # convention, imported with its helper): a re-wrap moves newlines without
    # changing a word, and a test that went red on a re-flow would train its
    # reader to re-quote reflexively — the habit that lets a genuine rewording
    # through. A word change still fails.
    for flag, (path, quote) in RECORDED_REASON_SOURCES.items():
        assert MATRIX[flag][0] == NOT_COVERED_RECORDED
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        assert _collapse(quote) in _collapse(text), (
            f"the recorded reason for the {flag!r} cell is gone from {path}. "
            "Either restore it or restate the cell's position — do not leave a "
            "cell citing a reason the tree no longer holds."
        )

    # ⛔ THE SECOND CLAUSE, pinned separately. See RECORDED_REASON_SECOND_
    # CLAUSES for why one quote cannot hold this cell honest: the mechanism
    # (bare form inert) and the prohibition (valued form forbidden) are
    # separable sentences, and deleting either one leaves the cell asserting
    # something the tree no longer says. The valued-form half is the one that
    # actually protects somebody.
    for flag, (path, quote) in RECORDED_REASON_SECOND_CLAUSES.items():
        assert MATRIX[flag][0] == NOT_COVERED_RECORDED
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        assert _collapse(quote) in _collapse(text), (
            f"the VALUED-FORM prohibition for the {flag!r} cell is gone from "
            f"{path}. The inertness sentence alone would leave a reader "
            "believing the bare flag is the danger — it is not; "
            "--disable-spoofing=canvas is. Restore the warning."
        )


def test_the_screen_cells_name_a_route_that_still_exists():
    # A COVERED_ELSEWHERE cell is a claim that the vector is covered SOMEWHERE
    # ELSE, and it is honest only while that somewhere-else exists. If
    # device_ext.py stopped pinning the screen, these three cells would still
    # read "covered elsewhere" over a vector nothing covers at all — a strictly
    # worse state than an admitted gap, because it reads as resolved.
    path, quote = SCREEN_ROUTE_SOURCE
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    assert _collapse(quote) in _collapse(text), (
        f"{path} no longer states why it pins devicePixelRatio. The three "
        "screen cells claim it as their covering route — re-read them before "
        "restoring the sentence."
    )
    for flag in (
        "fingerprint-screen-width",
        "fingerprint-screen-height",
        "fingerprint-device-scale-factor",
    ):
        assert MATRIX[flag][0] == COVERED_ELSEWHERE
        assert "device_ext.py" in MATRIX[flag][1], (
            f"the {flag!r} cell is COVERED_ELSEWHERE but no longer names the "
            "route. COVERED_ELSEWHERE that cannot name one is NOT_ESTABLISHED."
        )


def test_the_switch_deadness_readings_still_in_tree():
    # The measurements the screen cells rest on. Both engines are cited on
    # purpose: PS-344 is the reading on the PUBLISHED 152 binary and is what the
    # cells actually claim, while PS-301 is the 144 trial-build predecessor
    # showing the defect did not arrive with the shipped engine.
    #
    # ⚠️ PS-301's numbers may NEVER be quoted as 152 numbers — that report says
    # so itself — which is exactly why the 152 reading is the primary citation
    # and this test pins both rather than either.
    for label, (path, quote) in SWITCH_DEADNESS_READINGS.items():
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        assert _collapse(quote) in _collapse(text), (
            f"the {label} reading behind the screen-switch cells is gone from "
            f"{path}. Those cells assert a MEASURED inertness; without the "
            "reading they assert a guess."
        )


def test_the_location_cell_rests_on_a_refusal_that_is_still_recorded():
    # ⭐ THE UNUSUAL GUARD, and the one most worth understanding.
    #
    # fingerprint-location is STATICALLY IDENTICAL to its three dead siblings:
    # declared, consumed by nobody, never passed. The ONLY thing keeping it an
    # honest unknown rather than a fourth dead switch is that PS-301 explicitly
    # refused to claim it, having run no geolocation probe. So this cell's
    # honesty depends on a REFUSAL surviving in the tree — and if those
    # sentences vanished, a future reader would close the cell by analogy with
    # its neighbours and record a verdict nobody ever measured.
    for label, (path, quote) in LOCATION_REFUSAL_SOURCES.items():
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        assert _collapse(quote) in _collapse(text), (
            f"PS-301's {label!r} disclaimer is gone from {path}. The "
            "fingerprint-location cell is NOT_ESTABLISHED precisely because "
            "that refusal exists; without it, the cell would be closed by "
            "analogy with three siblings it only RESEMBLES."
        )
    assert MATRIX["fingerprint-location"][0] == NOT_ESTABLISHED


# --- shape invariants: an unknown may never read as coverage -----------------


@pytest.mark.parametrize("flag", sorted(MATRIX))
def test_every_cell_states_a_position_and_its_evidence(flag):
    # AC2's structural guarantee. Every row carries one of the five positions
    # and a non-empty note — there is no way to add a switch to this matrix and
    # leave it blank, and a blank would read as "no problem" rather than as
    # "nobody looked".
    position, note = MATRIX[flag]
    assert position in POSITIONS
    assert note.strip(), f"{flag} states a position with no evidence"


def test_the_open_cells_are_the_deliverable_and_are_named():
    # AC3. The headline, pinned AS DATA so it cannot drift out of the docstring.
    # These are the switches nobody has established a position on — the actual
    # finding, and the thing a future slice closes ONE AT A TIME, deleting each
    # name here in the commit that establishes it.
    #
    # ⛔ THE SET SHRINKS ONLY AGAINST A READING. Both remaining cells are open
    # for the same reason and it is not laziness: each needs a MEASUREMENT this
    # ticket did not take (no browser was launched, no engine was built), and
    # the tree holds no prior one. Closing either by argument would mint exactly
    # the kind of unmeasured verdict the five-position vocabulary exists to
    # refuse.
    unknown = {flag for flag, (position, _) in MATRIX.items() if position == NOT_ESTABLISHED}
    assert unknown == {
        "fingerprint-location",
        "fingerprint-platform-version",
        # NOTHING HAS LEFT THIS SET YET — this file is the census that created
        # it. When one does, delete its name here in the SAME commit that
        # records the reading, and say in the cell what was measured. The two
        # rows that WOULD have been here (fingerprint-brand-version,
        # fingerprint-hardware-concurrency) were closed by PS-356 and PS-354
        # before this file was written, which is the pattern: a row leaves this
        # set when somebody looks.
    }


def test_no_cell_claims_coverage_without_a_route(monkeypatch, tmp_path):
    # The invariant that makes the matrix worth asserting: a COVERED cell must
    # be traceable to a route the product actually TAKES — a switch on a real
    # launch's command line — and never to prose. A cell that cannot name one
    # is COVERED_ELSEWHERE (which names a different route) or NOT_ESTABLISHED.
    #
    # The converse is asserted too and is the sharper half: a switch the launch
    # DOES pass may not sit in a cell that denies coverage, which is what would
    # happen if somebody quietly wired --disable-spoofing while its cell still
    # read NOT_COVERED_RECORDED.
    passed = launch_switches(monkeypatch, tmp_path)
    declared = declared_switches()
    for constant, flag in declared.items():
        # An unpositioned switch is ALREADY reported, by name and with an
        # instruction, by test_the_matrix_covers_every_declared_switch. Raising
        # a bare KeyError here would bury that message under a traceback in the
        # same run — so this test states its dependency and steps aside.
        if flag not in MATRIX:
            continue
        position, _ = MATRIX[flag]
        if position == COVERED:
            assert flag in passed, (
                f"{flag} claims COVERED but the launch does not pass it. "
                "Either the flag was dropped from spawn_browser or the cell is "
                "claiming a route the product no longer takes."
            )
        else:
            assert flag not in passed, (
                f"{flag} is passed at launch but its cell reads {position!r}. "
                "A passed switch is COVERED — unless it is passed and "
                "discarded, in which case say so explicitly rather than "
                "leaving the two halves disagreeing."
            )
        if position == COVERED_ELSEWHERE:
            assert consumer_patches(constant, flag) == [], (
                f"{flag} is COVERED_ELSEWHERE, a position that rests on the "
                "engine reading it NOWHERE. It now has a consumer, so the "
                "engine may well be the authority after all — re-read the "
                "cell."
            )
