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
from .exit_guard import (
    DEFAULT_CREDENTIAL_PATH,
    ENVIRONMENT_CREDENTIAL_VAR,
    ExitNotProven,
    prove_exit,
    redact,
)
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
from .matrix_silence import (
    NotEnoughRecords,
    alarms as silence_alarms,
    discover_record_paths,
    format_silence,
    load_record as load_silence_record,
    silence_pass,
)
from .matrix_consistency import (
    consistency_pass,
    coverage_holes,
    # Aliased: `matrix_diff.findings` is already imported above and is used by
    # the `compare` lane. Two lanes, two different questions, same word — the
    # alias keeps them apart rather than letting the later import silently
    # shadow the earlier one.
    findings as consistency_findings,
    format_consistency,
    host_leaks,
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

    ⚠️ DELIBERATELY NOT RENAMED TO ``Personium`` (PS-224). This is a RECORDED
    MEASUREMENT IDENTIFIER, not a display name, and the ticket asked for the
    decision to be made rather than let drift out of a UI rename. It stays for
    three reasons, in order of how expensive changing it would be:

    1. IT WOULD SILENTLY BREAK EXISTING COMPARISONS. 26 committed reading sets
       under ``readings/`` carry ``"engine": "fingerprint-chromium/<version>"``
       as a header value, and 36 carry the identifier somewhere. Re-derive
       either figure rather than trusting this one::

           git ls-files readings/ | xargs grep -lE \
               '"engine"[[:space:]]*:[[:space:]]*"fingerprint-chromium/' | wc -l
           git ls-files readings/ | xargs grep -l 'fingerprint-chromium/' | wc -l

       ``compare`` and the matrix tooling hold a new record against an old one;
       a changed header makes old and new readings incomparable, and NOTHING
       would report that — the exact silent-drift failure the ticket names.
    2. IT WOULD BREAK A LIVE LOOKUP, TODAY. ``pool_depth.engine_report`` finds
       an arm by case-insensitive SUBSTRING of the engine header:
       ``"chromium" in "fingerprint-chromium/148...".lower()`` is True, and
       ``"chromium" in "personium/148...".lower()`` is False. Renaming this
       would raise ``KeyError: no engine arm matching 'chromium'`` on every
       chromium pool-depth lookup.
    3. NO OPERATOR READS IT AS OUR ENGINE'S NAME. It appears in a reading
       record's JSON header, produced by a developer-facing CLI. It names the
       UPSTREAM BUILD a measurement was taken against, which is exactly what
       it should say while the binary we launch IS that upstream build.

    WHEN IT SHOULD CHANGE: when we ship a binary we built ourselves, the thing
    being measured genuinely stops being ``fingerprint-chromium`` and the label
    becomes factually wrong. At that point the change is a MEASUREMENT-BASELINE
    decision — old readings describe a different engine and must not be
    compared against new ones as though they were the same — and it belongs
    with the self-build work, not with a UI rename.
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


def _resolve_layer_vectors(
    args: argparse.Namespace,
    engines: "list[str] | None" = None,
) -> "tuple[str, ...] | None":
    """The layer subset for a subtraction arm, or None for the full product set.

    ``--layer-vectors`` and ``--drop-layer-vector`` are two spellings of one
    question ("which spoofs are installed?") and are mutually exclusive: given
    both, the operator has stated the subset twice and there is no reading that
    honours both spellings, so the run is refused rather than resolved by a
    precedence rule nobody would remember.

    ``engines`` is the engine list the run will actually use. A subset is a
    FIREFOX-ONLY capability — ``build_chromium_layer`` takes no vectors
    parameter and builds the full extension set — so naming one for chromium is
    refused here rather than silently ignored downstream. Left None only by
    callers that are resolving the value for a run whose engine is already
    fixed and checked.

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
    # Refused rather than ignored, and refused BEFORE the unknown-name check:
    # on chromium a correctly-spelled vector is the dangerous case. The layer
    # is built by ``build_chromium_layer``, which takes no vectors parameter
    # and assembles the FULL extension set, and ``read_browser_tier`` does not
    # forward ``layer_vectors`` down the chromium branch — so the subset would
    # be discarded while ``_notes_for`` stamped the record "REMOVED: locale".
    # That is the exact false exoneration the unknown-name refusal below exists
    # to prevent, reachable by a name that is spelled right.
    if engines is not None and CHROMIUM in engines:
        raise SystemExit(
            f"--layer-vectors/--drop-layer-vector name a SUBSET of the masking "
            f"layer, which only the {FIREFOX} engine can build: persona's "
            f"chromium layer ships as extensions assembled as a whole set, "
            f"with no vectors parameter to narrow. Refused rather than "
            f"ignored: the subset would be silently discarded, chromium would "
            f"read with the FULL layer installed, and the record would state "
            f"the removal anyway — so an adverse row would exonerate the very "
            f"vector you meant to remove. Take subtraction arms on "
            f"--engine {FIREFOX}."
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
    allow_small_dev_shm: bool = False,
    install_layer: bool = True,
    layer_vectors: "tuple[str, ...] | None" = None,
    credential_detail: "str | None" = None,
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
        if allow_small_dev_shm:
            notes.append(
                "THIS READING WAS TAKEN WITH --disable-dev-shm-usage. This "
                "host's /dev/shm is below the floor the tier insists on "
                "(see chromium_tier.MIN_DEV_SHM_BYTES), and the operator "
                "waived it explicitly (--allow-small-dev-shm). The flag moves "
                "chromium's renderer transport, GPU command buffers and "
                "font-data service off shared memory and onto disk. persona's "
                "own launch path passes it NOWHERE, so this is not the surface "
                "the product presents. It is disclosed rather than inferred "
                "because the failure it works around does not announce itself: "
                "chromium on a too-small /dev/shm dies MID-PAGE with a "
                "TargetClosedError that names no cause, and the run then "
                "attributes the death to whatever configuration was being "
                "read — which is how PS-128 came to report a renderer crash as "
                "a property of fingerprint seed 4242 (PS-133)."
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
            f"(--layer-vectors/--drop-layer-vector), NOT OF THE PRODUCT. "
            f"Installed: {kept}. "
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
    if credential_detail:
        # WHICH CHANNEL THE CREDENTIAL CAME FROM (PS-145).
        #
        # In the record rather than only on stderr, because the question this
        # answers is asked LATER: an operator comparing two records, or reading
        # one taken weeks ago, cannot recover which channel it used from the
        # terminal output of a run that has long since scrolled away. The
        # credential arrives on two channels now, and when they disagree the
        # run uses one of them — a record that does not say which is a record
        # whose exit cannot be fully accounted for.
        #
        # Safe to write: `detail` names a PATH or a VARIABLE NAME and is
        # already redacted at its source (`exit_guard.Credential`). The
        # credential VALUE is never in it — that split is the reason the
        # object carries `detail` separately from `proxy_url` at all.
        notes.append(f"CREDENTIAL SOURCE: {credential_detail}")
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
        proxy_url, observed, credential = prove_exit(
            credential_path=args.credential
        )
        # A PROVEN exit is not necessarily a PLACED one, and the difference is
        # only knowable HERE. `observe_exit` proves the exit on `ip` and
        # `country` alone, so a provider payload carrying no `timezone` key
        # yields a fully proven `Exit` whose zone is the empty string — and
        # that empty string means the OPPOSITE thing one layer down. To
        # `chromium_tier._launch_args` an empty zone is the honest "this venue
        # has no exit" of the loopback differential, so it passes no flag;
        # for a run that HAS an exit that puts the engine straight back on the
        # HOST clock and restores the very defect PS-132 closes. One sentinel,
        # two opposite meanings: they are separated at this boundary rather
        # than by teaching the launch path to guess which one it holds.
        #
        # Refusing rather than filling the gap in is the product's own settled
        # answer one axis over: `process.py:_profile_timezone` will not launch
        # a profile whose proxy has no geography, because "deriving the
        # timezone from the host would declare the operator's real location
        # inside the tunnel". `exit_guard`'s docstring is on the same side —
        # report and stop, never fall back. A reading taken here would not be
        # merely thin: it would describe the CONTAINER, and the checker's free
        # timezone-against-address cross-check would manufacture a
        # `timezone_spoofed` verdict against a product that is correct.
        #
        # Raised as `ExitNotProven` so it lands in the refusal path already
        # below rather than beside it: the caller's correct response is
        # identical to every other unmet precondition — record nothing for
        # this configuration — which is the reason that class is deliberately
        # one class (see its docstring).
        #
        # GATED TO THE CONFIGURATION THAT ACTUALLY READS A CLOCK, because the
        # cost of refusing is a reading the operator does not get and only
        # ONE configuration is buying anything with it:
        #
        #   * `engine == CHROMIUM` — Chromium is the engine with no fallback,
        #     which is the whole asymmetry this ticket rests on. Firefox given
        #     no zone resolves geography from the egress IP itself
        #     (`invisible_launch.py`: "with no timezone it discovers the egress
        #     IP"), so on a zoneless exit it reports the EXIT's zone, its
        #     timezone-against-address cross-check passes and the record is
        #     good. Refusing there describes no container and prevents no leak;
        #     it only throws a correct reading away.
        #   * `not args.skip_browser` — the host clock can only reach a page
        #     through a browser. A run that launches none reads no clock at
        #     all, so there is nothing to refuse; the JSON tier reads the
        #     EXIT's own address from the network, not this machine's.
        #
        # The record header does still carry a blank `exit.timezone` on those
        # runs. That is a thinness rather than a falsehood — the observation
        # genuinely did not carry a zone, and the header reports the
        # observation — so it is not worth the reading it would cost. The
        # refusal here answers "would this record describe the CONTAINER",
        # which is a narrower question than "is this record complete".
        if engine == CHROMIUM and not observed.timezone and (
            not args.skip_browser
        ):
            raise ExitNotProven(
                f"the exit {observed.ip} was proven ({observed.country}) but "
                "the observation carries no timezone. Chromium pins no zone "
                "of its own, so this reading would describe the HOST clock, "
                "and the checker's timezone-against-address cross-check would "
                "call the product spoofed for it (PS-132). Refusing to read "
                "rather than record a reading of the container."
            )
    except ExitNotProven as exc:
        print(f"REFUSED: {redact(str(exc))}", file=sys.stderr)
        print(
            f"Nothing was read and nothing was written for {label}.",
            file=sys.stderr,
        )
        return None

    print(
        f"exit proven: {observed.ip} {observed.city}/{observed.country} "
        f"{observed.org} {observed.timezone}",
        file=sys.stderr,
    )
    # WHICH CHANNEL THE CREDENTIAL CAME FROM. `detail` is built from a path or
    # a variable NAME and is already redacted — the value itself never reaches
    # here (see `exit_guard.Credential`).
    print(f"credential: {credential.detail}", file=sys.stderr)

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
                # The zone of the exit THIS RUN PROVED, from the same
                # observation the record header is built from — so the browser
                # and the record cannot disagree about where the profile is.
                # Chromium pins nothing of its own: without this it reports the
                # HOST clock, and a reading behind a Warsaw exit came back
                # UTC+0 with the checker's own timezone-vs-address cross-check
                # calling it spoofed (PS-132). Ignored on firefox, whose engine
                # resolves the zone from the egress IP when given none.
                timezone=observed.timezone,
                allow_unsandboxed=args.allow_unsandboxed_chromium,
                allow_small_dev_shm=args.allow_small_dev_shm,
                layer_sink=_capture_layer,
                install_layer=not args.no_masking_layer,
                layer_vectors=_resolve_layer_vectors(args, [engine]),
                include_geo=getattr(args, "match_product_geo", False),
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
            allow_small_dev_shm=args.allow_small_dev_shm,
            install_layer=not args.no_masking_layer,
            layer_vectors=_resolve_layer_vectors(args, [engine]),
            credential_detail=credential.detail,
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

    # Checked here against the WHOLE plan, not just per-configuration inside
    # _read_one: `--engine both --drop-layer-vector locale` would otherwise
    # spend a full firefox browser run before refusing on the chromium leg,
    # and a subtraction arm is only meaningful next to its pair.
    _resolve_layer_vectors(args, engines)

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


def _cmd_silence(args: argparse.Namespace) -> int:
    """Ask a SET of records which checkers have never once answered.

    The one lane in this CLI whose input is a set rather than a pair. `compare`
    takes exactly two records because "what moved" is a question about a pair;
    "has this checker EVER answered" is a quantifier over a set, and no number
    of pairwise comparisons recovers it — a checker that is unobtainable on
    both sides of every pair reads to the comparator as a row that did not
    move, which is its agreement signal.

    Reads files already on disk and nothing else. No network, no exit, no
    reading taken.

    The exit codes mirror the discipline the other lanes already keep, rather
    than inventing a scheme:

    ``2``
        REFUSED — the set could not support the question (unreadable file, not
        a record, or fewer than two records). Nothing was established, so it
        can never wear a code that means something was.
    ``3``
        A readable-tier checker never answered. 3 already means "no finding
        about the product, but the coverage this rests on is not what you
        think", which is exactly what this is: the matrix presents a width it
        has never actually read. It is NOT 1 — the product did not move, and
        folding a coverage fact into the drift code is the mislabelling this
        subsystem has repeatedly refused.
    ``0``
        Every readable-tier checker answered somewhere in the set.

    The catalogue-declared unreadable checkers are printed as CARRIED and
    deliberately do NOT influence the code. They are silent by design, and a
    gate that fired on them would be switched off within a week.
    """
    paths = list(args.records)
    if args.discover:
        paths.extend(discover_record_paths(args.discover))
    # Deduplicate while keeping a stable order: a record named explicitly AND
    # found by discovery must not be counted twice, or "answered in 0/N"
    # quotes a denominator the set does not have.
    seen: "set[str]" = set()
    ordered = []
    for path in paths:
        key = os.path.realpath(path)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path)

    try:
        records = [load_silence_record(path) for path in ordered]
        entries = silence_pass(records)
    except (NotARecord, RecordUnreadable, NotEnoughRecords) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        print(
            "Nothing was established, so this is NOT 'no checker is silent'.",
            file=sys.stderr,
        )
        return 2

    print(format_silence(entries, records=len(records)))
    if silence_alarms(entries):
        return 3
    return 0


def _cmd_consistency(args: argparse.Namespace) -> int:
    """Ask ONE record whether it agrees with ITSELF.

    The one lane in this CLI whose input is a single record. `compare` needs a
    pair because "what moved" is a question about a pair, and `silence` needs a
    set because "never" is a quantifier over a set. A SELF-CONTRADICTION needs
    neither: it is a property of one record, which is what makes this runnable
    over the committed corpus exactly as it stands.

    Reads files already on disk and nothing else. No network, no exit, no
    reading taken.

    The exit codes mirror the discipline the other lanes keep rather than
    inventing a scheme:

    ``2``
        REFUSED — the file could not be read, or is not a checker-matrix
        record. Nothing was established, so it can never wear a code that
        means something was. This is the code the ps135-shaped files and
        `arm-c-stock-vs-packaged.json` take: they carry no ``readings`` list,
        and "I could not read this record" must never be reported as "this
        record is fine".
    ``1``
        A vector CONTRADICTS ITSELF — the record says the machine has more
        than one GPU. This is a finding ABOUT THE PRODUCT, which is what 1
        means in this CLI, and it is deliberately not 3: the contradiction is
        a real defect the record demonstrates, not a gap in coverage.
    ``3``
        No contradiction, but at least one comparable vector could not be read
        well enough to establish agreement. 3 already means "no finding about
        the product, but the coverage this rests on is not what you think",
        which is exactly a record whose GPU rows are all null. It is NOT 0 —
        a set of identical nulls is a set of size one, and letting that exit
        clean is precisely how "we never looked" comes to read as "they
        agreed".
    ``0``
        Every comparable vector was read and names one identity.
    """
    try:
        record = _load_record(args.record)
        entries = consistency_pass(record)
    except (NotARecord, RecordUnreadable) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        print(
            "Nothing was established, so this is NOT 'the record is "
            "consistent'.",
            file=sys.stderr,
        )
        return 2

    print(format_consistency(entries, source=args.record))
    if consistency_findings(entries):
        return 1
    if coverage_holes(entries):
        return 3
    return 0


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
        allow_small_dev_shm=args.allow_small_dev_shm,
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
        "--allow-small-dev-shm", action="store_true",
        help=(
            "run persona's chromium with --disable-dev-shm-usage on a host "
            "whose /dev/shm is below the tier's floor. OFF by default and "
            "never inferred, exactly like --allow-unsandboxed-chromium: the "
            "flag is not on persona's own launch path, so a reading taken "
            "with it is not the product's surface and the record says so. "
            "Without it, such a host is REFUSED up front rather than allowed "
            "to produce a reading that dies mid-page and blames whatever "
            "configuration was being read (PS-133)"
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
    rd.add_argument(
        "--match-product-geo", action="store_true",
        help=(
            "install the geolocation extension the PRODUCT installs, closing a "
            "measured tier-versus-product gap. persona's launch path builds "
            "build_geo_extension for EVERY proxied profile (process.py:547) — "
            "in DENY mode when the exit carries no usable coordinates, so "
            "getCurrentPosition cannot fall through to the real host coords "
            "while locale and timezone already name the exit country. This "
            "tier installed it for NONE of them, and every reading in this "
            "campaign is proxied. Off by default so an existing reading cannot "
            "move underneath a caller that did not ask; the record names 'geo' "
            "in masking_layer.installed either way, so which surface was read "
            "is never inferred. Chromium only"
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
        "--allow-small-dev-shm", action="store_true",
        help=(
            "run persona's chromium with --disable-dev-shm-usage on a host "
            "whose /dev/shm is below the tier's floor. OFF by default and "
            "never inferred, exactly like --allow-unsandboxed-chromium: the "
            "flag is not on persona's own launch path, so a reading taken "
            "with it is not the product's surface and the record says so. "
            "Without it, such a host is REFUSED up front rather than allowed "
            "to produce a reading that dies mid-page and blames whatever "
            "configuration was being read (PS-133)"
        ),
    )
    df.add_argument(
        "-o", "--output", default="-",
        help="write the differential record here ('-' for stdout)",
    )
    df.set_defaults(func=_cmd_differential)

    sil = sub.add_parser(
        "silence",
        help="ask a SET of records which checkers have NEVER answered",
        description=(
            "Reports checkers that never once produced a reading across the "
            "whole set of records supplied. This is the question `compare` "
            "cannot be asked: it takes exactly two records, and a checker "
            "that is unobtainable on both sides of every pair reads to it as "
            "a row that did not move. Splits the answer on the catalogue's "
            "`tier`: a readable-tier checker that never answered is a "
            "FINDING (exit 3), while a catalogue-declared unreadable one is "
            "CARRIED and expected. Refuses a set of fewer than two records: "
            "over one record, every checker that happened to fail in that "
            "run reads as 'never'. Reads files only; no network, no exit."
        ),
    )
    sil.add_argument(
        "records", nargs="*",
        help="record files to range over (two or more)",
    )
    sil.add_argument(
        "--discover", metavar="ROOT",
        help=(
            "also find records anywhere under ROOT, by PAYLOAD SHAPE rather "
            "than by directory name — anything keyed on a remembered "
            "subdirectory stops ranging over the artifacts it exists to read "
            "the moment the next recording campaign lands"
        ),
    )
    sil.set_defaults(func=_cmd_silence)

    con = sub.add_parser(
        "consistency",
        help="ask ONE record whether it agrees with ITSELF",
        description=(
            "Reports vectors in which a single record carries two materially "
            "different values — one profile claiming more than one GPU in one "
            "run. This is the question NEITHER other lane can be asked: "
            "`compare` is strictly pairwise and keyed per row, so two rows "
            "inside one record are never brought into contact and a "
            "self-contradiction reads to it as two rows that each held "
            "still; `silence` quantifies over a set. Per-row `adverse` "
            "scoring cannot see it either, because both halves of a "
            "contradiction are individually plausible. Comparability is "
            "DECLARED per vector, never inferred: `gpu_claimed` values are "
            "strings persona chooses and must agree, while `gpu_rendered` "
            "rows are per-checker hashes over pixels each checker drew "
            "itself and can never be equal even on a healthy run. A vector "
            "whose rows are null or unidentifiable is a COVERAGE HOLE (exit "
            "3), never a pass — a set of identical nulls is a set of size "
            "one. Reads one file; no network, no exit."
        ),
    )
    con.add_argument("record", help="the record file to judge")
    con.set_defaults(func=_cmd_consistency)

    ls = sub.add_parser("list", help="print the checker inventory")
    ls.set_defaults(func=_cmd_list)
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
