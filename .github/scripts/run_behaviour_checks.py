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
3. THE THREE EXIT CODES MUST STAY THREE. See below.

THE EXIT CODES ARE NOT COLLAPSED INTO "NON-ZERO"
------------------------------------------------
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
import shutil
import subprocess
import sys
import tempfile
import time

#: The lane this step runs. `--skip-launch` selects the three checks that need
#: no browser, no display and no exit: proxy-assignment-survives-edit,
#: launch-refuses-broken-geography, certificate-key-material. The four
#: launch-backed checks are deliberately NOT run here — provisioning a display
#: is separate work, argued on its own evidence.
COMMAND = [
    sys.executable,
    "-m",
    "src.services.verify.behaviour_cli",
    "run",
    "--skip-launch",
]

#: Set on the re-exec so the child knows the home was provisioned deliberately.
#: Kept in step with ``behaviour_cli._REEXEC_FLAG``.
REEXEC_FLAG = "PERSONA_BEHAVIOUR_CLI_REEXEC"

VERDICTS = {
    0: "every selected check ran, was shown capable of failing, and the behaviour held",
    1: "a check RAN and the behaviour did NOT hold — this is a FINDING about the product",
    2: "a check COULD NOT RUN. Nothing was certified — this is NOT a pass",
}


def main() -> int:
    home = tempfile.mkdtemp(prefix="persona-behaviour-ci-")
    env = dict(os.environ)
    env["PERSONA_HOME"] = home
    env[REEXEC_FLAG] = "1"

    print(f"scratch PERSONA_HOME={home}", flush=True)
    print(f"$ {' '.join(COMMAND[1:])}", flush=True)

    started = time.monotonic()
    try:
        completed = subprocess.run(COMMAND, env=env)
    finally:
        # The checks write profiles and certificate material into the scratch
        # home. Removing it keeps a self-hosted or cached runner from carrying
        # one run's fixtures into the next — the collision described above.
        shutil.rmtree(home, ignore_errors=True)
    elapsed = time.monotonic() - started

    rc = completed.returncode
    print(f"\nbehavioural checks finished in {elapsed:.2f}s, exit {rc}", flush=True)

    verdict = VERDICTS.get(rc)
    if verdict is None:
        # Neither a pass, a finding, nor a stated "could not run" — the harness
        # did not speak this vocabulary at all (a crash, a signal, an import
        # error escaping main). Treated as "nothing was measured", never as a
        # pass, and reported with the raw code so it is not mistaken for one of
        # the three.
        print(
            f"UNEXPECTED EXIT {rc}: the harness did not report one of its three "
            "verdicts. Nothing was certified.",
            file=sys.stderr,
            flush=True,
        )
        return rc if rc != 0 else 2

    print(f"verdict: {verdict}", flush=True)
    if rc != 0:
        print(
            "FAILING THE JOB. Exit 1 and exit 2 are different failures and are "
            "deliberately not collapsed: 1 says the product misbehaved, 2 says "
            "the check could not look. Neither is a pass.",
            file=sys.stderr,
            flush=True,
        )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
