"""Run the no-launch behavioural checks and let their verdict gate the job.

WHY THIS EXISTS
---------------
``src/services/verify/behaviour_cli.py`` is a working instrument that nothing
executed. Its check bodies are not covered by any test — a ``raise
AssertionError`` planted at the head of six of the seven ``_run_*`` bodies
leaves ``tests/test_behaviour_checks.py`` at 38 passed, because that file tests
the RULES that decide whether a verdict may be published and deliberately never
the checks themselves. So the bodies could rot silently and the one instrument
that would notice was wired to nothing. This step is the execution venue.

WHY A SCRIPT AND NOT AN INLINE `run:` LINE
------------------------------------------
Three things need to be true on all three platforms, and a bash one-liner gets
each of them wrong somewhere:

1. THE SCRATCH HOME MUST BE FRESH ON EVERY RUN. The checks create fixed-name
   profiles (``ps70-proxy-holder`` and friends), so a REUSED home makes the
   second run collide with the first's leftovers: measured locally, run 1 exits
   0 and run 2 exits 2 with "could not create scratch profile" on all three
   checks. A step that reused a directory would report "nothing was measured"
   for a reason that has nothing to do with the product.
2. THE PATH MUST BE ONE PYTHON RESOLVES THE SAME WAY. ``mktemp -d`` under
   git-bash on ``windows-latest`` yields ``/tmp/...``, which is a bash-ism, not
   a path the interpreter reads as that directory. ``require_scratch_home``
   compares ``os.path.realpath`` of the declared home against the resolved
   store, so a shell-shaped path is exactly the mismatch it refuses. Creating
   the directory with ``tempfile`` means the string is produced by the same
   library that will later resolve it.
3. THE THREE EXIT CODES MUST STAY THREE, AND EACH MUST BE CORROBORATED. See
   the two sections below — this is the part a one-liner cannot do at all.

THE THREE EXIT CODES ARE NOT COLLAPSED INTO "NON-ZERO"
------------------------------------------------------
``behaviour.py`` defines 0 / 1 / 2 as pass / a finding about the product /
nothing was measured, and the whole reason that split exists is that a run
which could not measure must never read as a pass. This script therefore exits
with the child's OWN code rather than a boolean, so the job's red says WHICH of
the two failures happened:

    0  every selected check ran, was shown capable of failing, and held
    1  a check ran and the behaviour did NOT hold      -> job RED (a finding)
    2  a check COULD NOT RUN. Nothing was certified.   -> job RED (not a pass)

Exit 2 failing the job is the point, not an oversight: a permanently-green step
that quietly stopped looking is the defect this whole instrument exists to
catch, and it would be absurd to reintroduce it here.

⚠️ A RAW EXIT CODE IS A CLAIM, NOT EVIDENCE — SO 0 AND 1 ARE CORROBORATED
-------------------------------------------------------------------------
Both were caught by turning this ticket's own thesis on this script, and each
is the defect it exists to remove, re-created one level up. Neither is
hypothetical: both were reproduced by hand before being fixed.

  * A **0** IS ONLY A PASS IF THE REPORT CERTIFIES THE EXPECTED CHECKS.
    ``run_checks(..., skip_launch=True)`` filters the registry to
    ``[c for c in selected if not c.needs_launch]`` with no floor on what
    survives, and ``exit_code([])`` returns ``EXIT_OK`` because all three of
    its ``any()`` calls are false over an empty list. So if the three no-launch
    checks are ever renamed, retired, or grow a launch dependency, the lane
    empties and the harness prints "0 passed, 0 finding(s), 0 could not run"
    and *"the behaviour held"* — over nothing — and exits 0. Measured, by
    flipping the three ``needs_launch=False`` flags: ``GATE EXIT: 0``.
    ``behaviour.py``'s own comment above the name-validation branch says it
    "keeps the 'selects nothing, exits 0' hole closed on every path" — it is
    closed on the ``--check`` path and open on the ``--skip-launch`` path,
    which is the path this gate is the first caller of. So a 0 is honoured
    only when every check in ``EXPECTED_CHECKS`` reported ``[PASS]``.

  * A **1** IS ONLY A FINDING IF THE HARNESS LIVED LONG ENOUGH TO REPORT ONE.
    Python's default exit code for an uncaught exception is 1, which collides
    with ``EXIT_FINDING``. ``behaviour_cli`` guards its own seams against that
    collision (it translates ``BaselineUnavailable`` into ``EXIT_CANNOT_RUN``
    precisely so the codes cannot alias), but it cannot guard a failure that
    happens BEFORE it loads. Measured, running this script from ``/tmp`` before
    the cwd anchor below existed: ``No module named 'src'`` — the harness never
    started — announced as *"a FINDING about the product"*, exit 1. So a 1 is
    honoured only when the report actually states at least one finding;
    otherwise nothing was measured and the code is 2.

THE ASYMMETRY IS DELIBERATE: 0 and 1 are claims about the product and must be
earned; 2 already says "nothing was certified" and is honoured unconditionally,
because every correction here can only ever move a verdict TOWARDS 2. This
script can make the job redder than the harness asked for. It can never make it
greener.

WHY THE COMMAND IS ANCHORED TO THE REPO ROOT
--------------------------------------------
``python -m`` resolves the module against ``sys.path[0]``, which is the
CALLER's working directory. CI happens to run at the repo root today, so an
unanchored command works there — which makes an unanchored command latent
rather than safe, and a latent gap in a gate is what nobody notices. The sibling
gate ``check_protocol_conformance.py`` anchors on
``Path(__file__).resolve().parent.parent.parent`` rather than trusting cwd;
this does the same and passes it as the child's ``cwd``.

WHY THE RE-EXEC IS BYPASSED
---------------------------
``behaviour_cli.main`` normally re-execs itself via ``os.execve`` to set
``PERSONA_HOME`` before ``core.config`` reads it at import time. That is the
right default for an operator typing the command, but ``os.execve`` has
spawn-and-exit semantics on Windows, and a gate whose non-zero exit does not
reach the runner is decoration. Here the variable is already set in the child's
environment BEFORE the process starts — which is the exact condition the
re-exec exists to guarantee — so ``PERSONA_BEHAVIOUR_CLI_REEXEC`` is set and
the re-exec is skipped.

THIS DOES NOT DEFEAT THE SAFETY GUARD, and that was verified rather than
assumed: with the flag set and ``PERSONA_HOME`` UNSET, the command still exits
2 with "refusing to run: PERSONA_HOME is not set". ``require_scratch_home``
runs on its own terms and still refuses the default store, which matters
because the trash check calls ``wipe_all_profiles``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

#: ``python -m`` resolves against the CALLER's cwd, so the command is run from
#: here rather than from wherever the step happened to be. Same derivation as
#: ``check_protocol_conformance.py``'s ``DEFAULT_ROOT``.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: The module the harness lives in, as both an import path and a file path. The
#: file path is checked before the run so "the gate is pointed at nothing" is
#: reported as such instead of arriving as an opaque child exit code.
MODULE = "src.services.verify.behaviour_cli"
MODULE_FILE = REPO_ROOT.joinpath(*MODULE.split(".")).with_suffix(".py")

#: The lane this step runs. `--skip-launch` selects the three checks that need
#: no browser, no display and no exit. The four launch-backed checks are
#: deliberately NOT run here — provisioning a display is separate work, argued
#: on its own evidence.
COMMAND = [sys.executable, "-m", MODULE, "run", "--skip-launch"]

#: WHAT THE LANE MUST CERTIFY BEFORE A 0 COUNTS AS A PASS — the three
#: ``needs_launch=False`` checks in ``behaviour_checks.CHECKS``.
#:
#: Listed by NAME rather than counted, and listed EXPLICITLY rather than
#: derived from the registry, both on purpose. Deriving it would make this
#: agree with an empty registry by construction, which is the failure being
#: guarded against; and a name says WHICH check went missing, where a bare
#: count says only that one did.
#:
#: This constant is brittle in the useful direction. Renaming or retiring a
#: no-launch check turns the gate red at 2 until somebody updates this line —
#: which is the point: that edit should be noticed, not absorbed silently.
#: ADDING a no-launch check does not break it (the rule is "at least these
#: passed"), though adding the new name here is what makes the new check
#: load-bearing rather than merely present.
EXPECTED_CHECKS = (
    "proxy-assignment-survives-edit",
    "launch-refuses-broken-geography",
    "certificate-key-material",
)

#: Set on the re-exec so the child knows the home was provisioned deliberately.
#: Kept in step with ``behaviour_cli._REEXEC_FLAG``.
REEXEC_FLAG = "PERSONA_BEHAVIOUR_CLI_REEXEC"

#: ``behaviour.format_report``'s summary line and its per-check badges. If
#: either format drifts, this script stops being able to corroborate a 0 or a 1
#: and says "nothing was certified" instead of guessing — the safe direction,
#: and one that forces the drift to be looked at.
#: ``tests/test_ps315_behaviour_gate.py`` runs the REAL harness through these
#: patterns, so a format drift breaks a test rather than only a CI run.
SUMMARY_RE = re.compile(
    r"^\s*(\d+) passed, (\d+) finding\(s\), (\d+) could not run", re.MULTILINE
)
PASS_RE = re.compile(r"^\[PASS\] (\S+)", re.MULTILINE)

VERDICTS = {
    0: "every selected check ran, was shown capable of failing, and the behaviour held",
    1: "a check RAN and the behaviour did NOT hold — this is a FINDING about the product",
    2: "a check COULD NOT RUN. Nothing was certified — this is NOT a pass",
}

#: Printed whenever a claimed 0 or 1 is downgraded, so the log says which of the
#: two corroboration rules fired rather than only that the job went red.
_DOWNGRADE = (
    "DOWNGRADED TO 2 — nothing was certified. The harness reported {claimed}, "
    "but {why}. A verdict this script cannot corroborate is not a verdict: an "
    "expensive check that is permanently green because it quietly stopped "
    "looking is the defect this gate exists to remove."
)


def echo(text: str, *, err: bool = False) -> None:
    """Write text to the step log without a locale-dependent re-encode.

    USED FOR THE PARENT'S OWN OUTPUT AS WELL AS THE CHILD'S REPORT, because the
    parent has exactly the same problem and it is easy to miss: `VERDICTS`,
    `_DOWNGRADE` and the failure banner all contain an em-dash, and this
    process's `sys.stdout` resolves to the ANSI code page on Windows just as
    the child's did. Measured on the falsification run 34000438536, windows
    leg — this line is the PARENT's, not the harness's:

        b'Nothing was certified \\xef\\xbf\\xbd this is NOT a pass'

    So pinning only the child (PYTHONIOENCODING, set in `main`) would have left
    the gate's own verdict corrupted on 1 of 3 platforms. Both halves are
    needed; neither is sufficient.

    Writing encoded bytes to the underlying buffer is what makes this
    independent of the console's codec. `errors="replace"` is deliberate here
    and is NOT the defect above: this is the last stop before the log, so a
    character that cannot be written must degrade rather than raise and take
    the gate's verdict down with it.
    """
    stream = sys.stderr if err else sys.stdout
    buffer = getattr(stream, "buffer", None)
    if buffer is None:  # a substituted stream in a test, not a real console
        stream.write(text)
        stream.flush()
        return
    stream.flush()
    buffer.write(text.encode("utf-8", errors="replace"))
    buffer.flush()


def summary(output: str) -> "tuple[int, int, int] | None":
    """``(passed, findings, could_not_run)`` from the report, or None if absent.

    None means the harness never printed a summary at all — it did not get far
    enough to say anything about the product.
    """
    match = SUMMARY_RE.search(output)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def passed_checks(output: str) -> "set[str]":
    """The names the report actually badged ``[PASS]``."""
    return set(PASS_RE.findall(output))


def adjudicate(
    rc: int, output: str, expected: "tuple[str, ...]" = EXPECTED_CHECKS
) -> "tuple[int, str | None]":
    """Return the code this gate exits with, and why if it differs from ``rc``.

    Kept a pure function of the child's code and its output so the corroboration
    rules can be driven directly in tests, rather than only through the shapes a
    subprocess happens to make reachable.

    ``expected`` DEFAULTS to this lane's own floor, so every existing caller and
    every existing test is unaffected. It is a parameter rather than a hard
    read of the module global because the LAUNCH lane
    (``run_launch_behaviour_checks.py``, PS-336) needs the same corroboration
    over a DIFFERENT floor, and the alternative — a second copy of these rules —
    is the shape that lets two gates drift into two different definitions of
    "a 0 was earned". One owner, two floors.

    ⚠️ ``expected`` MUST NOT BE EMPTY, and that is checked rather than trusted:
    an empty floor makes ``missing`` empty for EVERY report, so the rule below
    would honour a 0 over a lane that certified nothing — which is PS-315's
    own hole re-created inside the rule written to close it. An empty floor is
    a defect in the CALLER, so it lands on 2 like every other thing this script
    cannot corroborate.
    """
    if not expected:
        return 2, _DOWNGRADE.format(
            claimed=f"exit {rc}",
            why=(
                "this gate was given an EMPTY expected-check floor, so it has "
                "nothing to corroborate a verdict against. A floor of no names "
                "is satisfied by a report certifying nothing"
            ),
        )

    if rc == 2:
        # Already "nothing was certified". There is nothing to corroborate and
        # nowhere safer to move it.
        return 2, None

    if rc == 0:
        missing = [name for name in expected if name not in passed_checks(output)]
        if missing:
            counts = summary(output)
            observed = (
                "the report is missing entirely — the harness printed no summary"
                if counts is None
                else f"it certified {counts[0]} check(s)"
            )
            return 2, _DOWNGRADE.format(
                claimed="a PASS",
                why=(
                    f"{observed} and did not certify: {', '.join(missing)}. "
                    "The no-launch lane selects checks by filtering the registry, "
                    "and an EMPTY selection exits 0 with 'the behaviour held' — "
                    "over nothing. These names must each be certified for a 0 to "
                    "mean anything"
                ),
            )
        return 0, None

    if rc == 1:
        counts = summary(output)
        if counts is None:
            return 2, _DOWNGRADE.format(
                claimed="a FINDING about the product",
                why=(
                    "it printed no report at all, so the harness never ran. Exit 1 "
                    "is also Python's code for an uncaught exception, and a failure "
                    "BEFORE the harness loads (a bad invocation, an import error) "
                    "cannot be a finding about the product"
                ),
            )
        if counts[1] < 1:
            return 2, _DOWNGRADE.format(
                claimed="a FINDING about the product",
                why=(
                    f"its own report states {counts[1]} finding(s). A code and a "
                    "report that disagree certify nothing"
                ),
            )
        return 1, None

    # Neither a pass, a finding, nor a stated "could not run" — the harness did
    # not speak this vocabulary at all (a crash, a signal, an import error
    # escaping main). Reported with the raw code so it is not mistaken for one
    # of the three, and never mapped to 0.
    return rc, (
        f"UNEXPECTED EXIT {rc}: the harness did not report one of its three "
        "verdicts. Nothing was certified."
    )


def main() -> int:
    if not MODULE_FILE.is_file():
        echo(
            f"CANNOT RUN: {MODULE_FILE} does not exist, so this gate is pointed "
            "at nothing. Nothing was certified.\n",
            err=True,
        )
        return 2

    home = tempfile.mkdtemp(prefix="persona-behaviour-ci-")
    env = dict(os.environ)
    env["PERSONA_HOME"] = home
    env[REEXEC_FLAG] = "1"
    # THE CHILD MUST WRITE WHAT THIS PARENT CLAIMS TO READ. Naming
    # encoding="utf-8" on subprocess.run below governs only the DECODE; nothing
    # governs the child's ENCODE, and a Python process whose stdout is a PIPE on
    # Windows resolves to the ANSI code page (cp1252). Measured on
    # windows-latest, run 34004209963, against the same line on ubuntu:
    #
    #   ubuntu   b'behaviour \xe2\x80\x94 whether a SOCKS handshake'   <- em-dash
    #   windows  b'behaviour \xef\xbf\xbd whether a SOCKS handshake'   <- U+FFFD
    #
    # U+FFFD is not a rendering artifact; it is the record of a decode that
    # already failed and threw the original byte away. The em-dash merely
    # ROUND-TRIPS badly because it exists in cp1252 (0x97). A character that
    # does NOT — a '✓', an arrow, a non-Latin-1 name in a check's detail — makes
    # the child RAISE UnicodeEncodeError instead: rc 1, and the summary line
    # never printed. That is EXIT_FINDING's code produced by a crash, on a
    # truncated report, which is precisely the 1-vs-2 collision this gate exists
    # to close. `adjudicate` does refuse it (no summary -> 2), but that safety
    # comes from the harness's CURRENT vocabulary rather than from anything this
    # script controls, and behaviour.py's report text is owned elsewhere.
    env["PYTHONIOENCODING"] = "utf-8"
    # PYTHONUTF8 alone is NOT sufficient and is not a substitute: it is ignored
    # when PYTHONIOENCODING is set, and the reverse is the direction that
    # matters here. Both are harmless together; the line above is the one that
    # pins the stream.

    echo(f"scratch PERSONA_HOME={home}\n")
    echo(f"cwd={REPO_ROOT}\n")
    echo(f"$ {' '.join(COMMAND[1:])}\n")

    started = time.monotonic()
    try:
        completed = subprocess.run(
            COMMAND,
            cwd=str(REPO_ROOT),
            env=env,
            # Merged so the log keeps the harness's stdout report and its
            # stderr refusals in the order they happened, and so both are
            # available to the corroboration rules: `CANNOT RUN:` goes to
            # stderr while the report goes to stdout.
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            # Named rather than inherited, and PAIRED with PYTHONIOENCODING
            # above: this is the DECODE half, that is the ENCODE half, and
            # pinning either alone leaves the two ends disagreeing by
            # construction — which is the PS-184 defect exactly.
            encoding="utf-8",
            errors="replace",
        )
    finally:
        # The checks write profiles and certificate material into the scratch
        # home. Removing it keeps a self-hosted or cached runner from carrying
        # one run's fixtures into the next — the collision described above.
        shutil.rmtree(home, ignore_errors=True)
    elapsed = time.monotonic() - started

    output = completed.stdout or ""
    echo(output)

    rc = completed.returncode
    echo(f"\nbehavioural checks finished in {elapsed:.2f}s, exit {rc}\n")

    code, note = adjudicate(rc, output)

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
