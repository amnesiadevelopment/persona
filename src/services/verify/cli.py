"""Operator CLI for the verification snapshot.

    python -m src.services.verify.cli record <profile> -o before.json
    # ... update the engine, restart the profile ...
    python -m src.services.verify.cli record <profile> -o after.json
    python -m src.services.verify.cli diff before.json after.json

``record`` observes an **already-running** profile. It launches nothing, wires
into no app container, and needs no API server — so the continuity question
("did this identity survive an engine update?") is answerable today, by hand,
before anything is automated on top of it.

This closes no leak and gates no release. ``diff`` exits non-zero when the two
snapshots disagree because that is what a differ does for an operator; wiring
that exit code into CI is a later slice with its own approval.
"""

from __future__ import annotations

import argparse
import sys

from .diff import diff_realms, diff_snapshots, format_diff
from .probes import ALL_REALMS, PROBES, WINDOW, WORKER
from .runner import run_probes
from .snapshot import build_snapshot, dumps, load, write


def _parse_realms(raw: str) -> tuple[str, ...]:
    realms = tuple(r.strip() for r in raw.split(",") if r.strip())
    unknown = [r for r in realms if r not in ALL_REALMS]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown realm(s) {unknown}; valid realms are {list(ALL_REALMS)}"
        )
    if not realms:
        raise argparse.ArgumentTypeError("at least one realm is required")
    return realms


def _cmd_record(args: argparse.Namespace) -> int:
    # Imported here, not at module scope: transport pulls playwright (lazily,
    # itself) and the profile store. `list` and `diff` must work without them.
    from .transport import TransportUnavailable, evaluate_for

    try:
        transport = evaluate_for(args.profile)
    except TransportUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    with transport:
        results = run_probes(transport.evaluate, args.realms)
        engine = transport.engine

    snapshot = build_snapshot(
        results, engine=engine, profile=args.profile, realms=args.realms
    )
    if args.output == "-":
        sys.stdout.write(dumps(snapshot))
    else:
        write(snapshot, args.output)
        errors = sum(
            1
            for realm in snapshot["probes"].values()
            for entry in realm.values()
            if "error" in entry
        )
        total = sum(len(realm) for realm in snapshot["probes"].values())
        print(
            f"wrote {args.output}: {total} readings across "
            f"{len(snapshot['realms'])} realm(s), {errors} error(s)",
            file=sys.stderr,
        )
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    entries = diff_snapshots(
        load(args.expected), load(args.observed), include_meta=args.meta
    )
    print(format_diff(entries))
    return 1 if entries else 0


def _cmd_realms(args: argparse.Namespace) -> int:
    entries = diff_realms(load(args.snapshot), args.left, args.right)
    print(format_diff(entries))
    return 1 if entries else 0


def _cmd_list(args: argparse.Namespace) -> int:
    for probe in PROBES:
        print(f"{probe.id}\t{','.join(probe.realms)}")
    print(f"\n{len(PROBES)} probes", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.services.verify.cli",
        description=(
            "Record what a live profile actually exposes, and diff two "
            "recordings."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser(
        "record", help="probe an already-running profile into a snapshot file"
    )
    rec.add_argument("profile", help="profile name (must already be running)")
    rec.add_argument(
        "-o",
        "--output",
        default="-",
        help="snapshot path, or '-' for stdout (default: -)",
    )
    rec.add_argument(
        "--realms",
        type=_parse_realms,
        default=(WINDOW, WORKER),
        help=f"comma-separated realms (default: {WINDOW},{WORKER})",
    )
    rec.set_defaults(func=_cmd_record)

    dif = sub.add_parser("diff", help="compare two snapshot files")
    dif.add_argument("expected", help="baseline snapshot (the 'before')")
    dif.add_argument("observed", help="new snapshot (the 'after')")
    dif.add_argument(
        "--meta",
        action="store_true",
        help="also report header disagreements (engine, profile, app_version)",
    )
    dif.set_defaults(func=_cmd_diff)

    rlm = sub.add_parser(
        "realms",
        help="compare two realms within ONE snapshot (window vs worker)",
    )
    rlm.add_argument("snapshot")
    rlm.add_argument("--left", default=WINDOW)
    rlm.add_argument("--right", default=WORKER)
    rlm.set_defaults(func=_cmd_realms)

    lst = sub.add_parser("list", help="print the probe inventory")
    lst.set_defaults(func=_cmd_list)

    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
