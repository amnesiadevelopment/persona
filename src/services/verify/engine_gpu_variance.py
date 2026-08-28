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
#
# ⚠️ SINCE PS-191 THIS NO LONGER DECIDES A VERDICT. The raw bar comparison is
# still COMPUTED and REPORTED (`meets_bar` on every arm) because an operator
# must keep seeing how often two profiles actually collide — but it is not what
# flags an arm any more. See `uniform_collision_p_value` for why, and keep this
# tolerance: the reported comparison has exactly the same ulp knife-edge it
# always did.
BAR_TOLERANCE = 1e-9

# The significance level the variance judgement is made at. An arm is flagged
# only when a UNIFORM draw from persona's own pool would produce a result this
# collided less than ALPHA of the time — see `uniform_collision_p_value`.
#
# 0.05 is PS-185's own alpha, kept deliberately so this gate's verdicts and
# that measurement's published p-values are read on one scale rather than two.
# It is a false-POSITIVE rate: with four arms checked daily, a healthy fleet
# still trips roughly one arm every five runs by chance alone, which is the
# price of a test that can also go red for a real reason.
#
# ⚠️ WHY PER-ARM AND NOT A MULTIPLICITY CORRECTION (PS-191 review). Four arms
# at 0.05 is an 18.5% family-wise false-positive rate, and this ticket's own
# motivation is that "a gate that flags three arms out of four teaches its
# readers to ignore it" — so the tension is real and was measured rather than
# argued. Bonferroni (α/4 = 0.0125) was the obvious candidate:
#
#   * It changes NOTHING today. All eight of PS-185's committed cells return
#     the identical verdict under both alphas — the two true signals sit at
#     p=0.000 and the six healthy cells at p>=0.064.
#   * It COSTS REAL DETECTION. At pool=8/N=24 an engine collapsed to 5 of its
#     8 identities scores p=0.042: caught at 0.05, MISSED at 0.0125. That is a
#     37% narrowing of the identity space going unreported.
#
# So the correction buys no accuracy on any reading we have and pays for it in
# power, against a threat model where a partial collapse is exactly what an
# engine regression looks like. The multiplicity concern is also weaker here
# than the arm count suggests: the four arms are judged and REPORTED
# INDEPENDENTLY, one verdict per arm, so an operator reads "linux is narrow",
# never "the fleet failed" — there is no single family-wise claim being made
# for a correction to protect.
#
# The alarm-fatigue problem the reviewer rightly raises is real, but its cause
# was the BROKEN COMPARISON, not this threshold: the old gate flagged three of
# four arms EVERY run, deterministically. This one flags a healthy arm
# occasionally and at random, which is a different and much smaller problem.
# If fatigue persists once this ships, the honest fix is requiring two
# consecutive reds before alarming (which cuts the rate to 0.25% without
# touching power at all), NOT a threshold nudge — see the PS-191 constraint
# that any correction be statistical rather than a tolerance widened to taste.
ALPHA = 0.05

# Beyond this much work the exact null distribution is not enumerated and a
# deterministic Monte-Carlo estimate is used instead. The exact enumeration is
# O(k * N^2 * |states|) and superlinear in practice: measured on this tree,
# k=11/N=24 takes 14ms and k=20/N=100 takes 13.6s. Every realistic run sits far
# below the cap (the shipped DEFAULT_SEEDS is N=15, PS-185 measured N=24, and
# the widest pool is 11), so the exact path is the one that actually runs; this
# exists so an operator passing --seeds with a hundred entries gets a slower
# answer rather than a hung job.
EXACT_P_VALUE_MAX_WORK = 60_000

# Trials for the Monte-Carlo fallback, and its FIXED seed. Fixed so two runs of
# the same record return the same verdict: a gate whose red/green flickers on
# re-run teaches its readers to re-run it until it is green.
MC_TRIALS = 20_000
MC_SEED = 20260826

# Below this many readable seeds an arm is INCONCLUSIVE rather than passed. A
# collision probability estimated from a couple of samples is not evidence, and
# a cheap run must not be able to certify the property.
#
# RE-EXAMINED UNDER PS-191 AND DELIBERATELY LEFT AT 8. The finding is recorded
# here rather than acted on, because the two things this floor could be wrong
# about pull in opposite directions and only one of them was ever real.
#
# 1. THE BIAS ARGUMENT FOR RAISING IT IS NOW MOOT. The old comparison's error
#    term was ``(1 − 1/k)/N``, which is largest exactly where N is smallest —
#    so the floor that exists to prevent a lucky PASS sat precisely where the
#    false FLAG was worst. At the floor itself this was not marginal: at N=8
#    against today's 11-entry MAC_GPUS the bias term is 0.1136 while the whole
#    bar is 0.0909, i.e. a PERFECTLY uniform draw was expected to score above
#    its own bar by more than the bar's own width, and a run could be flagged
#    for being flawless. Raising MIN_SEEDS would have bought that back one
#    sample at a time. It is not needed: `uniform_collision_p_value` compares
#    against the null AT THE OBSERVED N, so the bias is inside the null rather
#    than beside it, and the floor no longer carries any part of that job.
#
# 2. WHAT THE FLOOR STILL BUYS IS POWER, AND THAT ARGUMENT SURVIVES. A small
#    sample now fails SAFE (a wide null flags nothing) rather than fails LOUD,
#    which is the better direction but is not free: the gate simply cannot see
#    a moderate narrowing at N=8. Measured on this implementation, the typical
#    case of an engine collapsed to j identities is caught at α=0.05 when:
#
#        N=8,  k=5  → j ≤ 2      N=15, k=5  → j ≤ 3
#        N=8,  k=11 → j ≤ 3      N=24, k=8  → j ≤ 5
#                                N=24, k=4  → j ≤ 3
#
#    So eight seeds catch a CONSTANT arm and a halved pool, and miss the rest.
#    That is a real limit and it is why DEFAULT_SEEDS is 15 and why PS-185
#    measured 24 — the floor is the refusal threshold, not the recommendation.
#
# NOT RAISED, for the reason the floor is safe as it stands: an arm below it is
# INCONCLUSIVE, which is EXIT_CANNOT_RUN and NOT a pass, so a weak sample can
# never certify the property — it can only decline to judge. Raising the number
# would convert runs that currently say "we could not tell" into runs that do
# not happen at all, trading an honest non-answer for less coverage. Changing
# it is a judgement about how much engine time a scheduled job should spend,
# which is a different question from the one this ticket fixed.
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


def _identity_counts(values: "list[str]") -> "list[int]":
    """How many profiles got each distinct identity, commonest first."""
    counts: "dict[str, int]" = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return sorted(counts.values(), reverse=True)


def _exact_work_estimate(n: int, k: int) -> int:
    """Rough cost of enumerating the exact null. See EXACT_P_VALUE_MAX_WORK."""
    return k * (n + 1) * (n + 1)


def _sum_of_squares_null_weights(n: int, k: int) -> "dict[int, int]":
    """EXACT distribution of Σcᵢ² when n profiles are drawn uniformly from k.

    Returns ``sum-of-squared-counts -> number of the kᴺ equally likely
    assignments producing it``. Integer arithmetic throughout, so there is no
    floating-point error to reason about and the weights provably total kᴺ
    (asserted by the caller).

    Dynamic programme over the k identities: place some number of the n
    profiles on each in turn, carrying the multinomial coefficient. The state
    is (profiles placed so far -> Σcᵢ² so far), which collapses the kᴺ raw
    assignments into a few hundred states at the sizes this gate runs at.
    """
    from math import comb

    # profiles_placed -> {sum_of_squares: weight}
    states: "dict[int, dict[int, int]]" = {0: {0: 1}}
    for identity in range(k):
        is_last = identity == k - 1
        nxt: "dict[int, dict[int, int]]" = {}
        for placed, by_sumsq in states.items():
            # The last identity must absorb every remaining profile; the others
            # may take any share of what is left.
            lowest = n - placed if is_last else 0
            for count in range(lowest, n - placed + 1):
                ways = comb(n - placed, count)
                squared = count * count
                target = nxt.setdefault(placed + count, {})
                for sumsq, weight in by_sumsq.items():
                    target[sumsq + squared] = (
                        target.get(sumsq + squared, 0) + weight * ways
                    )
        states = nxt
    return states[n]


def _monte_carlo_p_value(observed_sumsq: int, n: int, k: int) -> float:
    """Sampled stand-in for the exact null, for samples too large to enumerate.

    Deterministic: its own generator seeded from the fixed ``MC_SEED``, never
    the global one, so this cannot be perturbed by — or perturb — anything else
    that draws random numbers in the same process.
    """
    import random

    rng = random.Random(MC_SEED)
    at_least_as_collided = 0
    for _ in range(MC_TRIALS):
        counts = [0] * k
        for _ in range(n):
            counts[rng.randrange(k)] += 1
        if sum(c * c for c in counts) >= observed_sumsq:
            at_least_as_collided += 1
    return at_least_as_collided / MC_TRIALS


def uniform_collision_p_value(values: "list[str]", pool_size: int) -> "float | None":
    """P(a UNIFORM draw from ``pool_size`` collides at least this much at this N).

    ⚠️ THIS IS THE COMPARISON THE GATE JUDGES ON, AND IT REPLACES A BROKEN ONE.
    PS-191. Read this before changing anything here.

    THE DEFECT IT FIXES
    -------------------
    :func:`collision_probability` is the PLUG-IN Simpson index, and it is
    BIASED UPWARD at finite N. Under a genuinely uniform draw from k
    identities::

        E[Ŝ] = 1/k + (1 − 1/k)/N

    :func:`bar_for` returns ``1/k`` — the collision probability of a uniform
    draw **in the limit**. Comparing the first against the second compares an
    estimate against a quantity it does not estimate, and the gap is the whole
    bias term. At N=24 and k=4 a PERFECTLY uniform pool is expected to score
    0.2812 against a 0.2500 bar, so it is flagged ``TOO_NARROW`` for being
    exactly what it should be. That is not a tuning problem; the two numbers
    were never comparable.

    PS-185 caught it on android, and its case is airtight: android scored
    0.2743 — BELOW the 0.2812 a uniform draw is expected to score — and the
    gate still flagged it. An arm cannot be worse than uniform while scoring
    better than uniform predicts.

    WHY DE-BIASING THE ESTIMATOR IS NOT ENOUGH — the correction that was tried
    -------------------------------------------------------------------------
    The obvious repairs are to un-bias the estimate (compare
    ``(Σcᵢ² − N)/(N(N−1))`` against ``1/k``) or to move the bar
    (compare ``Ŝ`` against ``E[Ŝ]``). BOTH ARE STILL A POINT ESTIMATE AGAINST A
    POINT, and both still flag macos and linux on PS-185's records. They fix
    the CENTRE of the null and ignore its WIDTH.

    That is the deeper defect. The bar is the collision probability of the very
    pool the engine is being asked to match, so an arm that matches it exactly
    sits ON the boundary — and a point comparison against a boundary the truth
    sits on is a COIN FLIP. Roughly half of all healthy runs land above it. No
    amount of centring fixes a test that is wrong half the time by
    construction; the sampling spread has to enter the verdict.

    WHAT THIS DOES INSTEAD
    ----------------------
    Asks a question with an actual answer: **if the engine really were drawing
    uniformly from a pool as wide as ours, how often would it look at least
    this collided?** That is a one-sided hypothesis test, and it is the honest
    form of the question the gate was always trying to ask. Small p means the
    reading is hard to explain as a healthy engine having an unlucky day, which
    is precisely when an operator should be told.

    Returns None when there is no pool to compare against (the same "no bar"
    state :func:`bar_for` reports with None), never a number that could be
    mistaken for a pass.

    ⚠️ IT IS EXACT, NOT SAMPLED, at every size this gate realistically runs at.
    The null is enumerated by dynamic programme in integer arithmetic — no
    numpy, no scipy (neither is a dependency of this tree), no random seed, no
    trial count, and the same record re-judged tomorrow returns the same verdict
    to the last digit. Verified against PS-185's independent 200,000-trial
    Monte-Carlo estimates, which it reproduces to within sampling noise
    (android .5791 vs .5797, linux .1648 vs .1638, macos .3075 vs .3084,
    windows 1.0 vs 1.0). Above ``EXACT_P_VALUE_MAX_WORK`` it degrades to a
    fixed-seed Monte-Carlo estimate rather than hanging.

    ⚠️ A LARGE p IS NOT A CERTIFICATE THAT THE ARM IS SAFE. It says the reading
    is CONSISTENT WITH uniform selection from a pool of this size — it says
    nothing about whether a pool of this size is wide enough. A two-entry pool
    drawn perfectly uniformly collides 50% of the time and will score p≈1 here
    while linking half the fleet. The POOL WIDTH question is a different one,
    it is owned by the pool (PS-183 for ``MAC_GPUS``), and it is why
    :func:`classify` keeps reporting the absolute collision rate and the bar
    beside this verdict rather than replacing them with it.
    """
    if pool_size <= 0:
        return None
    if not values:
        return None

    counts = _identity_counts(values)
    n = sum(counts)
    if n <= 0:
        return None

    # ⚠️ THERE IS DELIBERATELY NO `len(counts) > pool_size` SHORT-CIRCUIT HERE.
    # One was tried and it was a true-signal regression (PS-191 review). It
    # returned the maximally-green p-value whenever the observation held more
    # distinct identities than our pool, which is a DISTINCT-COUNT pass — the
    # exact fallacy this module's header refuses (PS-161: "does it vary?" is
    # the wrong question; skew is what matters). Measured at pool=5, N=24 —
    # the live windows cell, which has 9 distinct identities and so took that
    # branch on every real run:
    #
    #     [19,1,1,1,1,1]  63.5% collision  ->  p=1.0  OK      (old gate: TOO_NARROW)
    #     [16,2,2,2,1,1]  46.9% collision  ->  p=1.0  OK      (old gate: TOO_NARROW)
    #
    # 79% of the fleet handed one card, passing silently, on a gate built to
    # catch exactly that. It also made the verdict NON-MONOTONE in the very
    # statistic it polices: [8,8,8] at 33.3% flagged while 63.5% passed.
    #
    # The case that branch existed to protect needs no special case, because
    # the arithmetic already reaches it HONESTLY: a genuinely wider-than-our-
    # pool draw has a sum-of-squares below anything the null produces, so it
    # scores p=1.0 on its own merits. Verified on PS-185's real windows cell
    # ([5,4,4,3,2,2,2,1,1], 9 distinct vs pool 5): p=1.0 with or without the
    # branch, and not one of the eight committed fixture cells moves.
    #
    # If an explicit branch is ever wanted here it must key on the STATISTIC
    # (observed Σcᵢ² below the null's support), NEVER on len(counts).
    observed_sumsq = sum(c * c for c in counts)

    if _exact_work_estimate(n, pool_size) <= EXACT_P_VALUE_MAX_WORK:
        weights = _sum_of_squares_null_weights(n, pool_size)
        total = sum(weights.values())
        # The enumeration is exhaustive over kᴺ equally likely assignments; if
        # that identity fails the programme is wrong, and a wrong p-value must
        # not be returned as if it were right.
        assert total == pool_size ** n, (
            f"exact null enumeration is unsound: weights total {total}, "
            f"expected {pool_size}**{n}"
        )
        at_least_as_collided = sum(
            w for sumsq, w in weights.items() if sumsq >= observed_sumsq
        )
        return at_least_as_collided / total

    return _monte_carlo_p_value(observed_sumsq, n, pool_size)


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


def classify(
    readings: "dict[str, dict[int, str | None]]",
    pool_sizes: "dict[str, int] | None" = None,
) -> dict:
    """Turn per-arm, per-seed identity readings into a verdict. PURE.

    ⚠️ ``pool_sizes`` PINS THE NULL TO THE POOL THE READINGS ACTUALLY FACED,
    and exists because this function is called on ARCHIVED records as well as
    on live runs (PS-239). The verdict is a comparison against persona's own
    pool, so it is only meaningful against the pool that was in place WHEN THE
    READINGS WERE TAKEN. Omit it and the pool is read live — correct for a run
    happening now, and WRONG for a record from before a pool was widened,
    because the reading is then judged against a null it never faced.

    PS-183 widened ``MAC_GPUS`` 2 -> 11, so PS-185's macos readings (measured
    against a 2-entry pool, p=0.064) re-judged live against 11 entries score
    p=1.3e-14 and flip ``OK`` -> ``TOO_NARROW`` — an arm condemned for
    colliding at a rate that was unremarkable in the pool it was drawn from.

    It is a ``{arm: k}`` MAPPING rather than a generation index, matching
    ``uniformity_check.analyse``'s ``pool_sizes`` argument — the convention
    this codebase already uses to pin ``k`` to a measurement epoch. ``k`` is an
    ENVIRONMENTAL INPUT the sweep recorded, not a property of the readings, so
    the witness is the sweep's own ``fallback_pool_size`` (see
    ``uniformity_check.epoch_pool_sizes``). An arm absent from the mapping
    falls back to the live pool, so a caller may pin some arms and not others.

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
        # The pinned `k` for this arm, or None to read the pool live. A pinned
        # size is used AS the pool size rather than mapped through a
        # generation: `k` is what the null needs, and the sweep recorded `k`.
        # The UNPINNED path calls `fallback_pool_size`/`bar_for` exactly as it
        # always did — single-argument — so the stubs existing tests substitute
        # for them keep working and the live behaviour is unchanged.
        pinned = (pool_sizes or {}).get(arm)
        if pinned is None:
            pool = fallback_pool_size(arm)
            bar = bar_for(arm)
        else:
            pool = int(pinned)
            bar = (1.0 / pool) if pool > 0 else None
        bar_missing = bar is None and has_known_pool(arm)
        entry: dict = {
            "seeds_requested": len(by_seed),
            "seeds_readable": len(readable),
            "distinct_identities": len(distinct),
            "identities": distinct,
            # The `k` the null was pinned to, recorded so a verdict carries the
            # pool it was judged against rather than leaving the reader to
            # assume "today". None means the pool was read live.
            "pinned_pool_size": pinned,
            "fallback_pool_size": pool,
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
        # The RAW bar comparison — still computed, still reported, no longer
        # what decides the verdict (PS-191). An operator must keep seeing how
        # often two profiles actually collide against the pool we gave up, so
        # a p-value pass can never read as "this arm is safe" when the pool
        # behind it is two entries wide. See uniform_collision_p_value.
        entry["meets_bar"] = (
            None if bar is None else not (p > bar * (1.0 + BAR_TOLERANCE))
        )
        # The bias term that made the old raw comparison unsound, recorded so
        # the record carries the arithmetic rather than only its conclusion.
        entry["expected_collision_under_uniform"] = (
            None
            if not entry["fallback_pool_size"]
            else bar + (1.0 - bar) / len(readable)
        )
        p_uniform = uniform_collision_p_value(
            readable, entry["fallback_pool_size"])
        entry["uniform_p_value"] = p_uniform
        entry["alpha"] = ALPHA
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
        elif p_uniform is not None and p_uniform <= ALPHA:
            # THE FINDING, and since PS-191 it is a STATISTICAL one. Not "the
            # estimate landed above the bar" — which a perfectly uniform pool
            # does roughly half the time at these sample sizes — but "a pool as
            # wide as ours, drawn uniformly, would look this collided less than
            # ALPHA of the time". See uniform_collision_p_value.
            entry["verdict"] = "TOO_NARROW"
            entry["detail"] = (
                f"two profiles collide {p:.1%} of the time over "
                f"{len(readable)} seeds. A uniform draw from a pool as wide as "
                f"persona's own {entry['fallback_pool_size']}-entry pool for "
                f"this arm would look at least this collided only "
                f"{p_uniform:.1%} of the time (p={p_uniform:.4f} ≤ α={ALPHA}), "
                "so this is not a sampling accident: the engine's selection is "
                "genuinely narrower than what we gave up. Deferring is COSTING "
                "unlinkability rather than merely removing a second author, so "
                "the arm should return to gpu_ext's authorship."
            )
        else:
            entry["verdict"] = "OK"
            # ⚠️ WHAT THIS PASS DOES AND DOES NOT SAY. It says the SELECTION is
            # consistent with drawing uniformly from a pool of this width. It
            # does NOT say the width is adequate — a 2-entry pool drawn
            # perfectly collides 50% of the time and passes here. So the
            # absolute collision rate stays in the sentence, and on an arm
            # where it is above the bar the pass says so IN THE SAME BREATH
            # rather than reporting a bare green. Removing that clause would
            # turn this gate into the laundering machine PS-191 warned about.
            detail = (
                f"{len(distinct)} distinct identities over {len(readable)} "
                f"seeds; two profiles collide {p:.1%} of the time"
            )
            if p_uniform is not None and bar is not None:
                if entry["meets_bar"]:
                    detail += (
                        f", at or below the {bar:.1%} of persona's own "
                        f"{entry['fallback_pool_size']}-entry pool "
                        f"(p={p_uniform:.4f})"
                    )
                else:
                    detail += (
                        f", ABOVE the {bar:.1%} of persona's own "
                        f"{entry['fallback_pool_size']}-entry pool — but that "
                        "gap is what a finite sample does to this estimator, "
                        "not evidence of narrowing: a uniform draw of "
                        f"{len(readable)} seeds is EXPECTED to score "
                        f"{entry['expected_collision_under_uniform']:.1%}, and "
                        f"this reading has p={p_uniform:.4f} > α={ALPHA}. NOT "
                        "a clean bill of health for the arm — it says selection "
                        "is unbiased, not that the pool is wide enough."
                    )
            elif bar is not None:
                detail += (
                    f", at or below the {bar:.1%} of persona's own "
                    f"{entry['fallback_pool_size']}-entry pool"
                )
            entry["detail"] = detail
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
        bar = e.get("bar_collision_probability")
        pu = e.get("uniform_p_value")
        lines.append(
            f"    {e['distinct_identities']} distinct over "
            f"{e['seeds_readable']}/{e['seeds_requested']} readable seeds"
            + (f", collision {p:.1%}" if p is not None else "")
            + (f" vs bar {bar:.1%}" if bar is not None else "")
            + (f", p={pu:.4f}" if pu is not None else "")
        )
        lines.append(f"    {e['detail']}")
        # PS-191: an arm that passes the STATISTICAL test while sitting above
        # its own bar gets that said out loud, on its own line, every time.
        # The pass means "selection is not skewed"; it does NOT mean "this arm
        # is safe", and the two are easy to conflate precisely on the arms
        # where the pool is narrowest. Printing it here keeps the distinction
        # in front of the reader rather than buried in `detail`.
        if e["verdict"] == "OK" and e.get("meets_bar") is False:
            lines.append(
                f"    ⚠️ collision {p:.1%} is ABOVE this arm's {bar:.1%} bar, "
                "and this is still a PASS — the gap is the finite-sample bias "
                "of the estimator, not narrowing (see the p-value). This says "
                "SELECTION is uniform; it does NOT say the POOL is wide "
                "enough. If this arm's collision rate is the concern, that is "
                "a question about the pool's WIDTH, not about the engine."
            )
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
