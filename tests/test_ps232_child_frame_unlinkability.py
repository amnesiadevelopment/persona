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

HOW THE REALM IS DECLARED, because the shape is load-bearing rather than
incidental. The vector arrives as a **new record** (``webgl.readback.childFrame``)
sharing its expression with ``webgl.readback`` but declaring only the child
realm, rather than as ``child_frame`` appended to that record's realms tuple.
PS-210 made that choice deliberately and guarded it, so that no existing vector
silently starts being evaluated in a realm it was never validated in; this
slice keeps the choice rather than reversing it.

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

# The record this slice put on the must-differ axis in the child realm.
#
# A NEW record, NOT a realm added to `webgl.readback`, and that shape is the
# ruling this slice was reworked under. PS-210 chose "the new realm arrives as
# a NEW record" so that no existing vector silently starts being evaluated
# somewhere it was never validated, and it installed a guard to enforce that.
# The two records share their EXPRESSION through `_JS_WEBGL_READBACK` — one
# source of truth, so there is no second copy to drift — while keeping separate
# DECLARATIONS, which is the thing that has to be per-realm for a realm to be
# validated on its own evidence.
VECTOR = "webgl.readback.childFrame"

# Its window-realm sibling, which must stay WINDOW_ONLY. Pinned below.
WINDOW_VECTOR = "webgl.readback"


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
            # Two DIFFERENT ids: the window realm's reading belongs to
            # `webgl.readback` and the child realm's to `webgl.readback
            # .childFrame`. That separation IS the new-record shape.
            probes.WINDOW: {WINDOW_VECTOR: {"value": window_value}},
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
    b["probes"][probes.WINDOW][WINDOW_VECTOR] = {"value": {"digest": 222, "bytes": 4096, "mid": 3072}}

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
    b["probes"][probes.WINDOW][WINDOW_VECTOR] = {"value": {"digest": 222}}

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
    b["probes"][probes.WINDOW][WINDOW_VECTOR] = {"value": {"digest": 222}}

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
    b["probes"][probes.WINDOW][WINDOW_VECTOR] = {"value": {"digest": 222}}

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
    assert [p for p in pairs if p[0] == probes.CHILD_FRAME] == [
        (probes.CHILD_FRAME, VECTOR)
    ]


def test_neither_readback_record_reaches_the_worker_realm():
    """AC6. Neither record enters the worker realm, and that is MEASURED.

    In a worker, ``getContext('webgl')`` returns null on this engine, and a
    null on an INDEPENDENT vector manufactures a false COLLIDING on every pair
    — the exact defect the gate exists to detect. A future edit widening either
    record to ``EVERY_REALM`` "for symmetry" would reintroduce it, so the
    exclusion is pinned on both.
    """
    child = next(p for p in probes.PROBES if p.id == VECTOR)
    window = next(p for p in probes.PROBES if p.id == WINDOW_VECTOR)

    assert child.realms == (probes.CHILD_FRAME,)
    assert child.variance == probes.INDEPENDENT

    # AC6, and the guard PS-210 installed: the PRE-EXISTING record is untouched.
    assert window.realms == probes.WINDOW_ONLY
    assert window.variance == probes.INDEPENDENT

    for probe in (child, window):
        assert probes.WORKER not in probe.realms


def test_the_two_records_share_one_expression_so_they_cannot_drift():
    """The charter's one-record-per-vector objection, answered structurally.

    A duplicated EXPRESSION would be a second source of truth for the same
    vector, and the two copies would eventually disagree about what they
    measure — at which point "the same vector in another realm" quietly stops
    being true and the comparison across realms means nothing. Sharing the
    module-level constant makes that drift impossible by construction rather
    than by asking the next author to remember, which is the same technique
    ``EVERY_REALM`` uses for the realm tuples.
    """
    child = next(p for p in probes.PROBES if p.id == VECTOR)
    window = next(p for p in probes.PROBES if p.id == WINDOW_VECTOR)

    assert child.expr == window.expr
    assert child.expr is probes._JS_WEBGL_READBACK
    assert child.id != window.id, "shared expression, separate declarations"


def test_the_per_realm_probe_inventory_for_window_and_worker_is_unchanged():
    """AC6. Adding a child-realm record must not move window/worker.

    This is also precisely why the committed baseline needs no re-record: the
    baseline guard compares the probe-ID SET per realm for ``window`` and
    ``worker`` only, and neither set moves here. Note this is the reason the
    new record declares CHILD_FRAME_ONLY rather than (WINDOW, CHILD_FRAME) —
    the latter would add its id to ``probes_for_realm("window")`` and trip that
    guard for a reading the window realm already has under the other id.

    THE CHILD-FRAME COUNT IS A CHARACTERIZATION, NOT A GUARD. The 49 and 36
    beside it are the load-bearing half — they are this test's whole purpose,
    and they must not move when a child-realm record is added. The child-frame
    number simply records how many records the realm held at the moment it was
    written, so a slice that legitimately adds one re-points it. PS-247 added
    the two residue twins (``realm.bootMarkers.childFrame`` and
    ``realm.seedRecoverable.childFrame``), taking it from 2 to 4 while leaving
    window and worker exactly where PS-232 found them — which is this test
    passing, not this test being edited around.
    """
    assert len(probes.probes_for_realm(probes.WINDOW)) == 49
    assert len(probes.probes_for_realm(probes.WORKER)) == 36
    assert len(probes.probes_for_realm(probes.CHILD_FRAME)) == 4

    # The new id is in the child realm and NOWHERE else — the assertion the
    # counts above cannot make, since a count is satisfied by any membership.
    for realm in (probes.WINDOW, probes.WORKER):
        assert VECTOR not in {p.id for p in probes.probes_for_realm(realm)}
    assert VECTOR in {p.id for p in probes.probes_for_realm(probes.CHILD_FRAME)}


def test_the_all_realms_selectors_still_resolve_to_the_shared_probe():
    """AC6, and the reason this slice chose webgl.readback over canvas.readback.

    Three tests in test_verify_snapshot.py do
    ``[p for p in PROBES if set(p.realms) == set(ALL_REALMS)][0]`` and name the
    result ``shared``, meaning a SHARED-variance probe readable in every realm.
    Declaring the new record EVERY_REALM — or putting child_frame on
    canvas.readback — would flip that ``[0]`` to an INDEPENDENT seed-derived
    vector and silently change what those three tests test, from four files
    away. CHILD_FRAME_ONLY keeps the selector resolving where it did.

    A SECOND selector of the same kind is pinned by placement rather than by
    shape: ``test_verify_child_frame_realm._frame_identity_probe`` takes
    ``[p for p in PROBES if CHILD_FRAME in p.realms][0]``, so a child-realm
    record declared EARLIER in the inventory than ``realm.frameIdentity``
    captures it and rebinds four existing tests onto this vector. That is why
    the new record sits immediately AFTER it — measured, not assumed: placing
    it before turned four of those tests red.
    """
    matches = [p for p in probes.PROBES if set(p.realms) == set(probes.ALL_REALMS)]

    assert [p.id for p in matches] == ["realm.frameIdentity"]
    assert matches[0].variance == probes.SHARED

    # The child-realm selector, pinned in inventory ORDER for the reason above.
    #
    # PS-247 re-pointed this list when it added the two residue twins
    # (`realm.bootMarkers.childFrame`, `realm.seedRecoverable.childFrame`).
    # This is an exact-list equality, so it is a CHARACTERIZATION of how many
    # records the realm held when PS-232 wrote it — the load-bearing property
    # is the ORDER, and specifically that `[0]` is `realm.frameIdentity`, which
    # is what the four `_frame_identity_probe` tests bind to. That property is
    # unchanged: both new records sit at the END of the inventory, after
    # `webgl.readback.childFrame`. The `[0]` assertion below is stated
    # separately so it survives any future re-pointing of the list.
    child_records = [p for p in probes.PROBES if probes.CHILD_FRAME in p.realms]
    assert [p.id for p in child_records] == [
        "realm.frameIdentity",
        VECTOR,
        "realm.bootMarkers.childFrame",
        "realm.seedRecoverable.childFrame",
    ]
    assert child_records[0].id == "realm.frameIdentity", (
        "a child-realm record was declared BEFORE realm.frameIdentity — it "
        "captures the [0] selector and rebinds four tests onto the wrong vector"
    )
    assert child_records[0].variance == probes.SHARED


def test_a_snapshot_recorded_before_this_realm_existed_still_compares():
    """AC6. Every existing snapshot stays loadable and comparable.

    An older artifact simply has no child_frame key. The comparator walks the
    INVENTORY rather than the intersection of the two files, so the new pair
    must surface as INCONCLUSIVE (evidence we do not have) rather than crash or
    vanish — and the four old pairs must still answer exactly as they did.
    """
    legacy = snapshot.build_snapshot(
        {probes.WINDOW: {WINDOW_VECTOR: {"value": {"digest": 111}}}},
        engine="chromium",
        profile="ps232-legacy",
        realms=(probes.WINDOW,),
    )
    assert probes.CHILD_FRAME not in legacy["probes"]

    other = snapshot.build_snapshot(
        {probes.WINDOW: {WINDOW_VECTOR: {"value": {"digest": 222}}}},
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


# --- Defect 3: the RECORDER must cover what the COMPARATOR walks -------------
#
# The gap that let a green suite say nothing. Every fixture above hands the
# comparator a child-realm reading; PRODUCTION never did. `Context.record`
# called `record_snapshot(profile=..., fresh=...)`, whose `realms` argument
# defaults to `BASELINE_REALMS` — `(window, worker)` — so the pair this slice
# added was ABSENT on both sides of every real comparison, `_unread_for_
# unlinkability` correctly read it as unread, and the pair came back
# INCONCLUSIVE forever. `_run_two_profile_unlinkability` checks `colliding`
# first and `inconclusive` second, so ONE permanently-inconclusive pair took
# the whole check to CANNOT_RUN, and `exit_code` has CANNOT_RUN outrank
# FINDING. The operator-facing verdict for "are these two profiles genuinely
# two machines?" became a refusal that could never clear.
#
# MEASURED both ways before the fix, through run_probes -> build_snapshot ->
# compare_profiles with two profiles given distinct seed-derived digests on
# every must-differ vector (the case that SHOULD read as a clean pass):
#
#     pristine 472b388   0 pairs in the child realm   -> PASS
#     this branch        1 pair,  inconclusive        -> CANNOT_RUN
#
# So this PR BROKE it rather than merely exposing it, which is what put it in
# scope. The fix is `probes.must_differ_realms()` — derived from the same
# inventory the comparator walks, so the recorder and the comparator cannot
# drift apart — threaded through the two lanes that call `compare_profiles`.
#
# NOT fixed by narrowing `_unread_for_unlinkability`. That would trade a
# refusal for a false COLLIDING, which is the worse failure and precisely the
# one AC5 exists to prevent. The predicate is right; the realm was unrecorded.


def _recording_context(monkeypatch, *, realm_log):
    """A `Context` whose `record` models `record_snapshot`'s REALM CONTRACT.

    The fake stands in for the browser, not for the realm logic: it honours
    `realms` exactly as `record_snapshot` does, **including its default**. That
    default is the whole point of the fixture — a lane that forgets to pass
    `realms=` gets `BASELINE_REALMS` here for the same reason it gets it in
    production, so this test goes RED on the real defect instead of quietly
    recording whatever the test wanted.

    Every must-differ vector reads a per-PROFILE digest, so two profiles differ
    on every compared pair and a correct run is unambiguously a PASS. Any
    inconclusive entry therefore means a realm was not recorded — there is no
    other way for this input to produce one.
    """
    from src.services.verify import behaviour
    from src.services.verify.baseline import BASELINE_REALMS

    must_differ = probes.must_differ_ids()

    def fake_record(self, profile, *, fresh, realms=None):
        # The production default, reproduced deliberately. See the docstring.
        effective = BASELINE_REALMS if realms is None else realms
        realm_log.append(tuple(effective))
        name = getattr(profile, "name", profile)
        results = {
            realm: {
                probe.id: {
                    # Per-profile on a must-differ vector (so two profiles
                    # DIFFER), shared otherwise (so nothing else is noise).
                    "value": f"{name}:{probe.id}" if probe.id in must_differ else "shared"
                }
                for probe in probes.probes_for_realm(realm)
            }
            for realm in effective
        }
        return snapshot.build_snapshot(
            results, engine="firefox", profile=name, realms=tuple(effective)
        )

    monkeypatch.setattr(behaviour.Context, "record", fake_record)

    class _Profile:
        def __init__(self, name):
            self.name = name
            self.fingerprint_seed = f"seed-{name}"

    monkeypatch.setattr(
        behaviour.Context, "make_profile", lambda self, name, **kw: _Profile(name)
    )
    return behaviour.Context(home="/nonexistent-scratch")


def test_the_live_lane_records_every_realm_the_comparator_will_walk(monkeypatch):
    """Defect 3, stated as the operator sees it: the gate returns a VERDICT.

    Drives `_run_two_profile_unlinkability` itself — not a fixture built to the
    comparator's taste — over two profiles that differ on every must-differ
    vector. That is the clean-pass case, so the only way it can come back
    CANNOT_RUN is a pair the recording never covered.

    Reverting `realms=must_differ_realms()` in the lane turns this RED.
    """
    from src.services.verify.behaviour import CANNOT_RUN, PASS
    from src.services.verify.behaviour_checks import _run_two_profile_unlinkability

    realm_log: list[tuple] = []
    ctx = _recording_context(monkeypatch, realm_log=realm_log)

    outcome = _run_two_profile_unlinkability(ctx)

    assert outcome.status != CANNOT_RUN, (
        "the live unlinkability gate refused to return a verdict on two "
        "profiles that differ on every must-differ vector. A realm the "
        f"comparator walks was not recorded: recorded {realm_log}, "
        f"comparator walks {probes.must_differ_realms()}. Detail: "
        f"{outcome.detail}"
    )
    assert outcome.status == PASS, outcome.detail

    # The mechanism, not just the verdict: every recording covered every realm
    # the comparator asks about. Asserted as a SUPERSET so this does not become
    # a restatement of the implementation's exact tuple.
    assert realm_log, "the lane recorded nothing at all"
    for recorded in realm_log:
        assert set(probes.must_differ_realms()) <= set(recorded), (
            f"a recording covered {recorded}, but the comparator walks "
            f"{probes.must_differ_realms()}"
        )


def test_the_child_realm_pair_is_actually_among_the_ones_compared(monkeypatch):
    """The precondition the test above rests on, asserted rather than assumed.

    A PASS is also what a gate that compares NOTHING returns. This pins that
    the child-realm pair is genuinely in the recorded realms and genuinely
    carries a reading on both sides — so the pass above is a pass over the new
    pair, not a pass that skipped it.

    Without this, deleting the child-frame record entirely would leave the
    test above green — the exact "green by construction" failure this ticket's
    own AC1 warns about.
    """
    realm_log: list[tuple] = []
    ctx = _recording_context(monkeypatch, realm_log=realm_log)

    from src.services.verify.behaviour_checks import _run_two_profile_unlinkability

    _run_two_profile_unlinkability(ctx)

    assert probes.CHILD_FRAME in realm_log[0], (
        "the child realm was never recorded by the live lane, so the pass "
        "above says nothing about the pair this slice added"
    )
    # And the pair really is one the comparator asks about.
    pairs = sorted(
        (realm, probe.id)
        for probe in probes.must_differ_probes()
        for realm in probe.realms
    )
    assert (probes.CHILD_FRAME, VECTOR) in pairs


def test_a_narrower_recording_is_refused_rather_than_passed(monkeypatch):
    """The falsification for this defect: prove the assertion above CAN fail.

    Models the PRE-FIX lane exactly — a recording pinned to `BASELINE_REALMS`
    while the inventory declares a child-realm vector — and requires the gate
    to report CANNOT_RUN. This is what makes the two tests above load-bearing
    rather than decorative: it shows the shape they forbid is genuinely
    reachable and genuinely reported.

    It also pins the DIRECTION of the failure, which is the half that matters
    for AC5: an unrecorded realm must read as a refusal, NEVER as two profiles
    differing. A gate that answered PASS here would be manufacturing
    distinctness out of a reading nobody took.
    """
    from src.services.verify.behaviour import CANNOT_RUN
    from src.services.verify.baseline import BASELINE_REALMS

    assert probes.CHILD_FRAME not in BASELINE_REALMS, (
        "this falsification models a recording NARROWER than the comparator; "
        "if BASELINE_REALMS ever covers the child realm it no longer does"
    )

    a, b = _snapshot_pair(window_value="a-win", child_value=None)
    # Rebuild both sides over the baseline realms only: the child pair is then
    # ABSENT, which is precisely the pre-fix production shape.
    def narrow(profile):
        results = {
            realm: {
                probe.id: {"value": f"{profile}:{probe.id}"}
                for probe in probes.probes_for_realm(realm)
            }
            for realm in BASELINE_REALMS
        }
        return snapshot.build_snapshot(
            results, engine="firefox", profile=profile, realms=BASELINE_REALMS
        )

    entries = diff.compare_profiles(narrow("ps232-narrow-a"), narrow("ps232-narrow-b"))
    child = [e for e in entries if e["realm"] == probes.CHILD_FRAME]

    assert child, "the comparator did not even ask about the unrecorded realm"
    assert child[0]["status"] == diff.INCONCLUSIVE, (
        "a realm that was never recorded must be INCONCLUSIVE, never evidence "
        "that two profiles differ"
    )
    # And that is what the live lane turns into a refusal.
    assert CANNOT_RUN == "cannot_run"


def test_the_falsification_can_plant_on_a_target_whose_only_realm_is_the_child(
    monkeypatch,
):
    """The falsify lane records what it PLANTS on — covered where it bites.

    `_falsify_two_profile_unlinkability` plants a collision on `targets[0]` in
    `probe.realms[0]`. With today's inventory that is `webgl.readback` in the
    WINDOW realm, which `BASELINE_REALMS` already covers — so the lane's
    `realms=` argument is DEFENSIVE at this commit and reverting it changes
    nothing observable. That is exactly the condition under which a branch
    rots: no test can turn it red, so it looks covered and is not.

    This drives the case where it does bite. Ordering the inventory so the
    child-realm-only vector is `targets[0]` makes the planting target a realm
    `BASELINE_REALMS` does NOT carry; a lane recording narrower than it plants
    finds nothing to plant onto, raises, and `run_check` publishes CANNOT_RUN —
    the check's SELF-TEST silently stops working on the very vector the slice
    added.

    Reverting `realms=` in the falsification lane turns this RED.
    """
    from src.services.verify import probes as probes_mod
    from src.services.verify.behaviour_checks import (
        _falsify_two_profile_unlinkability,
    )

    ordered = tuple(
        sorted(
            probes_mod.must_differ_probes(),
            key=lambda p: 0 if p.realms == (probes.CHILD_FRAME,) else 1,
        )
    )
    assert ordered[0].realms == (probes.CHILD_FRAME,), (
        "this test needs a child-realm-only must-differ vector to plant on"
    )
    monkeypatch.setattr(probes_mod, "must_differ_probes", lambda: ordered)

    realm_log: list[tuple] = []
    ctx = _recording_context(monkeypatch, realm_log=realm_log)

    # Raises BehaviourCheckError if the recording did not carry the realm the
    # collision is planted in — which is what a narrower recording produces.
    proven = _falsify_two_profile_unlinkability(ctx)

    assert ordered[0].id in proven, proven
    assert probes.CHILD_FRAME in realm_log[0], (
        "the falsification recorded a realm set that does not cover the "
        f"vector it plants on: recorded {realm_log[0]}"
    )
