"""Two checker-matrix records in, an ordered list of what MOVED out.

PS-59 built the reader and took the first reading. This is the thing those
readings were for: holding a later record against an earlier one and answering
one question — *did anything change that should not have?*

It reports. It does not gate, it does not decide whether a difference is a
defect, and it does not take readings. A difference opens a triage; what the
triage finds is what blocks, and the triage is a human or an agent reading this
output with the charter in hand.

What is reported, and what is deliberately silent
-------------------------------------------------
**Only differences.** A row that read the same in both records produces no
output at all. The committed record carries 53 rows; a comparison that printed
all 53 would not be read, and an unread comparison is the same as no
comparison. Entries come back ordered so the ones that matter are read first.

**The SORT decides how a difference is reported.** This is the heart of the
module, and it is the entire return on accepting a rotating exit:

``fingerprint``
    The reading the whole apparatus exists to surface. Loudest — and loudest of
    all when the EXIT also moved between the two records, because a
    fingerprint-driven reading that moves when only the address moved is a
    COUPLING, which is precisely what a rotating exit was chosen to expose.
``exit``
    Expected. The exit rotates by design, so this is context, never a problem.
``host``
    Attention when the two records came from the SAME machine; ordinary when
    they did not. The header records which machine each reading came from, so
    this module uses it rather than treating every host difference alike.
``harness``
    This repository's own tooling — the JSON tier is fetched by our Python
    client, so the TLS shape those checkers report is Python's, not persona's.
    Reported in its own section so it can never be mistaken for a product
    finding. That mislabelling is a mistake PS-59 caught in its own work and
    retagged, and a future OpenSSL bump must not read as the identity moving.
``""`` (untagged)
    Cannot be classified, so it is NOT quietly filed under the harmless
    headings. Reported with the findings, saying plainly that the sort is
    missing — the conservative direction, since an untagged row that moved
    could be a fingerprint row.

**A row that became unreadable is not a row that changed.** ``read`` ->
``unobtainable`` means the RUN failed, not that the product moved. Those are
reported separately as coverage lost, and they never count as drift.

**A row that appeared or vanished is reported as such.** The catalogue grows
when a checker is added (PS-62 will add engine-driven TLS rows) and shrinks
when one dies. Neither is drift, and folding them into the diff would
misattribute a maintenance change to the product.

**A comparison that cannot be made is REFUSED, not attempted.** See
:func:`require_comparable`.

Where this agrees with ``diff.py``, and the one place it deliberately does not
--------------------------------------------------------------------------
The vocabulary is shared on purpose. An unobtainable reading is never a pass
(PS-29); a file that is not a record is refused rather than reported as clean
(PS-41); a refusal is never the drift code (PS-61). :class:`ComparisonNotControlled`
is imported from ``diff`` rather than re-minted, because it is the same concept
one artifact over: the two inputs cannot answer the question asked of them.

**The deliberate divergence is the ASYMMETRIC unreadable row.**
``diff_snapshots`` calls a probe that read fine before and throws now
``CHANGED`` — "the loudest continuity signal this subsystem can produce" — and
it is right to, because there a throwing probe is the ENGINE misbehaving inside
a controlled process. Here it is not the same event. ``unobtainable`` in a
checker record overwhelmingly means the NETWORK died: the committed record
carries 24 unobtainable rows from a single mobile-exit DNS outage mid-run. If
those were reported as differences, every run that already went badly would
produce a page of red that means nothing about the identity — and a reader
trained to skim the output is a comparator that has stopped working. So an
asymmetric unreadable row is COVERAGE LOST here, and never drift.

There is also a third state ``diff.py``'s schema has no equivalent for.
``absent`` is a real OBSERVATION — the checker answered and did not say this
(for an adverse item it is the good news) — so ``absent`` counts as evidence
here, and a row moving ``read`` <-> ``absent`` is a genuine verdict movement.
Only ``unobtainable`` and "not in the record at all" are non-evidence.
"""

from __future__ import annotations

from typing import Any

from .checkers import EXIT, FINGERPRINT, HARNESS, HOST
from .diff import ComparisonNotControlled
from .matrix import ABSENT as STATE_ABSENT, READ, UNOBTAINABLE

# Recorded in place of a row that does not exist on that side of the
# comparison. Mirrors ``diff.ABSENT`` in role; named MISSING here so it is
# never confused with the record's own ``absent`` STATE, which is the opposite
# thing — a reading that was successfully obtained.
MISSING = {"missing": True}

# --- classifications --------------------------------------------------------
#
# One label per way a row can differ. They are not severities and they are not
# verdicts: each names WHAT KIND of difference this is, which is exactly the
# judgement the sort makes possible and the triage needs.

#: A ``fingerprint`` row moved AND the exit moved between the two records.
#: The finding the whole apparatus exists to surface.
COUPLING = "coupling"
#: A ``fingerprint`` row moved while the exit stayed put.
FINGERPRINT_MOVED = "fingerprint-moved"
#: A ``host`` row moved between two readings from the SAME machine.
HOST_MOVED = "host-moved"
#: A row whose ``sort`` is empty moved. Unclassifiable, so reported loudly.
UNSORTED_MOVED = "unsorted-moved"

#: A ``fingerprint`` row moved, but the two records used DIFFERENT seeds and
#: the operator overrode the refusal. Not a coupling: the engine's fingerprint
#: is seed-derived, so this is the expected consequence of the override.
SEED_EXPLAINED = "seed-explained"
#: A ``fingerprint`` row moved, but the two records DECLARED DIFFERENT MACHINES
#: and the operator overrode the refusal. Not a coupling: the declared machine
#: is the spine of a presented identity — it constrains GPU strings, voices,
#: fonts, screen conventions, platform flags, the user agent and client hints —
#: so those rows were never supposed to match. Same shape as SEED_EXPLAINED,
#: kept distinct so the report names WHICH configuration explains the movement.
MACHINE_EXPLAINED = "machine-explained"
#: An ``exit`` row moved. Expected — the exit rotates by design.
EXIT_ROTATED = "exit-rotated"
#: A ``host`` row moved between readings from two DIFFERENT machines.
HOST_MACHINE_DIFFERS = "host-machine-differs"
#: A ``harness`` row moved. About the instrument, never about the product.
HARNESS_MOVED = "harness-moved"
#: The verdict did not move, but the checker's page text (or our pattern) did.
#: Recorded because "the verdict changed" and "the checker reworded its page"
#: are two facts that are indistinguishable if only the value is kept.
REWORDED = "reworded"

#: A row that was READ or ABSENT before and is UNOBTAINABLE now. The run
#: failed; the product did not move. Never drift — see the module docstring.
COVERAGE_LOST = "coverage-lost"
#: The reverse. The README is explicit that the first later run which actually
#: reads the outage rows must NOT be treated as a regression.
COVERAGE_REGAINED = "coverage-regained"
#: Unobtainable on BOTH sides. No evidence either way, so nothing moved and
#: nothing was established. Never silent, never a pass.
UNREAD_BOTH = "unread-both"

#: The row is in the later record only — a checker or item was added.
APPEARED = "appeared"
#: The row is in the earlier record only — a checker or item died.
VANISHED = "vanished"

# Read order. Lower sorts first, so the findings are the first thing on screen
# and a reader who stops after ten lines has read the ten that matter.
_RANK = {
    COUPLING: 0,
    FINGERPRINT_MOVED: 1,
    HOST_MOVED: 2,
    UNSORTED_MOVED: 3,
    SEED_EXPLAINED: 4,
    MACHINE_EXPLAINED: 5,
    EXIT_ROTATED: 6,
    HOST_MACHINE_DIFFERS: 7,
    HARNESS_MOVED: 8,
    REWORDED: 9,
    COVERAGE_LOST: 10,
    COVERAGE_REGAINED: 11,
    UNREAD_BOTH: 12,
    APPEARED: 13,
    VANISHED: 14,
}

# Sections, in the order they render. The grouping is the report's whole
# argument: a harness move can never appear under FINDINGS, and a lost row can
# never appear as drift, because the section a classification renders under is
# fixed here rather than decided per run.
FINDINGS = "FINDINGS"
CONTEXT = "CONTEXT"
HARNESS_SECTION = "HARNESS (this repo's instrument, not the product)"
COVERAGE = "COVERAGE"
CATALOGUE = "CATALOGUE"

_SECTION = {
    COUPLING: FINDINGS,
    FINGERPRINT_MOVED: FINDINGS,
    HOST_MOVED: FINDINGS,
    UNSORTED_MOVED: FINDINGS,
    SEED_EXPLAINED: CONTEXT,
    MACHINE_EXPLAINED: CONTEXT,
    EXIT_ROTATED: CONTEXT,
    HOST_MACHINE_DIFFERS: CONTEXT,
    REWORDED: CONTEXT,
    HARNESS_MOVED: HARNESS_SECTION,
    COVERAGE_LOST: COVERAGE,
    COVERAGE_REGAINED: COVERAGE,
    UNREAD_BOTH: COVERAGE,
    APPEARED: CATALOGUE,
    VANISHED: CATALOGUE,
}

_SECTION_ORDER = (FINDINGS, CONTEXT, HARNESS_SECTION, COVERAGE, CATALOGUE)

# One sentence per classification, rendered under its entries. The output has
# to be readable by someone who has not read this module.
_MEANING = {
    COUPLING: (
        "a FINGERPRINT row moved while the exit ALSO moved — this is the "
        "coupling a rotating exit was chosen to expose. Triage it."
    ),
    FINGERPRINT_MOVED: (
        "a FINGERPRINT row moved on one exit. The address did not move, so "
        "this is not exit rotation. Triage it."
    ),
    HOST_MOVED: (
        "a HOST row moved between two readings taken on the SAME machine, so "
        "the machine does not explain it."
    ),
    UNSORTED_MOVED: (
        "a row with NO sort moved, so it could not be classified. Read it as "
        "potentially fingerprint-driven until the catalogue tags it."
    ),
    SEED_EXPLAINED: (
        "a FINGERPRINT row moved, but the two records used different SEEDS and "
        "the refusal was overridden. The engine's fingerprint is seed-derived, "
        "so this is NOT evidence of a coupling."
    ),
    MACHINE_EXPLAINED: (
        "a FINGERPRINT row moved, but the two records DECLARED DIFFERENT "
        "MACHINES and the refusal was overridden. The declared machine is the "
        "spine of a presented identity — it constrains GPU strings, voices, "
        "fonts, screen conventions, platform flags, the UA and client hints — "
        "so this is NOT evidence of a coupling."
    ),
    EXIT_ROTATED: "an EXIT row moved. The exit rotates by design — not news.",
    HOST_MACHINE_DIFFERS: (
        "a HOST row moved between readings from two DIFFERENT machines. "
        "Host-driven readings are expected to differ per machine."
    ),
    HARNESS_MOVED: (
        "this describes THIS REPO'S OWN INSTRUMENT (the Python client that "
        "fetches the JSON tier), never persona's identity. A Python or OpenSSL "
        "upgrade moves these. Never triage one as a product finding."
    ),
    REWORDED: (
        "the verdict did NOT move; the page text or our pattern did. The "
        "checker reworded itself."
    ),
    COVERAGE_LOST: (
        "readable before, UNOBTAINABLE now. The RUN failed here — this is not "
        "the product moving, and nothing may be inferred about the identity."
    ),
    COVERAGE_REGAINED: (
        "UNOBTAINABLE before, readable now. Coverage came back; this is not a "
        "regression and the new reading has no predecessor to be compared to."
    ),
    UNREAD_BOTH: (
        "unobtainable in BOTH records. Nothing was compared here, so this is "
        "NOT agreement — the rows simply have no evidence behind them."
    ),
    APPEARED: (
        "in the later record only — the catalogue GREW. A maintenance change, "
        "never drift."
    ),
    VANISHED: (
        "in the earlier record only — the catalogue SHRANK. A maintenance "
        "change, never drift."
    ),
}


class NotARecord(ValueError):
    """Raised when an object handed in is not a checker-matrix record at all.

    The sibling of ``diff.NotASnapshot``, one artifact over and refusing for
    the identical reason. Without it the failure is silent and INVERTED: a file
    with no ``readings`` yields zero rows to compare, the comparator returns
    ``[]``, and an empty list is this module's agreement signal — so a file that
    was never a record renders "nothing moved" and exits 0. The tool would be
    at its most confident exactly when it holds the least evidence, and a false
    green is worse than a false red because a red gets investigated while
    "nothing moved" gets believed.
    """


class RecordUnreadable(ValueError):
    """Raised when a record file could not be READ at all.

    The sibling of ``diff.SnapshotUnreadable``, and the step before
    :class:`NotARecord` in the same story: that one refuses a file that read
    back perfectly well and is not a record; this one refuses a file that never
    became an object to inspect — the path does not exist, the bytes are not
    UTF-8, or the text is not JSON.

    Both surface as the CLI's exit 2, which is the part that matters and is the
    convention PS-61 settled: nothing was compared, so neither can be drift,
    and the drift code never means "I could not look".
    """


def require_record(obj: Any, source: str | None = None) -> dict:
    """Return ``obj`` if it is a checker-matrix record; else refuse.

    ``readings`` is the discriminator because :func:`matrix.build_record`
    ALWAYS emits it — every record this subsystem produces has one, so its
    absence means the file did not come from ``checker_cli read``.
    Deliberately shallow: this answers "is this a record at all", not "are its
    rows well-formed".
    """
    if isinstance(obj, dict) and isinstance(obj.get("readings"), list):
        return obj
    where = f"{source!r} " if source else "the input "
    raise NotARecord(
        f"{where}is not a checker-matrix record: it carries no 'readings' "
        "list, so NOTHING WAS COMPARED. This is NOT 'nothing moved' — no "
        "reading was obtained from it, and a comparison of zero readings "
        "cannot establish that anything held still. Take a reading first: "
        "`python -m src.services.verify.checker_cli read -o reading.json`."
    )


def require_comparable(
    before: dict,
    after: dict,
    *,
    allow_cross_engine: bool = False,
    allow_different_seed: bool = False,
    allow_different_machine: bool = False,
) -> None:
    """Refuse a comparison whose premise does not hold.

    Read before a single row is compared, because the alternative to refusing
    is emitting a diff that reads as catastrophic drift when in fact the two
    records were never supposed to match. **The bias is deliberate and stated:
    a refusal a human overrides is cheap; a false alarm that gets believed is
    not — and the second one spent, the next real finding is not believed
    either.**

    Four premises. Two of them can be overridden and two cannot, and which is
    which is the whole design:

    * **The seed must be RECORDED on both sides — no override.** The engine's
      fingerprint is SEED-DERIVED, so without the seed a comparison cannot tell
      a real coupling from a different profile — measured on this project, the
      renderer moved ``NVIDIA GTX 980`` -> ``Intel HD Graphics 400`` between
      two runs purely because the seed differed. A record that does not say
      which seed it used cannot answer the question, and no flag can supply the
      fact after the fact. Note ``0`` is a legitimate VALUE (the engine's own
      default), not a missing seed — the key's ABSENCE is what refuses here.

    * **Different, NAMED seeds — overridable** with ``allow_different_seed``.
      The fingerprint rows were never supposed to match, so the default is a
      refusal; but an operator who wants the exit/host/harness rows out of two
      differently-seeded records has a real use, and they have weighed the
      caveat. Overriding does NOT make the fingerprint rows interpretable: they
      report as :data:`SEED_EXPLAINED` context rather than as a coupling, so
      the override cannot manufacture the module's loudest finding.

    * **The engine must be RECORDED on both sides — no override**, and this is
      NOT relaxed by ``allow_cross_engine``, exactly as ``diff._require_controlled``
      reasons one artifact over. That flag opts in to a KNOWN, NAMED engine
      difference whose caveat the operator has weighed; an unrecorded engine
      gives them nothing to weigh. Two records that both omit the field must
      not slip through on ``None == None``, which reads as "same engine" while
      meaning "no idea".

    * **Different, NAMED engine builds — overridable** with
      ``allow_cross_engine``. A different engine build is a different
      fingerprint generator, so a row that moved may have moved because of the
      BUILD. Opt-in rather than a wall, because comparing across an engine
      update is a legitimate question to ask deliberately — it is, in fact, the
      question an update-reliability triage asks.

    * **The schema version must match — no override.** The row vocabulary
      itself is what changed, so ``sort``, ``state`` and the identity of a row
      may not mean the same thing on both sides. Every classification below is
      built on those meanings, so there is nothing left to be conservative
      WITH: an override would be asking the operator to weigh a caveat neither
      they nor this module can state. Re-read the older record with the current
      reader instead.

    * **The DECLARED MACHINE — overridable** with ``allow_different_machine``,
      on exactly the argument that put the seed here. PS-69 made this field
      real: the declared machine is the spine of a presented identity — it
      constrains GPU strings, voices, fonts, screen conventions, platform
      flags, the user agent and client hints — so two records taken on
      different declared machines differ for a reason that has nothing to do
      with a coupling. Without this guard EVERY fingerprint row across two
      machines reports as the module's loudest finding.

      Its ABSENCE is handled differently from the seed's, and the difference is
      forced by a fact about the artifact rather than chosen: **PS-69 added the
      field without bumping ``SCHEMA_VERSION``**, so a pre-PS-69 record and a
      post-PS-69 one both say ``schema_version: 1``. The schema guard therefore
      cannot separate them and this one must:

      - **both missing** — no refusal. Both records predate the field; there is
        no difference to detect, and refusing would make the tool useless on
        the one committed record, which is the whole test corpus.
      - **exactly one missing** — REFUSED. The two were produced by different
        readers and nothing in either file says whether the machine moved.
        Silently treating "no field" as "same machine" is the ``None == None``
        error one field over.
      - **both present and different** — refused unless overridden, and under
        the override fingerprint rows report as :data:`MACHINE_EXPLAINED`
        context rather than as a coupling.

    The EXIT and the SKIPPED TIERS deliberately do NOT refuse. Each changes how
    a difference is READ rather than whether it can be read at all, so each is
    an annotation on the report (see :func:`compare_records`) — the exit one
    especially: two records taken through two exits is the NORMAL case this
    comparator exists for, not an error.
    """
    if "seed" not in before or "seed" not in after:
        raise ComparisonNotControlled(
            "cannot compare: both records must state the SEED they were taken "
            f"under (got {before.get('seed', '<missing>')!r} and "
            f"{after.get('seed', '<missing>')!r}). The engine's fingerprint is "
            "seed-derived, so without it a moved fingerprint row cannot be "
            "told apart from a different profile — which is the entire "
            "analysis this record exists to enable. Note 0 is a real seed "
            "(the engine's own default); it is the MISSING key that refuses. "
            "Re-take the reading with a reader that records the seed."
        )
    if before["seed"] != after["seed"] and not allow_different_seed:
        raise ComparisonNotControlled(
            f"cannot compare: the records were taken under different seeds "
            f"({before['seed']!r} vs {after['seed']!r}), so the "
            "fingerprint-driven rows were NEVER SUPPOSED TO MATCH and a diff "
            "would read as catastrophic drift. Re-take both on one seed, or "
            "pass --allow-different-seed to compare anyway — the fingerprint "
            "rows are then reported as seed-explained context, never as a "
            "coupling."
        )
    before_engine, after_engine = before.get("engine"), after.get("engine")
    if not before_engine or not after_engine:
        raise ComparisonNotControlled(
            "cannot compare: both records must name the ENGINE they were taken "
            f"under (got {before_engine!r} and {after_engine!r}). A row that "
            "moved is evidence about the identity only if both readings came "
            "from a comparable engine, and 'no engine recorded' is not "
            "evidence that they did. --allow-cross-engine does NOT relax this: "
            "it opts in to a KNOWN, NAMED build difference whose caveat the "
            "operator has weighed, and an unrecorded engine gives them nothing "
            "to weigh."
        )
    if before_engine != after_engine and not allow_cross_engine:
        raise ComparisonNotControlled(
            f"cannot compare: the records were taken under different engine "
            f"builds ({before_engine!r} vs {after_engine!r}), so a row that "
            "moved may have moved because of the BUILD rather than the "
            "identity. Re-take both on one build, or pass --allow-cross-engine "
            "to compare anyway (comparing across an engine update is a "
            "legitimate question — but it is a different one)."
        )
    # PS-69 added `declared_machine` WITHOUT bumping SCHEMA_VERSION, so the
    # schema guard below cannot separate a pre-PS-69 record from a post-PS-69
    # one — both say 1. This guard is what covers that seam, which is why its
    # missing-field case is handled per-side rather than with the seed's
    # "absent on either side refuses" rule.
    before_has_machine = "declared_machine" in before
    after_has_machine = "declared_machine" in after
    if before_has_machine != after_has_machine:
        raise ComparisonNotControlled(
            "cannot compare: only one record states the DECLARED MACHINE "
            f"(before: {before.get('declared_machine', '<missing>')!r}, after: "
            f"{after.get('declared_machine', '<missing>')!r}). The two came "
            "from different readers, and nothing in either file says whether "
            "the machine moved — so a moved fingerprint row cannot be told "
            "apart from a different declared identity. Treating 'no field' as "
            "'same machine' is exactly the assumption that would report a "
            "configuration change as a coupling. Re-take the older reading "
            "with the current reader."
        )
    if (
        before_has_machine
        and before.get("declared_machine") != after.get("declared_machine")
        and not allow_different_machine
    ):
        raise ComparisonNotControlled(
            "cannot compare: the records declared different machines "
            f"({before.get('declared_machine')!r} vs "
            f"{after.get('declared_machine')!r}). The declared machine is the "
            "spine of a presented identity — it constrains GPU strings, "
            "voices, fonts, screen conventions, platform flags, the user agent "
            "and client hints — so the fingerprint-driven rows were NEVER "
            "SUPPOSED TO MATCH and a diff would read as catastrophic drift. "
            "Re-take both on one machine, or pass --allow-different-machine to "
            "compare anyway — the fingerprint rows are then reported as "
            "machine-explained context, never as a coupling."
        )
    before_schema = before.get("schema_version")
    after_schema = after.get("schema_version")
    if before_schema != after_schema:
        raise ComparisonNotControlled(
            f"cannot compare: the records use different schema versions "
            f"({before_schema!r} vs {after_schema!r}), so 'sort', 'state' and "
            "the identity of a row may not mean the same thing on both sides — "
            "and every classification this comparator makes is built on those "
            "meanings. Re-take the older reading with the current reader."
        )


def _key(row: Any) -> "tuple[str, str]":
    if not isinstance(row, dict):
        return ("", "")
    return (str(row.get("checker", "")), str(row.get("item", "")))


def _rows(record: dict) -> "dict[tuple[str, str], dict]":
    readings = record.get("readings")
    if not isinstance(readings, list):
        return {}
    return {_key(r): r for r in readings if isinstance(r, dict)}


def _obtained(row: Any) -> bool:
    """True when this side carries a reading that was actually OBTAINED.

    Two states count, and the second is the one that separates this module
    from ``diff.py``. ``read`` is obviously evidence. ``absent`` is evidence
    TOO: the checker answered and did not say this, which for an adverse item
    (``proxy_detected``) is precisely the GOOD news. Folding absent in with
    unobtainable would make every clean page look unread.

    ``unobtainable``, a missing row, and anything malformed are not evidence.
    The safe default in this subsystem is to treat what we cannot recognise as
    evidence we do not have.
    """
    if not isinstance(row, dict):
        return False
    return row.get("state") in (READ, STATE_ABSENT)


def _verdict(row: dict) -> tuple:
    """The part of a row that IS the reading — what moving means.

    ``state`` and ``value`` only. ``pattern`` and ``matched_text`` are how the
    reading was obtained and what the page said around it; they are compared
    separately (see :data:`REWORDED`) so that the checker rewording its page is
    never reported as the verdict changing.
    """
    return (row.get("state"), row.get("value"))


def _sort_classification(
    sort: str,
    *,
    exit_moved: bool,
    same_machine: bool,
    seed_differs: bool,
    machine_differs: bool = False,
) -> str:
    """Map a row's SORT to how its movement is reported. The heart of the module."""
    if sort == FINGERPRINT:
        if seed_differs:
            # The operator overrode the seed refusal. A seed-derived reading
            # moving under a different seed is the expected consequence of that
            # override, and calling it a coupling would let a flag manufacture
            # this module's loudest finding.
            return SEED_EXPLAINED
        if machine_differs:
            # Same reasoning one field over, and the same rule: an override
            # must never be able to produce this module's loudest finding.
            # The declared machine is the spine of a presented identity, so a
            # fingerprint row moving across two machines is the expected
            # consequence of the override rather than a coupling.
            return MACHINE_EXPLAINED
        return COUPLING if exit_moved else FINGERPRINT_MOVED
    if sort == EXIT:
        return EXIT_ROTATED
    if sort == HOST:
        # The record says which machine it came from; use it rather than
        # treating every host difference alike.
        return HOST_MOVED if same_machine else HOST_MACHINE_DIFFERS
    if sort == HARNESS:
        return HARNESS_MOVED
    return UNSORTED_MOVED


def compare_records(
    before: dict,
    after: dict,
    *,
    allow_cross_engine: bool = False,
    allow_different_seed: bool = False,
    allow_different_machine: bool = False,
) -> "list[dict]":
    """Compare two checker-matrix records and return what MOVED, ranked.

    ``before`` is the earlier record, ``after`` the later one. Entries come
    back ordered by ``(rank, checker, item)`` so the findings are first, each
    shaped::

        {"checker": ..., "item": ..., "sort": ...,
         "classification": <one of the module constants>,
         "section": ..., "rank": ...,
         "before": <row or MISSING>, "after": <row or MISSING>,
         "observed": <bool>}

    ``observed`` is True when the entry rests on a reading someone actually
    obtained AND names a movement rather than a hole. It is what
    :func:`observed_count` counts and what the CLI's exit code keys off, so
    "the identity moved" and "we failed to look" can never collapse into one
    signal.

    An EMPTY list means every row present in either record was read on both
    sides and agreed. That is a strong statement, and it is only true because
    rows resting on no evidence are REPORTED (as coverage) rather than skipped.

    Both inputs must BE records, and the comparison must be one that can be
    made at all — see :func:`require_record` and :func:`require_comparable`.
    """
    require_record(before)
    require_record(after)
    require_comparable(
        before,
        after,
        allow_cross_engine=allow_cross_engine,
        allow_different_seed=allow_different_seed,
        allow_different_machine=allow_different_machine,
    )

    # The header facts that change how a row difference is READ. None of them
    # refuses here; each one re-classifies.
    #
    # Note the two DIFFERENT machine questions, which must not be conflated:
    # `same_machine` is the HOST the reading was taken on (`environment`) and
    # governs host-sorted rows; `machine_differs` is the machine the profile
    # DECLARED (`declared_machine`, PS-69) and governs fingerprint-sorted ones.
    # A reading taken on one laptop can declare Windows or macOS, so the two
    # are independent.
    exit_moved = before.get("exit") != after.get("exit")
    same_machine = before.get("environment") == after.get("environment")
    seed_differs = before.get("seed") != after.get("seed")
    machine_differs = before.get("declared_machine") != after.get("declared_machine")

    before_rows = _rows(before)
    after_rows = _rows(after)

    out: "list[dict]" = []
    for key in sorted(set(before_rows) | set(after_rows)):
        a = before_rows.get(key)
        b = after_rows.get(key)
        entry = _classify(
            a,
            b,
            exit_moved=exit_moved,
            same_machine=same_machine,
            seed_differs=seed_differs,
            machine_differs=machine_differs,
        )
        if entry is not None:
            out.append(entry)

    out.sort(key=lambda e: (e["rank"], e["checker"], e["item"]))
    return out


def _classify(
    a: "dict | None",
    b: "dict | None",
    *,
    exit_moved: bool,
    same_machine: bool,
    seed_differs: bool,
    machine_differs: bool = False,
) -> "dict | None":
    """Classify one row pair, or return None when it did not move at all.

    The order of the branches is the argument, and each one is a rule the
    ticket states outright:

    1. Present on ONE side only -> the CATALOGUE changed. Checked first
       because an added or dropped checker is a maintenance fact, and letting
       it fall through to a verdict comparison would misattribute it.
    2. Obtained on NEITHER side -> no evidence was ever gathered here. Not a
       difference, and never silent.
    3. Obtained on ONE side only -> COVERAGE moved, not the product. This is
       the deliberate divergence from ``diff.py`` documented at module level.
    4. Obtained on BOTH -> the only branch where a real comparison happens, so
       the only branch that can produce a finding.
    """
    if a is None or b is None:
        present = b if a is None else a
        classification = APPEARED if a is None else VANISHED
        return _entry(
            a,
            b,
            classification,
            sort=str(present.get("sort", "")),
            # An added row whose only reading was unobtainable is an inventory
            # change with no evidence behind it: still worth REPORTING, but it
            # must not be counted as a difference anyone observed.
            observed=_obtained(present),
        )

    a_has, b_has = _obtained(a), _obtained(b)
    sort = str(b.get("sort", a.get("sort", "")))

    if not a_has and not b_has:
        return _entry(a, b, UNREAD_BOTH, sort=sort, observed=False)
    if a_has != b_has:
        classification = COVERAGE_LOST if a_has else COVERAGE_REGAINED
        return _entry(a, b, classification, sort=sort, observed=False)

    if _verdict(a) != _verdict(b):
        classification = _sort_classification(
            sort,
            exit_moved=exit_moved,
            same_machine=same_machine,
            seed_differs=seed_differs,
            machine_differs=machine_differs,
        )
        return _entry(a, b, classification, sort=sort, observed=True)

    if (a.get("matched_text"), a.get("pattern")) != (
        b.get("matched_text"),
        b.get("pattern"),
    ):
        # The verdict held; the wording moved. Reported, quietly.
        return _entry(a, b, REWORDED, sort=sort, observed=True)

    # Read on both sides and agreed. The silent pass, and the reason this
    # report is short enough to be read.
    return None


def _entry(
    a: "dict | None",
    b: "dict | None",
    classification: str,
    *,
    sort: str,
    observed: bool,
) -> dict:
    present = a if a is not None else b
    return {
        "checker": str((present or {}).get("checker", "")),
        "item": str((present or {}).get("item", "")),
        "sort": sort,
        "classification": classification,
        "section": _SECTION[classification],
        "rank": _RANK[classification],
        "before": a if a is not None else dict(MISSING),
        "after": b if b is not None else dict(MISSING),
        "observed": observed,
    }


def findings(entries: "list[dict]") -> "list[dict]":
    """The entries that name a movement someone should TRIAGE.

    The FINDINGS section only — a fingerprint row that moved, a host row that
    moved on one machine, an untagged row that moved. Exit rotation, harness
    movement, rewording, coverage and catalogue changes are all reported, and
    none of them is a finding.
    """
    return [e for e in entries if e["section"] == FINDINGS]


def coverage_lost(entries: "list[dict]") -> "list[dict]":
    """The entries where a row that WAS readable stopped being readable.

    Kept separate from :data:`UNREAD_BOTH` deliberately, and this distinction
    is what makes the CLI's exit code mean anything. Rows unobtainable on BOTH
    sides are the STANDING state of this matrix, not an event: the catalogue
    permanently carries click-gated checkers, a Cloudflare challenge and a
    paywall, so the committed record has 24 of them and every record always
    will. A code that fired on those would fire on every comparison forever and
    would be ignored within a week.

    ``read``/``absent`` -> ``unobtainable`` is the thing that actually
    happened on THIS run: coverage the matrix had and no longer has.
    """
    return [e for e in entries if e["classification"] == COVERAGE_LOST]


def observed_count(entries: "list[dict]") -> int:
    """How many entries name a movement someone actually observed.

    The complement is not "unimportant" — it is coverage lost, coverage
    regained, rows unreadable on both sides, and inventory changes with no
    reading behind them. Those are real and they are reported; they are simply
    not evidence that anything MOVED, and the exit code has to be able to say
    so.
    """
    return sum(1 for e in entries if e.get("observed"))


def header_notes(before: dict, after: dict) -> "list[str]":
    """The header facts that change how the report is READ, as plain sentences.

    None of these is a difference in a row, and none of them refuses. They are
    printed above the entries because the same moved fingerprint row means
    something different depending on whether the exit rotated, and a reader who
    does not know which happened cannot triage either.
    """
    notes: "list[str]" = []
    notes.append(
        f"before: {before.get('observed_at', '?')}    "
        f"after: {after.get('observed_at', '?')}"
    )
    before_exit = before.get("exit") or {}
    after_exit = after.get("exit") or {}
    if before_exit != after_exit:
        notes.append(
            "EXIT MOVED: "
            f"{_exit_label(before_exit)} -> {_exit_label(after_exit)}. "
            "Expected — the exit rotates by design. It is why a fingerprint "
            "row that moved is reported as a COUPLING here."
        )
    else:
        notes.append(
            f"exit held: {_exit_label(before_exit)}. A fingerprint row that "
            "moved cannot be blamed on the address."
        )
    # Two DIFFERENT machine questions live in this header and they must not be
    # said in the same words. `environment` is the HOST the reading was taken
    # ON (it governs host-sorted rows); `declared_machine` is the machine the
    # profile PRESENTED (PS-69, it governs fingerprint-sorted rows). One laptop
    # can declare Windows or macOS, so the two are independent — and a reader
    # who conflates them will triage the wrong thing.
    before_env = before.get("environment") or "?"
    after_env = after.get("environment") or "?"
    if before_env != after_env:
        notes.append(
            f"DIFFERENT HOST MACHINES: {before_env!r} -> {after_env!r}. "
            "Host-driven rows are expected to differ; they are reported as "
            "context."
        )
    else:
        notes.append(f"same host machine: {before_env}. Host rows should hold.")
    before_declared = before.get("declared_machine")
    after_declared = after.get("declared_machine")
    if before_declared != after_declared:
        notes.append(
            f"DECLARED MACHINE DIFFERS: {before_declared!r} -> "
            f"{after_declared!r} (refusal overridden). The declared machine is "
            "the spine of a presented identity, so fingerprint rows moving is "
            "NOT evidence of a coupling."
        )
    elif before_declared is not None:
        notes.append(
            f"same declared machine: {before_declared}"
            + (
                " (NOT honoured by this engine — it presents Windows "
                "regardless, so the field states what was actually declared)"
                if before.get("declared_machine_honoured") is False
                else ""
            )
            + "."
        )
    if before.get("seed") != after.get("seed"):
        notes.append(
            f"SEED DIFFERS: {before.get('seed')!r} -> {after.get('seed')!r} "
            "(refusal overridden). Fingerprint rows are seed-derived, so their "
            "movement is NOT evidence of a coupling."
        )
    if before.get("engine") != after.get("engine"):
        notes.append(
            f"ENGINE BUILD DIFFERS: {before.get('engine')!r} -> "
            f"{after.get('engine')!r} (refusal overridden). A row that moved "
            "may have moved because of the build."
        )
    before_skipped = list(before.get("skipped_tiers") or [])
    after_skipped = list(after.get("skipped_tiers") or [])
    if before_skipped != after_skipped:
        notes.append(
            f"skipped tiers differ: {before_skipped or 'none'} -> "
            f"{after_skipped or 'none'}. A skipped tier's rows are recorded "
            "unobtainable, so this explains coverage entries below rather than "
            "being drift."
        )
    return notes


def _exit_label(exit_block: dict) -> str:
    if not isinstance(exit_block, dict) or not exit_block:
        return "<no exit recorded>"
    ip = exit_block.get("ip", "?")
    org = exit_block.get("org", "?")
    city = exit_block.get("city", "?")
    country = exit_block.get("country", "?")
    return f"{ip} ({org}, {city}/{country})"


def format_comparison(
    entries: "list[dict]", *, notes: "list[str] | None" = None
) -> str:
    """Render a comparison for an operator: findings first, sections labelled.

    Empty input renders as the explicit statement that nothing moved, never as
    blank output — and the statement is precise about what it does and does not
    claim, because "no differences" is exactly the string a reader over-reads.
    """
    lines: "list[str]" = []
    for note in notes or []:
        lines.append(note)
    if notes:
        lines.append("")

    if not entries:
        lines.append(
            "nothing moved: every row present in either record was read on "
            "both sides and agreed."
        )
        return "\n".join(lines)

    for section in _SECTION_ORDER:
        in_section = [e for e in entries if e["section"] == section]
        if not in_section:
            continue
        lines.append(f"== {section} ==")
        current = None
        for entry in in_section:
            if entry["classification"] != current:
                current = entry["classification"]
                lines.append(f"-- {current}: {_MEANING[current]}")
            label = f"{entry['checker']}/{entry['item']} [{entry['sort'] or 'untagged'}]"
            if entry["classification"] == UNREAD_BOTH:
                # ONE LINE, deliberately. These rows are reported — PS-29's
                # rule is that an unobtainable reading is never a pass, and
                # staying silent about them would be exactly that — but they
                # are the one class that is BOTH numerous and identical on
                # both sides. The committed record carries 24 of them from a
                # single mid-run exit outage, each with a multi-line
                # navigation error as its reason; rendering those as
                # before/after blocks buries the findings under two screens of
                # stack trace on precisely the runs that already went badly,
                # and a reader trained to skim is a comparator that has
                # stopped working. So: named, counted, never hidden, never
                # expanded.
                lines.append(
                    f"{label}: {_render_row(entry['before'], compact=True)}"
                )
                continue
            lines.append(label)
            lines.append(f"  before: {_render_row(entry['before'])}")
            lines.append(f"  after:  {_render_row(entry['after'])}")
        lines.append("")

    moved = observed_count(entries)
    unmoved = len(entries) - moved
    summary = (
        f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}: "
        f"{moved} observed movement, {unmoved} without an observed movement"
    )
    if unmoved:
        summary += (
            " (coverage and catalogue changes — reported, but NOT evidence "
            "that anything moved, and never a pass either)"
        )
    lines.append(summary)
    return "\n".join(lines)


def _render_row(row: Any, *, limit: int = 240, compact: bool = False) -> str:
    """One line describing a side of a comparison, in the record's own terms.

    ``compact`` collapses internal whitespace, for the one-line rendering used
    where a row's reason is a multi-line navigation error. The reasons recorded
    by the reader are verbatim engine failures — ``Page.goto:
    NS_ERROR_UNKNOWN_HOST`` arrives with its own ``Call log:`` block — so a
    "one line per row" caller gets four lines per row without this, which is
    the readability failure it was trying to avoid in the first place.
    """
    if not isinstance(row, dict):
        return repr(row)
    if row.get("missing"):
        return "<not in this record>"
    state = row.get("state", "?")
    if state == READ:
        text = f"read {_render(row.get('value'))}"
        matched = row.get("matched_text")
        if matched:
            text += f"  matched {_render(matched)}"
    elif state == STATE_ABSENT:
        text = f"absent ({row.get('reason') or 'the checker did not say this'})"
    elif state == UNOBTAINABLE:
        text = f"unobtainable ({row.get('reason') or 'no reason recorded'})"
    else:
        text = f"{state} {_render(row.get('value'))}"
    if compact:
        text = " ".join(text.split())
        limit = 150
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _render(value: Any, *, limit: int = 160) -> str:
    import json as _json

    try:
        text = _json.dumps(value, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = [
    "APPEARED",
    "COUPLING",
    "COVERAGE_LOST",
    "COVERAGE_REGAINED",
    "ComparisonNotControlled",
    "EXIT_ROTATED",
    "FINGERPRINT_MOVED",
    "HARNESS_MOVED",
    "HOST_MACHINE_DIFFERS",
    "HOST_MOVED",
    "MACHINE_EXPLAINED",
    "MISSING",
    "NotARecord",
    "RecordUnreadable",
    "REWORDED",
    "SEED_EXPLAINED",
    "UNREAD_BOTH",
    "UNSORTED_MOVED",
    "VANISHED",
    "compare_records",
    "coverage_lost",
    "findings",
    "format_comparison",
    "header_notes",
    "observed_count",
    "require_comparable",
    "require_record",
]
