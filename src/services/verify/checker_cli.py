"""Operator CLI for reading the checker matrix (Level 3 of the project bar).

    python -m src.services.verify.checker_cli read -o reading.json

One subcommand, deliberately. This is a CAPABILITY THAT CAN BE INVOKED, not a
gate: it produces a reading and records it, and it must not acquire the
authority to stop anything. Comparison against a baseline, a triage rule and a
refusal are a later slice and depend on a baseline existing — which is what
this produces.

Exit codes — the refusal is the interesting one:

    0   the matrix was read and recorded. NOT a claim that every verdict was
        good: a recorded adverse verdict still exits 0, because this tool
        reports and does not gate. Read the record.
    2   REFUSED — the exit could not be proven, so nothing was read and nothing
        was written. A missing credential, an unusable one, a refused
        connection, a timeout, or an exit that is not Polish all land here.
        This is the code that matters: the alternative to refusing is a
        complete-looking reading of the OPERATOR'S REAL ADDRESS taken against a
        dozen fingerprinting services, and direct egress works from this
        container, so that outcome is one silent failure away at all times.
    1   the run itself broke (an unexpected error). Distinct from 2 so "we
        declined to measure" is never confused with "we crashed".

There is deliberately NO ``--no-proxy``, ``--allow-direct`` or ``--force``
flag. A caller cannot ask this tool to read a checker over a direct
connection, because there is no argument that would make that a good idea: a
fingerprint reading taken over the wrong exit is worse than no reading, since
it looks like data.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import platform
import sys

from .checkers import BROWSER_CHECKERS, JSON_CHECKERS, UNREADABLE_CHECKERS
from .exit_guard import DEFAULT_CREDENTIAL_PATH, ExitNotProven, prove_exit, redact
from .matrix import (
    build_record,
    dumps,
    read_json_tier,
    read_unreadable_tier,
    write,
)

# What this environment is, recorded in the header. The reading is only
# interpretable against the machine it was taken on: the WebGL renderer class
# is host-driven, and this container has no GPU.
def _environment() -> str:
    return f"{platform.system().lower()}-{platform.machine()} (agent sandbox)"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _engine_label() -> str:
    """The engine the browser tier ran under, as a recorded fact.

    Resolved from the installed ``invisible_playwright`` rather than hardcoded:
    a reading taken under a different engine build must not claim this one.
    Falls back to a name that says it is unknown rather than to a plausible
    guess.
    """
    try:
        from invisible_playwright.constants import BINARY_VERSION

        return f"invisible_playwright/{BINARY_VERSION}"
    except Exception:
        return "unknown"


def _cmd_read(args: argparse.Namespace) -> int:
    # The precondition. Everything below depends on it and nothing is recorded
    # until it holds.
    try:
        proxy_url, observed = prove_exit(credential_path=args.credential)
    except ExitNotProven as exc:
        print(f"REFUSED: {redact(str(exc))}", file=sys.stderr)
        print(
            "Nothing was read and nothing was written.",
            file=sys.stderr,
        )
        return 2

    print(
        f"exit proven: {observed.ip} {observed.city}/{observed.country} "
        f"{observed.org}",
        file=sys.stderr,
    )

    notes = [
        "Read from the agent sandbox: no GPU, so WebGL renders in software "
        "while the engine declares a discrete card. That pair is impossible on "
        "real hardware and is a KNOWN-ENVIRONMENTAL host-fact leak, recorded "
        "with its reason and never counted as a pass.",
        "The exit is a rotating mobile address. Rotation WITHIN Poland is the "
        "design, not a fault: an exit-driven reading is expected to move "
        "between runs, and a FINGERPRINT-driven reading that moves when only "
        "the address moved is a coupling worth its own ticket.",
    ]

    readings = []
    if not args.skip_json:
        readings.extend(read_json_tier(proxy_url))
        print(f"json tier: {len(readings)} readings", file=sys.stderr)

    if not args.skip_browser:
        # Imported here: the browser tier pulls the engine, which must not be
        # required to read the JSON tier or to print --help.
        from .browser_tier import read_browser_tier

        before = len(readings)
        readings.extend(read_browser_tier(proxy_url, seed=args.seed))
        print(
            f"browser tier: {len(readings) - before} readings",
            file=sys.stderr,
        )

    readings.extend(read_unreadable_tier())

    record = build_record(
        readings,
        exit_=observed,
        engine=_engine_label(),
        observed_at=_now(),
        environment=_environment(),
        notes=notes,
    )

    if args.output == "-":
        sys.stdout.write(dumps(record))
    else:
        write(record, args.output)
        counts = record["counts"]
        print(
            f"wrote {args.output}: {counts['total']} readings "
            f"({counts['read']} read, {counts['absent']} absent, "
            f"{counts['unobtainable']} unobtainable)",
            file=sys.stderr,
        )
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    for checker in JSON_CHECKERS + BROWSER_CHECKERS:
        print(f"{checker.tier}\t{checker.id}\t{len(checker.items)} item(s)")
    for checker in UNREADABLE_CHECKERS:
        print(f"{checker.tier}\t{checker.id}\t{checker.unreadable_reason}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.services.verify.checker_cli",
        description=(
            "Read the third-party checker matrix through the operator's exit "
            "and record every verdict, per checker, per item."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rd = sub.add_parser(
        "read",
        help="read the matrix and record it",
        description=(
            "Proves the exit BEFORE reading anything, and refuses (exit 2) if "
            "it cannot. There is no flag to read a checker over a direct "
            "connection."
        ),
    )
    rd.add_argument(
        "-o", "--output", default="-",
        help="record path, or '-' for stdout (default: -)",
    )
    rd.add_argument(
        "--credential", default=DEFAULT_CREDENTIAL_PATH,
        help=f"proxy credential path (default: {DEFAULT_CREDENTIAL_PATH})",
    )
    rd.add_argument(
        "--seed", type=int, default=0,
        help="engine fingerprint seed for the browser tier (default: 0)",
    )
    rd.add_argument(
        "--skip-browser", action="store_true",
        help=(
            "read only the JSON tier. The browser-tier rows are then ABSENT "
            "FROM the record rather than recorded as passing"
        ),
    )
    rd.add_argument(
        "--skip-json", action="store_true",
        help="read only the browser tier",
    )
    rd.set_defaults(func=_cmd_read)

    ls = sub.add_parser("list", help="print the checker inventory")
    ls.set_defaults(func=_cmd_list)
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
