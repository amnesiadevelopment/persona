#!/usr/bin/env python3
"""Reconcile the PUBLISHED engine releases against the provenance records in
``engine/releases/`` — and go RED, BY NAME, for a release that has no record.

WHY THIS FILE EXISTS (PS-385, wiring PS-343's territory)
────────────────────────────────────────────────────────
PS-343 built the record and the verifier, and both are good. What neither of
them asks is **whether every published release HAS a record**. Measured at the
commit this was written on, not inferred:

* ``scripts/ps343_verify_release_provenance.py`` ``load_records()`` globs
  ``engine/releases/personium-*.json`` and ``main()`` iterates **that list**. So
  the RECORD SET is the denominator. A published release with no record is not
  reported red — it is **absent from the question**.
* ``tests/test_ps343_release_provenance.py`` asserted the territory with a
  compile-time literal (``SHIPPED_TAG``). That asserts ONE hardcoded release has
  a record. It cannot assert EVERY published release has one.
* ``git grep -l "ps343" -- .github/`` returned **ZERO** while four positive
  controls in the same probe shape fired (``ps299_rebase_probe`` 2,
  ``ps307_verify_patches_in_tree`` 1, ``ps372_firefox_major_watch`` 1,
  ``ps359_package`` 1) and a negative control returned 0. The record is a manual
  checklist line in ``RELEASING.md`` and nothing noticed when it was skipped.

``engine/releases/README.md`` states the design goal in one sentence — *"For
every Personium engine we have **published**, a record here says…"* — and
*every published* is exactly the quantifier no code evaluated. This file is that
evaluation.

⭐ THE MISSING DENOMINATOR ALREADY SHIPS. This is wiring, not invention:
``src/services/engine/updater.engine_versions_newest_first()`` already
enumerates every published-tag engine version, through persona's own egress
policy, at every operator startup. Both halves existed; nothing had ever put
them side by side.

WHICH ENUMERATOR, AND WHY (the fork PS-385 left open, decided here)
───────────────────────────────────────────────────────────────────
Two defensible options: call ``engine_versions_newest_first()``, or read the
``git/matching-refs/tags/personium-`` endpoint directly in this file.

**We call ``engine_versions_newest_first()``.** Three reasons, and the third is
the deciding one:

1. It already goes through ``egress.fetch_json`` — persona's own egress policy —
   rather than a bare ``urlopen`` this file would have to grow.
2. It already owns the "a ref that survives the server filter is re-checked with
   ``is_engine_tag`` on our side" discipline, so an application tag cannot enter
   the denominator.
3. ⭐ Reading the endpoint here would make this the **second place in the tree
   that knows the engine tag prefix**, and PS-385's coordination note names that
   drift explicitly — three neighbouring gates resolving engine tags three
   different ways. The prefix is ``updater``'s to own. We cross the bare-version
   ↔ tag seam with ``engine_tag`` / ``version_from_tag`` for the same reason: a
   string slice here would be a driftable copy of a rule that already has a
   home.

⛔ ``engine_versions_newest_first``'s CONTRACT IS NOT TOUCHED. The operator's
hourly update path depends on it and this file only reads it.

⭐⭐ THE `[]` READING — STATED, BECAUSE AN UNSTATED CHOICE IS NOT A CHOICE
────────────────────────────────────────────────────────────────────────
``engine_versions_newest_first()`` returns ``[]`` **both** on any failure **and**
on a repository with no engine tag yet, and its docstring instructs its caller
to read that as *"nothing published"*, never as *"GitHub is unreachable"*.

**THIS GATE READS `[]` AS `CANNOT_ENUMERATE` — exit 2, NEVER green.**

That is deliberately the opposite reading from the operator's update path, and
the two are both correct because the two callers have opposite safe defaults:

* For the **updater**, ``[]`` must mean "nothing published" because the safe
  action is *do not install anything*. Reading a rate limit as "there is a new
  engine" would be the dangerous error there.
* For **this gate**, the safe action is the reverse. Reading ``[]`` as
  "zero published releases, therefore zero unrecorded releases, green" turns
  **every rate limit and every network blip into a permanent silent pass** —
  which is precisely the shape of unwiredness this ticket exists to remove. A
  gate that can only be observed passing is not coverage.

⛔ AND WE DO NOT REINTERPRET ``[]`` INSIDE ``updater``. The reinterpretation
lives HERE, at the call site, in ``enumerate_published()``, where it is one
``if`` with this paragraph next to it. Nothing about the shipped function
changes.

⚠️ THE COST OF THIS CHOICE, STATED RATHER THAN GLOSSED: a repository that has
genuinely never published an engine tag reads exit 2 here forever, rather than
green. That is an honest answer ("we could not establish a published set") and
not a false one, and it does not describe this repository, which has published
``personium-152.0.7977.75``. If persona ever legitimately has no engine tags,
this is the line to revisit — with the reasoning above, not by reflex.

THREE OUTCOMES, AND THEY MUST STAY THREE
────────────────────────────────────────
Reusing ``scripts/ps343_verify_release_provenance.py``'s own 0/1/2 vocabulary
(``Report.exit_code()``), which is also ``ps299_rebase_probe``'s and
``ps344_verdict``'s. A second vocabulary for one idea is a second thing to keep
in sync.

    0  QUIET               every published release has a record on disk
    1  UNRECORDED_RELEASE  a published release has NO record. NAMED, red.
    2  CANNOT_ENUMERATE    the published set could not be established (network,
                           rate limit, an empty answer). NOTHING is claimed —
                           this is NOT "every release is recorded".

⛔ COLLAPSING 1 AND 2 IS THE FAILURE THIS FILE IS BUILT AROUND. "A release is
unrecorded" and "we failed to look" are different facts with different remedies
(write the record / re-run the lookup), and the project's standing register —
``chromium-upstream-watch.yml``, ``engine-gpu-variance.yml`` — is that "we
failed to look" must never wear the colour of "we looked and it was fine". Both
are red here; what distinguishes them is the CODE and the NAME.

WHAT AN ORPHAN RECORD DOES, AND WHY IT IS NOT A THIRD COLOUR
────────────────────────────────────────────────────────────
A record on disk with no matching published tag (a yanked or deleted release,
or a record written ahead of publication) is reported as a NOTED row and
**changes no exit code**. Two reasons: the quantifier this gate evaluates is
``engine/releases/README.md``'s own — *every published release has a record* —
which says nothing about the converse; and a yanked release is a legitimate
state whose record is worth keeping. Reporting it is useful; scoring it would
invent a finding nobody asked for.

USAGE
─────
    # prove the judgement can still go red — no network, runs first
    python3 .github/scripts/ps343_release_audit.py --selftest

    # the live reconciliation
    python3 .github/scripts/ps343_release_audit.py --out /tmp/audit.json

    # THE FALSIFICATION PATH: inject a published tag that has no record and
    # watch the gate name it. This can only ever ADD to the published set, so
    # it cannot turn a red into a green.
    python3 .github/scripts/ps343_release_audit.py \
        --inject-published personium-152.0.7977.82
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

RECORDS_DIR = REPO / "engine" / "releases"

QUIET = 0
UNRECORDED_RELEASE = 1
CANNOT_ENUMERATE = 2

OUTCOME_NAMES = {
    QUIET: "QUIET",
    UNRECORDED_RELEASE: "UNRECORDED_RELEASE",
    CANNOT_ENUMERATE: "CANNOT_ENUMERATE",
}


def _updater():
    """Imported lazily, exactly as ``ps344_gate_plan.plan()`` does it.

    ``--selftest`` must be able to run on a runner where the project's
    dependencies are not installed yet, because it runs BEFORE provisioning.
    ``classify()`` needs only the two tag helpers, so the import is per-call and
    the pure path stays importable.
    """
    from src.services.engine import updater

    return updater


# ── the record side ─────────────────────────────────────────────────────────
def record_tags(records_dir: pathlib.Path = RECORDS_DIR) -> "list[str]":
    """Every provenance record on disk, as TAGS (``personium-<version>``).

    The same glob ``ps343_verify_release_provenance.load_records()`` uses, and
    deliberately so: this gate must reconcile against the set that verifier
    actually iterates, not against a second opinion about which files count.
    Only the NAME is read — a record's contents are the verifier's business and
    a record that fails to parse is still a record that EXISTS, which is the
    only question asked here.
    """
    if not records_dir.is_dir():
        return []
    return sorted(p.stem for p in records_dir.glob("personium-*.json"))


# ── the published side ──────────────────────────────────────────────────────
def enumerate_published(timeout: int = 30) -> "tuple[list[str] | None, str]":
    """The PUBLISHED engine versions, or ``(None, reason)`` when unestablished.

    ``None`` is this file's own vocabulary for "could not establish", kept
    distinct from ``[]`` so ``classify()`` never has to guess. See the module
    docstring for why ``[]`` from ``engine_versions_newest_first()`` becomes
    ``None`` HERE rather than being reinterpreted inside ``updater``.
    """
    updater = _updater()
    try:
        versions = updater.engine_versions_newest_first(timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return None, (
            "the published engine tag list could not be read — "
            f"{type(exc).__name__}: {exc}"
        )
    if not versions:
        return None, (
            "the published engine tag list came back EMPTY. "
            "`engine_versions_newest_first` answers `[]` both on failure and on "
            "a repository with no engine tag, and this gate reads that as "
            "UNESTABLISHED rather than as 'nothing published' — see this "
            "script's module docstring. Nothing is claimed about any record."
        )
    return versions, ""


# ── the judgement, and it is the only place an exit code is decided ─────────
def classify(
    published: "list[str] | None",
    records: "list[str]",
    *,
    reason: str = "",
) -> "tuple[int, dict]":
    """Reconcile the two sets. Returns ``(exit_code, body)``.

    ``published`` is BARE versions (what ``engine_versions_newest_first``
    returns) or ``None`` for "could not establish". ``records`` is TAGS (what
    ``record_tags`` returns). The two vocabularies are crossed with
    ``updater.engine_tag`` / ``version_from_tag``, never with a slice — those
    two functions exist precisely to stop a second copy of that rule drifting.
    """
    updater = _updater()

    if published is None:
        return CANNOT_ENUMERATE, {
            "outcome": OUTCOME_NAMES[CANNOT_ENUMERATE],
            "reason": reason or "the published engine set was not established",
            "published_count": None,
            "record_count": len(records),
            "unrecorded": [],
            "orphan_records": [],
            "recorded": [],
        }

    published_tags = [updater.engine_tag(updater.version_from_tag(v)) for v in published]
    record_set = {updater.engine_tag(updater.version_from_tag(t)) for t in records}

    unrecorded = sorted({t for t in published_tags if t not in record_set})
    recorded = sorted({t for t in published_tags if t in record_set})
    orphans = sorted(record_set - set(published_tags))

    body = {
        "outcome": OUTCOME_NAMES[QUIET],
        "published_count": len(set(published_tags)),
        "record_count": len(record_set),
        "published": sorted(set(published_tags)),
        "records": sorted(record_set),
        "recorded": recorded,
        "unrecorded": unrecorded,
        # NOTED, never scored — see the module docstring.
        "orphan_records": orphans,
        "reason": "",
    }

    if unrecorded:
        body["outcome"] = OUTCOME_NAMES[UNRECORDED_RELEASE]
        body["reason"] = (
            "published engine release(s) with NO provenance record in "
            "engine/releases/: " + ", ".join(unrecorded) + ". "
            "RELEASING.md's release procedure says to write the record and run "
            "scripts/ps343_verify_release_provenance.py; that step was not "
            "completed for these tags. The remedy is to write the record — see "
            "engine/releases/README.md 'Adding a record for a new release'. "
            "⛔ Do NOT write a record for a release you cannot account for: a "
            "manifest assembled from assumptions reads as evidence and is worse "
            "than a stated gap."
        )
        return UNRECORDED_RELEASE, body

    body["reason"] = (
        f"all {len(set(published_tags))} published engine release(s) have a "
        "provenance record in engine/releases/"
    )
    return QUIET, body


# ── the report ──────────────────────────────────────────────────────────────
def issue_title(code: int, body: dict) -> str:
    """Deterministic and EXACT-MATCHABLE, so re-running comments rather than
    filing the same news twice. Same discipline as
    ``chromium-upstream-watch.yml``'s title: it carries the status AND the
    subject, so a tag moving from unrecorded to recorded does not quietly edit
    away the fact that it was once unrecorded."""
    if code == UNRECORDED_RELEASE:
        return (
            "[release-provenance] no provenance record for "
            + ", ".join(body.get("unrecorded") or ["<unnamed>"])
        )
    if code == CANNOT_ENUMERATE:
        return "[release-provenance] the published engine set could not be established"
    return "[release-provenance] every published engine release has a record"


def render(code: int, body: dict) -> str:
    lines = [
        f"## {OUTCOME_NAMES[code]} (exit {code})",
        "",
        body.get("reason", ""),
        "",
    ]
    if code == UNRECORDED_RELEASE:
        lines.append("| published release | provenance record |")
        lines.append("|---|---|")
        for tag in body.get("unrecorded") or []:
            lines.append(f"| `{tag}` | ❌ **NONE** — `engine/releases/{tag}.json` does not exist |")
        for tag in body.get("recorded") or []:
            lines.append(f"| `{tag}` | ✅ `engine/releases/{tag}.json` |")
    elif code == QUIET:
        for tag in body.get("recorded") or []:
            lines.append(f"* ✅ `{tag}` — `engine/releases/{tag}.json`")
    else:
        lines.append("⚠️  **NOTHING WAS MEASURED.** This is not a pass. Whether every")
        lines.append("published engine release has a provenance record is UNKNOWN on this run.")

    orphans = body.get("orphan_records") or []
    if orphans:
        lines.append("")
        lines.append("Noted, and deliberately **not scored** — a record with no")
        lines.append("matching published tag (a yanked release, or a record written")
        lines.append("ahead of publication). The quantifier this gate evaluates is")
        lines.append("*every published release has a record*, which says nothing about")
        lines.append("the converse:")
        for tag in orphans:
            lines.append(f"* · `{tag}`")

    lines.append("")
    lines.append(
        "Reconciled `updater.engine_versions_newest_first()` (the PUBLISHED set) "
        "against `engine/releases/personium-*.json` (the RECORD set). "
        "Filed by `.github/workflows/engine-release-provenance-audit.yml`."
    )
    return "\n".join(lines)


# ── the selftest: proves the judgement can still go red, with no network ────
SELFTEST_CASES = [
    (
        "all published releases are recorded -> QUIET",
        ["152.0.7977.75"],
        ["personium-152.0.7977.75"],
        None,
        QUIET,
        [],
    ),
    (
        "a published release with no record -> UNRECORDED_RELEASE, NAMED",
        ["152.0.7977.82", "152.0.7977.75"],
        ["personium-152.0.7977.75"],
        None,
        UNRECORDED_RELEASE,
        ["personium-152.0.7977.82"],
    ),
    (
        "EVERY published release unrecorded -> UNRECORDED_RELEASE, all named",
        ["152.0.7977.82", "152.0.7977.75"],
        [],
        None,
        UNRECORDED_RELEASE,
        ["personium-152.0.7977.75", "personium-152.0.7977.82"],
    ),
    (
        "an empty published list -> CANNOT_ENUMERATE, never QUIET",
        None,
        ["personium-152.0.7977.75"],
        "the published engine tag list came back EMPTY",
        CANNOT_ENUMERATE,
        [],
    ),
    (
        "the enumeration raised -> CANNOT_ENUMERATE, never QUIET",
        None,
        ["personium-152.0.7977.75"],
        "URLError: <urlopen error [Errno -3] Temporary failure>",
        CANNOT_ENUMERATE,
        [],
    ),
    (
        "an orphan record is NOTED and does not move the exit code",
        ["152.0.7977.75"],
        ["personium-152.0.7977.75", "personium-151.0.0.1"],
        None,
        QUIET,
        [],
    ),
    (
        "the bare-version <-> tag seam is crossed, not sliced",
        # The published side speaks BARE versions and the record side speaks
        # TAGS. A naive comparison of the raw lists reports EVERY release as
        # unrecorded, which is the specific way this gate would fire on
        # everything and satisfy a red arm perfectly while discriminating
        # nothing.
        ["152.0.7977.75"],
        ["personium-152.0.7977.75"],
        None,
        QUIET,
        [],
    ),
]


def selftest() -> int:
    failures = []
    for name, published, records, reason, want_code, want_named in SELFTEST_CASES:
        code, body = classify(published, records, reason=reason or "")
        got_named = body.get("unrecorded") or []
        ok = code == want_code and got_named == want_named
        print(
            f"{'✅' if ok else '❌'} {name}\n"
            f"     exit {code} ({OUTCOME_NAMES[code]}), named={got_named}"
        )
        if not ok:
            failures.append(
                f"{name}: wanted exit {want_code} named={want_named}, "
                f"got exit {code} named={got_named}"
            )
    print("")
    if failures:
        print("❌ THE JUDGEMENT IS BROKEN. Nothing below this point would mean anything:")
        for f in failures:
            print(f"   - {f}")
        return 1
    print(
        f"✅ {len(SELFTEST_CASES)} synthesised cases: the judgement still "
        "distinguishes QUIET from UNRECORDED_RELEASE from CANNOT_ENUMERATE."
    )
    return 0


def _emit_github_output(body: dict, code: int, path: str) -> None:
    payload = {
        "outcome": OUTCOME_NAMES[code],
        "exit_code": str(code),
        "unrecorded": ",".join(body.get("unrecorded") or []),
        "title": issue_title(code, body),
        "report": "true" if code != QUIET else "false",
        "green": "true" if code == QUIET else "false",
    }
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in payload.items():
            fh.write(f"{key}={value}\n")


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--selftest",
        action="store_true",
        help=(
            "Drive synthesised published/record pairs through the SAME classify "
            "path the live run gates on, with no network call. Exits 1 if the "
            "judgement can no longer distinguish the three outcomes."
        ),
    )
    ap.add_argument("--records", type=pathlib.Path, default=RECORDS_DIR)
    ap.add_argument("--out", help="write the report as JSON to this path")
    ap.add_argument("--markdown", help="write the human-readable report to this path")
    ap.add_argument(
        "--inject-published",
        action="append",
        default=[],
        help=(
            "THE FALSIFICATION PATH. Add this tag (or bare version) to the "
            "published set as if upstream had published it. It can only ever "
            "ADD, so it cannot turn a red into a green — it exists so the RED "
            "arm can be observed on a real run rather than only in the "
            "selftest."
        ),
    )
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    published, reason = enumerate_published()
    if args.inject_published:
        # A deliberate, LOUD widening of the published set. Applied even when
        # enumeration failed, so the falsification arm is usable on a runner
        # that cannot reach GitHub at all.
        updater = _updater()
        injected = [updater.version_from_tag(t) for t in args.inject_published]
        print(
            "⚠️  FALSIFICATION ARM: injecting "
            + ", ".join(updater.engine_tag(v) for v in injected)
            + " into the published set. This ADDS to the set and can only make "
            "the gate redder, never greener."
        )
        published = sorted(set((published or []) + injected), reverse=True)
        reason = ""

    records = record_tags(args.records)
    code, body = classify(published, records, reason=reason)
    body["injected_published"] = list(args.inject_published)

    # ECHOED unconditionally, red or green. A gate whose remedy is "write the
    # record for tag X" is useless if the run does not print X.
    print(f"outcome          : {body['outcome']} (exit {code})")
    print(f"published set    : {body.get('published') if published is not None else '<UNESTABLISHED>'}")
    print(f"record set       : {body.get('records')}")
    print(f"unrecorded       : {body.get('unrecorded')}")
    print(f"orphan records   : {body.get('orphan_records')} (noted, not scored)")
    print(f"reason           : {body.get('reason')}")

    if args.out:
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(body, indent=2, sort_keys=True), encoding="utf-8")
    if args.markdown:
        md = pathlib.Path(args.markdown)
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(render(code, body), encoding="utf-8")

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        _emit_github_output(body, code, gh_out)

    return code


if __name__ == "__main__":
    raise SystemExit(main())
