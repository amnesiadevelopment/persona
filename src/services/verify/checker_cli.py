"""Operator CLI for reading the checker matrix (Level 3 of the project bar).

    python -m src.services.verify.checker_cli read -o reading.json

One subcommand, deliberately. This is a CAPABILITY THAT CAN BE INVOKED, not a
gate: it produces a reading and records it, and it must not acquire the
authority to stop anything. Comparison against a baseline, a triage rule and a
refusal are a later slice and depend on a baseline existing — which is what
this produces.

THE MATRIX: one command surface, both engines, more than one machine
--------------------------------------------------------------------
persona ships TWO engines and a profile declares a machine, so a reading that
covers one of each answers a fraction of the question. ``--engine``,
``--declared-machine`` and ``--seed`` each take a LIST, and the run is their
cross product — one record per configuration, each tagged in its own header
with the engine, the machine and the seed that produced it.

    # both engines, two machines, two seeds — 6 records (see the collapse below)
    python -m src.services.verify.checker_cli read \\
        --engine both --declared-machine windows,macos --seed 4242,1337 \\
        -o readings/

It is one surface rather than a second tool beside the first because the
alternative is two readers that drift: the engines already disagree about what
they present, and a difference between them is only readable if everything
except the engine was held identical. Both engines reach the SAME loop, the
same patterns and the same record format (see ``browser_tier``).

THE ENGINES ARE NOT SYMMETRIC, AND THE RECORD SAYS SO
------------------------------------------------------
Chromium honours the declared machine (``--fingerprint-platform``). The Firefox
engine CANNOT be asked — ``InvisiblePlaywright`` has no OS/platform parameter
at all — and presents Windows regardless, the same behaviour the product
records as #211. So:

* a firefox configuration records ``declared_machine: "windows"`` with
  ``declared_machine_honoured: false``, whatever was requested. Echoing the
  request instead would fabricate a machine the engine never declared, and a
  later comparison would read the difference as a product coupling.
* asking firefox for several machines COLLAPSES to one run, and the collapse is
  printed. Running it twice would spend a full browser run producing a second
  record identical to the first but claiming a different machine — the worst
  possible artefact for a comparator.

Exit codes — the refusal is the interesting one:

    0   every configuration asked for was read and recorded. NOT a claim that
        every verdict was good: a recorded adverse verdict still exits 0,
        because this tool reports and does not gate. Read the record.
    2   REFUSED — the exit could not be proven for AT LEAST ONE configuration,
        so that configuration read nothing and wrote nothing. A missing
        credential, an unusable one, a refused connection, a timeout, or an
        exit that is not Polish all land here. This is the code that matters:
        the alternative to refusing is a complete-looking reading of the
        OPERATOR'S REAL ADDRESS taken against a dozen fingerprinting services,
        and direct egress works from this container, so that outcome is one
        silent failure away at all times. On a multi-configuration run the
        records that DID succeed are still written and named on stderr; the
        non-zero code is what stops a partial matrix reading as a whole one.
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
import os
import platform
import sys

from .browser_tier import (
    CHROMIUM,
    DECLARED_MACHINES,
    DEFAULT_DECLARED_MACHINE,
    ENGINES,
    FIREFOX,
    declared_machine_for,
    honours_declared_machine,
)
from .checkers import BROWSER_CHECKERS, JSON_CHECKERS, UNREADABLE_CHECKERS
from .exit_guard import DEFAULT_CREDENTIAL_PATH, ExitNotProven, prove_exit, redact
from .matrix import (
    build_record,
    dumps,
    read_json_tier,
    read_unreadable_tier,
    readings_for_unread_checker,
    write,
)


# What this environment is, recorded in the header. The reading is only
# interpretable against the machine it was taken on: the WebGL renderer class
# is host-driven, and this container has no GPU.
def _environment() -> str:
    return f"{platform.system().lower()}-{platform.machine()} (agent sandbox)"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _firefox_label() -> str:
    """The Firefox engine build, as a recorded fact.

    Resolved from the installed ``invisible_playwright`` rather than hardcoded:
    a reading taken under a different engine build must not claim this one.
    Falls back to a name that says it is unknown rather than to a plausible
    guess.
    """
    try:
        from invisible_playwright.constants import BINARY_VERSION

        return f"invisible_playwright/{BINARY_VERSION}"
    except Exception:
        return "invisible_playwright/unknown"


def _chromium_label() -> str:
    """The Chromium engine build, read from what is actually installed.

    Same rule as the Firefox label and the same failure mode if it were
    hardcoded — with one extra trap this engine has and the other does not:
    stock chromium exists on most machines and is NOT the product, so a label
    that could not distinguish them would let a stock reading masquerade as a
    persona one. This names persona's own installed build or says unknown.
    """
    try:
        from ..engine.updater import current_version

        version = (current_version() or "").strip()
        return f"fingerprint-chromium/{version}" if version else (
            "fingerprint-chromium/unknown"
        )
    except Exception:
        return "fingerprint-chromium/unknown"


def _engine_label(engine: str) -> str:
    return _chromium_label() if engine == CHROMIUM else _firefox_label()


def _split_list(raw: "list[str] | None", default: "list[str]") -> "list[str]":
    """Flatten repeated and comma-separated flag values, order-preserving.

    ``--engine both --engine chromium`` and ``--engine both,chromium`` are the
    same request. Duplicates are dropped rather than run twice: two identical
    configurations produce two records that differ only by timestamp, which
    tells a comparator nothing and costs a full browser run each.
    """
    if not raw:
        return list(default)
    out: "list[str]" = []
    for chunk in raw:
        for part in str(chunk).split(","):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out


def _resolve_engines(raw: "list[str] | None") -> "list[str]":
    values = _split_list(raw, [FIREFOX])
    out: "list[str]" = []
    for value in values:
        if value == "both":
            for engine in ENGINES:
                if engine not in out:
                    out.append(engine)
            continue
        if value not in ENGINES:
            raise SystemExit(
                f"unknown engine {value!r}: persona ships "
                f"{' and '.join(ENGINES)} (or 'both')"
            )
        if value not in out:
            out.append(value)
    return out


def _resolve_machines(raw: "list[str] | None") -> "list[str]":
    values = _split_list(raw, [DEFAULT_DECLARED_MACHINE])
    for value in values:
        if value not in DECLARED_MACHINES:
            raise SystemExit(
                f"unknown declared machine {value!r}: choose from "
                f"{', '.join(DECLARED_MACHINES)}"
            )
    return values


def _resolve_seeds(raw: "list[str] | None") -> "list[int]":
    values = _split_list(raw, ["0"])
    out: "list[int]" = []
    for value in values:
        try:
            seed = int(value)
        except ValueError:
            raise SystemExit(f"seed must be an integer, got {value!r}")
        if seed not in out:
            out.append(seed)
    return out


def _plan(
    engines: "list[str]", machines: "list[str]", seeds: "list[int]"
) -> "tuple[list[tuple[str, str, int]], list[str]]":
    """The configurations to run, and what was collapsed on the way.

    The cross product, minus the runs that would be duplicates of each other.
    Firefox cannot declare a machine, so every machine asked of it names the
    SAME configuration; running each would burn a browser run per machine and
    produce records that differ only in a header field the engine never
    honoured. It is collapsed to one run and the collapse is REPORTED, because
    a silently-dropped request is indistinguishable from one that was never
    made.
    """
    plan: "list[tuple[str, str, int]]" = []
    notes: "list[str]" = []
    for engine in engines:
        effective = machines
        if not honours_declared_machine(engine) and len(machines) > 1:
            effective = machines[:1]
            notes.append(
                f"{engine}: asked for {len(machines)} declared machines "
                f"({', '.join(machines)}) but this engine has no OS parameter "
                f"and presents "
                f"{declared_machine_for(engine)} regardless — collapsed to one "
                "run rather than writing near-identical records claiming "
                "different machines"
            )
        for machine in effective:
            for seed in seeds:
                plan.append((engine, machine, seed))
    return plan, notes


def _record_name(engine: str, machine: str, seed: int) -> str:
    return f"reading.{engine}.{machine}.seed{seed}.json"


def _notes_for(
    engine: str, requested_machine: str, allow_unsandboxed: bool = False
) -> "list[str]":
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
    if not honours_declared_machine(engine):
        notes.append(
            f"This engine was asked to declare {requested_machine!r} and "
            f"CANNOT: invisible_playwright takes no OS/platform argument and "
            f"presents {declared_machine_for(engine)} regardless (the "
            "behaviour the product records as #211). The header states what "
            "the engine actually declared, with "
            "declared_machine_honoured=false. A reading whose header echoed "
            "the request would name a machine that was never presented."
        )
    if engine == CHROMIUM:
        notes.append(
            "Chromium cannot authenticate to a SOCKS5 proxy, so the credential "
            "was carried by persona's own hardened loopback relay "
            "(services/proxy/bridge.ProxyBridge) and the browser was pointed "
            "at 127.0.0.1. The relay's peer gate was claimed for this browser "
            "process, so no other local process could use the operator's exit."
        )
        if allow_unsandboxed:
            notes.append(
                "THIS READING WAS TAKEN WITH --no-sandbox. The host forbids "
                "the unprivileged user namespace chromium's sandbox needs, and "
                "the operator waived it explicitly "
                "(--allow-unsandboxed-chromium). persona's own launch path "
                "passes that flag NOWHERE, so this is NOT the surface the "
                "product presents to a checker: treat any difference against a "
                "sandboxed reading as possibly environmental until it is "
                "reproduced on a host where the sandbox works."
            )
    return notes


def _read_one(
    args: argparse.Namespace,
    engine: str,
    requested_machine: str,
    seed: int,
) -> "dict | None":
    """Read the whole matrix once, for ONE configuration.

    Returns the record, or ``None`` when the exit could not be proven — in
    which case nothing was read and nothing is written for this configuration.
    The exit is proven PER CONFIGURATION rather than once for the run: the
    exit rotates by design, and a record must carry the address its own
    readings were actually taken through.
    """
    label = f"{engine}/{declared_machine_for(engine, requested_machine)}/seed{seed}"
    print(f"\n=== {label} ===", file=sys.stderr)

    # The precondition. Everything below depends on it and nothing is recorded
    # until it holds.
    try:
        proxy_url, observed = prove_exit(credential_path=args.credential)
    except ExitNotProven as exc:
        print(f"REFUSED: {redact(str(exc))}", file=sys.stderr)
        print(
            f"Nothing was read and nothing was written for {label}.",
            file=sys.stderr,
        )
        return None

    print(
        f"exit proven: {observed.ip} {observed.city}/{observed.country} "
        f"{observed.org}",
        file=sys.stderr,
    )

    readings = []
    skipped_tiers: "list[str]" = []

    if args.skip_json:
        # A SKIPPED TIER KEEPS ITS FULL WIDTH. Dropping the rows would make the
        # record silently narrower on exactly the runs where less was read, and
        # a later comparison could not tell "the tier was skipped" from "those
        # checkers were dropped from the catalogue" from "that schema had no
        # such tier". Same principle as readings_for_unread_checker one level
        # up, and the same PS-58 vocabulary: a reading that did not happen must
        # never read as one that did.
        skipped_tiers.append("json")
        for checker in JSON_CHECKERS:
            readings.extend(
                readings_for_unread_checker(
                    checker, "tier skipped by --skip-json"
                )
            )
        print(
            f"json tier: SKIPPED ({len(readings)} rows recorded unobtainable)",
            file=sys.stderr,
        )
    else:
        readings.extend(read_json_tier(proxy_url))
        print(f"json tier: {len(readings)} readings", file=sys.stderr)

    before = len(readings)
    if args.skip_browser:
        skipped_tiers.append("browser")
        for checker in BROWSER_CHECKERS:
            readings.extend(
                readings_for_unread_checker(
                    checker, "tier skipped by --skip-browser"
                )
            )
        print(
            f"browser tier: SKIPPED "
            f"({len(readings) - before} rows recorded unobtainable)",
            file=sys.stderr,
        )
    else:
        # Imported here: the browser tier pulls the engine, which must not be
        # required to read the JSON tier or to print --help.
        from .browser_tier import read_browser_tier

        readings.extend(
            read_browser_tier(
                proxy_url,
                seed=seed,
                engine=engine,
                declared_machine=requested_machine,
                allow_unsandboxed=args.allow_unsandboxed_chromium,
            )
        )
        print(
            f"browser tier: {len(readings) - before} readings",
            file=sys.stderr,
        )

    readings.extend(read_unreadable_tier())

    return build_record(
        readings,
        exit_=observed,
        engine=_engine_label(engine),
        observed_at=_now(),
        environment=_environment(),
        seed=seed,
        declared_machine=declared_machine_for(engine, requested_machine),
        declared_machine_honoured=honours_declared_machine(engine),
        skipped_tiers=skipped_tiers,
        notes=_notes_for(engine, requested_machine),
    )


def _cmd_read(args: argparse.Namespace) -> int:
    engines = _resolve_engines(args.engine)
    machines = _resolve_machines(args.declared_machine)
    seeds = _resolve_seeds(args.seed)
    plan, collapse_notes = _plan(engines, machines, seeds)

    for note in collapse_notes:
        print(f"note: {note}", file=sys.stderr)
    print(
        f"plan: {len(plan)} configuration(s) — "
        + ", ".join(
            f"{e}/{declared_machine_for(e, m)}/seed{s}" for e, m, s in plan
        ),
        file=sys.stderr,
    )

    if len(plan) > 1 and args.output == "-":
        raise SystemExit(
            "a multi-configuration run writes one record per configuration, "
            "so --output must be a DIRECTORY rather than '-'. Concatenating "
            "several records on stdout would produce a file that is not a "
            "record of anything."
        )

    written: "list[str]" = []
    refused = 0

    for engine, machine, seed in plan:
        record = _read_one(args, engine, machine, seed)
        if record is None:
            refused += 1
            continue

        if args.output == "-":
            sys.stdout.write(dumps(record))
            written.append("-")
            continue

        if len(plan) > 1:
            os.makedirs(args.output, exist_ok=True)
            path = os.path.join(
                args.output,
                _record_name(
                    engine, declared_machine_for(engine, machine), seed
                ),
            )
        else:
            path = args.output
        write(record, path)
        written.append(path)
        counts = record["counts"]
        print(
            f"wrote {path}: {counts['total']} readings "
            f"({counts['read']} read, {counts['absent']} absent, "
            f"{counts['unobtainable']} unobtainable)",
            file=sys.stderr,
        )

    if refused:
        print(
            f"\nREFUSED {refused} of {len(plan)} configuration(s); "
            f"{len(written)} record(s) written. A partial matrix must not read "
            "as a whole one.",
            file=sys.stderr,
        )
        return 2
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
            "connection. --engine, --declared-machine and --seed each take a "
            "list and the run is their cross product, one record per "
            "configuration."
        ),
    )
    rd.add_argument(
        "-o", "--output", default="-",
        help=(
            "record path, or '-' for stdout (default: -). A run covering more "
            "than one configuration writes one record per configuration and "
            "treats this as a DIRECTORY"
        ),
    )
    rd.add_argument(
        "--credential", default=DEFAULT_CREDENTIAL_PATH,
        help=f"proxy credential path (default: {DEFAULT_CREDENTIAL_PATH})",
    )
    rd.add_argument(
        "--seed", action="append",
        help=(
            "engine fingerprint seed for the browser tier (default: 0). "
            "Repeatable or comma-separated: two profiles differing ONLY by "
            "seed should present two different machines, which is not "
            "observable from a single-seed run"
        ),
    )
    rd.add_argument(
        "--engine", action="append",
        help=(
            f"which of persona's engines reads the pages: "
            f"{', '.join(ENGINES)}, or 'both' (default: {FIREFOX}). "
            "Repeatable or comma-separated"
        ),
    )
    rd.add_argument(
        "--declared-machine", action="append",
        help=(
            f"the OS the profile PRESENTS: {', '.join(DECLARED_MACHINES)} "
            f"(default: {DEFAULT_DECLARED_MACHINE}). Repeatable or "
            "comma-separated. Honoured on chromium; the firefox engine has no "
            "OS parameter and presents "
            f"{declared_machine_for(FIREFOX)} regardless, which the record "
            "states rather than echoing the request"
        ),
    )
    rd.add_argument(
        "--allow-unsandboxed-chromium", action="store_true",
        help=(
            "run persona's chromium with --no-sandbox. OFF by default and "
            "never inferred: persona's own launch path passes that flag "
            "NOWHERE, so a reading taken with it is not the product's surface "
            "and the record says so. Needed only on a host that forbids the "
            "unprivileged user namespace the sandbox requires, where chromium "
            "otherwise dies before opening a debug port"
        ),
    )
    rd.add_argument(
        "--skip-browser", action="store_true",
        help=(
            "read only the JSON tier. The browser-tier rows are still RECORDED "
            "— as UNOBTAINABLE, with 'tier skipped' as the reason, and the "
            "header names the skipped tier. A skipped tier never shrinks the "
            "record and is never recorded as passing"
        ),
    )
    rd.add_argument(
        "--skip-json", action="store_true",
        help=(
            "read only the browser tier. The JSON-tier rows are still RECORDED "
            "as UNOBTAINABLE, exactly as with --skip-browser"
        ),
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
