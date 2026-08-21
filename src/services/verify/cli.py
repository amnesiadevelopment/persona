"""Operator CLI for the verification snapshot.

    python -m src.services.verify.cli record <profile> -o before.json
    # ... update the engine, restart the profile ...
    python -m src.services.verify.cli record <profile> -o after.json
    python -m src.services.verify.cli diff before.json after.json

``record`` observes an **already-running** profile. It launches nothing, wires
into no app container, and needs no API server — so the continuity question
("did this identity survive an engine update?") is answerable today, by hand,
before anything is automated on top of it.

Exit codes for ``diff`` and ``realms`` — three VERDICTS, not two, because "the
identity moved" and "we failed to read it" are different facts and a caller
must be able to tell them apart from the exit code alone; plus a fourth code
that is not a verdict at all, the refusal:

    0   every probe was READ and agreed
    1   at least one probe genuinely differs — a reading changed, a probe that
        WAS readable is now unreadable, or a probe with an obtained reading
        appeared or vanished. Reported even if some other probe was also
        inconclusive, because a real difference is the louder fact
    3   nothing was observed to differ, and at least one entry rests on no
        reading at all — every side of it errored or was absent. NOT a pass:
        an unobtainable reading is inconclusive, and inconclusive is never a
        pass. Distinct from 1 so a future CI gate can treat "look again"
        differently from "the identity drifted"
    2   refused. A file handed in is not a snapshot at all — it carries no
        ``probes`` object, so NOTHING WAS COMPARED. Never 0: a comparison of
        zero readings is not agreement, and an empty entry list is this
        subsystem's agreement signal, so without this refusal a typo'd path
        prints "no differences" and exits 0 — the tool at its most confident
        with the least evidence. Never 1 either, for the reason ``compare``
        already gives below: a refusal is not a finding

That refusal covers a file that PARSED but is not a snapshot. Two input
failures are still NOT covered and remain as they were before it existed: a
path that does not exist, and a file that is not valid JSON, both still exit
**1** with a traceback rather than a refusal. 1 is the DRIFT code, so those
two read as "the identity moved" — which is precisely the confusion 2 exists
to end, and a typo'd path is the likeliest way into them. Verified identical
with the guard absent, so this is pre-existing and not a regression of it;
closing it is a separate slice. ``baseline.py`` already guards both.

Note which side of that line the asymmetric case falls on: a vector that read
"Apple GPU" in the baseline and throws after an engine update exits **1**, not
3. One side WAS read, so this is not a failure to look — it is the strongest
continuity signal this subsystem can produce, and sorting it into the retry
bucket would mute it.

This closes no leak and gates no release. ``diff`` exits non-zero when the two
snapshots disagree, or when it could not obtain the evidence to say they agree,
because that is what a differ owes an operator; wiring that exit code into CI
is a later slice with its own approval.

``compare`` (cross-profile unlinkability) reuses those three codes with the
polarity inverted — 0 is "the profiles DIFFER on every compared vector", 1 is
"they agree somewhere, so they are linkable there", 3 is "the comparison rested
only on readings nobody obtained". It refuses on a SECOND premise the other two
subcommands do not have, reusing the same 2:

    2   refused. The two snapshots cannot answer the question at all — the same
        profile compared with itself, or two different engines compared as if
        they were one. Both verdicts would be wrong (a false leak and a false
        certificate respectively), so neither is given. Deliberately not 1: a
        refusal is not a finding

Note that 2 means one thing across all three subcommands — "could not do what
you asked", the same value ``record`` returns when it cannot reach a profile.
What varies is only WHICH premise failed: for ``diff``/``realms`` the input was
not a snapshot, for ``compare`` the pair could not answer the unlinkability
question. A caller reading exit codes alone never has to know the difference:
2 is never a verdict about an identity.
"""

from __future__ import annotations

import argparse
import sys

from .diff import (
    ComparisonNotControlled,
    NotASnapshot,
    compare_profiles,
    diff_realms,
    diff_snapshots,
    format_comparison,
    format_diff,
    inconclusive_count,
    require_snapshot,
)
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


def _exit_code(entries: "list[dict]") -> int:
    """0 = read and agreed, 1 = something moved, 3 = something was never read.

    A real difference outranks an inconclusive one: when both are present the
    caller is told the identity moved, which is the louder fact. But a diff
    carrying ONLY inconclusive entries must never return 0 — that would be a
    claim of agreement resting on evidence nobody gathered.

    "Inconclusive" here is the comparator's own definition: an entry NEITHER
    side of which carries an obtained reading. An entry with a reading on one
    side is a difference someone actually observed, so it takes the 1 — which
    keeps this function's answer consistent with the contract documented at the
    top of this module, rather than routing an observed difference into the
    "look again" bucket.
    """
    if not entries:
        return 0
    unread = inconclusive_count(entries)
    return 3 if unread == len(entries) else 1


def _load_snapshot(path: str) -> dict:
    """Load a snapshot file, refusing anything that is not a snapshot.

    The refusal is raised HERE, at the load site, rather than being left to the
    comparators, so the message can name the FILE the operator typed. A typo'd
    path is the expected way into this branch, and "which of my two arguments
    was wrong" is the only thing they need to know.

    ``json.load`` on a non-object (``[1,2,3]``, ``null``, a bare string) is
    caught by the same guard: those parse fine and used to reach ``_probes`` as
    a traceback on exit 1. A traceback is not a diff verdict.
    """
    return require_snapshot(load(path), source=path)


def _cmd_diff(args: argparse.Namespace) -> int:
    try:
        expected = _load_snapshot(args.expected)
        observed = _load_snapshot(args.observed)
    except NotASnapshot as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    entries = diff_snapshots(expected, observed, include_meta=args.meta)
    print(format_diff(entries))
    return _exit_code(entries)


def _cmd_realms(args: argparse.Namespace) -> int:
    try:
        snap = _load_snapshot(args.snapshot)
    except NotASnapshot as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    entries = diff_realms(snap, args.left, args.right)
    print(format_diff(entries))
    return _exit_code(entries)


def _cmd_compare(args: argparse.Namespace) -> int:
    """Cross-profile comparison — the one subcommand whose polarity is inverted.

    ``diff`` and ``realms`` ask "did these agree?" and exit 0 on agreement.
    This asks "are these two profiles distinguishable?", where agreement on a
    seed-derived vector is the DEFECT. ``_exit_code`` is reused verbatim and
    its three outcomes still mean what the module header says — an empty list
    is the pass, a reported entry is a finding, and an entry resting on no
    obtained reading is never a pass — but what produces each one is inverted:
    here the pass is the two profiles DIFFERING on every compared vector.

    Exit 2, the refusal, is reachable here on a premise specific to this mode:
    when the two snapshots cannot answer the unlinkability question at all —
    one profile compared with itself, or two engines compared as if they were
    one — both verdicts this mode can give would be wrong, so it gives neither.
    ``diff`` and ``realms`` also refuse with 2, on their own premise (the input
    was not a snapshot); what is specific to this mode is the premise, not the
    code. 2 is reused from ``record``'s "could not do the thing you asked"
    rather than minted fresh, and it is deliberately NOT 1: a refusal is not a
    finding, and a future gate must not read it as a leak.
    """
    try:
        entries = compare_profiles(
            load(args.a), load(args.b), allow_cross_engine=args.allow_cross_engine
        )
    except ComparisonNotControlled as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(format_comparison(entries))
    return _exit_code(entries)


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

    cmp_ = sub.add_parser(
        "compare",
        help=(
            "compare two DIFFERENT profiles' snapshots — reports vectors they "
            "AGREE on (the profiles are linkable there)"
        ),
        description=(
            "Cross-profile comparison (unlinkability). Inverted polarity: only "
            "vectors that are seed-derived and must vary are compared, and "
            "AGREEMENT is the finding. Exits 0 when the two profiles differ on "
            "every compared vector, 1 when any vector collides, 3 when the "
            "comparison rests only on readings nobody obtained."
        ),
    )
    cmp_.add_argument("a", help="snapshot of the first profile")
    cmp_.add_argument("b", help="snapshot of the second profile")
    cmp_.add_argument(
        "--allow-cross-engine",
        action="store_true",
        help=(
            "compare snapshots taken on DIFFERENT engines (refused by default: "
            "a vector may differ because of the engine rather than the seed, "
            "so an empty result would not be evidence of unlinkability)"
        ),
    )
    cmp_.set_defaults(func=_cmd_compare)

    lst = sub.add_parser("list", help="print the probe inventory")
    lst.set_defaults(func=_cmd_list)

    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
