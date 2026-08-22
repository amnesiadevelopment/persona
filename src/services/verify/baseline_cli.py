"""One command: does this profile still look exactly as it did on the last engine?

    # compare a live reading against the committed baseline (the usual case)
    xvfb-run -a python -m src.services.verify.baseline_cli check

    # re-record the baseline, once a bump has been REVIEWED and accepted
    xvfb-run -a python -m src.services.verify.baseline_cli record

``check`` exits 0 when every probe was read on both sides and none of them
moved. It prints which probe, in which realm, expected versus observed, so the
decision "benign or leak?" can be made without opening the JSON.

The two non-zero codes mean DIFFERENT things and must not be collapsed into
"non-zero":

    1  the check RAN and disagreed — a probe drifted, or a probe could not be
       read. This is the finding.
    2  the check COULD NOT RUN — no display, or nothing usable to compare
       against. Nothing was compared.

Exit 1 is the drift signal, so anything that prevents a comparison has to exit
2 instead. A baseline reported as 1 when nothing was read at all would tell a
caller the engine changed the identity — a false red on the most alarming
signal this system has, and the inverse of the trap ``BaselineResult.ok``
closes on the green side. Whenever the check cannot run it says so in those
words: it is NOT drift.

"Nothing usable" is deliberately wider than "unreadable": it covers a baseline
that is missing or corrupt, AND one that parses perfectly well but is not a
snapshot (a list, a bare null, some other JSON file reached by a typo) or is a
snapshot carrying no readings. Getting bytes off disk is not the same question
as having something to compare against — and those last shapes are the
dangerous ones, because with no probes on the reference side every observed
probe diffs as "added" and the tool prints a confident maximum-alarm DRIFT/FAIL
for a comparison that never happened. No traceback, so it reads as a real
answer. All of it exits 2.

WHAT THIS DOES NOT DO — read this before trusting a green run
-------------------------------------------------------------
**This check does not fire on the automatic engine bump.**
``.github/workflows/engine-autoupdate.yml`` runs daily at 06:00 UTC, rewrites
the engine pins, commits to ``main`` and pushes a tag that triggers a release —
with nobody looking and, as of this writing, without ever invoking this command.
Both workflows run on a runner with no display server, no engine and no xvfb, so
wiring this in is a real slice of work (install the engine, provide a display,
keep it deterministic on a headless runner) and it has not been done.

What exists today is a check an operator can RUN. That is the precondition for
automating it, not a substitute for it. Until the CI slice lands, the bump still
ships unverified.

The 19→20 transition is likewise unverified and will stay that way: the baseline
was first recorded on firefox-20, which was already current. This machinery
makes 20→21 and everything after it checkable.
"""

from __future__ import annotations

import argparse
import sys

from .baseline import (
    BASELINE_ARTIFACT,
    BaselineResult,
    BaselineUnavailable,
    check,
    count_errors,
    record_snapshot,
)
from .snapshot import quote_path, write


def _cmd_record(args: argparse.Namespace) -> int:
    snapshot = record_snapshot(fresh=not args.reuse_profile)
    errors = count_errors(snapshot)
    total = sum(len(realm) for realm in snapshot["probes"].values())
    summary = (
        f"{total} readings across {len(snapshot['realms'])} realm(s) on engine "
        f"build {snapshot.get('engine_build')}, {errors} error(s)"
    )

    if errors:
        # A baseline with holes in it is not a baseline: every probe it could
        # not read is a probe that will compare "equal" against a future
        # failure and report agreement.
        #
        # VALIDATE BEFORE WRITING, and never write the rejected reading to the
        # path being blessed. --output defaults to the COMMITTED artifact, so
        # writing first would destroy the good reference and then print a
        # message claiming it had been refused. That fires exactly when the
        # operator is doing the right thing — re-recording after an accepted
        # bump — and the damage surfaces later as a `check` passing against a
        # holed baseline, which is the two-non-readings trap reintroduced
        # through the write path.
        rejected = f"{args.output}.rejected"
        # The REFUSAL is the verdict here, and it stands whether or not the
        # unusable reading can be parked for inspection. So a failed .rejected
        # write degrades the message rather than replacing it with a traceback,
        # and the exit code stays 1: probes genuinely could not be read, which
        # is a real finding, NOT a "could not run".
        try:
            write(snapshot, rejected)
            kept = f"Wrote the unusable reading to {rejected} for inspection; "
        except OSError as exc:
            kept = (
                f"Could not even park the unusable reading at {rejected} "
                f"({exc}), so it is lost — fix the path if you want to inspect "
                "it. "
            )
        print(
            f"REFUSING to treat this as a good baseline: {errors} probe(s) "
            "could not be read. A probe that errors here will compare EQUAL "
            "against the same error later and be reported as agreement. "
            f"{kept}"
            f"{args.output} is UNCHANGED. Investigate the errors, then "
            "re-record.",
            file=sys.stderr,
        )
        return 1

    # A good reading that cannot be written is a run that did not happen, not a
    # bad baseline: report it as "could not run" (exit 2), like every other
    # missing precondition, instead of tracebacking.
    try:
        write(snapshot, args.output)
    except OSError as exc:
        raise BaselineUnavailable(
            f"recorded a clean reading ({summary}) but could not write it to "
            f"{quote_path(args.output)}: {exc}. Nothing was written; the "
            "previous artifact, if any, is UNCHANGED. Fix the path or pass -o."
        ) from exc
    print(f"wrote {args.output}: {summary}", file=sys.stderr)
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    result: BaselineResult = check(args.baseline)
    print(result.report())
    return 0 if result.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.services.verify.baseline_cli",
        description=(
            "Record the pinned baseline profile, or check the current engine "
            "against the committed baseline."
        ),
        epilog=(
            "Needs a real browser, so it needs a display: prefix with "
            "'xvfb-run -a' on a headless machine. This does NOT run on the "
            "automatic engine bump — see the module docstring."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser(
        "record",
        help="record the pinned profile into a baseline artifact (do this "
        "only when a bump has been reviewed and ACCEPTED)",
    )
    rec.add_argument(
        "-o",
        "--output",
        default=BASELINE_ARTIFACT,
        help=f"where to write the artifact (default: {BASELINE_ARTIFACT})",
    )
    rec.add_argument(
        "--reuse-profile",
        action="store_true",
        help="do NOT wipe the baseline profile's data dir first (default is to "
        "wipe it, so the recording starts from a known state)",
    )
    rec.set_defaults(func=_cmd_record)

    chk = sub.add_parser(
        "check",
        help="record the profile NOW and compare it against the committed "
        "baseline; exits non-zero on drift or on an unread probe",
    )
    chk.add_argument(
        "--baseline",
        default=BASELINE_ARTIFACT,
        help=f"baseline artifact to compare against (default: {BASELINE_ARTIFACT})",
    )
    chk.set_defaults(func=_cmd_check)

    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BaselineUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
