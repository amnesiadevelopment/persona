"""PS-316: the child realm's residue probes get a COMPARATOR.

THE DEFECT, in one sentence: PS-247/PS-210 shipped three probes that only
exist in the child realm, and the committed baseline artifact did not record
that realm — so those probes produced a reading every run that no comparator
ever read.

That is worse than an absent probe and it is the reason this slice exists. An
absent probe is visibly absent. A WRITE-ONLY probe reports a clean number, the
suite is green, and the artifact looks like it covers the realm — so a residue
regression in a child frame is reported by nothing, and the state
"we looked and found nothing" is byte-indistinguishable from "we never looked".

MEASURED BEFORE THE CHANGE, planting a `__pna` marker and diffing through the
real `diff_snapshots` against the committed artifact:

    window      /realm.bootMarkers            -> 1 changed   CAUGHT
    worker      /realm.bootMarkers            -> 1 changed   CAUGHT
    child_frame /realm.bootMarkers.childFrame -> realm absent, NOT DEFENDED

The two positive controls FIRE, so the child-realm zero is the defect and not
a comparator that is simply quiet. Those controls are reproduced here as
`test_the_same_residue_planted_in_the_original_realms_is_still_caught`, which
is the half that stops a "fix" that widens recording while breaking
comparison: widen the artifact and break `diff_snapshots`, and AC1's tests
below would still go green on a comparison that no longer compares anything.

⛔ WHAT THIS SLICE IS NOT. It ships no fix and closes no leak. The residue
probes PASS today — every marker count in the committed artifact is zero and
the live child readings were measured clean. Nothing a page observes changes.
This makes an existing rule OBSERVABLE; it does not change the rule.
"""

from __future__ import annotations

import copy
import json
import pathlib

import pytest

from src.services.verify import probes
from src.services.verify.diff import CHANGED, diff_snapshots

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The residue witnesses this slice gives a comparator, and the realm each one
#: lives in. Listed as (realm, probe_id) pairs rather than derived from the
#: inventory, because the CLAIM is "these specific probes were write-only and
#: now are not" — a derived list would follow the inventory wherever it went
#: and could never go stale, which is the same tautology trap AC6's guard has.
RESIDUE_WITNESSES = (
    (probes.CHILD_FRAME, "realm.bootMarkers.childFrame"),
    (probes.CHILD_FRAME, "realm.seedRecoverable.childFrame"),
)

#: The ORIGINAL-realm twins of the first witness above. These are the positive
#: controls: they were already defended, and they must STAY defended.
ORIGINAL_TWINS = (
    ("window", "realm.bootMarkers"),
    ("worker", "realm.bootMarkers"),
)

#: What a residue regression LOOKS like. `probes.py` says of
#: `realm.bootMarkers.childFrame` that "a non-empty `markers` list here is the
#: regression", so the planted value is a non-empty list rather than an
#: arbitrary mutation — the test plants the real failure shape, not a token.
PLANTED_RESIDUE = {"markerCount": 1, "markers": ["__pna"]}


def _artifact() -> dict:
    path = REPO_ROOT / "tests" / "fixtures" / "engine-fingerprint-baseline.firefox.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _with_residue(snapshot: dict, realm: str, probe_id: str) -> dict:
    """A copy of ``snapshot`` with residue planted in one (realm, probe).

    Plants into a snapshot that ALREADY carries the probe, and asserts so: a
    plant that silently CREATED the entry would manufacture an `added` diff
    rather than a `changed` one, which is a different verdict reached for a
    different reason and would let this suite pass over the very absence it
    exists to detect.
    """
    out = copy.deepcopy(snapshot)
    assert probe_id in out["probes"].get(realm, {}), (
        f"{realm}/{probe_id} is not in the snapshot being planted into, so "
        "this would test an ADDED probe rather than a CHANGED reading"
    )
    out["probes"][realm][probe_id] = {"value": PLANTED_RESIDUE}
    return out


def _entries_for(before: dict, after: dict, realm: str, probe_id: str) -> list[dict]:
    return [
        e
        for e in diff_snapshots(before, after)
        if e["realm"] == realm and e["probe_id"] == probe_id
    ]


# --- AC1: the child realm's witnesses now have a comparator -----------------


@pytest.mark.parametrize(
    "realm,probe_id", RESIDUE_WITNESSES, ids=[p for _, p in RESIDUE_WITNESSES]
)
def test_residue_planted_in_the_child_realm_is_reported_as_changed(realm, probe_id):
    """AC1. Fails before this slice, passes after — for the RIGHT reason.

    Before the widening the committed artifact carried no `child_frame` key at
    all, so this could not even plant: the probe it needs to mutate was not
    there. That is the defect stated as a test rather than as prose.
    """
    committed = _artifact()
    planted = _with_residue(committed, realm, probe_id)

    entries = _entries_for(committed, planted, realm, probe_id)

    assert entries, (
        f"{realm}/{probe_id} carries planted residue and the comparator said "
        "NOTHING. The reading is write-only: it is produced every run and no "
        "comparison reads it, so a regression here is reported by nobody."
    )
    assert entries[0]["status"] == CHANGED, (
        f"{realm}/{probe_id} must be reported CHANGED — an `added`/`removed` "
        "verdict would mean the realm's inventory moved, which is a different "
        f"finding: got {entries[0]['status']}"
    )
    assert entries[0]["observed"]["value"] == PLANTED_RESIDUE


def test_the_committed_artifact_actually_carries_the_child_realm():
    """The precondition every test in this file rests on, asserted separately.

    Kept apart from the tests above so a two-realm artifact fails HERE, with a
    message naming the re-record, instead of failing four tests with a
    `KeyError` that reads like a broken test rather than a stale artifact.
    """
    snap = _artifact()

    assert probes.CHILD_FRAME in snap["probes"], (
        "the committed artifact has no child_frame realm, so its three residue "
        "probes have nothing to be compared against. Re-record: "
        "xvfb-run -a python -m src.services.verify.baseline_cli record"
    )
    recorded = set(snap["probes"][probes.CHILD_FRAME])
    live = {p.id for p in probes.probes_for_realm(probes.CHILD_FRAME)}
    assert recorded == live, (
        f"the artifact's child realm and the live inventory disagree: "
        f"missing {sorted(live - recorded)}, stale {sorted(recorded - live)}"
    )
    # And provenance must SAY so, or a reader cannot tell a three-realm
    # artifact from a two-realm one without counting keys.
    assert probes.CHILD_FRAME in snap["provenance"]["realms"]
    assert snap["provenance"]["realms"] == snap["realms"]


# --- AC2: the positive controls, which are what make AC1 mean anything ------


@pytest.mark.parametrize(
    "realm,probe_id", ORIGINAL_TWINS, ids=[r for r, _ in ORIGINAL_TWINS]
)
def test_the_same_residue_planted_in_the_original_realms_is_still_caught(
    realm, probe_id
):
    """AC2. The control against a fix that widens RECORDING and breaks
    COMPARISON.

    These two fired before this slice and must still fire after it. Without
    them, AC1's tests could be satisfied by a change that grew the artifact
    while quietly breaking `diff_snapshots` — every test above would go green
    on a comparator that no longer compares anything, which is the same
    write-only failure one level up.
    """
    committed = _artifact()
    planted = _with_residue(committed, realm, probe_id)

    entries = _entries_for(committed, planted, realm, probe_id)

    assert len(entries) == 1, (
        f"the positive control for {realm}/{probe_id} stopped firing — the "
        "comparator itself is broken, so this slice's child-realm evidence "
        "says nothing"
    )
    assert entries[0]["status"] == CHANGED


# --- the falsification: what the PRE-FIX comparator said --------------------


def test_the_pre_fix_narrow_artifact_reports_child_residue_as_NOTHING():
    """⛔ THE FALSIFICATION, and without it AC1 above proves very little.

    Drives the REAL `diff_snapshots` over the pre-fix artifact SHAPE — the
    committed document with its child realm removed — carrying residue that is
    genuinely there, and requires it to report NO entry for that probe.

    That silence is the whole defect, and it is worth being precise about why
    it is worse than an error: the pre-fix comparator does not warn, does not
    report inconclusive, and does not say the realm is missing. It says exactly
    what it says for a perfectly clean browser. The indistinguishability IS the
    defect.

    ⚠️ NOTE THE ASYMMETRY WITH `diff_realms`, which is a different question and
    gives a different answer: that comparator DOES emit one INCONCLUSIVE entry
    for `realm.frameIdentity` (the only probe declaring both window and child),
    because it compares realms within one snapshot. The two CHILD_FRAME_ONLY
    witnesses are absent from even that, and `diff_snapshots` — the comparator
    `check` actually uses — is silent about all three. Do not read one
    comparator's inconclusive as cover for the other's silence.
    """
    narrow = _artifact()
    narrow["probes"].pop(probes.CHILD_FRAME, None)
    narrow["realms"] = [r for r in narrow["realms"] if r != probes.CHILD_FRAME]

    realm, probe_id = RESIDUE_WITNESSES[0]

    # The residue is genuinely present in the observed reading. Constructed
    # from the INVENTORY rather than from the artifact, so this falsification
    # models the pre-fix shape whether or not the committed artifact carries
    # the realm today — it must keep proving the defect was real after the
    # artifact has been re-recorded.
    observed = _artifact()
    observed["probes"].setdefault(probes.CHILD_FRAME, {})
    observed["probes"][probes.CHILD_FRAME][probe_id] = {"value": PLANTED_RESIDUE}
    # ...but the pre-fix BASELINE cannot see it, because it has no child realm
    # to compare against. Restrict the observed side the same way the pre-fix
    # recorder would have: it never entered the realm either.
    observed_narrow = copy.deepcopy(observed)
    observed_narrow["probes"].pop(probes.CHILD_FRAME, None)
    observed_narrow["realms"] = list(narrow["realms"])

    entries = _entries_for(narrow, observed_narrow, realm, probe_id)

    assert not entries, (
        "the pre-fix shape reported something about the child realm — then the "
        "defect this slice fixes was not what it was measured to be, and AC1's "
        "evidence needs re-deriving"
    )
    # And the control: the SAME comparison over the SAME two documents does
    # still speak about the original realms, so the silence above is about the
    # missing realm and not about a comparator that failed to run at all.
    observed_narrow["probes"]["window"]["realm.bootMarkers"] = {
        "value": PLANTED_RESIDUE
    }
    control = _entries_for(narrow, observed_narrow, "window", "realm.bootMarkers")
    assert control and control[0]["status"] == CHANGED, (
        "the comparator said nothing about the WINDOW realm either, so the "
        "silence above is a dead comparison rather than a missing realm"
    )


def test_the_witnesses_are_genuinely_child_realm_only():
    """The premise behind calling them write-only: they exist NOWHERE else.

    If either witness also declared the window realm, the committed artifact
    would already have been comparing that reading and the defect would be
    narrower than stated. Asserted rather than assumed, because it is the
    difference between "three probes had no comparator" and "three probes had
    a comparator in another realm".
    """
    for realm, probe_id in RESIDUE_WITNESSES:
        declaring = {
            p.realms for p in probes.PROBES if p.id == probe_id
        }
        assert declaring, f"{probe_id} is not in the inventory at all"
        assert declaring == {(probes.CHILD_FRAME,)}, (
            f"{probe_id} declares {declaring}, not CHILD_FRAME_ONLY — it was "
            "readable in another realm, so it was never write-only"
        )
        assert realm == probes.CHILD_FRAME
