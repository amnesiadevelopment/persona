"""Run the behavioural checks and report what the product actually did.

    # everything, in a throwaway home the command provisions itself
    xvfb-run -a python -m src.services.verify.behaviour_cli run

    # only the checks that need no browser and no exit (runnable anywhere)
    python -m src.services.verify.behaviour_cli run --skip-launch

    # one surface
    xvfb-run -a python -m src.services.verify.behaviour_cli run \
        --check restart-continuity

Exit codes are three, never two, and must not be collapsed into "non-zero":

    0  every check ran, was shown capable of failing, and the behaviour held
    1  a check RAN and the behaviour did NOT hold — this is the finding
    2  a check COULD NOT RUN. Nothing was certified.

Exit 2 covers the case this project keeps getting bitten by: a run that fails
for an environmental reason, records the failure "correctly", and still exits 0
over a near-empty record. A missing display, an unreadable probe, or a check
whose own falsification did not go red all land here — never on 0, and never on
1 either, because "nothing was measured" is not a finding about the product.

WHY THIS PROVISIONS ITS OWN HOME
---------------------------------
The trash check calls ``wipe_all_profiles``, which is irreversible and purges
the trash as part of its job. Against an operator's real ``~/.persona`` that
destroys every profile they own. So the default is a fresh ``mkdtemp``, and the
harness independently REFUSES to run against the default store even if one is
somehow configured. ``--home`` accepts an explicit scratch directory for a run
whose artifacts you want to keep; it is still refused if it resolves to the
real store.

Because ``core.config`` reads ``PERSONA_HOME`` at IMPORT time, the variable has
to be set before this process starts importing the stores. That is why ``main``
re-executes itself once with the variable set rather than setting it inline: a
guard that read one path while the stores lived at another would be worse than
no guard.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

#: Set on the re-exec so the child knows the home was provisioned deliberately.
_REEXEC_FLAG = "PERSONA_BEHAVIOUR_CLI_REEXEC"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="behaviour_cli",
        description=(
            "Observe the product doing its job: restart continuity, "
            "two-profile unlinkability, edit stability, proxy assignment, "
            "certificate key material and the trash bin."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the behavioural checks")
    run.add_argument(
        "--check",
        action="append",
        dest="checks",
        metavar="NAME",
        help="run only this check (repeatable). Default: all of them.",
    )
    run.add_argument(
        "--skip-launch",
        action="store_true",
        help=(
            "run only the checks that need no browser. Useful where there is "
            "no display; the launch-backed surfaces are then NOT covered, and "
            "the report says so."
        ),
    )
    run.add_argument(
        "--home",
        metavar="DIR",
        help=(
            "scratch PERSONA_HOME to run against. Default: a fresh temporary "
            "directory. Never point this at a real store — the trash check "
            "wipes it."
        ),
    )
    sub.add_parser("list", help="list the checks and the surface each observes")
    return parser


def _cmd_list() -> int:
    from .behaviour import UNCOVERED_SURFACES
    from .behaviour_checks import CHECKS

    print("behavioural checks:")
    for c in CHECKS:
        needs = "launch" if c.needs_launch else "no launch"
        print(f"  {c.name:<34} ({needs})")
        print(f"      {c.surface}")
    print()
    print("NOT covered by this module:")
    for surface, why in UNCOVERED_SURFACES:
        print(f"  * {surface} — {why}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from .behaviour import (
        EXIT_CANNOT_RUN,
        BehaviourCheckError,
        exit_code,
        format_report,
        run_checks,
    )

    try:
        outcomes, ctx = run_checks(args.checks, skip_launch=args.skip_launch)
    except BehaviourCheckError as exc:
        # Includes the safety refusal and a missing display. Nothing ran, so
        # this is EXIT_CANNOT_RUN and is stated in those words.
        #
        # The display case only arrives here because require_display TRANSLATES
        # baseline's BaselineUnavailable into this class — untranslated it
        # escapes main() and Python's default exit 1 collides with EXIT_FINDING,
        # reporting "nothing was measured" as "the product is broken". Keep that
        # translation if the seam is ever refactored.
        print(f"CANNOT RUN: {exc}", file=sys.stderr)
        return EXIT_CANNOT_RUN

    print(format_report(outcomes, ctx))
    if args.skip_launch:
        print()
        print(
            "NOTE: --skip-launch was used, so every launch-backed surface "
            "(restart continuity, two-profile unlinkability, edit stability, "
            "the trash bin's 'came back whole') was NOT observed on this run."
        )
    return exit_code(outcomes)


def main(argv: "list[str] | None" = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "list":
        return _cmd_list()

    if not os.environ.get(_REEXEC_FLAG):
        # core.config resolves PERSONA_HOME at import time, so it must be set
        # before the stores are imported. Re-exec once with a scratch home
        # rather than setting it inline and hoping nothing imported yet.
        home = args.home or tempfile.mkdtemp(prefix="persona-behaviour-")
        env = dict(os.environ)
        env["PERSONA_HOME"] = home
        env[_REEXEC_FLAG] = "1"
        print(f"running against scratch PERSONA_HOME={home}", file=sys.stderr)
        os.execve(sys.executable, [sys.executable, "-m", __spec__.name, *(argv or sys.argv[1:])], env)

    return _cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
