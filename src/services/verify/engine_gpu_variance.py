"""Does the ENGINE still give different profiles different GPUs?

This is the guard that makes PS-161's "defer to the engine" arms SAFE to hold.

WHY IT EXISTS
-------------
PS-161 settled who authors the WebGL identity pair per arm. On an arm listed in
``browser.gpu_ext.ENGINE_AUTHORED_IDENTITY_ARMS`` persona deliberately stops
spoofing it and lets fingerprint-chromium's own seed-derived value reach the
page — one author per vector, so the two-spoofer contradiction PS-155/PS-161
chased cannot recur there by construction.

That trade makes one of OUR invariants depend on a THIRD PARTY'S implementation
detail. The engine autobumps on a schedule. If a future build stops varying its
GPU by seed — or narrows its pool — every persona profile on that arm silently
shares a graphics card, which is a cross-profile identifier and a direct breach
of Level 2 of the project bar (mutual unlinkability). Nothing else in the
subsystem would notice: the value stays perfectly plausible, so no per-row
"is this a tell?" judgement fires, and `matrix_consistency` cannot see it either
because that module asks whether ONE record agrees with ITSELF. A shared card is
not a self-contradiction — every record is individually consistent, and the
whole population is linked. **The defect is a property of a SET of profiles, and
nothing in the subsystem held a set.** Hence a new lane rather than a reuse:
this is the same shape of gap `matrix_consistency`'s own header describes, one
axis over.

WHAT IT MEASURES, AND THE BAR IT HOLDS THE ENGINE TO
-----------------------------------------------------
For each engine-authored arm: launch N profiles that differ ONLY by seed, with
the masking layer OFF, and read UNMASKED_RENDERER_WEBGL. Then ask not merely
"did it vary?" but **"did it vary at least as well as the pool we gave up?"**

Counting distinct values is not enough, and macOS is the measured proof: across
30 seeds the engine returned two values, which "varies" would score as a pass,
but they were skewed 87/13 — a 76.9% chance that two profiles collide, WORSE
than the 50% of the two-entry pool persona removed. So the metric is the
**pairwise collision probability** (the chance two randomly chosen profiles are
handed the same card, i.e. the Simpson index), which is sensitive to that skew,
and the bar is the collision probability of the arm's own fallback pool in
``gpu_ext``. Deferring must not COST unlinkability relative to authoring it
ourselves; if it does, the arm belongs back under our own layer.

This is deliberately a comparison against something already in the tree rather
than a hand-chosen constant: the number it must beat moves automatically if the
fallback pool is ever edited, so the two cannot drift apart.

WHY THE SAMPLE SIZE IS PART OF THE VERDICT
--------------------------------------------
An estimate from a handful of seeds can clear a bar by luck. A run with too few
seeds is reported ``INCONCLUSIVE`` and is NOT a pass — the same discipline the
rest of this package keeps, where "we failed to look" and "we looked and it was
fine" must never wear the same code. That is what stops this gate from being
quietly satisfied by a cheap two-seed run.

WHERE IT RUNS — STATED PLAINLY, INCLUDING WHERE IT DOES NOT
-------------------------------------------------------------
Since PS-176 this reading is WIRED TO A PATH THAT CAN GO RED:
``.github/workflows/engine-gpu-variance.yml``, daily at 06:40 UTC. That job
provisions the engine itself (xvfb + the tree's own driver pins + a
``download_engine`` of whatever upstream is serving) and fails on a narrowed
arm. Before PS-176 the judgement was gated and the reading was wired to
nothing; the header below used to say so, and no longer needs to.

⚠️ THE JOB IS DELIBERATELY UNPINNED, and that is the whole design. The
tempting shape is to pin a known engine build so the job is reproducible — but
THE RISK IS UPSTREAM'S ``/releases/latest``, which is exactly what a pin hides.
A gate on a pinned build stays green forever while the build users actually
receive goes bad. So the job measures the same bytes ``updater.fetch_latest()``
hands the operator's app. The cost is accepted knowingly: this job can go red
because upstream changed something, which IS the signal.

It is NOT wired into ``engine-autoupdate.yml``, and that is not an oversight.
Verified by re-running the greps: that job bumps the FIREFOX engine and only
Firefox (``engine-baseline.txt`` is ``firefox-20``, both provisioning steps
import ``services.engine.firefox``), and fingerprint-chromium is touched by no
workflow at all. A chromium variance check hosted there would be a gate that
can never fire on the event it exists to catch.

The two halves, and both are real:

* :func:`classify` is a PURE function over readings. It carries the whole
  verdict — the bar, the skew sensitivity, the sample-size floor — and it is
  exercised in CI on every run, including the cases where it must go RED. A
  regression in the judgement is caught by the normal test suite.
* :func:`measure` is the live half. It needs the product's own engine, which
  the normal CI jobs do not provision (``browser_firefox`` only, see
  ``ci.yml``). It runs in the scheduled job above, and on any operator machine
  via ``python -m src.services.verify.engine_gpu_variance check``.

Because a live ``check`` can only ever demonstrate the outcome the engine
happens to produce today — a pass — the scheduled job runs
``... engine_gpu_variance selftest`` FIRST. That drives synthesised
low-variance readings through the same ``classify`` → ``exit_code_for`` path
and asserts each lands on the exit code it must, so the gate's ability to FAIL
is demonstrated on every run rather than assumed. A check only ever observed
passing is not coverage.

⚠️ WHAT IS STILL NOT COVERED, named rather than left to be discovered:
DETECTION IS DAILY, INSTALLATION IS HOURLY. ``app.py:_check_engines_periodic``
polls every hour, unattended, and installs whatever upstream published, with
``policy.KNOWN_BAD_VERSIONS`` empty and no ceiling — so a bad build can reach
machines up to ~24h before this gate reads it. That window is NOT closable by
measuring at install time: you cannot seed-vary a build before installing it,
and a 15-launch, minutes-long measurement inside an unattended install would
wedge the app. The remedy for a red run is therefore to name the tag in
``policy.KNOWN_BAD_VERSIONS`` — every chromium install passes through
``policy.check()``, so that refusal reaches operators by name without waiting
for a persona release. The record this module writes carries ``engine_build``
for exactly that reason: a finding you cannot attribute to a tag cannot be
acted on. ``replay`` therefore PRESERVES that field rather than re-deriving
it — the machine re-reading an artifact is not the machine that measured, and
a re-stamped tag would point the blocklist at the wrong build.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import time

# The arms whose identity persona has handed to the engine. Imported rather than
# restated so this gate can never police a different set than the product ships:
# add an arm there and it is measured here automatically.
from ..browser.gpu_ext import ENGINE_AUTHORED_IDENTITY_ARMS

EXIT_PASS = 0
EXIT_FINDING = 1
EXIT_CANNOT_RUN = 2

# Tolerance for the bar comparison. Both sides are sums/quotients of floats, so
# an arm sitting EXACTLY at its bar lands a few ulp off it: five evenly-used
# identities over ten seeds gives 5*(0.2)^2 = 0.20000000000000004, which is
# strictly greater than the 0.2 bar and would flip a healthy arm to TOO_NARROW
# on a rounding artefact. Matching the pool we gave up costs nothing and must
# not be a finding, so the comparison is made with a relative tolerance rather
# than on raw `>`. Small enough that it cannot absorb a real narrowing: the
# smallest genuine step at these pool sizes is on the order of a percent.
BAR_TOLERANCE = 1e-9

# Below this many readable seeds an arm is INCONCLUSIVE rather than passed. A
# collision probability estimated from a couple of samples is not evidence, and
# a cheap run must not be able to certify the property.
MIN_SEEDS = 8

# Seeds used when the caller names none. Arbitrary but FIXED, so two runs are
# comparable, and spread rather than sequential.
DEFAULT_SEEDS = (
    9001, 4242, 1337, 7, 101, 555, 2024, 31337,
    86420, 12345, 99, 777, 31415, 271828, 161803,
)

SETTLE_SECONDS = 2.0


class VarianceCannotRun(RuntimeError):
    """The reading could not be taken, so nothing was established."""


def collision_probability(values: "list[str]") -> float:
    """P(two independently chosen profiles are handed the same identity).

    The Simpson index — sum of squared frequencies. Chosen over a bare distinct
    count because it is sensitive to SKEW, which is the failure the macOS
    measurement actually exhibited: two values split 87/13 collide 77% of the
    time, while two values split 50/50 collide 50% of the time. A distinct
    count scores those identically and would have called the first one a pass.

    1.0 means every profile shares one identity; lower is better.
    """
    if not values:
        return 1.0
    n = len(values)
    counts: "dict[str, int]" = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return sum((c / n) ** 2 for c in counts.values())


# The arms this module knows persona ships a fallback pool for, and the name
# each one's pool is registered under in ``gpu_ext.GPU_POOLS``. Separated from
# the lookup itself so "this arm has no pool by design" and "this arm HAS a pool
# and we failed to read it" can be told apart — they are different facts and
# must not share a return value.
#
# THESE WERE JS VARIABLE NAMES READ BY REGEX UNTIL PS-190. The pools are now
# tagged Python records (``gpu_ext.GpuEntry``) that the JS is rendered from, so
# the name is a dict key rather than a token to scrape and the count is a
# ``len()`` rather than a substring tally. The failure mode that motivated the
# careful 0-means-two-things contract below — a regex drifting out of step with
# the literals' formatting — can no longer occur, but the contract is KEPT: a
# name missing from the registry still has to read as "we failed to look".
_POOL_VAR_FOR_ARM = {
    "windows": "WIN_GPUS",
    "macos": "MAC_GPUS",
    "linux": "LINUX_GPUS",
    "android": "ANDROID_GPUS",
}


def has_known_pool(arm: str) -> bool:
    """Whether persona is KNOWN to ship a fallback pool for this arm.

    Answered from the arm name alone, never from the size lookup — so a lookup
    that returns nothing on an arm that IS in this map reads as a failure to
    read the pool rather than as an arm with no pool.
    """
    return arm in _POOL_VAR_FOR_ARM


def _pool_entry_generations(arm: str) -> "list[int] | None":
    """The ``since`` generation of EVERY entry in the arm's pool, in order.

    An entry with no explicit ``since`` is generation 0 — that is the
    dataclass default on :class:`gpu_ext.GpuEntry`, the same default the
    emitted JS's own ``visible()`` applies, and the same one
    ``hardware_generation`` documents.

    READ FROM THE TAGGED PYTHON RECORDS, NOT FROM THE EMITTED JS. This used to
    scrape ``_CONTENT_SCRIPT`` with a pair of regexes and cross-check them
    against each other, because the pools existed only as hand-maintained JS
    literals. PS-190 lifted them into ``gpu_ext.GPU_POOLS`` as real objects, so
    the generations are now read off ``entry.since`` directly. The whole class
    of failure that cross-check existed to catch — two regexes drifting apart
    as the literals' formatting changed, and reporting a pool size no pool
    has — cannot occur any more, so the cross-check is gone rather than kept
    as ceremony.

    None still means WE FAILED TO LOOK, which is deliberately NOT the same as
    an empty pool: the arm has no registry entry, or the name it registers is
    absent from ``GPU_POOLS``. A None propagates to a 0 from
    :func:`fallback_pool_size` and thence to an ``INCONCLUSIVE`` verdict, which
    is this module's standing "we failed to look" answer.
    """
    from .. import browser  # noqa: F401  (kept for a stable import root)
    from ..browser import gpu_ext

    name = _POOL_VAR_FOR_ARM.get(arm)
    if not name:
        return None
    pool = gpu_ext.GPU_POOLS.get(name)
    if pool is None:
        return None
    return [int(getattr(e, "since", 0) or 0) for e in pool]


def pool_sizes_by_generation(arm: str) -> "dict[int, int]":
    """How big the arm's VISIBLE pool is, per profile generation.

    Maps generation -> number of entries a profile of that generation can be
    picked onto. Keys are every generation at which the answer CHANGES (0
    plus each distinct ``since``), so a pool nobody has tagged returns a single
    ``{0: n}`` and a split pool shows the split.

    This is the reading :func:`fallback_pool_size` collapses to one number, and
    it is the one to quote when the question is about profiles that ALREADY
    EXIST rather than about profiles minted today. Empty dict when the scrape
    could not be trusted — see :func:`_pool_entry_generations`.
    """
    from types import SimpleNamespace

    from ...models.hardware_generation import visible_entries

    sinces = _pool_entry_generations(arm)
    if sinces is None:
        return {}
    entries = [SimpleNamespace(since=s) for s in sinces]
    # visible_entries owns the filter rule; re-implementing it here is exactly
    # the duplication this module refuses everywhere else.
    return {g: len(visible_entries(entries, g)) for g in sorted(set([0] + sinces))}


def fallback_pool_size(arm: str, generation: "int | None" = None) -> int:
    """How many entries OUR OWN pool for this arm holds AT ``generation``.

    Read from ``gpu_ext.GPU_POOLS`` — the tagged Python records the emitted JS
    is rendered from — rather than duplicated here, so the bar tracks the pool
    automatically. This used to scrape the JS template with a regex; PS-190
    lifted the pools into Python data, so the count is now a ``len()`` of the
    real list and cannot drift with the literals' formatting.

    NOT COUNTED UNFILTERED — AND THAT IS A DELIBERATE REVERSAL, RECORDED HERE
    BECAUSE IT OVERTURNS WHAT THIS DOCSTRING USED TO PROMISE. PS-190 stated
    the count was taken "UNFILTERED, ACROSS EVERY GENERATION, DELIBERATELY",
    reasoning that filtering "would make the bar depend on which profile
    happened to be measured". That reasoning is preserved and still binds — the
    bar must NOT wobble from one measured profile to the next — but an
    unfiltered ``len()`` is the wrong instrument for it, and while every entry
    was ``since=0`` the two were indistinguishable so nothing exposed the
    difference. PS-183 tagged nine of eleven ``MAC_GPUS`` entries and separated
    them: an unfiltered count claims a variety NO profile is ever offered,
    because ``pick()`` divides by the length of the profile's OWN generation's
    visible pool and never by the whole array's. So the count is filtered, and
    stability is bought the honest way instead — by a generation ARGUMENT that
    defaults to a fixed constant rather than to the profile under measurement.
    Same guarantee, sound arithmetic.

    ⚠️ THE POOL IS NOT ONE NUMBER — IT IS ONE NUMBER PER GENERATION. Entries
    carry a ``since`` tag and a profile only sees entries at or below its own
    frozen generation (``models/hardware_generation.py``), so "the size of
    MAC_GPUS" is an ill-formed question. ``generation`` defaults to
    ``CURRENT_HARDWARE_GENERATION`` — the pool a profile created TODAY draws
    from — which is the right default for this gate's own question (see
    :func:`bar_for`) and is the WIDEST answer, hence the most flattering.

    It is therefore NOT the figure the installed base sees, and on macOS the
    gap is the whole finding rather than a rounding detail: ``MAC_GPUS`` holds
    11 entries of which 9 are ``since=1`` (PS-183), so this returns 11 at the
    default while every macOS profile that existed before that widening sees
    **2**, and collides at 50.0% rather than 9.1%. Call
    :func:`pool_sizes_by_generation` when you need that split, and never quote
    this function's default as "how often two macOS profiles collide".

    Returns 0 in TWO different situations, which callers must NOT conflate:
    the arm has no pool at all (:func:`has_known_pool` is False), or the arm
    has one and it could not be read. Pair every call with
    :func:`has_known_pool`: 0 on a known-pool arm means WE FAILED TO LOOK, and
    a missing bar must never be read as a bar that was met.
    """
    from ...models.hardware_generation import CURRENT_HARDWARE_GENERATION

    sizes = pool_sizes_by_generation(arm)
    if not sizes:
        return 0
    gen = CURRENT_HARDWARE_GENERATION if generation is None else int(generation)
    # The visible pool at `gen` is the one for the highest tagged generation
    # at or below it.
    eligible = [g for g in sizes if g <= gen]
    if not eligible:
        return 0
    return sizes[max(eligible)]


def bar_for(arm: str, generation: "int | None" = None) -> "float | None":
    """The collision probability the engine must BEAT on this arm.

    It is the collision probability of persona's own pool for the arm, assumed
    uniform (which is what ``pick()``'s modulo over a hash produces, and what
    the measured distributions confirm to within a fraction of a percent). None
    when there is no pool to compare against.

    ⚠️ WHICH GENERATION'S POOL, AND WHY — stated because it is a judgement, not
    an obvious default. This gate asks "would deferring to the engine COST us
    unlinkability?", and that is a decision about profiles minted from now on:
    profiles that already exist keep the identity they were issued, whichever
    way the arm is decided. So the pool we would be GIVING UP is the one a new
    profile draws from, and the bar defaults to ``CURRENT_HARDWARE_GENERATION``.
    That is also the STRICTER reading on any widened arm — a wider pool is a
    higher bar for the engine to clear — so the default cannot let a deferral
    through on a technicality.

    It does mean this number is NOT a claim about the installed base. On macOS
    the bar is 9.1% (11 entries at generation 1) while every pre-PS-183 macOS
    profile sits in a 2-entry pool at 50.0%. Both are true; they answer
    different questions. :func:`classify` reports the split alongside the bar
    for exactly that reason.
    """
    # Called with ONE argument on the default path, deliberately. Tests
    # simulate a drifted scrape by substituting a single-parameter stub for
    # fallback_pool_size, and a gate whose failure path cannot be exercised is
    # worse than a slightly awkward call site here.
    n = fallback_pool_size(arm) if generation is None else fallback_pool_size(
        arm, generation
    )
    if n <= 0:
        return None
    return 1.0 / n


def completeness(per_arm: "dict[str, dict]") -> dict:
    """How much of the requested sample was actually READ. DERIVED, never told.

    ``classify`` correctly EXCLUDES unreadable cells from its statistics — an
    unreadable cell and a colliding cell are different findings. That exclusion
    is right and is not weakened here. The defect PS-192 closes is on the other
    side of it: **the caller cannot tell "nothing to exclude" from "half the
    run was excluded"**, because both produce the same clean-looking verdict.

    That gap is not theoretical. A process leak exhausts the machine, later
    launches degrade into a contentless ``TargetClosedError``, those cells come
    back ``None``, ``classify`` drops them — and the verdict is computed from a
    POSITION-BIASED subset (the seeds that ran early) while reporting exactly
    like a full run. Nothing in the output says the sample was truncated.

    ⚠️ EVERY FIELD HERE IS COMPUTED FROM ``per_arm``. That is the whole design
    constraint, and it is a lesson paid for: ``readings/ps177-2026-08-25/
    derive.py:372 coverage_section()`` HARDCODED the sentence "all four GPU
    arms returned 24/24 readable seeds" and took no records at all. A reviewer
    mutation-proved it by nulling 12 of 24 android readings — it still printed
    24/24, beside a per-arm table correctly reporting 12. The claim was true
    and STRUCTURALLY UNABLE TO BECOME FALSE, which is precisely the property
    that must not exist in a completeness check. Never write a summary sentence
    here that does not read the numbers.
    """
    requested = sum(e["seeds_requested"] for e in per_arm.values())
    readable = sum(e["seeds_readable"] for e in per_arm.values())
    truncated = sorted(
        a for a, e in per_arm.items() if e["seeds_readable"] < e["seeds_requested"]
    )
    complete = bool(per_arm) and not truncated

    if not per_arm:
        detail = "no arm was measured, so there is no sample to be complete."
    elif complete:
        detail = (
            f"every requested seed was read: {readable}/{requested} across "
            f"{len(per_arm)} arm(s). Nothing was excluded, so the verdict "
            "above was computed over the FULL sample."
        )
    else:
        per = ", ".join(
            f"{a} {per_arm[a]['seeds_readable']}/{per_arm[a]['seeds_requested']}"
            for a in truncated
        )
        detail = (
            f"TRUNCATED: only {readable} of {requested} requested seeds were "
            f"readable ({per}). The unreadable cells were EXCLUDED from the "
            "statistics — correctly, but that means the verdict above was "
            "computed over a SUBSET, and the seeds that fail are the ones that "
            "ran LAST, so the surviving sample is position-biased rather than "
            "random. A resource leak exhausting the machine produces exactly "
            "this shape (PS-192): launches degrade into a contentless "
            "TargetClosedError instead of failing loudly. Treat this as a run "
            "to REPEAT, not as a reading to act on."
        )

    return {
        "seeds_requested": requested,
        "seeds_readable": readable,
        "seeds_unreadable": requested - readable,
        "complete": complete,
        "arms_truncated": truncated,
        "detail": detail,
    }


def classify(readings: "dict[str, dict[int, str | None]]") -> dict:
    """Turn per-arm, per-seed identity readings into a verdict. PURE.

    ``readings`` maps arm -> {seed: identity string or None}. A None is a seed
    that could not be read, and is EXCLUDED from the statistics rather than
    counted as a value — an unreadable cell and a colliding cell are different
    findings, and merging them would let a broken run read as a narrow pool.

    Every arm gets one of:

    ``OK``            varied at least as well as the pool we gave up.
    ``TOO_NARROW``    the finding. The engine's identities collide MORE often
                      than persona's own pool would have, so deferring is
                      costing unlinkability and the arm should return to
                      ``gpu_ext``'s authorship.
    ``CONSTANT``      every profile got the SAME identity — the severe form of
                      TOO_NARROW, called out separately because it is a flat
                      Level 2 breach rather than a degradation.
    ``INCONCLUSIVE``  we could not say. Too few readable seeds, OR the arm's
                      own bar could not be read (see ``bar_missing`` below).
                      NOT a pass.

    ⚠️ A MISSING BAR IS NOT A MET BAR. When an arm persona ships a pool for
    (:func:`has_known_pool`) yields no bar, the comparison this gate exists to
    make cannot be made at all, and the arm is ``INCONCLUSIVE`` rather than
    falling through to ``OK`` on the weaker "did it vary at all?" question.
    That weaker question is demonstrably insufficient here: macos varies (2
    distinct values) while colliding 76.9% of the time, so it passes "varied"
    and fails the bar. The same "we failed to look ≠ we looked and it was fine"
    discipline this module applies to ``MIN_SEEDS``, applied to the other input.
    """
    per_arm: "dict[str, dict]" = {}
    for arm in sorted(readings):
        by_seed = readings[arm]
        readable = [v for v in by_seed.values() if v]
        distinct = sorted(set(readable))
        bar = bar_for(arm)
        bar_missing = bar is None and has_known_pool(arm)
        entry: dict = {
            "seeds_requested": len(by_seed),
            "seeds_readable": len(readable),
            "distinct_identities": len(distinct),
            "identities": distinct,
            "fallback_pool_size": fallback_pool_size(arm),
            "bar_collision_probability": bar,
            # The per-generation split behind the two figures above. Both of
            # those describe the pool a profile minted TODAY draws from; on an
            # arm that has been widened, profiles that already exist sit in a
            # SMALLER pool and collide MORE often, and reporting only the
            # flattering number would be the same "varied, therefore fine"
            # move this module exists to refuse. Recorded per arm so the
            # archived reading carries it too — see pool_sizes_by_generation.
            "pool_sizes_by_generation": pool_sizes_by_generation(arm),
        }
        if len(readable) < MIN_SEEDS:
            entry["verdict"] = "INCONCLUSIVE"
            entry["collision_probability"] = None
            entry["detail"] = (
                f"only {len(readable)} of {len(by_seed)} seeds produced a "
                f"reading; {MIN_SEEDS} are required before this gate will say "
                "anything. This is NOT a pass — an estimate from too few "
                "samples can clear the bar by luck."
            )
            per_arm[arm] = entry
            continue

        p = collision_probability(readable)
        entry["collision_probability"] = p
        if len(distinct) == 1:
            entry["verdict"] = "CONSTANT"
            entry["detail"] = (
                f"every one of {len(readable)} profiles was handed the SAME "
                f"identity ({distinct[0]!r}). That is a shared cross-profile "
                "identifier and a direct breach of Level 2 (mutual "
                "unlinkability). This arm must NOT be left engine-authored: "
                "remove it from gpu_ext.ENGINE_AUTHORED_IDENTITY_ARMS so "
                "persona's own pool authors the identity again."
            )
        elif bar_missing:
            # The arm HAS a pool and we could not read it — the bar this gate
            # compares against is missing, so the comparison cannot be made.
            # Falling through to OK here would silently downgrade the gate to
            # "did it vary at all?", which macos passes while colliding 76.9%
            # of the time. A missing bar is a failure to look, not a pass.
            entry["verdict"] = "INCONCLUSIVE"
            entry["detail"] = (
                f"persona ships a fallback pool for {arm!r}, but this module "
                "could not read its size. The arm is named in "
                f"_POOL_VAR_FOR_ARM as {_POOL_VAR_FOR_ARM.get(arm)!r}, but "
                "that name yielded no entries from gpu_ext.GPU_POOLS — either "
                "the key is absent from the registry (the two have drifted "
                "apart) or the pool registered under it is empty. Without the "
                "bar there is nothing to compare against, so this is NOT a "
                f"pass: the {p:.1%} collision rate measured here is unjudged. "
                "Re-register the pool in gpu_ext.GPU_POOLS (or correct the arm "
                "mapping in _POOL_VAR_FOR_ARM) and re-run."
            )
        elif bar is not None and p > bar * (1.0 + BAR_TOLERANCE):
            entry["verdict"] = "TOO_NARROW"
            entry["detail"] = (
                f"two profiles collide {p:.1%} of the time, WORSE than the "
                f"{bar:.1%} of persona's own {entry['fallback_pool_size']}-entry "
                "pool for this arm. Deferring to the engine is now COSTING "
                "unlinkability rather than merely removing a second author, so "
                "the arm should return to gpu_ext's authorship."
            )
        else:
            entry["verdict"] = "OK"
            entry["detail"] = (
                f"{len(distinct)} distinct identities over {len(readable)} "
                f"seeds; two profiles collide {p:.1%} of the time"
                + (
                    f", at or below the {bar:.1%} of persona's own "
                    f"{entry['fallback_pool_size']}-entry pool"
                    if bar is not None
                    else ""
                )
            )
        per_arm[arm] = entry

    findings = [a for a, e in per_arm.items()
                if e["verdict"] in ("CONSTANT", "TOO_NARROW")]
    inconclusive = [a for a, e in per_arm.items()
                    if e["verdict"] == "INCONCLUSIVE"]
    return {
        "per_arm": per_arm,
        "findings": findings,
        "inconclusive": inconclusive,
        "arms_checked": sorted(per_arm),
        # PS-192: derived from per_arm, never asserted. See `completeness`.
        "completeness": completeness(per_arm),
    }


def exit_code_for(result: dict) -> int:
    """PASS / FINDING / CANNOT_RUN, keeping this package's discipline.

    An INCONCLUSIVE arm is EXIT_CANNOT_RUN, never EXIT_PASS: "we failed to
    look" must not wear the code that means "we looked and it was fine".

    ⚠️ PS-192: A TRUNCATED SAMPLE CANNOT EXIT_PASS. The same discipline, one
    level up. An arm can clear MIN_SEEDS and still have lost a third of its
    requested seeds to a machine that ran out of resources mid-sweep — and
    because ``classify`` (correctly) excludes unreadable cells, that arm's
    verdict is computed from the seeds that ran EARLY and reports exactly like
    a full run. "We read part of it and that part was fine" is a different
    claim from "we looked and it was fine", so it does not get the code that
    means the latter. It is EXIT_CANNOT_RUN: a run to repeat, not a green.

    This is deliberately gated on the FINDINGS check, not ahead of it — a
    truncated run that still caught a CONSTANT or TOO_NARROW arm keeps its
    finding. Truncation can hide a defect; it cannot invent one.
    """
    if result["findings"]:
        return EXIT_FINDING
    if result["inconclusive"] or not result["arms_checked"]:
        return EXIT_CANNOT_RUN
    # `.get` so a record classified before this key existed still resolves,
    # rather than raising on a replay of an archived document.
    if not result.get("completeness", {}).get("complete", True):
        return EXIT_CANNOT_RUN
    return EXIT_PASS


def format_result(result: dict) -> str:
    lines = ["ENGINE GPU IDENTITY VARIANCE — do different profiles get different GPUs?"]
    if not result["arms_checked"]:
        lines.append("")
        lines.append(
            "  No arm is engine-authored, so there is nothing to police. "
            "(gpu_ext.ENGINE_AUTHORED_IDENTITY_ARMS is empty.)"
        )
        return "\n".join(lines)
    for arm in result["arms_checked"]:
        e = result["per_arm"][arm]
        p = e["collision_probability"]
        lines.append("")
        lines.append(f"  {arm}: {e['verdict']}")
        lines.append(
            f"    {e['distinct_identities']} distinct over "
            f"{e['seeds_readable']}/{e['seeds_requested']} readable seeds"
            + (f", collision {p:.1%}" if p is not None else "")
        )
        lines.append(f"    {e['detail']}")
        # An arm whose pool is split across generations gets the split printed,
        # because the bar above describes only the newest one. Silent on a
        # single-generation arm — there is nothing to disambiguate there, and
        # printing {0: 5} on every run would train the reader to skip the line.
        sizes = e.get("pool_sizes_by_generation") or {}
        if len(sizes) > 1:
            split = ", ".join(
                f"gen {g}: {n} entries, {1.0 / n:.1%}" if n else f"gen {g}: none"
                for g, n in sorted(sizes.items())
            )
            lines.append(
                f"    ⚠️ our own pool is NOT one size — {split}. The bar above "
                "is the NEWEST generation's; profiles created before the pool "
                "was widened still sit in the smaller one."
            )

    # PS-192: the SAMPLE's own line, printed on every run — complete or not.
    # Printed unconditionally on purpose: a completeness note that appears only
    # when something is wrong trains a reader to skim past its absence, and
    # "the sample was whole" is exactly the claim that must be stated rather
    # than assumed. Every number here is read from the records (see
    # `completeness`), so this sentence CAN go false — which is the property
    # derive.py's hardcoded "24/24" lacked.
    cov = result.get("completeness")
    if cov:
        lines.append("")
        lines.append(
            "  SAMPLE: "
            + ("COMPLETE" if cov["complete"] else "TRUNCATED — NOT A CLEAN RUN")
        )
        lines.append(
            f"    {cov['seeds_readable']}/{cov['seeds_requested']} requested "
            f"seeds readable"
            + (
                ""
                if cov["complete"]
                else f"; truncated arms: {', '.join(cov['arms_truncated'])}"
            )
        )
        lines.append(f"    {cov['detail']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# The LIVE half. Everything above this line is pure and runs in CI.
# --------------------------------------------------------------------------

_IDENTITY_PROBE_PAGE = """<!doctype html>
<meta charset="utf-8"><title>engine gpu identity</title>
<body><pre id="out">reading...</pre>
<script>
(function () {
  var out = {};
  try {
    var c = document.createElement('canvas');
    var gl = c.getContext('webgl') || c.getContext('experimental-webgl');
    if (!gl) { out.error = 'no webgl context'; }
    else {
      var d = gl.getExtension('WEBGL_debug_renderer_info');
      if (!d) { out.error = 'no debug_renderer_info'; }
      else {
        out.vendor = String(gl.getParameter(d.UNMASKED_VENDOR_WEBGL));
        out.renderer = String(gl.getParameter(d.UNMASKED_RENDERER_WEBGL));
      }
    }
  } catch (e) { out.error = String(e); }
  document.getElementById('out').textContent = JSON.stringify(out);
})();
</script></body>
"""


def _serve():
    """Loopback server for the probe page.

    The venue is 127.0.0.1 deliberately: this reads what the BROWSER reports to
    a page, and contacts no third party, so there is no address to leak and no
    exit to prove. That is the same venue ``local_probe`` establishes, and it is
    NOT a waiver of the proxied-exit rule, which governs checker reads.
    """
    import http.server
    import threading

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = _IDENTITY_PROBE_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a, **kw):
            pass

    class _S:
        def __enter__(self):
            self._srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
            self._t = threading.Thread(
                target=self._srv.serve_forever,
                kwargs={"poll_interval": 0.1}, daemon=True)
            self._t.start()
            h, p = self._srv.server_address[:2]
            self.url = f"http://{h}:{p}/"
            return self

        def __exit__(self, *e):
            try:
                self._srv.shutdown()
            finally:
                self._srv.server_close()
            self._t.join(timeout=5)

    return _S()


def measure(
    arms: "tuple[str, ...]", seeds: "tuple[int, ...]"
) -> "dict[str, dict[int, str | None]]":
    """Read the engine's own identity for each (arm, seed), layer OFF.

    ``install_layer=False`` is the whole point: this measures what the engine
    produces WITHOUT persona's masking, which is exactly what an
    engine-authored arm ships to a page.
    """
    from . import chromium_tier

    readings: "dict[str, dict[int, str | None]]" = {a: {} for a in arms}
    with _serve() as server:
        for arm in arms:
            for seed in seeds:
                value = None
                try:
                    session = chromium_tier.ChromiumSession(
                        "",
                        seed=seed,
                        declared_machine=arm,
                        allow_unsandboxed=True,
                        allow_no_proxy=True,
                        install_layer=False,
                    )
                    with session as live:
                        page = live.new_page()
                        page.goto(server.url, timeout=90000, wait_until="load")
                        time.sleep(SETTLE_SECONDS)
                        data = json.loads(page.inner_text("body"))
                    if data.get("vendor") and data.get("renderer"):
                        value = f"{data['vendor']} | {data['renderer']}"
                except Exception as exc:  # a cell that failed is recorded as None
                    print(
                        f"[variance] {arm}/{seed}: {type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
                readings[arm][seed] = value
                print(f"[variance] {arm}/seed{seed}: {value}", flush=True)
    return readings


def engine_build() -> str:
    """The chromium build this reading was taken under, or ``"unknown"``.

    A variance reading whose build is unknown cannot be acted on: the whole
    remedy for a finding is to name the bad tag (``policy.KNOWN_BAD_VERSIONS``),
    and you cannot blocklist a build you cannot name. So the record carries it.

    ⚠️ Resolved from ``version.txt``, which ``download_engine`` does NOT write —
    the UI writes it after a successful install (``app.py``). A provisioning
    step that only downloads therefore leaves this ``"unknown"``, which is why
    the workflow writes it explicitly. Mirrors ``snapshot.engine_build``'s
    contract: never raises, and an unresolved value reads as ``"unknown"``
    rather than as an empty string that looks like a value.
    """
    try:
        from ..engine.updater import current_version

        resolved = current_version()
        return str(resolved) if resolved else "unknown"
    except Exception:
        return "unknown"


def _record(
    readings: dict, result: dict, *, source: "dict | None" = None
) -> dict:
    """The artifact written by both ``check`` and ``replay``.

    ``source`` is the record being RE-VERDICTED, and is passed by ``replay``
    only. Its provenance — ``measured_at``, ``engine_build`` and
    ``engine_authored_arms`` — is PRESERVED, never re-derived.

    Those THREE fields are the whole provenance set: they answer *when*, *which
    build*, and *which arms were deferring* at the moment of measurement. Any
    field added here that describes the MEASURING machine belongs in that list
    too — the defect this guards against has now been fixed once per field,
    because each fix searched for the previous field's symptom rather than for
    the class ("what does this helper derive from the replaying machine?").

    ⚠️ That rule is ENFORCED, not merely stated here. ``tests/
    test_verify_engine_gpu_variance.py::
    test_replay_partitions_EVERY_key_so_a_new_field_cannot_be_added_unclassified``
    asserts set-EQUALITY over the keys this function writes, partitioned into
    source-preserved / recomputed / parameter / replay-stamped. **Adding a key
    below without classifying it there turns that test RED**, which is the
    whole point: three rounds of this ticket were each lost to a new field
    that every existing test was structurally unable to see, because a test
    that names three fields leaves the fourth free. If you are reading this
    because that test just failed, the fix is to decide which half of the
    partition your new field belongs in — if it describes the machine that
    MEASURED, preserve it from ``source`` as the three above are.

    ⚠️ What is deliberately NOT preserved is ``result``: it is RECOMPUTED, and
    that is the point of a re-verdict. See :func:`_cmd_replay` for the one
    consequence of that which an operator must know about.

    ⚠️ This is load-bearing, not tidiness. ``replay`` runs on a machine that
    is NOT the machine that measured: the documented case is a laptop with no
    engine, no display and no runner, reading an artifact long after the
    runner that produced it was destroyed. Re-deriving there does not blank
    the field — it substitutes a real-looking version string (or ``"unknown"``
    where no engine is installed) for the tag that was actually measured.

    The whole documented remedy for a red run is to name the bad tag in
    ``policy.KNOWN_BAD_VERSIONS``. An operator who blocklists the REPLAYING
    machine's build refuses a good build and leaves the bad one installing
    hourly — the same shape of failure this module exists to catch: a value
    that stays perfectly plausible, so no per-row tell fires.

    ``replayed_at`` records when the re-verdict happened, so the moment of
    measurement and the moment of re-reading are both present and cannot be
    mistaken for one another.
    """
    src = source or {}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    record = {
        # A falsy source value is treated as absent, so a record written
        # before this field existed still resolves to something rather than
        # to an empty string that reads like a value.
        "measured_at": src.get("measured_at") or now,
        "engine_build": src.get("engine_build") or engine_build(),
        # Membership, NOT the `or` used above: an empty list is a LEGITIMATE
        # value here ("no arm was deferring") and it is falsy, so `or` would
        # silently substitute this machine's set for a real measured empty —
        # the same defect, reintroduced for the one case that most needs it.
        # The remedy for a red run is to REMOVE the arm from
        # ENGINE_AUTHORED_IDENTITY_ARMS, so the local set is EXPECTED to
        # differ from the archived one by the time anyone replays it.
        "engine_authored_arms": (
            src["engine_authored_arms"]
            if "engine_authored_arms" in src
            else sorted(ENGINE_AUTHORED_IDENTITY_ARMS)
        ),
        "readings": {
            a: {str(s): v for s, v in by.items()} for a, by in readings.items()
        },
        "result": result,
    }
    if source is not None:
        record["replayed_at"] = now
    return record


def _cmd_check(args: argparse.Namespace) -> int:
    arms = tuple(
        a.strip() for a in (args.arms or "").split(",") if a.strip()
    ) or tuple(sorted(ENGINE_AUTHORED_IDENTITY_ARMS))
    if not arms:
        print(
            "No arm is engine-authored, so there is nothing to police.",
            file=sys.stderr,
        )
        return EXIT_PASS
    seeds = tuple(
        int(s.strip()) for s in (args.seeds or "").split(",") if s.strip()
    ) or DEFAULT_SEEDS

    try:
        readings = measure(arms, seeds)
    except Exception as exc:
        print(f"REFUSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "Nothing was established, so this is NOT 'the engine varies'.",
            file=sys.stderr,
        )
        return EXIT_CANNOT_RUN

    result = classify(readings)
    print(format_result(result))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(_record(readings, result), fh, indent=2)
    return exit_code_for(result)


def _cmd_replay(args: argparse.Namespace) -> int:
    """Re-verdict readings from a record file, taking no new measurement.

    This is the FORENSIC half. When the scheduled job goes red it uploads its
    record as an artifact (``engine-gpu-variance.yml`` step "Keep the reading
    when the gate goes red"), and this re-reads that file to reproduce the
    verdict — on a machine with no engine, no display and no runner, long after
    the runner that measured it was destroyed.

    ⚠️ It is NOT what proves the gate can go red. That is ``selftest``, which
    the workflow runs FIRST (before the engine is even downloaded) and which
    needs no engine. ``replay`` is OPERATOR-INVOKED: no workflow calls it, and
    none should be assumed to — verified by grep, not by reading.

    It deliberately cannot be mistaken for a measurement: it takes no reading
    and is a separate subcommand from ``check``. With ``--output`` it inherits
    the source record's ``measured_at``, ``engine_build`` and
    ``engine_authored_arms`` rather than stamping its own (see
    :func:`_record`), and records its own moment as ``replayed_at`` — so the
    re-verdict can never be read as a fresh reading, and the tag an operator
    blocklists is the one that was actually measured.

    ⚠️ NOT COVERED — the verdict is re-judged against TODAY'S BAR, not the bar
    the reading was measured against. ``classify`` re-derives
    ``bar_collision_probability``, ``fallback_pool_size`` and
    ``pool_sizes_by_generation`` from THIS machine's ``gpu_ext`` pools, because
    the readings are replayed but the bar is not carried. If persona's own pool
    for an arm is edited between the measurement and the replay, an identical
    archived reading can be re-judged differently. Measured, not reasoned — 10
    seeds at 38% collision:

        pool 5 (bar 20%)  -> TOO_NARROW, exit 1     <- as measured
        pool 2 (bar 50%)  -> OK,         exit 0     <- same readings, replayed

    The blast radius is bounded, and was measured rather than assumed:

    * ``CONSTANT`` is IMMUNE — ``len(distinct) == 1`` is decided before the bar
      is consulted, so the Level 2 breach this module exists to catch cannot be
      re-judged away at any pool size (verified at pools 2, 5 and 50).
    * ``INCONCLUSIVE`` is IMMUNE — the ``MIN_SEEDS`` floor precedes the bar.
    * An unreadable pool fails SAFE (``INCONCLUSIVE``/exit 2, never a pass).
    * Only ``TOO_NARROW`` <-> ``OK`` can move, and only if a pool is edited.

    It does NOT affect the gate: ``check`` measures and judges on the same
    machine in the same run, so the scheduled job is unaffected. This is a
    property of the FORENSIC path only. Whether ``replay`` should re-judge
    against the current bar (a deliberate "would this still fail today?") or
    reproduce the archived verdict is a DESIGN DECISION, not a defect to be
    quietly patched — it is recorded here and raised on PS-176 rather than
    decided by the worker that found it.
    """
    try:
        with open(args.record, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"REFUSED: cannot read {args.record}: {exc}", file=sys.stderr)
        return EXIT_CANNOT_RUN

    raw = doc.get("readings")
    if not isinstance(raw, dict) or not raw:
        print(
            f"REFUSED: {args.record} carries no readings to re-verdict.",
            file=sys.stderr,
        )
        return EXIT_CANNOT_RUN

    # Seeds round-trip through JSON as strings; classify only counts values, but
    # the ints are restored so a replayed record is shaped like a measured one.
    readings: "dict[str, dict[int, str | None]]" = {}
    for arm, by_seed in raw.items():
        if not isinstance(by_seed, dict):
            print(f"REFUSED: {arm!r} readings are malformed.", file=sys.stderr)
            return EXIT_CANNOT_RUN
        restored: "dict[int, str | None]" = {}
        for seed, value in by_seed.items():
            try:
                key = int(seed)
            except (TypeError, ValueError):
                print(f"REFUSED: {arm!r} has non-integer seed {seed!r}.",
                      file=sys.stderr)
                return EXIT_CANNOT_RUN
            restored[key] = value if isinstance(value, str) and value else None
        readings[arm] = restored

    result = classify(readings)
    print(format_result(result))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            # source=doc: this machine did not take the reading, so the
            # record's own provenance survives the re-verdict.
            json.dump(_record(readings, result, source=doc), fh, indent=2)
    return exit_code_for(result)


# The synthesised readings the self-test drives the gate with. Each is a
# (name, builder, expected exit code) triple. Built from a size the arm's own
# pool makes meaningful rather than from literals, so these keep testing the
# real bar if a pool is ever edited.
def _selftest_cases(arm: str) -> "list[tuple[str, dict, int]]":
    seeds = list(DEFAULT_SEEDS)
    n = len(seeds)
    one = "Vendor | RENDERER-A"
    two = "Vendor | RENDERER-B"
    return [
        # Every profile handed the same card: a flat Level 2 breach.
        ("CONSTANT", {arm: {s: one for s in seeds}}, EXIT_FINDING),
        # Varies (2 distinct, so "did it vary?" would PASS it) but skewed hard,
        # which is the macOS-shaped failure the collision metric exists to catch.
        ("TOO_NARROW",
         {arm: {s: (two if i >= n - 2 else one) for i, s in enumerate(seeds)}},
         EXIT_FINDING),
        # Too few readable seeds. Must be CANNOT_RUN, never PASS.
        ("INCONCLUSIVE",
         {arm: {s: (one if i < 3 else None) for i, s in enumerate(seeds)}},
         EXIT_CANNOT_RUN),
    ]


def _cmd_selftest(args: argparse.Namespace) -> int:
    """Prove this gate can still FAIL, before trusting a green from it.

    A gate is only worth wiring if it can go red, and the live ``check`` cannot
    demonstrate that: the engine currently varies, so a scheduled job would
    only ever be observed passing. That is precisely the "check that could not
    have failed" this project does not count as coverage.

    So the job runs this FIRST. It drives synthesised low-variance readings
    through the same ``classify`` → ``exit_code_for`` path the live check
    gates on and asserts each lands on the exit code it must. If the judgement
    is ever broken such that a shared graphics card reads as a pass, THIS goes
    red — on the gate's own path, on every run — instead of the job quietly
    reporting a green it is no longer able to withhold.
    """
    arm = (args.arm or "").strip() or next(
        iter(sorted(ENGINE_AUTHORED_IDENTITY_ARMS)), ""
    )
    if not arm:
        print(
            "No arm is engine-authored, so there is nothing to police.",
            file=sys.stderr,
        )
        return EXIT_PASS

    failures = []
    for name, readings, expected in _selftest_cases(arm):
        actual = exit_code_for(classify(readings))
        ok = actual == expected
        print(
            f"[selftest] {name:<13} expected exit {expected}, got {actual} "
            f"— {'ok' if ok else 'WRONG'}"
        )
        if not ok:
            failures.append((name, expected, actual))

    if failures:
        print(
            "\nSELF-TEST FAILED: this gate can no longer be trusted to fail.",
            file=sys.stderr,
        )
        for name, expected, actual in failures:
            print(
                f"  {name}: should exit {expected}, exited {actual}",
                file=sys.stderr,
            )
        print(
            "A green from the live check below would be meaningless while this "
            "is broken, so the job stops here rather than reporting one.",
            file=sys.stderr,
        )
        return EXIT_FINDING

    print(
        f"[selftest] the gate still goes red on {arm!r} for a narrowed pool, "
        "and refuses to pass an under-sampled run."
    )
    return EXIT_PASS


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="engine_gpu_variance", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser(
        "check",
        help="launch one profile per seed per engine-authored arm and verdict it",
    )
    c.add_argument("--arms", default="", help="override the arms to check")
    c.add_argument("--seeds", default="", help="override the seeds to use")
    c.add_argument("--output", default="", help="write the record here")
    c.set_defaults(func=_cmd_check)

    r = sub.add_parser(
        "replay",
        help="re-verdict a red run's uploaded record, without measuring",
    )
    r.add_argument("record", help="a record file written by `check --output`")
    r.add_argument("--output", default="", help="write the re-verdict here")
    r.set_defaults(func=_cmd_replay)

    s = sub.add_parser(
        "selftest",
        help="prove the gate can still go red, without needing the engine",
    )
    s.add_argument("--arm", default="", help="arm to synthesise readings for")
    s.set_defaults(func=_cmd_selftest)
    return ap


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
