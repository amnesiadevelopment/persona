"""Operator CLI for reading the checker matrix (Level 3 of the project bar).

    python -m src.services.verify.checker_cli read -o reading.json
    python -m src.services.verify.checker_cli compare before.json after.json

``read`` produces a reading and records it. ``compare`` holds one record
against another and reports what MOVED, classified by whether it could have
moved on its own.

Neither is a gate. These are CAPABILITIES THAT CAN BE INVOKED, and they must
not acquire the authority to stop anything: the charter's rule is that a
difference opens a triage, and what the triage finds is what blocks. Wiring a
refusal into a release is a later slice that depends on this existing first.

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

Exit codes for ``read`` — the refusal is the interesting one:

    0   every configuration asked for was read and recorded, AND each one
        gathered enough fingerprint evidence to be read as a reading. NOT a
        claim that every verdict was good: a recorded adverse verdict still
        exits 0, because this tool reports and does not gate. Read the record.
    3   INCONCLUSIVE — at least one configuration was read and written, and
        did not gather enough to mean anything. The exit was proven, the file
        exists and its counts are honest; what it does not contain is evidence.
        PS-110 is the run that made this code necessary: pixelscan crashed the
        Chromium renderer, every checker sequenced after it died with the
        context, and the run recorded TWO fingerprint-bearing rows out of 27,
        printed "browser tier: 37 readings", wrote the record and exited 0 —
        indistinguishable, in every field it carried, from a clean run.

        Not folded into 2, because nothing was refused: the exit WAS proven
        and a record WAS written, and a caller that treats this as "I could
        not look" would discard a record that is worth reading. Not folded
        into 0 for the obvious reason, and never into 1 — the run did not
        crash. It is deliberately the same code ``compare`` uses for "no
        finding, but the coverage this rests on is not what you think",
        because that is exactly what this is, one lane over.

        THIS IS NOT A VERDICT ABOUT PERSONA. It says the RUN failed to
        measure, never that the product failed anything — the same line
        ``baseline.py`` draws with "an unobtainable reading is inconclusive,
        and inconclusive is never a pass". A gate that read this as a product
        regression would fire on a dead browser; one that read it as success
        cannot fail for the reason it exists. The record carries the verdict
        too, in ``evidence``, because the record outlives the terminal.
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

Exit codes for ``compare`` — the SAME convention, which PS-61 settled and this
subcommand adopts rather than re-deciding:

    0   nothing to triage. Either the records agree, or everything that moved
        was exit rotation, harness movement, rewording, a catalogue change, or
        a row that is unreadable in both records (the standing state of this
        matrix — see ``matrix_diff.coverage_lost``). Read the output anyway:
        0 means "no FINDING", never "no differences"
    1   at least one FINDING — a fingerprint row moved, a host row moved on one
        machine, or an untagged row moved. This is what opens a triage, and it
        is deliberately the same code ``cli.py`` uses for drift
    3   no finding, but THE COVERAGE THIS RESTS ON IS NOT WHAT YOU THINK. Two
        ways in, sharing this code because they share that meaning and the
        same response — look at the run, not at the product:
          * COVERAGE WAS LOST: a row that was readable in the earlier record
            is unobtainable in the later one
          * NO EVIDENCE AT ALL: not one row was read or absent on EITHER side,
            so nothing was compared. This is the AGGREGATE floor and it is a
            different question from a row unreadable on both sides — 24 of 53
            unreadable is this matrix's designed steady state (see
            ``matrix_diff.coverage_lost``), 0 of 53 is a run that did not
            happen. Both used to exit 0, which told a gate the product was
            clean on a run that observed nothing
        Never folded into 1, because a run that failed is not the product
        moving — that conflation is exactly what would train a reader to skim
        a red report
    2   REFUSED. A file that could not be read, a file that is not a record, or
        two records that cannot be compared at all (different seeds, different
        engine builds, different schema versions). Never 1: a refusal is not a
        finding, and the drift code must never mean "I could not look"

Note 2 means one thing across both subcommands — "could not do what you asked"
— exactly as it does across ``cli.py``'s three. What varies is only WHICH
premise failed, and a caller reading exit codes alone never has to know: 2 is
never a verdict about the identity.

There is deliberately NO ``--no-proxy``, ``--allow-direct`` or ``--force``
flag on ``read``. A caller cannot ask this tool to read a checker over a direct
connection, because there is no argument that would make that a good idea: a
fingerprint reading taken over the wrong exit is worse than no reading, since
it looks like data.

``compare`` needs no exit at all — it reads two files — which is deliberate and
is what makes the comparison testable when the link is down, on exactly the
runs where an operator most wants to know what the last good record said.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import sys
from typing import Any

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
from .masking_layer import FIREFOX_VECTORS as FIREFOX_LAYER_VECTORS
from .layer_differential import (
    AXIS_LAYER,
    AXIS_SEED,
    DEFAULT_CONTROL_SEED as DIFF_DEFAULT_CONTROL_SEED,
    DEFAULT_SEED as DIFF_DEFAULT_SEED,
)
from .exit_guard import DEFAULT_CREDENTIAL_PATH, ExitNotProven, prove_exit, redact
from .evidence import is_inconclusive
from .matrix import (
    ABSENT,
    READ,
    UNOBTAINABLE,
    build_record,
    dumps,
    read_json_tier,
    read_unreadable_tier,
    readings_for_unread_checker,
    write,
)
from .matrix_diff import (
    ComparisonNotControlled,
    NotARecord,
    RecordUnreadable,
    compare_records,
    coverage_lost,
    findings,
    format_comparison,
    header_notes,
    no_evidence,
    require_record,
)
from .snapshot import quote_path


# What this environment is, recorded in the header. The reading is only
# interpretable against the machine it was taken on.
#
# NOTE this no longer excuses the GPU. It used to say "this container has no
# GPU" as the standing explanation for a software renderer; the owner withdrew
# that exemption on 2026-08-22 (PS-10). The environment is still recorded —
# a reading must say where it was taken — but a GPU row is now judged as the
# product's, not the container's.
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


def _resolve_layer_vectors(args: argparse.Namespace) -> "tuple[str, ...] | None":
    """The layer subset for a subtraction arm, or None for the full product set.

    ``--layer-vectors`` and ``--drop-layer-vector`` are two spellings of one
    question ("which spoofs are installed?") and are mutually exclusive: given
    both, the operator has stated the subset twice and there is no reading that
    honours both spellings, so the run is refused rather than resolved by a
    precedence rule nobody would remember.

    Returns ``None`` when neither was passed, which is the full product set —
    the arm that actually describes what an operator presents.
    """
    keep = _split_list(getattr(args, "layer_vectors", None), [])
    drop = _split_list(getattr(args, "drop_layer_vector", None), [])
    if keep and drop:
        raise SystemExit(
            "--layer-vectors and --drop-layer-vector contradict each other: "
            "both name the subset to install, one by what stays and one by "
            "what goes. Pass one."
        )
    if not keep and not drop:
        return None
    if getattr(args, "no_masking_layer", False):
        raise SystemExit(
            "--no-masking-layer removes the WHOLE layer, so naming a subset of "
            "it with --layer-vectors/--drop-layer-vector asks for two different "
            "arms at once. Drop one: --no-masking-layer for the control arm, "
            "the subset flags for a subtraction arm."
        )
    known = set(FIREFOX_LAYER_VECTORS)
    named = keep or drop
    unknown = sorted(set(named) - known)
    if unknown:
        raise SystemExit(
            f"not vector(s) persona's firefox layer builds: "
            f"{', '.join(unknown)}. This engine builds "
            f"{', '.join(FIREFOX_LAYER_VECTORS)}. Refused rather than ignored: "
            f"a subtraction arm that silently installed the FULL layer would "
            f"read as an exoneration of the vector you meant to remove."
        )
    if keep:
        return tuple(v for v in FIREFOX_LAYER_VECTORS if v in set(keep))
    return tuple(v for v in FIREFOX_LAYER_VECTORS if v not in set(drop))


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
    engine: str,
    requested_machine: str,
    allow_unsandboxed: bool = False,
    install_layer: bool = True,
    layer_vectors: "tuple[str, ...] | None" = None,
) -> "list[str]":
    notes = [
        "The GPU rows are PRODUCT rows, not environment notes. This container "
        "has no GPU, and that is NOT an exemption: the owner ruled "
        "(2026-08-22, PS-10) that there will be no dev-VM and no GPU machine "
        "in the loop, and that the engine is expected to present a plausible "
        "GPU wherever it runs, including on a host that has none. A red on "
        "either GPU row is therefore a masking finding, filed against "
        "undetectable-masking with the reading attached — never written off "
        "as the container's fault.",
        "The two GPU vectors are recorded SEPARATELY and must be reported "
        "that way: vector=gpu_claimed is what the renderer SAYS IT IS (the "
        "WEBGL_debug_renderer_info strings, which persona chooses), and "
        "vector=gpu_rendered is what the checker's OWN RENDERING PRODUCED "
        "(canvas/WebGL hashes computed from pixels, which persona does not "
        "choose). They have different fixes, so a merged 'GPU red' cannot be "
        "acted on. A plausible claimed string beside a hash produced by "
        "software rendering is the 'the string is right but the render gives "
        "us away' case the owner called a defect rather than an accepted "
        "limit — and neither row alone can show it.",
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
    if not install_layer:
        notes.append(
            "THIS READING IS OF THE PACKAGED ENGINE ONLY, with none of "
            "persona's masking layer (--no-masking-layer). It is the CONTROL "
            "ARM of a differential, not a reading of the product. A verdict "
            "here describes what the engine presents BEFORE persona's layer "
            "reaches the page — so a clean row is not evidence the product is "
            "clean, and an adverse row is not evidence the layer caused it. "
            "Read it only ALONGSIDE the arm taken with the layer installed, "
            "and only if both arms record the SAME exit address: the exit "
            "rotates by design, and two arms taken through different addresses "
            "are not a comparison."
        )
    if install_layer and layer_vectors is not None:
        kept = ", ".join(layer_vectors) or "(none)"
        print_removed = ", ".join(
            v for v in FIREFOX_LAYER_VECTORS if v not in layer_vectors
        ) or "(none)"
        notes.append(
            f"THIS READING IS OF A DELIBERATELY INCOMPLETE LAYER "
            f"(--layer-vectors), NOT OF THE PRODUCT. Installed: {kept}. "
            f"REMOVED: {print_removed}. It is a SUBTRACTION ARM — the point is "
            f"to find which spoof a checker reacts to by removing them one at a "
            f"time, so a clean row here means only that the REMAINING vectors "
            f"did not trigger it, and an adverse row does not implicate any "
            f"single one of them. The product installs the full set, so no "
            f"verdict in this record describes what an operator presents. Read "
            f"it only ALONGSIDE the full-layer arm, and only if both arms "
            f"record the SAME exit address: the exit rotates by design, and "
            f"two arms taken through different addresses are not a comparison."
        )
    return notes


def _tally(readings, since: int = 0) -> str:
    """What a tier gathered, with READINGS meaning rows that were read.

    THE COUNT THAT USED TO LIE. This line was ``len(readings) - before`` — the
    number of ROWS the tier appended, unobtainable ones included. Every unread
    checker contributes its full width by design (see
    ``matrix.readings_for_unread_checker``), so that figure GREW when nothing
    was read: the PS-110 run printed ``browser tier: 37 readings`` having
    obtained sixteen rows and two fingerprint-bearing ones. A number that goes
    up when the browser dies is not a count of readings.

    Unobtainable rows are still reported — they are useful, and dropping them
    would hide that the tier kept its width — but they are reported BESIDE the
    figure rather than summed into it.
    """
    rows = readings[since:]
    read = sum(1 for r in rows if r.state == READ)
    absent = sum(1 for r in rows if r.state == ABSENT)
    unobtainable = sum(1 for r in rows if r.state == UNOBTAINABLE)
    return (
        f"{read} read, {absent} absent "
        f"({unobtainable} unobtainable, {len(rows)} rows)"
    )


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
    # What persona's masking layer did, for the record header. None means the
    # browser tier never ran (it was skipped), which is recorded as null rather
    # than as an empty layer: "no layer was installed" is a measurement and
    # "this run did not ask the question" is not, and a consumer must be able
    # to tell them apart.
    layer_report: "list[Any]" = [None]

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
        print(f"json tier: {_tally(readings)}", file=sys.stderr)

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

        # The layer report arrives by callback because read_browser_tier's
        # return value is a list of readings that several callers unpack. It is
        # always written: read_browser_tier reports an ABSENT layer on the
        # engine-unavailable path too, so a header can never keep the
        # "not stated" default over a run that really did launch a browser.
        def _capture_layer(report):
            layer_report[0] = report

        readings.extend(
            read_browser_tier(
                proxy_url,
                seed=seed,
                engine=engine,
                declared_machine=requested_machine,
                allow_unsandboxed=args.allow_unsandboxed_chromium,
                layer_sink=_capture_layer,
                install_layer=not args.no_masking_layer,
                layer_vectors=_resolve_layer_vectors(args),
            )
        )
        print(
            f"browser tier: {_tally(readings, before)}",
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
        masking_layer=(
            layer_report[0].as_record() if layer_report[0] is not None else None
        ),
        skipped_tiers=skipped_tiers,
        notes=_notes_for(
            engine,
            requested_machine,
            allow_unsandboxed=args.allow_unsandboxed_chromium,
            install_layer=not args.no_masking_layer,
            layer_vectors=_resolve_layer_vectors(args),
        ),
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

    if args.no_masking_layer and args.skip_browser:
        raise SystemExit(
            "--no-masking-layer and --skip-browser contradict each other: the "
            "layer is installed in the BROWSER tier, so a run that skips that "
            "tier never installs it and never would have. The record would "
            "claim to be a deliberate control arm while carrying no browser "
            "reading to be the control arm OF — and a later comparison could "
            "not tell that from a real engine-only reading. Drop one."
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
    inconclusive = 0

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
        evidence = record["evidence"]
        print(
            f"wrote {path}: {counts['read']} read, {counts['absent']} absent "
            f"({counts['unobtainable']} unobtainable, {counts['total']} rows)",
            file=sys.stderr,
        )
        # The verdict, on the console AND in the file. The console line is what
        # a human watching sees; the record is what outlives the terminal and
        # what a later comparison reads. A run that measured nothing has to be
        # unmistakable on both, which is why neither is left to be inferred
        # from the counts above.
        if is_inconclusive(evidence):
            inconclusive += 1
            print(
                f"  INCONCLUSIVE: {evidence['fingerprint_obtained']} of "
                f"{evidence['fingerprint_total']} fingerprint-bearing rows "
                f"were obtained, from "
                f"{len(evidence['checkers_contributing'])} checker(s). "
                "This run did not gather enough to be read as a reading of "
                "the identity — it is NOT a clean result.",
                file=sys.stderr,
            )
            for reason in evidence["reasons"]:
                print(f"    - {reason}", file=sys.stderr)
        else:
            print(
                f"  evidence: SUFFICIENT "
                f"({evidence['fingerprint_obtained']}/"
                f"{evidence['fingerprint_total']} fingerprint rows, "
                f"{len(evidence['checkers_contributing'])} checkers)",
                file=sys.stderr,
            )
            for reason in evidence["reasons"]:
                # A run can clear the floor and still have lost a tail to a
                # dead session. Saying so on a PASS is the point: it is the
                # run worth looking at that nothing would otherwise flag.
                print(f"    note: {reason}", file=sys.stderr)

    if refused:
        print(
            f"\nREFUSED {refused} of {len(plan)} configuration(s); "
            f"{len(written)} record(s) written. A partial matrix must not read "
            "as a whole one.",
            file=sys.stderr,
        )
        return 2
    if inconclusive:
        print(
            f"\nINCONCLUSIVE {inconclusive} of {len(written)} record(s): the "
            "run did not gather enough fingerprint evidence to be read. The "
            "record(s) were still written and say so — read the `evidence` "
            "block. This is NOT a verdict about persona: it says the RUN "
            "failed to measure, not that the product failed anything.",
            file=sys.stderr,
        )
        return 3
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    """Hold one record against another and report what moved.

    Reads two files and nothing else. No exit is proven and no network is
    touched, deliberately: this consumes what ``read`` wrote and must not
    acquire a second path to the checkers. It also makes the comparison usable
    when the link is down — which is exactly when an operator wants to know
    what the last good record said.

    Every refusal below returns 2 and prints to stderr, so the report on stdout
    is never half a comparison. See the module header for why 2 is never 1.
    """
    try:
        before = _load_record(args.before)
        after = _load_record(args.after)
        entries = compare_records(
            before,
            after,
            allow_cross_engine=args.allow_cross_engine,
            allow_different_seed=args.allow_different_seed,
            allow_different_machine=args.allow_different_machine,
        )
    except (ComparisonNotControlled, NotARecord, RecordUnreadable) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        print(
            "Nothing was compared, so this is NOT a finding.", file=sys.stderr
        )
        return 2

    print(format_comparison(entries, notes=header_notes(before, after)))

    # The order is the argument. A FINDING outranks lost coverage: when both
    # happened, the caller is told the identity moved, which is the louder
    # fact. But lost coverage must never be folded INTO that code — a run that
    # failed is not the product moving.
    if findings(entries):
        return 1
    if coverage_lost(entries):
        return 3

    # The AGGREGATE evidence floor, asked once, of the records rather than of
    # the entries — and asked LAST on purpose. Neither rung above can be
    # reached when nothing was obtained (a finding needs a reading on both
    # sides, lost coverage needs one on exactly one), so this cannot change any
    # verdict that exists today: it can only split the old 0 into "read and
    # agreed" and "nothing was read". That is the whole change.
    #
    # It is NOT the per-row question and does not reopen it. UNREAD_BOTH stays
    # out of the ladder for the reason `coverage_lost` documents: 24 of 53 rows
    # permanently unreadable is this matrix's designed steady state, and a code
    # firing on it would be ignored within a week. Nought of 53 is a different
    # fact — a run that did not happen — and until now the two shared an exit
    # code. `cli.py` already draws this exact line for the snapshot lane, and
    # `baseline.py` refuses a zero-reading snapshot for the same reason.
    #
    # 3, not a new code: it already means "no finding, but the coverage this
    # rests on is not what you think", which is precisely what this is. Never
    # 0, and never folded into 1 — a run that did not happen is not the
    # product moving.
    if no_evidence(before, after):
        print(
            "\nNO EVIDENCE: not one reading was obtained on either side — no "
            "row is 'read' or 'absent' in either record, so nothing was "
            "compared. This is NOT agreement. That shape is what a REFUSED or "
            "truncated recording leaves behind (an engine that would not "
            "launch, a dead exit), not a clean matrix; check the recording "
            "before reading anything into this comparison."
        )
        return 3
    return 0


def _load_record(path: str) -> dict:
    """Load a record file, refusing anything that is not a readable record.

    The refusal is raised HERE, at the load site, rather than left to the
    comparator, so the message can name the FILE the operator typed — a typo'd
    path is the expected way into this branch, and "which of my two arguments
    was wrong" is the only thing they need to know.

    Three failures, all exiting 2 and never 1, sharing the property that is the
    point rather than a list to memorise: none of them COMPARED anything, so
    none of them can be a finding. This mirrors ``cli._load_snapshot`` one
    artifact over, on the same terms PS-61 settled.
    """
    if not os.path.isfile(path):
        raise RecordUnreadable(
            f"no checker-matrix record to read at {quote_path(path)}. Nothing was "
            "compared, so this is NOT a finding — check the path, or take a "
            "reading with: `python -m src.services.verify.checker_cli read "
            "-o reading.json`."
        )
    try:
        with open(path, encoding="utf-8") as handle:
            obj = json.load(handle)
    except (OSError, ValueError) as exc:
        # ValueError, NOT json.JSONDecodeError, and the width is deliberate:
        # a file that is not valid UTF-8 raises UnicodeDecodeError, which IS a
        # ValueError but is NOT a JSONDecodeError. Catching the narrower type
        # would let a truncated or corrupt-bytes record traceback out. OSError
        # covers the read that starts and then fails (a directory, a permission
        # denial, a broken link). Same pair, same reasoning, as `cli.py`.
        raise RecordUnreadable(
            f"the record at {quote_path(path)} could not be read: {exc}. Nothing was "
            "compared, so this is NOT a finding — the recording itself is "
            "unusable. Re-take it, and check that the file was written "
            "completely."
        ) from exc
    # OUTSIDE the try, deliberately: NotARecord subclasses ValueError, so
    # raising it in there would be caught by the guard above and re-labelled
    # "could not be read" for a file that read back perfectly well.
    return require_record(obj, source=path)


def _cmd_list(args: argparse.Namespace) -> int:
    for checker in JSON_CHECKERS + BROWSER_CHECKERS:
        print(f"{checker.tier}\t{checker.id}\t{len(checker.items)} item(s)")
    for checker in UNREADABLE_CHECKERS:
        print(f"{checker.tier}\t{checker.id}\t{checker.unreadable_reason}")
    return 0


def _cmd_differential(args: argparse.Namespace) -> int:
    """Demonstrate that persona's masking layer REACHES the page a checker reads.

    Needs no credential, no proxy and no exit: the page is served from
    loopback. That venue is deliberate and has a precedent — PS-69 hit the
    missing-credential wall and was re-scoped to prove its claims without the
    exit, and PS-10 records an instruction not to re-introduce the dependency.

    The exit code is the verdict, because this is the thing that would be worth
    gating on: 0 when a reading MOVED, 1 when every comparable vector read
    identically (the PS-97 shape — the code the layer was supposed to change did
    not change what the page sees), and 2 when nothing could be compared at all.

    That last code is separated from the failure ON PURPOSE. "The engine would
    not start here" and "the layer does not reach the page" are completely
    different findings, and a run that collapsed them would report an
    unprovisioned container as a masking defect.
    """
    from .layer_differential import dumps as diff_dumps, run_differential

    record = run_differential(
        axis=args.axis,
        engine=args.engine,
        seed=args.seed,
        control_seed=args.control_seed,
        allow_unsandboxed=args.allow_unsandboxed_chromium,
    )

    text = diff_dumps(record)
    if args.output and args.output != "-":
        # Written through matrix.write, the same atomic writer the checker
        # record uses: an interrupted run must not leave a half document that
        # the next reader has to guess about.
        write(record, args.output)
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)

    print(f"\n{record['verdict'].upper()}: {record['detail']}", file=sys.stderr)
    for name, moved in sorted(record["diff"]["moved"].items()):
        print(
            f"  {name}: {moved['before']} -> {moved['after']}", file=sys.stderr
        )
    return {"moved": 0, "unmoved": 1}.get(record["verdict"], 2)


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
        "--no-masking-layer", action="store_true",
        help=(
            "read WITHOUT persona's own masking layer — the CONTROL ARM of a "
            "differential, taken live through the proven exit. Off by default: "
            "a reading without the layer does not describe the product. The "
            "record says which arm it is, in masking_layer (route='none', with "
            "the reason) and in a header note, so it can never be mistaken for "
            "a reading of the product. Both arms of a comparison must be taken "
            "close together and through the SAME exit — the address rotates by "
            "design, and an arm that rotated is not a comparison. Refused with "
            "--skip-browser, which is where the layer would have been installed"
        ),
    )
    rd.add_argument(
        "--layer-vectors", action="append",
        help=(
            "install ONLY these vectors of persona's masking layer, as the "
            "SUBTRACTION ARM of a differential (firefox: "
            + ", ".join(FIREFOX_LAYER_VECTORS) + "). Repeatable or "
            "comma-separated. This is how you find WHICH spoof a checker "
            "reacted to — remove them one at a time and re-read, rather than "
            "reading the generated source for something that looks suspicious. "
            "A reading of a subset does NOT describe the product (the product "
            "installs the full set), and the record says so in a header note "
            "naming what was kept and what was removed. A name that is not a "
            "vector is REFUSED rather than ignored: an arm that silently "
            "installed the full layer would read as an exoneration of the "
            "vector you meant to remove. Contradicts --no-masking-layer, which "
            "removes the whole layer"
        ),
    )
    rd.add_argument(
        "--drop-layer-vector", action="append",
        help=(
            "the same subtraction arm named the other way round: install the "
            "whole layer EXCEPT these. Repeatable or comma-separated. "
            "'--drop-layer-vector webgl' is the one-at-a-time subtraction step "
            "and is usually what you want; --layer-vectors is the complement. "
            "Contradicts --layer-vectors (both name the subset) and "
            "--no-masking-layer"
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

    cmp_ = sub.add_parser(
        "compare",
        help="compare two records and report what moved",
        description=(
            "Holds a later record against an earlier one and reports the "
            "DIFFERENCES ONLY, classified by the sort of the row that moved: a "
            "fingerprint row is a finding, an exit row is expected, a harness "
            "row is about this repo's own instrument. Reads two files and "
            "needs no exit. Reports; never gates."
        ),
    )
    cmp_.add_argument("before", help="the earlier record")
    cmp_.add_argument("after", help="the later record")
    cmp_.add_argument(
        "--allow-cross-engine", action="store_true",
        help=(
            "compare records taken under DIFFERENT engine builds. Refused by "
            "default: a row that moved may have moved because of the build. "
            "Does NOT relax the refusal on an UNRECORDED engine — that gives "
            "you no caveat to weigh"
        ),
    )
    cmp_.add_argument(
        "--allow-different-seed", action="store_true",
        help=(
            "compare records taken under DIFFERENT seeds. Refused by default: "
            "the engine's fingerprint is seed-derived, so those rows were "
            "never supposed to match and a diff reads as catastrophic drift. "
            "With the flag, fingerprint rows report as SEED-EXPLAINED context "
            "and never as a coupling"
        ),
    )
    cmp_.add_argument(
        "--allow-different-machine", action="store_true",
        help=(
            "compare records that DECLARED DIFFERENT MACHINES. Refused by "
            "default: the declared machine is the spine of a presented "
            "identity (GPU strings, voices, fonts, screen conventions, "
            "platform flags, UA and client hints), so those rows were never "
            "supposed to match. With the flag, fingerprint rows report as "
            "MACHINE-EXPLAINED context and never as a coupling"
        ),
    )
    cmp_.set_defaults(func=_cmd_compare)

    df = sub.add_parser(
        "differential",
        help="show that persona's masking layer reaches the page (LOCAL, no exit)",
        description=(
            "Reads a LOCAL loopback page twice, varying exactly ONE axis, and "
            "reports what moved. This is the demonstration that the harness "
            "can OBSERVE persona's masking rather than merely install it: an "
            "assertion that a builder was called is not evidence the spoof "
            "reached the page (PS-78 measured exactly that gap). Needs no "
            "credential, no proxy and no exit."
        ),
    )
    df.add_argument(
        "--axis", choices=(AXIS_LAYER, AXIS_SEED), default=AXIS_LAYER,
        help=(
            f"which single axis to vary. {AXIS_LAYER!r} (default): same engine "
            f"and seed, persona's layer installed on one side only — the arm "
            f"that answers whether the layer reaches the page. {AXIS_SEED!r}: "
            "layer on BOTH sides, only the seed moves — the control that shows "
            "the vectors really are seed-derived. ONE axis at a time always: a "
            "difference seen while two axes moved is attributable to neither"
        ),
    )
    df.add_argument(
        "--engine", choices=ENGINES, default=FIREFOX,
        help="which of persona's engines to demonstrate against",
    )
    df.add_argument(
        "--seed", type=int, default=DIFF_DEFAULT_SEED,
        help="the profile seed both arms use (the layer axis)",
    )
    df.add_argument(
        "--control-seed", type=int, default=DIFF_DEFAULT_CONTROL_SEED,
        help="the second seed, used only by the seed axis",
    )
    df.add_argument(
        "--allow-unsandboxed-chromium", action="store_true",
        help=(
            "run persona's chromium with --no-sandbox. OFF by default and "
            "never inferred: persona's own launch path passes that flag "
            "NOWHERE, so a reading taken with it is not the product's surface "
            "and the record says so. Needed only on a host that forbids the "
            "unprivileged user namespace the sandbox requires — which is the "
            "usual state of the container this runs in, and where the chromium "
            "arm otherwise refuses before launching. Ignored on firefox"
        ),
    )
    df.add_argument(
        "-o", "--output", default="-",
        help="write the differential record here ('-' for stdout)",
    )
    df.set_defaults(func=_cmd_differential)

    ls = sub.add_parser("list", help="print the checker inventory")
    ls.set_defaults(func=_cmd_list)
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
