"""How MANY identities does a must-differ vector actually have across a SET?

WHY THIS EXISTS — THE QUESTION THE PAIRWISE GATE CANNOT ASK
-------------------------------------------------------------
:func:`diff.compare_profiles` is the Level-2 (mutual unlinkability) gate, and
it takes **exactly two** snapshots. That shape is correct for what it answers —
"are these two profiles distinguishable?" — and it is the wrong shape for the
question underneath it, because **"this PAIR collided" and "this vector only
ever takes two values" are different facts and only the first is currently
expressible.**

A pair that collides tells you those two profiles are linkable. It does not
tell you whether you were unlucky or whether the pool is two deep. Run the
pairwise gate over five profiles and you get ten verdicts, none of which says
the sentence an operator actually needs:

    chromium `audio.digest` = 2 distinct values across 5 seeds.

That is a property of a SET, and nothing in the subsystem held a set on this
axis. (``engine_gpu_variance`` holds one, but on a different question and a
different vector — it reads ``webgl.unmasked``, graded :data:`probes.POOLED`,
and is disjoint from the must-differ inventory.) So this is a new lane rather
than a reuse, for the same reason that module's own header gives, one axis
over.

WHAT IT REPORTS, AND WHY IT REPORTS TWO NUMBERS RATHER THAN ONE
----------------------------------------------------------------
Per vector, per engine: the **distinct-value count**, the **collision
probability**, and the **groups of identities that collide**.

Both numbers, deliberately. The distinct count is what an operator reads and
what AC1 is stated in; the collision probability is what is sensitive to SKEW,
which a bare distinct count is blind to. :func:`engine_gpu_variance.
collision_probability` is IMPORTED rather than re-derived, and its docstring
already argues the point on measured data: two values split 87/13 collide 77%
of the time while 50/50 collide 50% of the time, and a distinct count scores
those identically. One implementation of the Simpson index in the tree means
the two lanes cannot drift into disagreeing about what a collision probability
is.

⚠️ THIS SHIPS NO PROTECTION AND CHANGES NO EXISTING ANSWER. It makes an
already-recorded fact legible. ``compare_profiles``, ``diff_snapshots``,
``must_differ_probes`` and ``_require_controlled`` are untouched, and this lane
deliberately does NOT route through the pairwise comparator — see THE
PROFILE-LESS RECORDS below, which is the whole reason it reads records
directly.

THE THREE WAYS A DISTINCTNESS STATISTIC GETS SILENTLY CORRUPTED
------------------------------------------------------------------
All three are present in the committed corpus this was built against, all three
have already refuted a previous finding somewhere in this project, and all
three arrive by **counting something that is not a fresh identity**. They are
the reason the selection rules below are as strict as they are.

1. **THE CONTROL ARM, AND ITS HEADERS LIE.** Both
   ``counterfactual.chromium.no-fingerprint-flag.*`` records — profiles launched
   with the spoof flag ABSENT — carry a stale ``flag`` header contradicting
   their own filename (``--fingerprint=1337 (process.py:561)``) and a matching
   ``seed`` field. **Grouping by the ``seed``/``flag`` header pulls the no-flag
   control into the treatment group**, which is the exact error that refuted
   PS-89. So the arm is decided by PROVENANCE — where the record came from —
   and NEVER by a header field. (Sharpest row in that corpus: the no-flag
   control reads ``sum = 124.043475``, byte-identical to product seeds 111 and
   333. Those two profiles are indistinguishable from a profile with the spoof
   flag absent entirely.)

2. **A RERUN IS ONE IDENTITY RECORDED TWICE.** The corpus holds
   ``reading.chromium.seed1337.rerun.json`` and its firefox twin — the same
   profile measured again, not a second identity. Counting it adds a
   GUARANTEED duplicate value, which inflates the denominator and **overstates
   collision**. Excluded by provenance, on the same footing as the control arm.
   This is why the firefox arm is *3* identities and not the 4 files present.

3. **A VECTOR'S REALMS ARE NOT INDEPENDENT DRAWS.** ``canvas.readback``
   declares realms ``('window', 'worker')`` and every record carries the same
   digest in both. A reader that walks ``probes[realm][probe_id]`` and appends
   every cell collects **6 values for 3 identities** — one profile's two realms
   counted as two profiles. That inflates the denominator, and on a *healthier*
   vector it would UNDERSTATE collision by diluting a real duplicate. Same
   error as the rerun, arriving on a different axis. So the unit of counting is
   **the identity, not the (identity, realm) cell**: :func:`_fold_realms`
   collapses a vector's declared realms into ONE value per identity before
   anything is counted, and ``collision_probability`` is therefore fed exactly
   one element per identity — which is what its Simpson index assumes.

THE PROFILE-LESS RECORDS, AND WHY THIS LANE DOES NOT USE THE COMPARATOR
-------------------------------------------------------------------------
Every chromium record in the committed corpus carries **no ``profile`` header
at all**, and ``_require_controlled`` refuses on exactly that::

    compare_profiles(seed111, seed222)
    -> ComparisonNotControlled: "cannot compare: both snapshots must name their
       profile in the header (got None and None)."

All 10 chromium product pairs refuse; 0 run. **That refusal is correct and is
not relaxed here.** That mode needs positive evidence that two identities
differ, and "no name recorded" is not it — a comparator that slipped through on
``None == None`` would be issuing a certificate of unlinkability over a premise
it never established.

This lane can proceed where that one correctly cannot, and the difference is
not a loophole — it is a different question resting on a different premise.
``compare_profiles`` must know the two records are two *named* identities
before it can call agreement a leak. This lane never claims two records are
distinct profiles; it takes the identity from **provenance** (the filename that
says which seed was launched), reports pool DEPTH rather than a per-pair
verdict, and issues no unlinkability certificate for a header to have to back.
So the profile-less records are readable here without weakening anything there.

INCONCLUSIVE IS NOT A VALUE (the PS-21 rule, on this axis)
------------------------------------------------------------
An unread, errored or absent cell is reported ``inconclusive`` and is **never
counted as distinctness**. Two failed readings are not two different profiles —
and, just as importantly, they are not one collision either. The predicate is
``diff._unread_for_unlinkability``, IMPORTED rather than restated: on this axis
a ``null`` reading counts as UNREAD, because a probe that returns ``null``
returned it because the API it reads was not there, and that is the absence of
a reading wearing a reading's clothes. Two profiles that both failed to read
would otherwise compare equal and be reported as a collision — a false leak
report from a run resting on nothing.

An identity whose vector is inconclusive is dropped from that vector's counts
and named in ``inconclusive_identities``, so the denominator always says how
many identities actually CONTRIBUTED a reading rather than how many files were
opened.

AN EVIDENCE FLOOR, BECAUSE A SET OF ONE HAS NO POOL DEPTH
------------------------------------------------------------
A single record, or an empty set, is **refused** — :class:`NotEnoughProfiles` —
rather than reported clean. This is the PS-92/PS-55 evidence-floor discipline
applied to this lane: "we failed to look" and "we looked and it was fine" must
never wear the same code, and a one-record run trivially reports every vector
at full depth, which is the tool at its most confident with the least evidence.

HONEST BOUNDS — WHAT THIS REPORT IS NOT
------------------------------------------
* **NOT a population estimate.** The measurement it was built for is n=5
  chromium / n=3 firefox seeds, one campaign, one host. That is enough to show
  a 2-value pool EXISTS. It is not enough to estimate how deep any pool is, and
  :func:`format_report` prints the denominator next to every figure so no line
  of output can be quoted as one.
* **NOT a mechanism.** It reports the EFFECT. Why ``audio.digest`` collides is
  out of scope and genuinely unsettled — the rate-quantisation account
  (``browser/audio_ext.py:4-8``; ``sum`` tracks ``rate`` exactly,
  ``124.043475``↔``44100`` and ``124.036605``↔``44099.992188``) is
  consistent-with, not proven, and an arithmetic reconstruction of it failed to
  reproduce the collision. This module asserts no cause.
* **NOT a leak, and not Invariant #0 in the host-disclosure sense.** Nothing
  about the operator's real machine escapes. This is a Level-2 INSTRUMENT gap.
* **NOT a fix.** Per the charter seam, making the defect visible is PS-1's job;
  *how* a vector should be spoofed is the masking direction's judgement. A
  triage that ends "this is a real collision in vector X" hands off there.
* **No browser is executed.** This is a pure function over committed JSON.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys

# The unread predicate is IMPORTED, not restated. It carries the ``null``-is-
# unread rule that this axis needs (see the module header), and a second copy
# would be free to drift away from the comparator's reading of the same word.
from .diff import _unread_for_unlinkability

# The Simpson index is IMPORTED for the same reason: one implementation of
# "collision probability" in the tree, so this report and the engine-variance
# gate cannot come to disagree about what the number means.
from .engine_gpu_variance import collision_probability
from .probes import must_differ_probes
from .snapshot import quote_path

# Verdict codes. Same three-way shape the rest of this package keeps, and for
# the same reason: "we failed to look" must not wear the same code as "we
# looked and it was fine". These are this lane's OWN codes — no existing exit
# code is changed, reused or reinterpreted.
EXIT_OK = 0
EXIT_FINDING = 1
EXIT_REFUSED = 2
EXIT_INCONCLUSIVE = 3

# How a record's ARM is decided. By provenance — the filename — and never by a
# header field, because the control records' headers actively lie (see the
# module header, corruption #1). A record whose provenance is unrecognised is
# EXCLUDED rather than assumed to be product: this lane's whole value is that
# the set it counts is the set it says it counts.
PRODUCT = "product"
COUNTERFACTUAL = "counterfactual"
RERUN = "rerun"
UNRECOGNISED = "unrecognised"


def _identity_order(identity: str) -> "tuple[int, float, str]":
    """Sort identities NUMERICALLY where they are numbers, else by name.

    Cosmetic, but not pointless: a plain string sort renders the corpus's own
    seeds as ``111, 1337, 222, 333, 4242``, and an operator reading a colliding
    GROUP out of that ordering has to re-sort it mentally to check it against a
    seed list. Total and deterministic either way — two runs over one set print
    one ordering.
    """
    try:
        return (0, float(identity), identity)
    except ValueError:
        return (1, 0.0, identity)


class NotEnoughProfiles(ValueError):
    """Refused: a set this small has no pool depth to report.

    Raised rather than returning a clean report, because a one-record set
    trivially scores every vector at full depth — which would be the tool at
    its most confident with the least evidence, and is the failure the
    PS-92/PS-55 evidence floor exists to prevent.
    """


@dataclasses.dataclass(frozen=True)
class Record:
    """One snapshot file, with the provenance that decides whether it counts."""

    source: str
    identity: str
    arm: str
    is_rerun: bool
    engine: "str | None"
    snapshot: dict

    @property
    def counts(self) -> bool:
        """Whether this record may enter a distinctness statistic.

        Three refusals, and all three are the module header's corruptions:
        a control-arm record (its headers say otherwise and they are stale), a
        rerun (one identity recorded twice), and a record whose engine was
        never recorded (it cannot be partitioned, and lumping it in would be
        the cross-engine comparison ``_require_controlled`` refuses).
        """
        return self.arm == PRODUCT and not self.is_rerun and bool(self.engine)

    @property
    def excluded_because(self) -> "str | None":
        if self.is_rerun:
            return "rerun of an already-counted profile (one identity, two recordings)"
        if self.arm == COUNTERFACTUAL:
            return "control arm (no-flag counterfactual), excluded by provenance"
        if self.arm == UNRECOGNISED:
            return "provenance not recognised; not assumed to be a product reading"
        if not self.engine:
            return "no engine recorded, so it cannot be partitioned by engine"
        return None


@dataclasses.dataclass(frozen=True)
class VectorDepth:
    """The pool-depth reading for ONE must-differ vector on ONE engine."""

    probe_id: str
    engine: str
    realms: "tuple[str, ...]"
    distinct: int
    identities: int
    collision_p: float
    groups: "tuple[tuple[str, ...], ...]"
    colliding_groups: "tuple[tuple[str, ...], ...]"
    inconclusive_identities: "tuple[str, ...]"
    # The READING behind each group, aligned by index with ``groups``. Carried
    # because naming WHO collides without naming WHAT they collided on leaves
    # the finding unciteable: firefox's total canvas collision is only
    # actionable as "all three share digest 4242351214", and an operator
    # checking that against the corpus needs the value in the report rather
    # than in a second tool.
    group_values: "tuple[str, ...]" = ()

    def display_value(self, group: "tuple[str, ...]") -> str:
        """The shared reading, rendered for an operator to CITE.

        The stored value is the folded list across the vector's declared realms
        (see :func:`_fold_realms`), which for a two-realm vector prints the same
        digest twice — the honest bytes, and unreadably wide. When every realm
        agrees this collapses to the single reading and says so, because the
        finding an operator quotes is "all three share digest 4242351214", not
        a list restating one value per realm. Where the realms genuinely
        DISAGREE the full list is printed, since that difference is itself
        information and must not be hidden by a convenience.
        """
        raw = self.value_for(group)
        try:
            parts = json.loads(raw)
        except (TypeError, ValueError):
            return raw
        if isinstance(parts, list) and parts and all(p == parts[0] for p in parts):
            return json.dumps(parts[0], sort_keys=True, ensure_ascii=False)
        return raw

    def value_for(self, group: "tuple[str, ...]") -> str:
        """The reading the identities in ``group`` share, as stored (folded)."""
        for ids, value in zip(self.groups, self.group_values):
            if ids == tuple(group):
                return value
        raise KeyError(f"{group!r} is not a group on {self.probe_id!r}")

    @property
    def collides(self) -> bool:
        """True when two counted identities share a value on this vector."""
        return bool(self.colliding_groups)

    @property
    def measured(self) -> bool:
        """True when at least two identities contributed an obtained reading.

        Below that there is nothing to collide, so the vector has no verdict —
        it is not a pass.
        """
        return self.identities >= 2

    def to_dict(self) -> dict:
        return {
            "probe_id": self.probe_id,
            "engine": self.engine,
            "realms": list(self.realms),
            "distinct": self.distinct,
            "identities": self.identities,
            "collision_p": self.collision_p,
            "groups": [list(g) for g in self.groups],
            "group_values": list(self.group_values),
            "colliding_groups": [list(g) for g in self.colliding_groups],
            "inconclusive_identities": list(self.inconclusive_identities),
            "collides": self.collides,
            "measured": self.measured,
        }


@dataclasses.dataclass(frozen=True)
class EngineReport:
    """Every must-differ vector's pool depth for ONE engine.

    Engines are reported SEPARATELY and never blended. Blending them would hide
    both of the findings this was built to surface: chromium's 2-of-5
    ``audio.digest`` pool and firefox's total ``canvas.readback`` collision
    would each be diluted by the other engine's healthy readings on the same
    vector. It is also the premise ``_require_controlled`` protects, one axis
    over — a vector differing across engines is not evidence about the seeds.
    """

    engine: str
    identities: "tuple[str, ...]"
    vectors: "tuple[VectorDepth, ...]"

    def vector(self, probe_id: str) -> VectorDepth:
        for v in self.vectors:
            if v.probe_id == probe_id:
                return v
        raise KeyError(f"{probe_id!r} is not a must-differ vector on {self.engine!r}")

    def to_dict(self) -> dict:
        return {
            "engine": self.engine,
            "identities": list(self.identities),
            "identity_count": len(self.identities),
            "vectors": [v.to_dict() for v in self.vectors],
        }


@dataclasses.dataclass(frozen=True)
class PoolDepthReport:
    """The whole set-wide reading: one :class:`EngineReport` per engine."""

    engines: "tuple[EngineReport, ...]"
    excluded: "tuple[tuple[str, str], ...]"

    def engine_report(self, engine: str) -> EngineReport:
        """The arm whose engine header CONTAINS ``engine``, case-insensitively.

        A substring match because the header is the engine's own self-
        description and is not a tidy token: chromium records read
        ``'fingerprint-chromium (persona engine binary)'``. Partitioning is
        done on the RAW header (see :func:`build_report`) so two engines can
        never be merged by a normalisation this module invented; this lookup is
        an operator convenience over that partition, and it refuses an
        ambiguous match rather than picking one.
        """
        hits = [e for e in self.engines if engine.lower() in e.engine.lower()]
        if not hits:
            known = [e.engine for e in self.engines]
            raise KeyError(f"no engine arm matching {engine!r}; have {known}")
        if len(hits) > 1:
            raise KeyError(
                f"{engine!r} matches more than one engine arm "
                f"({[e.engine for e in hits]}); name it more precisely"
            )
        return hits[0]

    @property
    def findings(self) -> "tuple[VectorDepth, ...]":
        return tuple(v for e in self.engines for v in e.vectors if v.collides)

    def to_dict(self) -> dict:
        return {
            "engines": [e.to_dict() for e in self.engines],
            "excluded": [
                {"source": s, "reason": r} for s, r in self.excluded
            ],
        }


def classify_source(path: str) -> "tuple[str, bool, str]":
    """Decide ``(arm, is_rerun, identity)`` from a record's PROVENANCE.

    ⚠️ FROM THE FILENAME, NEVER FROM A HEADER FIELD. Both no-flag control
    records in the committed corpus carry a stale ``flag`` header naming a
    ``--fingerprint=<seed>`` they were NOT launched with, plus a matching
    ``seed`` field. A reader that grouped on either would pull the control into
    the treatment group — the exact error that refuted PS-89 — and would do it
    silently, because the corrupted grouping still produces a plausible report.

    The naming convention it reads is the corpus's own::

        reading.<engine>.seed<N>.json            -> product, identity "<N>"
        reading.<engine>.seed<N>.rerun.json      -> product but a RERUN
        counterfactual.<engine>.<what>.seed*.json-> control arm

    Anything else is :data:`UNRECOGNISED` and is EXCLUDED rather than assumed
    to be a product reading. That default is the conservative direction: an
    unrecognised file wrongly counted corrupts the statistic invisibly, while
    one wrongly excluded shows up in ``excluded`` where an operator sees it.
    """
    name = os.path.basename(path)
    stem = name[: -len(".json")] if name.endswith(".json") else name
    parts = stem.split(".")
    is_rerun = "rerun" in parts

    seed_tokens = [p for p in parts if p.startswith("seed")]
    identity = seed_tokens[-1][len("seed") :] if seed_tokens else stem
    if is_rerun:
        # Name the rerun for the identity it re-records, so an operator reading
        # `excluded` can see WHICH profile it duplicates.
        identity = f"{identity} (rerun)"

    if parts and parts[0] == "reading":
        arm = PRODUCT
    elif parts and parts[0] == "counterfactual":
        arm = COUNTERFACTUAL
    else:
        arm = UNRECOGNISED
    return arm, is_rerun, identity


def load_record(path: str) -> Record:
    """Read one snapshot file into a :class:`Record`, with its provenance."""
    with open(path, encoding="utf-8") as fh:
        snapshot = json.load(fh)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("probes"), dict):
        raise NotEnoughProfiles(
            f"{quote_path(path)} is not a snapshot: it carries no 'probes' "
            "object, so NO READING was obtained from it. An unreadable file is "
            "not a profile, and counting it would put a hole in the "
            "denominator."
        )
    arm, is_rerun, identity = classify_source(path)
    engine = snapshot.get("engine")
    return Record(
        source=path,
        identity=identity,
        arm=arm,
        is_rerun=is_rerun,
        engine=engine if isinstance(engine, str) and engine else None,
        snapshot=snapshot,
    )


def _cell(snapshot: dict, realm: str, probe_id: str):
    """One probe's reading in one realm, or ``None`` when it is not there.

    ``None`` for both "the realm is absent" and "the probe is absent within
    it", because :func:`_unread_for_unlinkability` reads ``None`` as unread and
    downstream nothing cares which of the two happened — neither is a reading
    that was obtained.
    """
    realms = snapshot.get("probes")
    if not isinstance(realms, dict):
        return None
    cells = realms.get(realm)
    if not isinstance(cells, dict):
        return None
    return cells.get(probe_id)


def _fold_realms(snapshot: dict, probe) -> "str | None":
    """ONE canonical value per identity for ``probe``, or ``None`` = inconclusive.

    ⚠️ THIS IS THE FUNCTION THAT KEEPS THE DENOMINATOR HONEST. A vector's
    realms are not independent draws: ``canvas.readback`` declares
    ``('window', 'worker')`` and carries the SAME digest in both on every
    record in the corpus. Appending each cell separately would count one
    profile's two realms as two profiles — 6 values for 3 identities on the
    firefox arm — and on a healthier vector would dilute a real duplicate into
    a false pass. ``collision_probability`` assumes each element is an
    independently drawn profile, so it must be handed exactly one element per
    identity.

    The realms walked are the INVENTORY's (``probe.realms``), not whichever
    realms a given file happens to carry. That is the rule
    ``must_differ_probes`` states in its own docstring, and it is what makes a
    vector MISSING from a snapshot report as inconclusive rather than vanish:
    intersecting the two files' realms would silently narrow the question to
    whatever both happened to record.

    A vector is inconclusive when ANY declared realm is unread. Conservative on
    purpose and in the safe direction — a partially-read vector is not evidence
    of an identity, and admitting the readable half would let one realm's value
    stand in for a profile whose other realm nobody obtained.
    """
    values = []
    for realm in probe.realms:
        side = _cell(snapshot, realm, probe.id)
        if _unread_for_unlinkability(side):
            return None
        values.append(side.get("value") if isinstance(side, dict) else side)
    # Canonical bytes so two equal readings compare equal whatever key order
    # the file used, and so a dict-valued reading is hashable for counting.
    return json.dumps(values, sort_keys=True, ensure_ascii=False, default=repr)


def _depth_for(engine: str, probe, counted: "list[Record]") -> VectorDepth:
    by_value: "dict[str, list[str]]" = {}
    inconclusive: "list[str]" = []
    for record in counted:
        folded = _fold_realms(record.snapshot, probe)
        if folded is None:
            inconclusive.append(record.identity)
            continue
        by_value.setdefault(folded, []).append(record.identity)

    # Built as (identities, value) PAIRS and sorted as pairs, so ``groups`` and
    # ``group_values`` cannot fall out of alignment with each other — two
    # independently sorted tuples would silently mislabel which group holds
    # which reading.
    paired = sorted(
        (
            (tuple(sorted(ids, key=_identity_order)), value)
            for value, ids in by_value.items()
        ),
        key=lambda pair: _identity_order(pair[0][0]),
    )
    groups = tuple(ids for ids, _ in paired)
    group_values = tuple(value for _, value in paired)
    # Only identities that CONTRIBUTED a reading are in the denominator, and
    # only their values are handed to the Simpson index. An inconclusive cell
    # is never distinctness (two failed readings are not two profiles) and is
    # never a collision either (nor are they one profile).
    values = [v for v, ids in by_value.items() for _ in ids]
    return VectorDepth(
        probe_id=probe.id,
        engine=engine,
        realms=tuple(probe.realms),
        distinct=len(by_value),
        identities=len(values),
        collision_p=collision_probability(values) if values else 1.0,
        groups=groups,
        colliding_groups=tuple(g for g in groups if len(g) > 1),
        group_values=group_values,
        inconclusive_identities=tuple(sorted(inconclusive, key=_identity_order)),
    )


def build_report(records: "list[Record]") -> PoolDepthReport:
    """Partition by engine and report every must-differ vector's pool depth.

    Partitioning is on the RAW ``engine`` header — the record's own account of
    itself — with no normalisation, so two engines cannot be merged by an
    equivalence this module invented. That is ``_require_controlled``'s premise
    held on the set axis: a vector may differ because the ENGINE differs rather
    than because the seeds do, so a blended count is not evidence about seeds.

    The vectors walked come from :func:`probes.must_differ_probes`, so
    classifying a probe stays a matter of editing its inventory record and this
    lane cannot drift out of step with the inventory it polices.
    """
    excluded = tuple(
        (r.source, r.excluded_because or "excluded")
        for r in records
        if not r.counts
    )
    counted = [r for r in records if r.counts]

    if len(counted) < 2:
        raise NotEnoughProfiles(
            f"refused: pool depth needs at least 2 counted profiles, got "
            f"{len(counted)}. A set this small has no pool depth — one record "
            "trivially scores every vector at full depth, which is not "
            "evidence that the pool is deep. "
            + (
                "Excluded by provenance: "
                + "; ".join(f"{quote_path(s)}: {why}" for s, why in excluded)
                if excluded
                else "No records were excluded, so the set really is this small."
            )
        )

    by_engine: "dict[str, list[Record]]" = {}
    for record in counted:
        by_engine.setdefault(record.engine or "", []).append(record)

    engines = []
    for engine in sorted(by_engine):
        arm = by_engine[engine]
        engines.append(
            EngineReport(
                engine=engine,
                identities=tuple(sorted((r.identity for r in arm), key=_identity_order)),
                vectors=tuple(
                    _depth_for(engine, probe, arm) for probe in must_differ_probes()
                ),
            )
        )
    return PoolDepthReport(engines=tuple(engines), excluded=excluded)


def report_for_paths(paths: "list[str]") -> PoolDepthReport:
    """Load every path and report. The whole lane, for a caller with files."""
    if not paths:
        raise NotEnoughProfiles(
            "refused: no snapshot files were given, and a report over zero "
            "records is not a clean result — nothing was read, so nothing was "
            "established."
        )
    return build_report([load_record(p) for p in paths])


def report_for_directory(directory: str) -> PoolDepthReport:
    """Report over every ``*.json`` snapshot in ``directory``."""
    try:
        names = sorted(
            n for n in os.listdir(directory) if n.endswith(".json")
        )
    except OSError as exc:
        raise NotEnoughProfiles(
            f"refused: cannot read {quote_path(directory)}: {exc}"
        ) from exc
    return report_for_paths([os.path.join(directory, n) for n in names])


def format_report(report: PoolDepthReport) -> str:
    """Operator-facing text.

    Every depth is printed as ``distinct / identities`` rather than as a bare
    count, because the denominator is part of the finding: "2 distinct" is not
    a fact anybody can act on, and at these sample sizes a figure quoted
    without its n would read as a population estimate, which this measurement
    explicitly is not.
    """
    lines: "list[str]" = []
    for arm in report.engines:
        lines.append(f"engine: {arm.engine}")
        lines.append(
            f"  identities ({len(arm.identities)}): {', '.join(arm.identities)}"
        )
        for v in arm.vectors:
            verdict = (
                "COLLIDING"
                if v.collides
                else ("no verdict" if not v.measured else "distinct")
            )
            lines.append(
                f"  {v.probe_id:<18} {v.distinct} distinct / {v.identities} "
                f"identities   collision_p={v.collision_p:.4f}   {verdict}"
            )
            for group in v.colliding_groups:
                shared = v.display_value(group)
                lines.append(
                    f"      collide: {{{', '.join(group)}}} on {shared}"
                )
            if v.inconclusive_identities:
                lines.append(
                    "      inconclusive (not counted as distinctness): "
                    + ", ".join(v.inconclusive_identities)
                )
        lines.append("")
    if report.excluded:
        lines.append("excluded by provenance (never by a header field):")
        for source, why in report.excluded:
            lines.append(f"  {os.path.basename(source)}: {why}")
        lines.append("")
    lines.append(
        "n is small and one campaign on one host: this shows a pool's depth "
        "on THESE records, and is not a population estimate."
    )
    return "\n".join(lines)


def exit_code_for(report: PoolDepthReport) -> int:
    """0 clean, 1 a vector collides, 3 nothing was measured.

    2 (refused) is not produced here — a refusal raises
    :class:`NotEnoughProfiles` before a report exists, which is the point: a
    set that cannot be reported on must not be able to return one.
    """
    if report.findings:
        return EXIT_FINDING
    if not any(v.measured for e in report.engines for v in e.vectors):
        return EXIT_INCONCLUSIVE
    return EXIT_OK


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.services.verify.pool_depth",
        description=(
            "Set-wide pool depth for the must-differ vectors: how many "
            "DISTINCT values each takes across a set of profiles, per engine."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="snapshot files, or a directory of them",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the report as JSON"
    )
    args = parser.parse_args(argv)

    try:
        if len(args.paths) == 1 and os.path.isdir(args.paths[0]):
            report = report_for_directory(args.paths[0])
        else:
            report = report_for_paths(args.paths)
    except (NotEnoughProfiles, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(format_report(report))
    return exit_code_for(report)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "COUNTERFACTUAL",
    "EXIT_FINDING",
    "EXIT_INCONCLUSIVE",
    "EXIT_OK",
    "EXIT_REFUSED",
    "EngineReport",
    "NotEnoughProfiles",
    "PRODUCT",
    "PoolDepthReport",
    "RERUN",
    "Record",
    "UNRECOGNISED",
    "VectorDepth",
    "build_report",
    "classify_source",
    "exit_code_for",
    "format_report",
    "load_record",
    "report_for_directory",
    "report_for_paths",
]
