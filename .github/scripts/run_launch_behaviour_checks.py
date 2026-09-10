"""Run the LAUNCH-backed behavioural checks and let their verdict gate the job.

WHY THIS EXISTS (PS-336)
------------------------
PS-315 gave ``behaviour_cli`` an execution venue for the ``--skip-launch`` lane
ONLY. The ``needs_launch=True`` checks — the ones that observe a LAUNCHED
profile — still had no venue anywhere, and ``ci.yml`` said so in the step it
added: *"The four launch-backed checks and any display provisioning are
separate work."* This is that work.

⭐ THE SHARPER HALF, AND WHY THIS IS NOT MERELY A GAP. Re-measured at this
branch's own base (``db6d528``), planting ``raise AssertionError("MUT-PLANTED")``
at the head of ONE dark body at a time and running every suite file that
references the module (240 tests):

    _run_restart_continuity        -> 240 passed   (mutant SURVIVES)
    _run_benign_edit_stability     -> 240 passed   (mutant SURVIVES)
    _run_trash_restore_and_wipe    -> 240 passed   (mutant SURVIVES)

    CONTROLS, same probe shape, so the three greens are a coverage gap and not
    a suite that cannot kill anything in this file:
    _run_two_profile_unlinkability -> 4 failed, 236 passed   (killed)
    _run_launch_refuses_broken_geography -> 6 failed, 234 passed (killed)

And PS-315's own gate, run over each sabotaged body in turn::

    GATE EXIT: 0 — "3 passed, 0 finding(s), 0 could not run"
    verdict: every selected check ran, was shown capable of failing, and the
             behaviour held

Three bodies that cannot execute at all, and a fresh gate printing "the
behaviour held" over them. Its ``EXPECTED_CHECKS`` floor is CORRECT and is
scoped to its own lane by construction, so it structurally cannot notice.

⭐ WHY THIS LANE IS THREE CHECKS AND NOT FOUR — THE LOAD-BEARING DECISION HERE
------------------------------------------------------------------------------
``two-profile-unlinkability`` is the fourth ``needs_launch=True`` check and it
is deliberately neither SELECTED nor in this lane's FLOOR. Argued rather than
asserted, because "the venue runs only the checks it expects to pass" is a real
criticism and this has to answer it.

**It does not pass on the engine this project ships, and that is a recorded,
published fact — not a discovery and not a regression.** Measured on this
branch under a real display against the Personium ``firefox-20`` build,
4 browser launches::

    [FINDING] two-profile-unlinkability
      2 seed-derived vector(s) AGREE across two distinct profiles
        | window/canvas.readback: colliding   digest 4242351214
        | worker/canvas.readback: colliding   digest 4242351214

``readings/ps135-2026-08-24/EVIDENCE.md`` §8 predicts exactly this, verbatim —
*"the two-profile unlinkability check will report COLLIDING on window and
worker and go to FINDING on that engine"* — and §7 hands it to PS-2 as product
work. §3's control makes it a statement about canvas rather than about a broken
probe: on the SAME snapshots, audio.digest, webgl.readback, webgl.unmasked,
webgl.parameters and hardwareConcurrency all varied across seeds, so the
masking layer was live and correctly seeded. Canvas 2D is simply not spoofed on
firefox — ``--fingerprint=`` is chromium-only and the firefox arm returns at
``process.py:353``, well before it. The digest read here (4242351214) is
BIT-IDENTICAL to the one committed in that reading directory for all three
seeds, so this is the same measurement, re-observed.

**Including it would make this gate PERMANENTLY RED, which is the same defect
as permanently green wearing the other colour.** A gate nobody can ever make
green is a gate people learn to ignore and then delete; and worse, it destroys
this venue's ONLY job — a red that is always red cannot tell a newly-rotted
dark body from the known collision. Every one of the three mutants below would
turn it red, and so would a clean tree, which proves sensitivity to nothing.

**It is also not DARK, which is what this ticket is about.** The caller census,
re-run at this base over ``src/ tests/ .github/`` excluding the registry file::

    _run_restart_continuity         0 external callers
    _run_benign_edit_stability      0 external callers
    _run_trash_restore_and_wipe     0 external callers
    _run_two_profile_unlinkability  11 external callers   <- probes.py x2,
                                    test_ps232_child_frame_unlinkability.py x9,
                                    including `outcome = _run_two_profile_unlinkability(ctx)`

That fourth row is the DISCRIMINATING CONTROL that makes the three zeros real
rather than a bad pathspec — and it is also the reason this check is out of
scope: a test file drives its body directly, so it is the one launch-backed
body that was never dark. Only its VENUE was shared with the other three. The
mutation arms agree: sabotaging it kills 4 tests, sabotaging any of the three
kills none.

⛔ WHAT THIS IS NOT, AND THE LINE THAT MUST NOT BE CROSSED: the fourth check is
EXCLUDED FROM THE SELECTION, never "run and then forgiven". Adjudicating its
FINDING down to a pass would launder a 1 into a 0 and break the asymmetry that
is the entire safety argument for having adjudication at all — every
correction moves a verdict TOWARDS 2, never towards 0. That rule is pinned by
``test_no_corroboration_rule_can_make_the_job_greener``, and nothing here
weakens it. Excluding a check is visible in the report (it is simply not
there); forgiving one is invisible, which is why only the first is admissible.

⚠️ WHEN PS-2 FIXES THE CANVAS COLLISION, add ``two-profile-unlinkability`` to
both ``SELECTED_CHECKS`` and ``EXPECTED_CHECKS`` below in the same change. The
registry-agreement test in ``tests/test_ps336_launch_behaviour_venue.py`` names
it as a permitted omission and says why, so that edit is prompted by a test
rather than left to be remembered.

⭐ THE SECOND OMISSION, AND IT IS A DIFFERENT REASON — NOT A WIDENED CARVE-OUT
------------------------------------------------------------------------------
``no-process-survives-a-closed-session`` (PS-347) is the fifth
``needs_launch=True`` check and is likewise neither SELECTED nor in the FLOOR.
Its reason is NOT the one above, and collapsing the two into a single "known
exclusions" set would hide that — so they are recorded separately and pinned
separately.

**This lane provisions FIREFOX, and that check launches CHROMIUM by
construction.** The engine step calls
``src.services.engine.firefox.download_engine`` against ``engine-baseline.txt``
(today ``firefox-20``); no chromium binary exists on this runner. PS-347's
check launches ``os_type=linux`` — and therefore chromium — DELIBERATELY, and
its own registry entry argues why: the leak it guards is a property of the
WRAPPER, multi-process launch, so the same measurement taken on this lane's
firefox fixtures would be VACUOUS. It is the one launch-backed check whose
engine this venue does not have.

**Measured on this branch rather than reasoned, by removing the chromium engine
and running the check under a real display:**

    [CANNOT RUN] no-process-survives-a-closed-session
      the falsification could not run: FileNotFoundError:
      '/tmp/pb-j3rzu3m_/engine/fpchrome.AppImage'
    0 passed, 0 finding(s), 1 could not run    ->  EXIT 2

So including it would make this gate PERMANENTLY EXIT 2 — "nothing was
measured" — which is the precise failure this whole venue was built to remove,
and which this header already refuses in the ``two-profile-unlinkability`` case
under its other colour. The control that makes that a statement about the
VENUE rather than about a broken check: with the chromium engine present, the
same command on the same branch under the same display reports ``[PASS] ...
peak live tree: 10 process(es) ... survivors after terminate(): 0``, exit 0,
and its falsifier reports 9 survivors when descendant teardown is sabotaged.
The check works; this runner cannot host it.

⭐⭐ THAT EXCLUSION IS OVER (PS-383). THE LANE NOW PROVISIONS CHROMIUM AND THE
CHECK IS SELECTED. The paragraph above is kept rather than deleted because it
is the RECORD OF WHY IT WAS DARK, and because the two provisioning defects
below are only legible against it. What follows is what it took, measured on a
real engine (``personium-152.0.7977.75``, 202,193,400 bytes) rather than
reasoned — and BOTH of these produce the SAME ``exit 2`` the exclusion cites,
so a change that adds the download and stops there reproduces the omission's
own evidence instead of closing it.

  1. ⛔ THE ENGINE DOES NOT LIVE AT A FIXED PATH — IT FOLLOWS ``PERSONA_HOME``.
     ``config.ENGINE_DIR`` is ``_under_home("engine", "PERSONA_ENGINE_DIR")``,
     and ``main()`` below hands the child a FRESH ``PERSONA_HOME`` on every
     run. So an engine downloaded by the workflow lands in the WORKFLOW's home
     and the child resolves ``$PERSONA_HOME/engine/fpchrome.AppImage`` —
     a directory created seconds ago by ``mkdtemp``, which is empty. Measured::

         scratch home: /tmp/persona-behaviour-launch-ci-yp34liw3
         ENGINE_BINARY= /tmp/.../engine/fpchrome.AppImage    exists= False

     The cure is ``PERSONA_ENGINE_DIR`` — the documented override, pinned by
     ``tests/test_config_home.py`` and already used by the PS-301/PS-344
     launch readings for exactly this — set to a directory OUTSIDE the scratch
     home, so the engine survives the per-run home while the profile store
     does not. ⚠️ THE FIREFOX ARM HID THIS FOR THREE CHECKS: it resolves
     through ``~/.cache/invisible-playwright``, which is not under
     ``PERSONA_HOME`` at all, so this lane has never had an engine that could
     be relocated by its own scratch home until now.

  2. ⛔ THIS RUNNER'S OWN SCRATCH PREFIX IS 6 BYTES OVER THE CHROMIUM WALL.
     ``behaviour.py`` sizes ``default_scratch_home`` against the two
     socket-bound profile names and calls the terseness of its ``pb-`` prefix
     "load-bearing rather than a style preference". THIS FILE provisions its
     own home and never consulted that budget, because no check it selected
     bound a socket. Measured::

         /tmp/persona-behaviour-launch-ci-hv7nlra0  -> 41 bytes, budget  -6
         /tmp/pb-3bbkckgh                           -> 16 bytes, budget  19
         (the names need 5: 'p347a', 'p347b')

     and end-to-end, engine correctly placed, under a real display::

         [CANNOT RUN] no-process-survives-a-closed-session
           SELF-TEST FAILED … profile 'p347b' puts its process-singleton
           socket at 119 bytes, and the limit is 107 …
           0 passed, 0 finding(s), 1 could not run, 0 browser launch(es)

     That is ``_survivor_profile``'s PRE-LAUNCH refusal working exactly as
     designed, and it is a fact about THIS FILE's prefix, not about the check.
     ⛔ THE CURE IS THE SHORTER PREFIX, NEVER A LOOSER GUARD — not a longer
     ``sun_path``, not a shorter profile name, not a relaxed ``_MIN_LIVE_TREE``.
     A budget checked on one side only is a budget that fails on the other,
     which is the sentence ``TestSingletonSocketBudget`` already carries one
     venue over; ``_assert_name_budget`` below is this venue's half of it, and
     it REFUSES up front rather than letting a 90-second launch time out.

⛔ ``ci.yml``'s REFUSAL IS UNTOUCHED AND STAYS CORRECT. It argues (:427-455)
against folding ``browser_chromium`` into its SIX-INSTANCE ``tests`` matrix —
a 200 MB download six times per PR. That reasoning is about that matrix. This
is a dedicated single-instance lane on a cron, which is the venue
``engine-gpu-variance.yml`` established for precisely this trade and which this
workflow already was. The two are not in tension and neither was weakened.

⚠️ WHEN PS-2 FIXES THE CANVAS COLLISION, ``two-profile-unlinkability`` is the
ONE remaining omission and the same instruction applies to it. Its reason is
unrelated to anything above: it reports a real FINDING on the shipped firefox
engine, so requiring it would make this gate permanently RED — the opposite
failure mode from the one just closed, which is why the two were never
collapsed into a single carve-out.

WHY A SEPARATE FLOOR AND NOT AN EXTENSION OF PS-315'S
------------------------------------------------------
``tests/test_ps315_behaviour_gate.py::test_the_expected_checks_are_the_registry_no_launch_lane``
pins that constant by SET EQUALITY to ``{c.name for c in CHECKS if not
c.needs_launch}``. Adding a launch name to it turns that guard red exactly as
retiring one does (measured on this ticket before it was converted: ``1 failed,
33 deselected``), and the cheapest-looking repair — relaxing ``==`` to ``>=``,
or deleting the assertion — would RE-OPEN the empty-selection hole PS-315 was
reworked to close. So the launch lane gets a floor of its OWN, with its own
registry-agreement guard mirroring the existing one. Neither constant is
touched by the other.

HOW THE "SELECTS NOTHING, EXITS 0" HOLE IS CLOSED ON THIS PATH
---------------------------------------------------------------
Two independent mechanisms, and BOTH are load-bearing here because this lane
uses ``--check`` (unlike PS-315's, which uses ``--skip-launch``):

1. ``--check`` VALIDATES ITS NAMES. ``behaviour.run_checks`` raises on an
   unknown name BEFORE the environment guard, and ``behaviour.py``'s own
   comment above that branch says it "keeps the 'selects nothing, exits 0'
   hole closed on every path". So a check RENAMED or RETIRED out from under
   this lane cannot silently shrink the selection: the run refuses with
   ``unknown check(s): ...`` and lands on exit 2. That is the hole PS-315's
   ``--skip-launch`` path structurally could not close, and it is closed here
   by construction rather than by adjudication.
2. ⭐ THE FLOOR CLOSES IT AGAIN, INDEPENDENTLY, and that redundancy is
   deliberate: mechanism 1 protects against the REGISTRY changing, and nothing
   protects against THIS FILE being edited to select fewer checks. ``adjudicate``
   (imported from ``run_behaviour_checks`` — ONE owner for both lanes rather
   than a second copy of the rules) honours a 0 only when the report certifies
   every name in ``EXPECTED_CHECKS``. A selection narrowed by hand prints
   "1 passed" and exits 0; this gate reads that report, finds the other names
   missing, and exits 2 saying which ones.

An EMPTY floor is refused by ``adjudicate`` itself (PS-336 added that guard),
because a floor of no names is satisfied by a report certifying nothing — the
hole re-created inside the rule written to close it.

THE THREE EXIT CODES STAY THREE — see ``run_behaviour_checks.py`` for the full
argument, which this file deliberately does not restate in different words:

    0  every check in the floor ran, was falsified, and held
    1  a check RAN and the behaviour did NOT hold      -> a FINDING
    2  a check COULD NOT RUN. Nothing was certified.   -> NOT a pass

WHICH ENGINE THIS LANE MEASURES, AND WHY
-----------------------------------------
The PERSONIUM build named by ``engine-baseline.txt`` (today ``firefox-20``),
downloaded by ``src.services.engine.firefox.download_engine`` — the same bytes
``updater`` hands an operator's app. NOT the stock playwright Firefox that
``ci.yml`` provisions with ``python -m playwright install firefox``.

The two are different artifacts and the answer changes what a green means. The
checks here observe a MASKED identity — a restart presenting the same machine,
an edit not moving it — and every one of those properties is produced by
persona's own layer running on the patched build. Stock Firefox carries none of
it, so a pass on stock would certify a browser nobody ships. The workflow
therefore provisions the engine the way ``engine-autoupdate.yml`` does, which
is the precedent for "the engine users actually receive".

⚠️ Verified rather than assumed on this branch: the engine is resolved through
``invisible_playwright``'s own cache layout (``~/.cache/invisible-playwright``),
NOT through playwright's (``~/.cache/ms-playwright``, which does not exist on a
machine where these checks pass). ``python -m playwright install firefox``
would populate the latter and leave the former empty.

THE DISPLAY IS PROVISIONED BECAUSE THE PREFLIGHT IS ENGINE-BLIND
-----------------------------------------------------------------
``run_checks`` calls ``require_display()`` whenever ANY selected check has
``needs_launch=True``, before any profile's engine is resolved.
``baseline._require_display`` raises on Linux with ``DISPLAY`` unset, and
``behaviour_cli`` translates that into exit 2 rather than letting Python's
default exit 1 alias ``EXIT_FINDING``. So without Xvfb this lane is exit 2 on
every run — a permanent "nothing was measured", which is the failure this whole
instrument exists to remove. The workflow runs it under ``xvfb-run -a``, the
same shape ``engine-autoupdate.yml`` and ``engine-gpu-variance.yml`` use.

THE SAFETY GUARD IS HONOURED, NOT DEFEATED — and this is the lane that actually
wipes: ``trash-restore-and-wipe`` calls ``wipe_all_profiles``, which is
irreversible. The scratch home is provisioned with ``tempfile.mkdtemp`` (a path
python resolves the same way it was written, which a bash ``mktemp -d`` under
git-bash is not) and removed afterwards, and ``require_scratch_home`` still runs
on its own terms — it still refuses an unset ``PERSONA_HOME`` and still refuses
the default store. Nothing here sets a flag that turns that refusal off.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: ONE owner for the corroboration rules, imported rather than copied. A second
#: hand-written copy is how two gates end up with two different definitions of
#: "a 0 was earned" — and the copy is always the one that quietly stops being
#: updated. Loaded by path because `.github/scripts/` is not a package and must
#: not become one: CI invokes these files directly.
_SIBLING = Path(__file__).resolve().parent / "run_behaviour_checks.py"


def _load_sibling():
    spec = importlib.util.spec_from_file_location("_ps315_runner_shared", _SIBLING)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(f"cannot load {_SIBLING}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_shared = _load_sibling()

echo = _shared.echo
adjudicate = _shared.adjudicate
summary = _shared.summary
passed_checks = _shared.passed_checks
VERDICTS = _shared.VERDICTS
REEXEC_FLAG = _shared.REEXEC_FLAG

MODULE = "src.services.verify.behaviour_cli"
MODULE_FILE = REPO_ROOT.joinpath(*MODULE.split(".")).with_suffix(".py")

#: THE CHECKS THIS LANE SELECTS — the launch-backed bodies that were DARK
#: before this venue existed. ONE launch-backed check is absent DELIBERATELY
#: (`two-profile-unlinkability`, a permanent FINDING on the shipped engine);
#: the header argues it at length and a test pins it by name. `--check` is
#: repeatable and VALIDATES each name, so a rename or a retirement fails loudly
#: here instead of silently shrinking the lane.
#:
#: ⭐ FOUR SINCE PS-383. `no-process-survives-a-closed-session` is a CHROMIUM
#: launch and this lane now provisions chromium, so the second omission — which
#: was never about the check and always about the venue — is closed. Its entry
#: is deleted from `DOCUMENTED_OMISSIONS` in the same change, as the header's
#: standing instruction required. ⛔ It is a WRAPPER launch that binds a
#: process-singleton socket, which is why `main()` below now sizes its scratch
#: home against the profile-name budget and pins `PERSONA_ENGINE_DIR` outside
#: it; read the header's two numbered defects before touching either.
SELECTED_CHECKS = (
    "restart-continuity",
    "benign-edit-stability",
    "trash-restore-and-wipe",
    "no-process-survives-a-closed-session",
)

COMMAND = [
    sys.executable,
    "-m",
    MODULE,
    "run",
    *[arg for name in SELECTED_CHECKS for arg in ("--check", name)],
]

#: WHAT THIS LANE MUST CERTIFY BEFORE A 0 COUNTS AS A PASS.
#:
#: Listed by NAME rather than counted, and EXPLICITLY rather than derived from
#: the registry, for the same reason PS-315's constant is: deriving it would
#: make this agree with an EMPTY registry by construction, which is the failure
#: being guarded against, and a name says WHICH check went missing.
#:
#: EQUAL TO `SELECTED_CHECKS` today, and WRITTEN OUT rather than aliased to it
#: — because they answer different questions ("what did we ask for?" versus
#: "what must have passed?") and a future lane that runs a check without
#: requiring its pass is a legitimate shape. A test pins them equal so the pair
#: cannot drift silently while that stays true.
#:
#: ⛔ DO NOT WRITE `EXPECTED_CHECKS = SELECTED_CHECKS`. That is what this file
#: shipped at PS-336 round 1, and it is not a copy — it is the SAME tuple
#: object, which silently disables mechanism 2 below. The floor's INDEPENDENCE
#: from the selection *is* mechanism 2: an alias makes a selection narrowed by
#: hand narrow the floor with it, so a lane certifying 1 of 3 finds nothing
#: missing and exits 0 — "the behaviour held" — which is precisely PS-315's
#: hole re-created inside the mechanism written to close it. It also makes
#: `test_the_selection_and_the_floor_agree` read `x == x`, so the guard that
#: was supposed to notice the drift cannot fail for any value.
#: Measured on this branch, selection narrowed to `("restart-continuity",)`:
#: aliased -> `adjudicate(0, "1 passed, ...")` = exit 0, and only 4 of 37 tests
#: fire, neither of them that guard; de-aliased -> exit 2 naming
#: `benign-edit-stability, trash-restore-and-wipe`, and that guard goes RED.
#:
#: ⛔ DO NOT relax the registry-agreement test that guards this constant, and
#: do not merge this floor into PS-315's. They are two lanes with two floors,
#: and PS-315's is pinned by SET EQUALITY to the no-launch lane — adding a name
#: there turns that guard red (measured: `1 failed, 33 deselected`), and the
#: cheapest-looking repair re-opens the empty-selection hole PS-315 was
#: reworked to close.
EXPECTED_CHECKS = (
    "restart-continuity",
    "benign-edit-stability",
    "trash-restore-and-wipe",
    "no-process-survives-a-closed-session",
)

#: Recorded so a reader of a red run knows what the lane costs and can tell a
#: genuinely wedged launch from a slow one. Measured on this branch, one host,
#: real engine, under xvfb-run, n=1 each:
#:
#:     the 3 selected checks          66.2s, 12 browser launches, exit 0
#:     the whole registry (all 7)     93.5s, 16 browser launches, exit 1
#:     engine download (cold cache)   ~10s, 584 MB extracted
#:
#: ⭐ PS-383 ADDS A SECOND ENGINE AND A FOURTH CHECK, and the figures above are
#: NOT re-stated from a guess. The chromium download was measured in a
#: container on this branch — `personium-152.0.7977.75`, 202,193,400 bytes,
#: fetched and verified in ~35s — and the survivor check's own cost is bounded
#: by its thresholds rather than by a stopwatch: `_TREE_GROW_TIMEOUT` is 90s per
#: launch and it takes two, so its WORST case is ~3 minutes and its measured
#: case (the tree settles at 10 within ~7s, per `_STABLE_SAMPLES`' own note) is
#: ~30s. So the lane's ceiling is roughly 66s + 3min + two downloads ≈ 6 min
#: against a 45-minute budget. ⛔ THE CHROMIUM FIGURE IS A DOWNLOAD
#: MEASUREMENT, NOT A LAUNCH ONE: the container that took it cannot launch
#: chromium at all (no unprivileged user namespaces — the engine exits FATAL
#: "No usable sandbox!" ~3s in), which is a fact about that sandbox and not
#: about the runner. See the workflow header for the CI-side figures.
_MEASURED_NOTE = (
    "measured on one linux host at n=1: the three firefox checks run in ~66s "
    "over 12 browser launches, plus ~10s to fetch the firefox engine; the "
    "chromium survivor check adds two wrapper launches bounded at 90s each "
    "and a ~200 MB engine download"
)

#: THE SCRATCH-HOME PREFIX, AND ITS LENGTH IS A CORRECTNESS PROPERTY.
#:
#: ⛔ DO NOT LENGTHEN THIS BACK. It was `persona-behaviour-launch-ci-` until
#: PS-383, which was fine while every selected check launched FIREFOX — firefox
#: binds no process-singleton socket, so no profile path here was measured
#: against `sun_path`. `no-process-survives-a-closed-session` launches CHROMIUM,
#: which does, and the old prefix produced a 41-byte home whose profile-name
#: budget was NEGATIVE SIX against names needing five. The lane would have been
#: permanently exit 2 for a reason that has nothing to do with the product.
#:
#: This mirrors `behaviour.default_scratch_home`'s own `pb-` prefix and the
#: sentence beside it: "every byte here is a byte taken off the profile-name
#: budget above". `_assert_name_budget` below checks the result rather than
#: trusting this constant, because a budget checked on one side only is a
#: budget that fails on the other.
_SCRATCH_PREFIX = "pb-ci-"

#: WHERE THE CHROMIUM ENGINE LIVES, and it must be OUTSIDE the scratch home.
#:
#: `config.ENGINE_DIR` is `_under_home("engine", "PERSONA_ENGINE_DIR")`, so
#: without this the child resolves the engine under the throwaway home this
#: script just created — an empty directory — and reports the exact
#: `FileNotFoundError: .../fpchrome.AppImage` that kept this check out of the
#: lane in the first place. The workflow downloads into this path; the child
#: reads from it. The PROFILE STORE stays in the scratch home and is still
#: destroyed after every run — only the 200 MB binary is exempted, and it is
#: exempted because it is provisioning rather than fixture state.
#:
#: Honoured only when the workflow (or an operator) has not already set it.
_ENGINE_DIR_ENV = "PERSONA_ENGINE_DIR"


#: WHERE THE SCRATCH HOME IS CREATED, and the base is CHOSEN rather than
#: accepted — the same rule `behaviour.default_scratch_home` states and for the
#: same measured reason.
#:
#: ⛔ `tempfile.mkdtemp()` ALONE HONOURS TMPDIR, AND THE BASES REAL RUNNERS HAND
#: IT ARE LONG. Measured on this branch's own CI, macos-latest:
#:
#:     /var/folders/d8/hvxvltxn0fl4rmnd52sncbth0000gn/T/pb-ci-fvhc6jzr
#:         -> 63 bytes, budget -28   (the names need 5)
#:
#: i.e. the prefix was already short enough and the BASE spent the whole budget
#: anyway. Shortening the prefix further cannot fix that — 53 of those bytes are
#: macOS's, before this script contributes anything. So the base is picked the
#: way `behaviour.py` picks it: shortest writable candidate first, `/tmp` asked
#: for rather than assumed (a hardened runner can have neither), and the result
#: still VERIFIED by `_assert_name_budget` rather than trusted.
#:
#: ⚠️ THE VERIFICATION IS NOT REDUNDANT WITH THE CHOICE. Choosing the shortest
#: base makes the common runner work; measuring the result is what turns a
#: runner where even that is too long into a sentence naming the cure instead
#: of a 90-second wait for a tree that cannot appear.
def _scratch_base() -> "str | None":
    """The shortest writable temporary base, or None to let mkdtemp decide."""
    candidates = [tempfile.gettempdir()]
    if os.name == "posix":
        candidates.append("/tmp")

    bases = sorted(
        {c for c in candidates if os.path.isdir(c) and os.access(c, os.W_OK)},
        key=len,
    )
    return bases[0] if bases else None


def _assert_name_budget(home: str) -> "str | None":
    """Refuse a scratch home no chromium profile can launch under.

    Returns an operator-facing message, or None when the home fits.

    ⭐ CHECKED HERE RATHER THAN LEFT TO THE CHECK, though the check does refuse
    on its own (`_survivor_profile` measures the same arithmetic before
    launching). Both are wanted and neither is redundant: the check's refusal
    protects an operator who passed `--home` by hand, and this one protects the
    VENUE — it is this file that chooses the prefix, so it is this file that
    must be shown to have chosen a workable one. A CI failure that says "your
    runner's temp prefix is six bytes too long" is a different and far more
    actionable sentence than one that says a browser tree never settled.

    ⛔ THE FIX FOR A FAILURE HERE IS A SHORTER PREFIX, NEVER A SHORTER PROFILE
    NAME AND NEVER A LOOSER GUARD. The names belong to the check and their
    length is already argued where they live.
    """
    try:
        from src.services.verify.behaviour import (
            profile_name_budget,
            singleton_socket_is_bound,
        )
        from src.services.verify.behaviour_checks import (
            SOCKET_BOUND_PROFILE_NAMES,
        )
    except Exception:  # pragma: no cover - the harness check below reports it
        # The import failing is the harness being unimportable, which `main`
        # already reports with a better message a few lines down. Do not
        # manufacture a second, worse one here.
        return None

    if not singleton_socket_is_bound():
        return None

    needed = max((len(n) for n in SOCKET_BOUND_PROFILE_NAMES), default=0)
    budget = profile_name_budget(home)
    if budget >= needed:
        return None

    return (
        f"CANNOT RUN: the scratch home {home!r} is {len(home)} bytes, which "
        f"leaves {budget} byte(s) for a profile name — and this lane launches "
        f"chromium under names needing {needed}. Chromium's process singleton "
        "binds a UNIX socket under the profile and the engine does not degrade "
        "when it does not fit: it exits FATAL 'Socket path too long' seconds "
        "into the launch, which looks from outside like a browser that started "
        "and vanished. Nothing would be measured.\n"
        f"Shorten this script's scratch prefix ({_SCRATCH_PREFIX!r}) or point "
        "TMPDIR at a shorter base. Do NOT shorten the check's profile names "
        "and do NOT relax its guards — the budget is a property of this home.\n"
        "Nothing was certified.\n"
    )


def main() -> int:
    if not MODULE_FILE.is_file():
        echo(
            f"CANNOT RUN: {MODULE_FILE} does not exist, so this gate is pointed "
            "at nothing. Nothing was certified.\n",
            err=True,
        )
        return 2

    # A DISPLAY IS REQUIRED AND ITS ABSENCE IS REPORTED HERE RATHER THAN
    # ARRIVING AS AN OPAQUE 2. `run_checks` calls require_display() as a
    # preflight, so without one every run of this lane is exit 2 — correct, but
    # indistinguishable from "the product could not be observed". Saying it in
    # this gate's own words means a misconfigured workflow reads as a
    # misconfigured workflow.
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        echo(
            "CANNOT RUN: no DISPLAY. Every check in this lane launches a real "
            "browser, and `run_checks` refuses the whole run when any selected "
            "check needs one. Run this under a virtual display:\n"
            "    xvfb-run -a python .github/scripts/run_launch_behaviour_checks.py\n"
            "(install with: sudo apt-get install -y xvfb)\n"
            "Nothing was certified.\n",
            err=True,
        )
        return 2

    home = tempfile.mkdtemp(prefix=_SCRATCH_PREFIX, dir=_scratch_base())

    # ⛔ BEFORE ANYTHING LAUNCHES. A home that is too long for the chromium
    # profile names produces a FATAL several seconds into a launch, which every
    # watcher reads as a browser that started and vanished. Refusing here turns
    # that into a sentence naming the cure, and it costs nothing on the path
    # where the home fits.
    too_long = _assert_name_budget(home)
    if too_long is not None:
        shutil.rmtree(home, ignore_errors=True)
        echo(too_long, err=True)
        return 2

    env = dict(os.environ)
    env["PERSONA_HOME"] = home
    # ⛔ THE ENGINE MUST NOT FOLLOW THE HOME. `config.ENGINE_DIR` is
    # `_under_home("engine", "PERSONA_ENGINE_DIR")`, so without this the child
    # looks for the chromium binary inside the throwaway directory created one
    # line above — which is empty — and reports the FileNotFoundError this
    # lane's own header quotes as the reason the check was excluded. The
    # profile store still lives in (and dies with) the scratch home; only the
    # engine is pinned outside it. An explicit setting from the workflow or an
    # operator wins, so a caller who has already placed the engine is honoured.
    env.setdefault(
        _ENGINE_DIR_ENV,
        os.path.join(os.path.expanduser("~"), ".persona-ci-engine"),
    )
    # The home is already set in the child's environment BEFORE the process
    # starts, which is the exact condition behaviour_cli's re-exec exists to
    # guarantee — and os.execve has spawn-and-exit semantics on Windows, so a
    # gate whose non-zero exit does not reach the runner is decoration. This
    # does NOT defeat require_scratch_home, which still runs and still refuses
    # an unset PERSONA_HOME and the default store.
    env[REEXEC_FLAG] = "1"
    # The ENCODE half of the pair; `encoding=` on subprocess.run below is the
    # DECODE half. Pinning either alone leaves the two ends disagreeing by
    # construction — see run_behaviour_checks.py for the measured cp1252 case.
    env["PYTHONIOENCODING"] = "utf-8"

    echo(f"scratch PERSONA_HOME={home}\n")
    echo(f"engine dir={env[_ENGINE_DIR_ENV]}\n")
    echo(f"cwd={REPO_ROOT}\n")
    echo(f"DISPLAY=[{os.environ.get('DISPLAY', '<unset>')}]\n")
    echo(f"$ {' '.join(COMMAND[1:])}\n")
    echo(f"floor: {', '.join(EXPECTED_CHECKS)}\n")
    echo(f"({_MEASURED_NOTE})\n")

    started = time.monotonic()
    try:
        completed = subprocess.run(
            COMMAND,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        # These checks write profiles, certificate material and trash entries
        # into the scratch home, and one of them wipes it. Removing it keeps a
        # cached runner from carrying one run's fixtures into the next: the
        # checks create FIXED-NAME profiles, so a reused home makes the second
        # run collide with the first's leftovers and report CANNOT RUN for a
        # reason that has nothing to do with the product.
        shutil.rmtree(home, ignore_errors=True)
    elapsed = time.monotonic() - started

    output = completed.stdout or ""
    echo(output)

    rc = completed.returncode
    echo(f"\nlaunch behavioural checks finished in {elapsed:.2f}s, exit {rc}\n")

    code, note = adjudicate(rc, output, EXPECTED_CHECKS)

    verdict = VERDICTS.get(code)
    if verdict is not None and note is None:
        echo(f"verdict: {verdict}\n")
    if note is not None:
        echo(note + "\n", err=True)

    if code != 0:
        echo(
            "FAILING THE JOB. Exit 1 and exit 2 are different failures and are "
            "deliberately not collapsed: 1 says the product misbehaved, 2 says "
            "the check could not look. Neither is a pass.\n",
            err=True,
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
