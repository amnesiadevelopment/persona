"""The two RESIDUE probes can now be asked of the child-frame realm (PS-247).

WHAT WAS WRONG, stated as the harness saw it rather than as a story:

``realm.bootMarkers`` and ``realm.seedRecoverable`` are the two records in the
inventory that ask *"what did the masking mechanism LEAVE BEHIND in this
realm?"* — the first reports any own property of the global whose name betrays
persona's machinery, the second reports any own property that is, or
transitively stringifies to, something carrying the profile seed. Both declared
``(window, worker)`` and neither could be asked of the child realm at all.

Measured at ``18e30eb`` before this slice, by executing the inventory rather
than grepping it::

    mechanism probes (realm.* + masking.*) : 12
    BLIND to child_frame                   : 11
    seeing child_frame                     :  1   (realm.frameIdentity)

    realm coverage across all 50 probes    : window 49 / worker 36 / child_frame 2
    child_frame declarers : ['realm.frameIdentity', 'webgl.readback.childFrame']

WHY THIS REALM, AND WHY NOW. PS-215 started installing a spoof leaf into child
frames reached by INDEXED access (``self[N]`` — the reach CreepJS uses, which
never invokes ``HTMLIFrameElement.prototype.contentWindow``). So a leaf is now
*delivered* into a realm, and the two probes that ask what an installation
leaves behind cannot read it. ``realm.seedRecoverable``'s own comment states
the mechanism verbatim: the seed is compiled *inside* each masking leaf on
purpose *"so that stringifying the leaf carries it across a realm boundary —
which is exactly why a readable reference to a leaf, or to its source,
published the identity."* This project has paid for that shape six times —
PS-48, PS-42, PS-56, PS-93, PS-139, PS-68 — every one in the WINDOW realm, and
every one found only because a probe read that realm.

WHAT IS **NOT** CLAIMED, and the bound matters more than the result:

*   **No leak is claimed to exist in the child realm.** The readings measured
    below come back clean. The claim this file closes is that the harness
    **could not ask** — the identical structural hole PS-232 closed on the
    must-differ axis, one axis over on the mechanism/residue axis.
*   **This ships no protection.** Nothing a page observes changes and no spoof
    moves. It widens what the witness can see.
*   A green run of this file on Linux does **not** establish that a real
    persona launch leaves the child realm clean. It establishes that the
    question is now askable, and that the two expressions answer it cleanly and
    deterministically in a genuinely entered child realm.

WHAT THIS FILE ASSERTS, and the rule it will not bend: **on the entries a real
``run_probes`` RETURNS.** Never on source text, never that a helper was called,
never that an id appears in a list. A test that grepped ``probes.py`` for
``child_frame`` would pass over records that are never evaluated anywhere.

THE BROWSER TESTS REUSE PS-210's CAPABILITY GATE rather than re-deriving one.
``shutil.which("chromium") is not None`` asks whether a FILE EXISTS; the hosted
runner has a ``/usr/bin/chromium`` that then renders nothing. ``requires_chromium``
(imported below) asks the honest question by rendering a trivial page. Chromium
is provisioned nowhere in CI, deliberately, so those skip there and the
non-browser tests carry the regression weight.
"""

from __future__ import annotations

import json

import pytest

from src.services.verify import probes, runner

from tests.test_verify_child_frame_realm import (  # noqa: F401  (fixture import)
    _evaluate,
    requires_chromium,
    usable_chromium,
)

BOOT_WINDOW = "realm.bootMarkers"
SEED_WINDOW = "realm.seedRecoverable"
BOOT_CHILD = "realm.bootMarkers.childFrame"
SEED_CHILD = "realm.seedRecoverable.childFrame"

# The twin pairs, as (pre-existing record id, child-realm twin id, expression
# constant). One table so every structural assertion below covers BOTH twins
# and neither can be quietly dropped.
_TWINS = (
    (BOOT_WINDOW, BOOT_CHILD, "_JS_BOOT_MARKERS"),
    (SEED_WINDOW, SEED_CHILD, "_JS_SEED_RECOVERABLE"),
)


def _probe(probe_id: str) -> probes.Probe:
    return next(p for p in probes.PROBES if p.id == probe_id)


# --- AC1 + AC7: readings that came back FROM the child realm ------------------


@requires_chromium
def test_run_probes_returns_residue_readings_from_inside_the_child_realm():
    """AC1. The child realm carries a bootMarkers AND a seedRecoverable reading.

    Asserted on the entries a real ``run_probes`` produces for
    ``probes.CHILD_FRAME`` — not on source text, not on a helper having run,
    not on an id appearing in a list. The realm is entered by the shipped
    ``child_frame_expression``, so a harness that never got into the frame
    cannot manufacture these entries: it would produce ``error`` keys, which is
    exactly what ``test_a_child_realm_that_cannot_be_entered_is_recorded_as_an_error``
    pins one file over.

    The SHAPE is asserted too, not merely presence. An entry whose value is not
    the record's documented shape is a reading of something else, and
    ``{"markerCount": 0}`` alone would be satisfied by a probe that never
    enumerated anything.
    """
    evaluate, _ = _evaluate()

    results = runner.run_probes(evaluate, (probes.CHILD_FRAME,))
    entries = results[probes.CHILD_FRAME]

    boot = entries[BOOT_CHILD]
    seed = entries[SEED_CHILD]
    assert "error" not in boot, boot
    assert "error" not in seed, seed

    assert sorted(boot["value"]) == ["markerCount", "markers"]
    assert isinstance(boot["value"]["markers"], list)
    assert boot["value"]["markerCount"] == len(boot["value"]["markers"])

    assert sorted(seed["value"]) == ["candidateCount", "candidates"]
    assert isinstance(seed["value"]["candidates"], list)
    assert seed["value"]["candidateCount"] == len(seed["value"]["candidates"])


@requires_chromium
def test_the_residue_readings_are_deterministic_in_the_child_realm():
    """AC7. Two records of the same realm are byte-identical.

    ``probes.py``'s stated contract is a hard one — *"two records of the same
    live profile must produce byte-identical snapshots"* — and *"an unstable
    probe is worse than no probe, because it makes a real difference
    unreadable."* PS-232 measured exactly this for its own record before
    declaring it; a record that read non-deterministically in this realm would
    be a finding to report, not a result to ship.

    Two independent page loads, so this is a real second entry into a real
    second child realm rather than the same reading read twice.
    """
    readings = []
    for _ in range(2):
        evaluate, _ = _evaluate()
        entries = runner.run_probes(evaluate, (probes.CHILD_FRAME,))[
            probes.CHILD_FRAME
        ]
        readings.append(
            json.dumps(
                {BOOT_CHILD: entries[BOOT_CHILD], SEED_CHILD: entries[SEED_CHILD]},
                sort_keys=True,
            )
        )

    assert readings[0] == readings[1], (
        "the residue vectors did not read identically across two records of "
        f"the child realm:\n{readings[0]}\n{readings[1]}"
    )


@requires_chromium
def test_the_child_realm_residue_readings_are_clean_and_the_probe_could_say_otherwise():
    """AC7's substance, WITH the control that makes it non-vacuous.

    The clean reading on its own is worth very little: an expression that
    enumerated nothing, or that threw and was swallowed, also returns an empty
    list. So this plants a marker and a seed-shaped value on the child realm's
    OWN global first, confirms both probes REPORT them, and only then asserts
    that the untouched realm reads clean.

    The plant runs inside the child realm via the preamble's hook on frame
    insertion, so nothing here mutates the parent realm's global.
    """
    # A page preamble that decorates every child realm as it is created. The
    # probes run AFTER the frame is appended and reached, so a property defined
    # on the child's global here is visible to them.
    plant = (
        "var __origAppend = Node.prototype.appendChild;\n"
        "Node.prototype.appendChild = function(node){\n"
        "  var r = __origAppend.call(this, node);\n"
        "  try{\n"
        "    if (node && node.tagName === 'IFRAME' && node.contentWindow) {\n"
        "      var w = node.contentWindow;\n"
        "      w.__pnaPlantedMarker = 1;\n"
        "      w.__plantedSeedHolder = new w.Function("
        "'return 9876543210;');\n"
        "    }\n"
        "  }catch(e){}\n"
        "  return r;\n"
        "};\n"
    )

    evaluate, _ = _evaluate(preamble=plant)
    planted = runner.run_probes(evaluate, (probes.CHILD_FRAME,))[probes.CHILD_FRAME]

    boot = planted[BOOT_CHILD]
    seed = planted[SEED_CHILD]
    assert "error" not in boot, boot
    assert "error" not in seed, seed
    assert "__pnaPlantedMarker" in boot["value"]["markers"], (
        "the bootMarkers twin did not report a marker planted on the CHILD "
        f"realm's global — it is not reading that realm: {boot}"
    )
    assert "__plantedSeedHolder" in seed["value"]["candidates"], (
        "the seedRecoverable twin did not report a seed-shaped value planted "
        f"on the CHILD realm's global — it is not reading that realm: {seed}"
    )

    # ...and with nothing planted, the realm reads clean. This is a MEASUREMENT
    # of today's engine, not a protection this slice ships: a non-empty list
    # here would be a finding to report, not a failure of these records.
    evaluate, _ = _evaluate()
    clean = runner.run_probes(evaluate, (probes.CHILD_FRAME,))[probes.CHILD_FRAME]

    assert clean[BOOT_CHILD]["value"]["markers"] == [], clean[BOOT_CHILD]
    assert clean[SEED_CHILD]["value"]["candidates"] == [], clean[SEED_CHILD]


# --- AC5: ONE definition of each expression, shared by IDENTITY ---------------


@pytest.mark.parametrize("window_id,child_id,constant", _TWINS)
def test_each_twin_pair_shares_one_expression_so_they_cannot_drift(
    window_id, child_id, constant
):
    """AC5, following ``test_the_two_records_share_one_expression_so_they_cannot_drift``.

    A duplicated EXPRESSION would be a second source of truth for the same
    vector, and the copies would eventually disagree about what they measure —
    at which point "the same vector in another realm" quietly stops being true
    and the cross-realm comparison means nothing. Identity, not equality: two
    hand-copied strings compare equal on the day they are written and that is
    precisely the state this asserts against.
    """
    window = _probe(window_id)
    child = _probe(child_id)
    shared = getattr(probes, constant)

    assert child.expr is shared
    assert window.expr is shared
    assert child.expr is window.expr
    assert child.id != window.id, "shared expression, separate declarations"


# --- AC3 + AC6: what must NOT have moved --------------------------------------


def test_the_window_and_worker_inventories_did_not_move():
    """AC3. The two realms the committed baseline records are untouched.

    The COUNTS guard child-realm additions from leaking into window/worker; the
    LOOP below is the assertion that actually names AC3, and it is unaffected by
    anything outside the child realm. It is also the reason both new records
    declare ``CHILD_FRAME_ONLY`` rather than ``(WINDOW, CHILD_FRAME)`` — the
    latter would add their ids to ``probes_for_realm("window")`` and trip that
    guard for readings the window realm already has under the pre-existing ids.

    PS-314 re-points the counts (49 -> 52, 36 -> 38) and that is this guard
    working rather than being worked around. The distinction is the DIRECTION of
    the addition: PS-247's twins are child-realm records that must NOT touch
    window/worker, whereas PS-314 deliberately adds window/worker probes (the
    own-property SHAPE axis, which no existing probe could read) — so unlike a
    child-realm slice it re-records the committed baseline in the same change.
    The sentence above about needing no re-record is therefore specific to a
    CHILD-REALM addition; it was never a claim that this file's numbers are
    frozen. Window gains three (``masking.shapeWebglGetParameter``,
    ``masking.shapeGetChannelData``, ``masking.shapeScreenWidthAccessor``) and
    worker two — the accessor probe is WINDOW_ONLY, a worker having no
    ``screen``. The identical counts live in
    ``tests/test_ps232_child_frame_unlinkability.py`` and move together.
    """
    assert len(probes.probes_for_realm(probes.WINDOW)) == 52
    assert len(probes.probes_for_realm(probes.WORKER)) == 38

    for _window_id, child_id, _constant in _TWINS:
        for realm in (probes.WINDOW, probes.WORKER):
            assert child_id not in {p.id for p in probes.probes_for_realm(realm)}
        assert child_id in {p.id for p in probes.probes_for_realm(probes.CHILD_FRAME)}


def test_the_pre_existing_residue_records_are_untouched():
    """AC3. Neither original record silently gained a realm or changed shape.

    The whole point of "the new realm arrives as a NEW record" is that no
    existing vector starts being evaluated somewhere it was never validated.
    """
    for window_id, _child_id, _constant in _TWINS:
        record = _probe(window_id)
        assert record.realms == probes.BOTH
        assert record.variance == probes.SHARED


def test_both_new_records_are_shared_and_the_must_differ_axis_is_unchanged():
    """AC6. No new pair enters the unlinkability walk.

    ``diff.py`` records that ``masking.*`` and ``realm.*`` observe the
    MECHANISM rather than the identity and should AGREE across profiles.
    Classifying either twin ``INDEPENDENT`` would put a vector on the
    must-differ axis whose healthy reading is an empty list in both profiles —
    which compares EQUAL and would report every pair of profiles as COLLIDING.
    That is the same hazard ``realm.frameIdentity`` documents for frame
    position, and it is why these are SHARED.

    Asserted on ``must_differ_probes()`` — the list ``compare_profiles``
    actually builds its work from — rather than on the records' fields.
    """
    for _window_id, child_id, _constant in _TWINS:
        assert _probe(child_id).variance == probes.SHARED

    assert [p.id for p in probes.must_differ_probes()] == [
        "webgl.readback",
        "audio.digest",
        "canvas.readback",
        "webgl.readback.childFrame",
    ]


def test_the_all_realms_selectors_still_resolve_to_the_shared_probe():
    """AC3's fourth-file consequence, pinned rather than hoped for.

    Three tests in test_verify_snapshot.py select
    ``[p for p in PROBES if set(p.realms) == set(ALL_REALMS)][0]`` and name the
    result ``shared``. A new record declaring ``EVERY_REALM`` would flip that
    ``[0]`` and silently change what those tests test, from four files away.

    A SECOND selector is pinned by PLACEMENT rather than by shape:
    ``test_verify_child_frame_realm._frame_identity_probe`` takes
    ``[p for p in PROBES if CHILD_FRAME in p.realms][0]``, so a child-realm
    record declared EARLIER in the inventory than ``realm.frameIdentity`` would
    capture it and rebind those tests onto a residue vector. Both new records
    sit at the END of the inventory, after ``webgl.readback.childFrame``, which
    is why that selector still resolves where PS-210 left it.
    """
    matches = [p for p in probes.PROBES if set(p.realms) == set(probes.ALL_REALMS)]
    assert [p.id for p in matches] == ["realm.frameIdentity"]

    child_declarers = [p for p in probes.PROBES if probes.CHILD_FRAME in p.realms]
    assert child_declarers[0].id == "realm.frameIdentity"


# --- AC8: falsification -------------------------------------------------------


def _child_entries_for_inventory(inventory, monkeypatch) -> dict:
    """The child realm's entry map that a real ``run_probes`` RETURNS for
    ``inventory``, with a stub evaluator standing in for the browser.

    ⚠️ THE SUBSTITUTION IS THE POINT. ``run_probes`` -> ``run_child_frame_realm``
    -> ``probes_for_realm`` all read the module-level ``probes.PROBES``, so
    swapping that binding is what makes a *reverted inventory* reach the real
    code path instead of being inspected beside it. Reconstructing the pre-fix
    id set as a comprehension and asserting on it would be an assertion that an
    id appears in a LIST — one of the two forms this file's header rules out,
    and it would stay green if every record in the inventory were unevaluated.

    The evaluator is a stub rather than a browser so the falsification runs in
    CI, where chromium is deliberately absent. It answers for exactly the
    probes the realm asks it about, so the entry set comes from the inventory
    walk under test and not from anything this helper decided.
    """
    monkeypatch.setattr(probes, "PROBES", inventory)

    def stub(_expression):
        return {p.id: {"v": {}} for p in probes.probes_for_realm(probes.CHILD_FRAME)}

    return runner.run_probes(stub, (probes.CHILD_FRAME,))[probes.CHILD_FRAME]


def test_without_the_new_records_the_child_realm_has_no_residue_reading(monkeypatch):
    """AC8. Revert only the two records and the AC1 assertions go RED.

    Reconstructs the pre-fix inventory — the two twins removed, nothing else
    touched — and drives the same ``run_probes`` path AC1 uses against a stub
    evaluator. The realm ANSWERS, and the two residue entries are simply ABSENT
    from the answer: exactly the gap this slice closes, stated as an executable
    fact rather than as a claim about the past.

    ⚠️ THE ASSERTION IS ON AN ENTRY MAP ``run_probes`` RETURNED, and it has to
    be, for the reason this file's header states and the reason the round-2
    finding on ``_REALM_NATIVE_IDS`` memorialises one file over: *a guard's
    proof must fail when the guard goes soft, which it can only do by reading
    the guard itself.* The same holds here — a falsification proof must fail
    when the RECORDS go away, which it can only do by driving the path those
    records feed. An earlier version of this test read the pre-fix id set out
    of a comprehension and asserted on that; it passed with ``run_probes``
    sabotaged to raise on call, which means it was not falsifying anything and
    its docstring said otherwise. Chromium is absent from CI by design, so this
    test is the whole of AC8's automated weight and cannot be the one test in
    the file that breaks the file's rule.

    Uses a stub rather than a browser deliberately, so the falsification runs
    in CI where no chromium exists. What it establishes is the INVENTORY-driven
    half — the realm's entry set is built from ``probes_for_realm`` — which is
    the half a reverted record actually changes.
    """
    live = probes.PROBES
    reverted = tuple(p for p in live if p.id not in (BOOT_CHILD, SEED_CHILD))
    assert len(reverted) == len(live) - 2, "premise: exactly two removed"

    # PRE-FIX: run_probes replies for the realm, and the reply does not carry
    # the residue readings. An entry map, not an id list.
    pre_fix = _child_entries_for_inventory(reverted, monkeypatch)
    assert set(pre_fix) == {"realm.frameIdentity", "webgl.readback.childFrame"}, (
        "premise: before this slice run_probes answered the child realm with "
        "exactly the two records PS-210/PS-232 gave it, got "
        f"{sorted(pre_fix)}"
    )
    assert BOOT_CHILD not in pre_fix, (
        "the reverted inventory still produced a bootMarkers reading for the "
        "child realm — this test is not reverting what it claims to"
    )
    assert SEED_CHILD not in pre_fix

    # POST-FIX: the same path, the LIVE inventory, and the readings are there.
    #
    # ⚠️ `live` is captured ABOVE, before the first patch. `monkeypatch` restores
    # at TEARDOWN, not between calls, so reading `probes.PROBES` here would read
    # the reverted tuple still installed by the call above and this assertion
    # would fail against its own setup. (It did, on the first draft of this
    # test — kept as a comment because the failure looked like a defect in the
    # records rather than in the harness.)
    post_fix = _child_entries_for_inventory(live, monkeypatch)
    assert {BOOT_CHILD, SEED_CHILD} <= set(post_fix), (
        "the live inventory did not produce the residue readings through the "
        f"run_probes path AC1 uses, got {sorted(post_fix)}"
    )

    # ADDITIONAL, and deliberately not load-bearing for AC8: the reach is
    # unchanged in kind. These are substring assertions on generated source and
    # cannot falsify a missing RECORD — they are here because the reach is what
    # makes the realm the right one to read, not because they prove the revert.
    expression = runner.child_frame_expression(
        probes.probes_for_realm(probes.CHILD_FRAME)
    )
    assert "self[idx]" in expression
    assert "contentWindow" not in expression


def test_an_unreachable_child_realm_errors_the_new_records_too():
    """A realm that cannot be entered must ERROR them, never read clean.

    The PS-21 rule, and the one that makes AC7's clean reading mean anything:
    an ABSENT reading compares as agreement downstream, so a harness that
    failed to enter the frame must not produce ``{"markers": []}`` — the
    healthy reading — for a realm it never saw. This is inherited behaviour
    from ``run_probes``, asserted here on the NEW ids because inheriting it is
    the property, not the implementation.
    """

    def evaluate(_expression):
        return {"__harness_error": "TypeError: this realm has no document"}

    entries = runner.run_probes(evaluate, (probes.CHILD_FRAME,))[probes.CHILD_FRAME]

    for probe_id in (BOOT_CHILD, SEED_CHILD):
        assert "error" in entries[probe_id]
        assert "value" not in entries[probe_id]
