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
it as the ONE permitted omission and says why, so that edit is prompted by a
test rather than left to be remembered.

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
#: before this venue existed. `two-profile-unlinkability` is absent
#: DELIBERATELY; the header argues why at length and a test pins the omission
#: by name. `--check` is repeatable and VALIDATES each name, so a rename or a
#: retirement fails loudly here instead of silently shrinking the lane.
SELECTED_CHECKS = (
    "restart-continuity",
    "benign-edit-stability",
    "trash-restore-and-wipe",
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
)

#: Recorded so a reader of a red run knows what the lane costs and can tell a
#: genuinely wedged launch from a slow one. Measured on this branch, one host,
#: real engine, under xvfb-run, n=1 each:
#:
#:     the 3 selected checks          66.2s, 12 browser launches, exit 0
#:     the whole registry (all 7)     93.5s, 16 browser launches, exit 1
#:     engine download (cold cache)   ~10s, 584 MB extracted
#:
#: See the workflow for how that is priced against ci.yml's 30-minute cap.
_MEASURED_NOTE = (
    "measured on one linux host at n=1: this lane runs in ~66s over 12 browser "
    "launches, plus ~10s to fetch the engine"
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

    home = tempfile.mkdtemp(prefix="persona-behaviour-launch-ci-")
    env = dict(os.environ)
    env["PERSONA_HOME"] = home
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
