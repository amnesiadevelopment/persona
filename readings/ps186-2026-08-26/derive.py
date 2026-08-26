#!/usr/bin/env python3
"""PS-177 — re-derive every number this sweep contributes to PS-16.

DoD #2 of the ticket: "Re-derive the numbers from the committed record with a
script; do not hand-type them." So this reads the committed records and PRINTS
what goes into the article. Nothing in EVIDENCE.md or in PS-16 that came from
this sweep was typed from memory or from a terminal scrollback.

    ./derive.py <record.json> [<record.json> ...]
    ./derive.py .                       # every record in this directory

WHAT IT REFUSES TO DO. It never reports a checker as clean because it was
silent. The distinction this whole script is built around:

  * a row that is `read` and `adverse`      -> the checker OBJECTED. A finding.
  * a row that is `read` and not adverse    -> the checker was asked and did
                                               not object. Evidence.
  * a row that is `absent`                  -> the adverse PATTERN did not
                                               match. Weak evidence of absence;
                                               the page was read.
  * a row that is `unobtainable`            -> NOBODY LOOKED. Never a pass.

`adverse: true` on an `absent`/`unobtainable` row is the CATALOGUE'S PATTERN
DEFINITION, not a verdict — the row is declaring "if this matched it would be
bad". Counting those as findings would report 10 adverse rows on a record whose
true count is 0, which is exactly the mistake this script exists to prevent.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict

STATE_READ = "read"
STATE_ABSENT = "absent"
STATE_UNOBTAINABLE = "unobtainable"

# The record's own classification of what a row IS. `fingerprint` rows are the
# ones that describe the PROFILE; `exit` rows describe the address we left
# through and rotate by design; `harness` rows are about our own instrument.
# This is the axis `evidence.fingerprint_total` counts, which is why the
# Level 2 comparison keys on it.
SORT_FINGERPRINT = "fingerprint"

# ---------------------------------------------------------------------------
# LINKAGE CLASSIFICATION — which rows CAN carry a Level 2 answer and which
# cannot carry one at any value.
#
# A row ties two profiles together only if its value is drawn from a LARGE
# SPACE. Two profiles sharing a canvas hash, a WebGL renderer string or a UA
# are linkable. Two profiles that both score "webdriver check: passed" are
# merely BOTH CLEAN — that is the success case of masking, not a leak.
#
# Conflating the two inverts the entire question this ticket exists to answer,
# so the classification is explicit and every row lands in exactly one class:
#
#   entropy  — high-cardinality profile attribute. CAN tie two profiles.
#   verdict  — detector output / low-cardinality token (True, "false", "33").
#              CANNOT tie two profiles at ANY value, identical or not.
#
# The asymmetry is deliberate: a verdict row is never promoted to evidence,
# and an arm holding no entropy row read on both sides is reported UNANSWERABLE
# rather than clean. Identical verdict rows are still PRINTED — as a footnote
# that names them as non-evidence — so nothing is hidden, only re-ranked.
VERDICT_TOKENS = {
    "true", "false", "yes", "no", "none", "", "ok", "pass", "fail",
    "passed", "failed",
}

# A hash or a renderer string reaches this; "33", "20", "0" and "True" do not.
ENTROPY_MIN_LEN = 8

CLASS_ENTROPY = "entropy"
CLASS_VERDICT = "verdict"


def linkage_class(row: dict) -> str:
    """Classify ONE row as `entropy` (can tie two profiles) or `verdict`.

    Order matters. The record's own `vector` annotation wins outright: a row
    the catalogue tagged `gpu_claimed` / `gpu_rendered` is a leak vector by the
    record's own account, so it is entropy-bearing whatever its value looks
    like. Only an untagged row is judged on the shape of its value.

    The residual bound is stated rather than hidden: a SHORT untagged string
    (say a 3-character adapter name) classifies as `verdict` and is therefore
    excluded from linkage evidence. That direction is the safe one — it can
    only ever make this tool report UNANSWERABLE where a human might have
    found a weak tie; it can never manufacture a tie that is not there.
    """
    if (row.get("vector") or "").strip():
        return CLASS_ENTROPY

    value = row.get("value")
    if value is None or isinstance(value, bool):
        return CLASS_VERDICT

    text = str(value).strip()
    if text.lower() in VERDICT_TOKENS:
        return CLASS_VERDICT

    # A bare number is a rating or a score — a detector's output about the
    # profile, not an attribute OF the profile. creepjs `headless_rating` 33
    # reads 33 on chromium and on firefox alike.
    try:
        float(text)
        return CLASS_VERDICT
    except ValueError:
        pass

    return CLASS_ENTROPY if len(text) >= ENTROPY_MIN_LEN else CLASS_VERDICT


def load(paths: "list[str]") -> "list[tuple[str, dict]]":
    files: "list[str]" = []
    for p in paths:
        if os.path.isdir(p):
            files += [
                os.path.join(p, n)
                for n in sorted(os.listdir(p))
                if n.startswith("reading.") and n.endswith(".json")
            ]
        else:
            files.append(p)
    out = []
    for f in files:
        with open(f) as fh:
            out.append((os.path.basename(f), json.load(fh)))
    return out


def engine_name(rec: dict) -> str:
    """persona's name for the engine, as PS-16's tables key on it.

    The record's `engine` field is a BUILD string (`invisible_playwright/
    firefox-20`, `fingerprint-chromium/148.0.7778.215`), and its first segment
    is the DRIVER, not the engine — so splitting on "/" yields
    "invisible_playwright" where PS-16's row is "firefox". Keyed on the build
    string's content rather than on a positional split for that reason.
    """
    build = rec.get("engine", "")
    if "firefox" in build or "invisible_playwright" in build:
        return "firefox"
    if "chromium" in build:
        return "chromium"
    return build or "unknown"


def cell(rec: dict) -> str:
    """The matrix cell this record fills, from the RECORD's own header.

    Read from `declared_machine`, never from the filename or the request: on
    firefox the two differ by design (#211) and the header is the one that
    states what was actually presented.

    `device_type` is hard-coded "desktop" and that is NOT an assumption: this
    tier exposes no device_type selector at all, so every record it can produce
    is a desktop one (PS-170). See EVIDENCE.md §"Not covered".
    """
    return (
        f"{engine_name(rec)} / {rec['declared_machine']} / "
        f"desktop / seed {rec['seed']}"
    )


def fired(rec: dict) -> "list[dict]":
    """Rows where a checker ACTUALLY objected — read AND adverse."""
    return [
        r for r in rec["readings"]
        if r.get("adverse") and r.get("state") == STATE_READ
    ]


def per_checker(rec: dict) -> "dict[str, Counter]":
    out: "dict[str, Counter]" = defaultdict(Counter)
    for r in rec["readings"]:
        out[r["checker"]][r["state"]] += 1
    return out


def report(name: str, rec: dict) -> None:
    print(f"\n{'=' * 78}\n{name}\n{'=' * 78}")
    print(f"  cell                     : {cell(rec)}")
    print(f"  engine build             : {rec['engine']}")
    print(f"  declared_machine_honoured: {rec['declared_machine_honoured']}")
    if not rec["declared_machine_honoured"]:
        print("     ^ the engine was NOT asked-and-obeyed. This record states "
              "what was PRESENTED,")
        print("       not what was requested (product issue #211).")
    ex = rec.get("exit", {})
    print(f"  exit                     : {ex.get('ip')} "
          f"{ex.get('city')}/{ex.get('country')} {ex.get('org')} "
          f"{ex.get('timezone')}")
    ml = rec.get("masking_layer", {})
    print(f"  masking layer            : route={ml.get('route')} "
          f"installed={','.join(ml.get('installed', []))} "
          f"complete={ml.get('complete')}")
    c = rec["counts"]
    print(f"  counts                   : {c['read']} read, {c['absent']} "
          f"absent, {c['unobtainable']} unobtainable ({c['total']} rows)")
    ev = rec.get("evidence", {})
    print(f"  evidence verdict         : {ev.get('verdict')} — "
          f"{ev.get('fingerprint_obtained')}/{ev.get('fingerprint_total')} "
          f"fingerprint rows "
          f"({(ev.get('fingerprint_fraction') or 0) * 100:.1f}%), from "
          f"{len(ev.get('checkers_contributing') or [])} checker(s)")

    f = fired(rec)
    print(f"\n  ADVERSE VERDICTS THAT ACTUALLY FIRED (read AND adverse): "
          f"{len(f)}")
    if not f:
        print("    (none)")
    for r in f:
        print(f"    FIRED  {r['checker']} :: {r['item']} = "
              f"{str(r.get('value'))[:60]}")

    declared = [
        r for r in rec["readings"]
        if r.get("adverse") and r.get("state") != STATE_READ
    ]
    print(f"\n  adverse PATTERNS that did not fire (definitions, NOT "
          f"findings): {len(declared)}")
    for r in declared:
        print(f"    {r['state']:14} {r['checker']} :: {r['item']}")

    print("\n  per-checker coverage:")
    for ck, st in sorted(per_checker(rec).items()):
        bits = ", ".join(f"{k}={v}" for k, v in sorted(st.items()))
        flag = ""
        if st[STATE_READ] == 0 and st[STATE_UNOBTAINABLE]:
            flag = "   <- NOBODY LOOKED (not a pass)"
        print(f"    {ck:28} {bits}{flag}")


def level2(records: "list[tuple[str, dict]]") -> None:
    """The Level 2 question: are two profiles at different seeds linkable?

    Groups records into (engine, declared_machine) arms and reports, per arm,
    whether it holds TWO seeds. An arm holding one seed CANNOT answer Level 2 —
    it is reported as unanswerable rather than as passing, because a single
    profile carries no information about whether two could be tied together.
    """
    print(f"\n\n{'#' * 78}\n# LEVEL 2 — mutual unlinkability, per arm\n"
          f"{'#' * 78}")
    arms: "dict[tuple[str, str], list[dict]]" = defaultdict(list)
    for _, rec in records:
        arms[(rec["engine"].split("/")[0], rec["declared_machine"])].append(rec)

    for (eng, machine), recs in sorted(arms.items()):
        seeds = sorted({r["seed"] for r in recs})
        print(f"\n  arm {eng} / {machine} / desktop — seeds {seeds}")
        if len(seeds) < 2:
            print("    UNANSWERABLE from this sweep: only ONE seed was read on "
                  "this arm.")
            print("    A single profile cannot answer whether TWO profiles are "
                  "linkable, at any")
            print("    level of detail. NOT a pass — recorded as not covered.")
            continue

        # Two or more seeds: compare the fingerprint-bearing rows that were
        # READ on BOTH sides. A row read on one side only is coverage, not
        # linkage, and is reported separately.
        by_seed = {r["seed"]: r for r in recs}
        a, b = by_seed[seeds[0]], by_seed[seeds[1]]

        def readable(rec):
            # Keyed on `sort == "fingerprint"`, NOT on the `vector` field.
            # `vector` is set on only a handful of rows (9 of 61 here) and is a
            # per-vector annotation, while `sort` is the record's own
            # classification of what the row IS -- and it is the same axis
            # `evidence.fingerprint_total` counts (28 rows). Filtering on
            # `vector` silently shrinks the comparison to a third of the
            # evidence and would let an unlinkability claim rest on rows that
            # were never compared.
            #
            # The `sort` filter decides what is COMPARED; `linkage_class`
            # decides what the comparison is allowed to CONCLUDE. Keeping the
            # two separate is the point: a verdict row is still read, still
            # printed, and still never counted as linkage evidence.
            return {
                (r["checker"], r["item"]): (r.get("value"), linkage_class(r))
                for r in rec["readings"]
                if r.get("state") == STATE_READ
                and r.get("sort") == SORT_FINGERPRINT
            }

        ra, rb = readable(a), readable(b)
        both = sorted(set(ra) & set(rb))
        if not both:
            print("    NO fingerprint-bearing row was read on BOTH sides — "
                  "nothing to diff.")
            print("    UNANSWERABLE (coverage, not a clean result).")
            continue

        # Split the overlap by whether a row CAN carry a linkage answer at
        # all. A verdict row is not weak evidence of unlinkability -- it is
        # NO evidence, identical or not, so it never reaches the finding.
        ent = [k for k in both if ra[k][1] == CLASS_ENTROPY]
        ver = [k for k in both if ra[k][1] == CLASS_VERDICT]

        print(f"    {len(both)} fingerprint row(s) read on both sides: "
              f"{len(ent)} entropy-bearing, {len(ver)} verdict/low-cardinality")

        if not ent:
            print("    UNANSWERABLE (coverage, not a clean result): NO "
                  "entropy-bearing row was read")
            print("    on both sides. The rows that overlap are detector "
                  "verdicts and low-cardinality")
            print("    tokens, which read the same for ANY well-masked "
                  "profile — they cannot tie two")
            print("    profiles together and they cannot establish that two "
                  "are unlinked.")
            if ver:
                print("    (Non-evidence, listed so nothing is hidden — "
                      "identical here means BOTH CLEAN,")
                print("     NOT linked:)")
                for k in ver:
                    flag = "same" if ra[k][0] == rb[k][0] else "differ"
                    print(f"        [{flag}] {k[0]} :: {k[1]} = "
                          f"{str(ra[k][0])[:44]}")
            continue

        same = [k for k in ent if ra[k][0] == rb[k][0]]
        diff = [k for k in ent if ra[k][0] != rb[k][0]]
        print(f"    of the {len(ent)} entropy-bearing row(s): {len(diff)} "
              f"DIFFER, {len(same)} IDENTICAL")
        if same:
            print("    ⚠ ROWS THAT TIE THE TWO PROFILES TOGETHER "
                  "(identical high-entropy values):")
            for k in same:
                print(f"        {k[0]} :: {k[1]} = {str(ra[k][0])[:55]}")
            print("    LEVEL 2 FAILS on this arm: a checker reading these "
                  "rows can tie the two")
            print("    profiles to each other.")
        else:
            print("    No entropy-bearing row is identical across the two "
                  "seeds on this arm.")
            print(f"    LEVEL 2 HOLDS on this arm, on the {len(ent)} "
                  f"entropy-bearing row(s) actually read.")
        if ver:
            print(f"    ({len(ver)} verdict/low-cardinality row(s) excluded "
                  f"from the verdict above —")
            print("     identical there means both profiles are CLEAN, not "
                  "that they are linked.)")


def matrix_summary(records: "list[tuple[str, dict]]") -> None:
    print(f"\n\n{'#' * 78}\n# CELLS THIS SWEEP FILLS (for PS-16 Table 1)\n"
          f"{'#' * 78}")
    for name, rec in records:
        f = fired(rec)
        ev = rec.get("evidence", {})
        looked = {
            r["checker"] for r in rec["readings"]
            if r.get("state") in (STATE_READ, STATE_ABSENT)
        }
        blind = {
            r["checker"] for r in rec["readings"]
            if r.get("state") == STATE_UNOBTAINABLE
        } - looked
        print(f"\n  {cell(rec)}")
        print(f"    evidence      : {ev.get('verdict')} "
              f"({ev.get('fingerprint_obtained')}/"
              f"{ev.get('fingerprint_total')} fingerprint rows)")
        print(f"    verdicts fired: {len(f)}"
              + (f" — {', '.join(sorted(x['checker'] + '::' + x['item'] for x in f))}"
                 if f else " (none)"))
        print(f"    asked & silent: {', '.join(sorted(looked))}")
        print(f"    NOT LOOKED AT : {', '.join(sorted(blind))}")
        print(f"    source        : {name}")


def main(argv: "list[str]") -> int:
    if not argv:
        print(__doc__)
        return 2
    records = load(argv)
    if not records:
        print("no records found", file=sys.stderr)
        return 2
    for name, rec in records:
        report(name, rec)
    matrix_summary(records)
    level2(records)
    print(f"\n\n{len(records)} record(s) read.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
