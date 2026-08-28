"""The Level-2 unlinkability gate can now ask about the child-frame realm (PS-232).

WHAT WAS WRONG, stated as the gate saw it rather than as a story:

``compare_profiles`` — the Level 2 gate, the one that answers "are these two
profiles distinguishable?" — builds its work list from
``must_differ_probes() x probe.realms`` (``diff.py``). It is inventory-driven by
charter (``probes.py:8``: *"Adding a vector MUST mean adding a record to PROBES
and nothing else"*), so it can only ask about a realm that some ``INDEPENDENT``
record actually declares. Before this slice, no ``INDEPENDENT`` record declared
``child_frame``, and the pair list was exactly::

    window       audio.digest
    window       canvas.readback
    window       webgl.readback
    worker       canvas.readback

Four pairs, and **none of them in a child realm**. Not a vector that reads
poorly there — the realm was structurally outside the unlinkability question.

That gap is not abstract, and this file exists because of the specific way it
was measured. PS-193 found a real Level 2 failure in **exactly this realm on
exactly this vector**: CreepJS built a phantom iframe and took it by INDEXED
access (``self[N]``), persona's chain hooked the ``contentWindow`` accessor
which indexed access never invokes, and Firefox's ``creepjs ::
webgl_pixel_hash`` received the *unperturbed* buffer — bit-identical to the
unspoofed baseline, across 4 seeds / 4 exits / 3 days. So: a collision was
measured in this realm, the realm was added to the harness for it (PS-210), and
the gate deciding linkability still could not look at it. PS-210's own honest
bound #3 said so — *"One realm is not the realm axis solved."* This is that
residue, closed one realm's worth.

WHAT THIS FILE ASSERTS, and the two rules it will not bend:

1. **On the entries a real call RETURNS.** Never on source text, never that a
   helper ran. A test that greps ``probes.py`` for ``child_frame`` would pass
   over a comparator that never walked the realm.
2. **BOTH HALVES of the reach, together.** A ``contentWindow`` hit count of
   zero is *equally satisfied by never reaching the frame at all*, so it is
   evidence of nothing on its own. Every reach assertion below pairs the zero
   count with a genuine child-realm reading in the same breath. That is the
   exact discrimination PS-193 turned on.

THE BROWSER TESTS REUSE PS-210's CAPABILITY GATE rather than re-deriving one,
and that reuse is deliberate. ``shutil.which("chromium") is not None`` asks
whether a FILE EXISTS; the hosted ubuntu runner has a ``/usr/bin/chromium``
that then renders nothing, which is how PS-210 shipped a local green and CI
caught four regressions — *the machine with the tool failed and the machine
without it passed*. ``requires_chromium`` (imported below) asks the honest
question by rendering a trivial page. Chromium is provisioned NOWHERE in CI,
deliberately, so these skip there and the non-browser tests below carry the
regression weight.
"""

from __future__ import annotations

import json

import pytest

from src.services.verify import diff, probes, runner, snapshot

# REUSED, not re-derived — see the module docstring. Importing the gate (rather
# than copying it) also means a future correction to the usability probe reaches
# this file automatically instead of leaving a second, staler copy behind.
from tests.test_verify_child_frame_realm import (
    _BROWSER_TEST_TIMEOUT_S,
    _COUNT_CONTENT_WINDOW,
    _evaluate,
    requires_chromium,
    # Imported for its SIDE EFFECT on this module's namespace, and flake8 is
    # told so below. `requires_chromium` is `usefixtures("usable_chromium")`,
    # and a pytest fixture is resolved from the test module's namespace or a
    # conftest — NOT from wherever the mark was defined. Importing the mark
    # alone therefore collects fine and then errors at setup with "fixture
    # 'usable_chromium' not found". Importing the fixture FUNCTION here binds
    # the name locally, which is what makes the mark resolvable.
    usable_chromium,  # noqa: F401
)

# The vector this slice put on the must-differ axis in the child realm.
#
# webgl.readback and NOT canvas.readback, and the reason is a test-suite
# hazard rather than a preference. canvas.readback declares (window, worker);
# adding child_frame would make its realm set exactly ALL_REALMS, and four
# tests in test_verify_snapshot.py select on `set(p.realms) ==
# set(probes.ALL_REALMS)` — three taking `[0]` and naming it `shared`. Because
# canvas.readback sorts EARLIER in the inventory than realm.frameIdentity, it
# would capture that selector and silently rebind three existing tests from a
# SHARED probe onto an INDEPENDENT seed-derived one, four files from the edit.
# webgl.readback's realm set stays a strict subset of ALL_REALMS. Pinned by
# test_the_all_realms_selectors_still_resolve_to_the_shared_probe below.
VECTOR = "webgl.readback"


def _snapshot_pair(*, window_value, child_value, b_child_value=None):
    """Two single-realm-pair snapshots from two NAMED, DIFFERENT profiles.

    ``compare_profiles`` refuses an uncontrolled comparison before it reads a
    single probe (``_require_controlled``): both snapshots must name a profile,
    the two must differ, and the engine must be recorded. So these headers are
    load-bearing preconditions, not decoration — a helper that omitted them
    would make every test below raise instead of assert.
    """
    if b_child_value is None:
        b_child_value = child_value

    def one(profile, child):
        results = {
            probes.WINDOW: {VECTOR: {"value": window_value}},
            probes.CHILD_FRAME: {VECTOR: child},
        }
        return snapshot.build_snapshot(
            results,
            engine="chromium",
            profile=profile,
            realms=(probes.WINDOW, probes.CHILD_FRAME),
        )

    return one("ps232-a", child_value), one("ps232-b", b_child_value)


# --- AC1: the gate's pair list reaches into the child realm -------------------


def test_compare_profiles_now_compares_a_vector_in_the_child_frame_realm():
    """AC1, first half: the pair list GAINS a child_frame entry.

    Asserted against the comprehension the comparator actually runs, not
    against a transcription of it. This is the assertion AC2 says is impossible
    before the realm declaration and AC8 says must go RED when it is reverted.
    """
    pairs = sorted(
        (realm, probe.id)
        for probe in probes.must_differ_probes()
        for realm in probe.realms
    )

    child_pairs = [p for p in pairs if p[0] == probes.CHILD_FRAME]

    assert child_pairs, (
        "the Level-2 gate has no child_frame pair to compare. The realm PS-193 "
        "measured a real collision in is structurally outside the unlinkability "
        "question again."
    )
    assert (probes.CHILD_FRAME, VECTOR) in pairs


def test_a_collision_in_the_child_frame_realm_is_reported_COLLIDING():
    """AC1, the half that matters: a real ``compare_profiles`` call REPORTS it.

    Two different profiles agreeing on a seed-derived vector is a linkable
    identity — the Level 2 finding. The assertion is on the returned entry, so
    it fails if the comparator never walked the realm, whatever the inventory
    says.
    """
    collision = {"digest": 2952899525, "bytes": 4096, "mid": 3072}
    a, b = _snapshot_pair(
        # The window realm DIFFERS, so it passes silently and cannot be the
        # thing this assertion is reading. The child realm is the only realm
        # left that can produce a finding.
        window_value={"digest": 111, "bytes": 4096, "mid": 3072},
        child_value={"value": collision},
    )
    b["probes"][probes.WINDOW][VECTOR] = {"value": {"digest": 222, "bytes": 4096, "mid": 3072}}

    entries = diff.compare_profiles(a, b)

    child = [
        e
        for e in entries
        if e["realm"] == probes.CHILD_FRAME and e["probe_id"] == VECTOR
    ]
    assert child, (
        f"compare_profiles returned no child_frame entry for {VECTOR}. "
        f"Entries were: {entries}"
    )
    entry = child[0]
    assert entry["status"] == diff.COLLIDING
    # The operator must read WHICH value links the two profiles.
    assert entry["value"] == collision


def test_two_profiles_that_differ_in_the_child_realm_are_a_silent_pass():
    """The negative control for AC1: the new pair can also NOT fire.

    Without this, ``test_a_collision_...`` above is satisfied by a comparator
    that reports COLLIDING unconditionally in this realm — which would be a
    false leak report on every pair rather than a working gate.
    """
    a, b = _snapshot_pair(
        window_value={"digest": 111},
        child_value={"value": {"digest": 2952899525}},
        b_child_value={"value": {"digest": 3141592653}},
    )
    b["probes"][probes.WINDOW][VECTOR] = {"value": {"digest": 222}}

    entries = diff.compare_profiles(a, b)

    assert not [
        e
        for e in entries
        if e["realm"] == probes.CHILD_FRAME and e["probe_id"] == VECTOR
    ], "two profiles that DIFFER in the child realm were reported as a finding"


# --- AC5: an unread child realm is inconclusive, never distinctness -----------


@pytest.mark.parametrize(
    "unread,label",
    [
        ({"error": "ChildFrameHarness: TimeoutError"}, "the realm errored"),
        ({"value": None}, "the API was not there (a null reading)"),
        (dict(diff.ABSENT), "the realm is absent from the snapshot"),
    ],
)
def test_an_unread_child_realm_is_inconclusive_and_never_reads_as_distinct(
    unread, label
):
    """AC5. A realm that could not be entered must NOT read as two profiles differing.

    The dangerous direction is the PASS, not the finding: a child realm that
    errored or read ``null`` must never be silently counted as "these two
    profiles differ here", which would issue a certificate of unlinkability
    resting on nothing. ``_unread_for_unlinkability`` (``diff.py:308``) already
    encodes this — including the ``null`` case, which is why the parametrisation
    covers it — and this pins the behaviour for the NEW pair without narrowing
    that predicate.
    """
    a, b = _snapshot_pair(
        window_value={"digest": 111},
        child_value=unread,
        b_child_value={"value": {"digest": 2952899525}},
    )
    b["probes"][probes.WINDOW][VECTOR] = {"value": {"digest": 222}}

    entries = diff.compare_profiles(a, b)

    child = [
        e
        for e in entries
        if e["realm"] == probes.CHILD_FRAME and e["probe_id"] == VECTOR
    ]
    assert child, f"{label}: reported as a silent PASS — i.e. as distinctness"
    assert child[0]["status"] == diff.INCONCLUSIVE, (
        f"{label}: expected INCONCLUSIVE, got {child[0]['status']}"
    )
    assert diff.inconclusive_count(entries) >= 1


def test_both_profiles_failing_to_read_the_child_realm_is_not_a_collision():
    """AC5's sharpest corner: two identical NON-readings must not compare EQUAL.

    ``snapshot`` records "the API was not there" as ``{"value": null}``, and a
    naive equality check makes two such sides agree and reports the profiles as
    linkable — a false leak on every pair, from a run resting on nothing. This
    is the documented reason ``_unread_for_unlinkability`` exists at all.
    """
    a, b = _snapshot_pair(
        window_value={"digest": 111},
        child_value={"value": None},
    )
    b["probes"][probes.WINDOW][VECTOR] = {"value": {"digest": 222}}

    entries = diff.compare_profiles(a, b)

    entry = next(
        e
        for e in entries
        if e["realm"] == probes.CHILD_FRAME and e["probe_id"] == VECTOR
    )
    assert entry["status"] == diff.INCONCLUSIVE
    assert entry["status"] != diff.COLLIDING


# --- AC6: this ADDS a comparison; it changes no existing answer ---------------


def test_the_four_pre_existing_pairs_are_compared_exactly_as_before():
    """AC6. The window/worker pairs are untouched — byte-identical.

    Transcribed as a literal rather than recomputed from the inventory: a
    recomputation would follow the inventory wherever it moved and could never
    catch the regression this guards against.
    """
    pairs = sorted(
        (realm, probe.id)
        for probe in probes.must_differ_probes()
        for realm in probe.realms
    )

    assert [p for p in pairs if p[0] != probes.CHILD_FRAME] == [
        (probes.WINDOW, "audio.digest"),
        (probes.WINDOW, "canvas.readback"),
        (probes.WINDOW, "webgl.readback"),
        (probes.WORKER, "canvas.readback"),
    ]
    # ADDS one comparison, changes none: 4 -> 5.
    assert len(pairs) == 5


def test_the_worker_realm_did_not_gain_the_webgl_vector():
    """AC6. webgl.readback stays OUT of the worker realm, and that is measured.

    In a worker, ``getContext('webgl')`` returns null on this engine, and a
    null on an INDEPENDENT vector manufactures a false COLLIDING on every pair.
    A future edit widening this record to ``EVERY_REALM`` "for symmetry" would
    reintroduce exactly that, so the exclusion is pinned.
    """
    probe = next(p for p in probes.PROBES if p.id == VECTOR)

    assert probes.WORKER not in probe.realms
    assert set(probe.realms) == {probes.WINDOW, probes.CHILD_FRAME}
    assert probe.variance == probes.INDEPENDENT


def test_the_per_realm_probe_inventory_for_window_and_worker_is_unchanged():
    """AC6. Adding a realm to an existing record must not move window/worker.

    This is also precisely why the committed baseline needs no re-record: the
    baseline guard compares the probe-ID SET per realm for ``window`` and
    ``worker`` only, and neither set moves here.
    """
    assert len(probes.probes_for_realm(probes.WINDOW)) == 49
    assert len(probes.probes_for_realm(probes.WORKER)) == 36
    assert len(probes.probes_for_realm(probes.CHILD_FRAME)) == 2


def test_the_all_realms_selectors_still_resolve_to_the_shared_probe():
    """AC6, and the reason this slice chose webgl.readback over canvas.readback.

    Three tests in test_verify_snapshot.py do
    ``[p for p in PROBES if set(p.realms) == set(ALL_REALMS)][0]`` and name the
    result ``shared``, meaning a SHARED-variance probe readable in every realm.
    Had this slice put child_frame on canvas.readback, that ``[0]`` would have
    flipped to an INDEPENDENT seed-derived vector and silently changed what
    those three tests test, from four files away. This pins the selector so the
    hazard is caught HERE, loudly, rather than there, quietly.
    """
    matches = [p for p in probes.PROBES if set(p.realms) == set(probes.ALL_REALMS)]

    assert [p.id for p in matches] == ["realm.frameIdentity"]
    assert matches[0].variance == probes.SHARED


def test_a_snapshot_recorded_before_this_realm_existed_still_compares():
    """AC6. Every existing snapshot stays loadable and comparable.

    An older artifact simply has no child_frame key. The comparator walks the
    INVENTORY rather than the intersection of the two files, so the new pair
    must surface as INCONCLUSIVE (evidence we do not have) rather than crash or
    vanish — and the four old pairs must still answer exactly as they did.
    """
    legacy = snapshot.build_snapshot(
        {probes.WINDOW: {VECTOR: {"value": {"digest": 111}}}},
        engine="chromium",
        profile="ps232-legacy",
        realms=(probes.WINDOW,),
    )
    assert probes.CHILD_FRAME not in legacy["probes"]

    other = snapshot.build_snapshot(
        {probes.WINDOW: {VECTOR: {"value": {"digest": 222}}}},
        engine="chromium",
        profile="ps232-other",
        realms=(probes.WINDOW,),
    )

    entries = diff.compare_profiles(legacy, other)

    entry = next(
        e
        for e in entries
        if e["realm"] == probes.CHILD_FRAME and e["probe_id"] == VECTOR
    )
    assert entry["status"] == diff.INCONCLUSIVE


# --- AC3 + AC4: the reading comes from a genuinely ENTERED child realm --------


@requires_chromium
@pytest.mark.timeout(_BROWSER_TEST_TIMEOUT_S)
def test_the_must_differ_vector_is_read_in_a_child_realm_entered_by_indexed_access():
    """AC3, non-waivable, asserted BEHAVIOURALLY and in BOTH halves.

    ``HTMLIFrameElement.prototype.contentWindow`` is replaced with a counting
    getter that still delegates, so the page behaves identically and the only
    observable difference is whether the accessor was consulted. The run must
    come back with:

    * a genuine reading of the must-differ vector from the child realm, AND
    * a ``contentWindow`` hit count of zero.

    Neither half alone is evidence — a zero count is equally satisfied by a
    harness that never reached the frame — which is why both are asserted
    together. This is the discrimination PS-193 turned on: a checker reaching a
    frame by index is invisible to an accessor hook, so a harness that could
    only see the accessor path returned a clean reading over a live defect.
    """
    evaluate, sink = _evaluate(
        preamble=_COUNT_CONTENT_WINDOW,
        extra="{contentWindowHits: __cwHits}",
    )

    results = runner.run_probes(evaluate, (probes.CHILD_FRAME,))
    entry = results[probes.CHILD_FRAME][VECTOR]

    # Half one: the must-differ vector really was READ, in that realm.
    assert "error" not in entry, f"the child realm was not read: {entry}"
    reading = entry["value"]
    assert reading is not None, (
        "the vector read null in the child realm. On an INDEPENDENT probe a "
        "null is not a reading — it makes two profiles compare EQUAL and "
        "manufactures a false collision on every pair."
    )
    assert isinstance(reading["digest"], int)
    assert reading["bytes"] > 0

    # Half two: and the frame was not reached through the accessor.
    assert sink, "the tripwire never reported"
    hits = [s.get("contentWindowHits") for s in sink]
    assert hits == [0] * len(hits), (
        "the child realm was reached through the contentWindow accessor "
        f"({hits} invocations). That is the PS-193 blind spot: a checker using "
        "indexed access would bypass any hook on that path."
    )


@requires_chromium
@pytest.mark.timeout(_BROWSER_TEST_TIMEOUT_S)
def test_the_child_realm_reading_of_the_must_differ_vector_is_deterministic():
    """AC4. Two records of one profile must be byte-identical in this realm too.

    ``probes.py:13-17`` refuses an unstable probe outright — *"an unstable probe
    is worse than no probe, because it makes a real difference unreadable"* —
    and on the must-differ axis instability is worse still: it would report two
    recordings of the SAME profile as distinguishable, and a real collision as
    noise. This is the new realm declaration's entrance exam.
    """
    evaluate, _ = _evaluate()

    first = runner.run_probes(evaluate, (probes.CHILD_FRAME,))
    second = runner.run_probes(evaluate, (probes.CHILD_FRAME,))

    a, b = first[probes.CHILD_FRAME][VECTOR], second[probes.CHILD_FRAME][VECTOR]
    assert "error" not in a and "error" not in b
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True), (
        f"the vector is not stable in the child realm: {a} != {b}"
    )


@requires_chromium
@pytest.mark.timeout(_BROWSER_TEST_TIMEOUT_S)
def test_the_gate_reports_a_real_child_realm_collision_end_to_end():
    """AC1 + AC3 together, with the readings taken from a REAL browser.

    Everything above either drives a browser or drives the comparator; this
    joins them, so the gate is exercised on evidence that actually came out of
    an entered child realm rather than out of a literal. Two snapshots are built
    from the same machine under two profile NAMES — so the child-realm readings
    genuinely agree — and the gate must call that agreement what it is.

    Note what this does NOT claim: agreement here is expected (one machine, one
    seed), so this is a test of the GATE's reporting, not evidence of a live
    leak. Honest bound #5 of the ticket: the claim is that the gate could not
    ask, not that profiles are linkable there.
    """
    evaluate, _ = _evaluate()
    results = runner.run_probes(evaluate, (probes.WINDOW, probes.CHILD_FRAME))

    child_entry = results[probes.CHILD_FRAME][VECTOR]
    assert "error" not in child_entry, child_entry
    assert child_entry["value"] is not None

    def snap(profile):
        return snapshot.build_snapshot(
            results,
            engine="chromium",
            profile=profile,
            realms=(probes.WINDOW, probes.CHILD_FRAME),
        )

    entries = diff.compare_profiles(snap("ps232-live-a"), snap("ps232-live-b"))

    child = [
        e
        for e in entries
        if e["realm"] == probes.CHILD_FRAME and e["probe_id"] == VECTOR
    ]
    assert child, (
        "the gate stayed silent over two profiles carrying an identical, "
        "REAL child-realm reading of a must-differ vector"
    )
    assert child[0]["status"] == diff.COLLIDING
    assert child[0]["value"] == child_entry["value"]
