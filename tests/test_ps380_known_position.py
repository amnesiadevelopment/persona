"""Pin the KNOWN-POSITION structure PS-380 added, and the rules that keep it
from rotting into a waiver list.

WHAT WAS BROKEN. `two-profile-unlinkability` is the only check anywhere that
observes **Level 2 of the bar (mutual unlinkability)**, and the one venue that
could run it — the launch lane — excluded it WHOLE-CHECK. The exclusion's
reasoning was correct and is unchanged: the check reports a FINDING on the
firefox engine this project ships, because canvas 2D is not spoofed there
(`readings/ps135-2026-08-24/EVIDENCE.md` §8 predicts it verbatim and §7.3 hands
it to PS-2), so requiring it whole would have made that gate permanently red.

But the check compares FIVE must-differ pairs and only TWO collide. Measured
live at PS-380 on the lane's own engine (firefox-20, Firefox 151.0 build
20260817150018), two runs of two fresh profiles each::

    child_frame/webgl.readback.childFrame   DIFFERS
    window/audio.digest                     DIFFERS
    window/canvas.readback                   COLLIDING   digest 4242351214
    window/webgl.readback                    DIFFERS
    worker/canvas.readback                   COLLIDING   digest 4242351214

So a whole-check exclusion taken to avoid ONE known red also stopped anyone
watching `window/webgl.readback`, `window/audio.digest` and
`child_frame/webgl.readback.childFrame` — which are exactly the vectors persona
ships Firefox spoofs for (`_install_spoof("webgl", ...)`,
`_install_spoof("audio", ...)`; canvas has no firefox arm). If either spoof
silently stopped being installed, two profiles would share a WebGL readback and
an audio digest and nothing in CI would have said so.

⛔ WHAT THIS FILE IS FOR. A per-vector exclusion is a DANGEROUS affordance: it
is one refactor away from being a waiver list, and the project has a recorded
stance against exactly that (`ci.yml`: *"NO ALLOWLIST IS CONFIGURED, ON ANY
PLATFORM, AND THAT IS DELIBERATE … a floor becomes permanent"*). The four rules
argued at `behaviour.KNOWN_POSITIONS` are what keep the two apart, and this file
is where each one is enforced rather than merely written down:

  1. SHRINK-ONLY — a pair may enter the set only by coming OUT of a check-level
     omission that already existed. A green check may not buy itself a pin.
  2. EXCLUDED VISIBLY, NEVER FORGIVEN — the pair is removed before a verdict
     exists and is named in the outcome and the report on every run. No verdict
     is adjudicated down, so the towards-2 asymmetry is untouched.
  3. THE PIN IS A READING — its only power is to remove an EXACTLY-MATCHING
     recorded collision. A different collision on a pinned pair is a finding,
     an unread one is still CANNOT_RUN, and an unrecognised engine build is
     pinned by nothing at all. A pair that STOPPED colliding rejoins the
     comparison and the dead pin is reported loudly — NOT a finding, because
     that would turn the day PS-2 ships its fix into a red lane.
  4. EVERY ENTRY CITES A FILE AND A VERBATIM QUOTE, re-read at its source, on
     the model of `test_engine_masking_matrix.py`'s RECORDED_REASON_SOURCES.

⚠️ NOTHING HERE LAUNCHES A BROWSER. The live five-pair reading above was taken
by hand under xvfb and is recorded in the PR and in the runner's header; these
tests drive the split and the check body over substituted comparisons, which is
how the SHAPE is pinned without spending 16 browser launches per suite run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.verify import behaviour, behaviour_checks
from src.services.verify.behaviour import (
    CANNOT_RUN,
    FINDING,
    KNOWN_POSITIONS,
    PASS,
    KnownPosition,
    known_positions_for,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The build every committed firefox recording of the collided digest was taken
#: on, and the build the lane provisions (`engine-baseline.txt`).
BUILD = "firefox-20"

#: The collided digest, bit-identical across every committed firefox-20
#: recording: `readings/ps135-2026-08-24/reading.firefox.seed{111,1337,4242}.json`
#: and `readings/ps290-2026-09-03/artifacts/*/fingerprint-before.json`.
CANVAS_DIGEST = 4242351214

#: The SAME profile on a LATER build. The collided value MOVES with the engine
#: (`readings/ps290-2026-09-03/artifacts/*/fingerprint-after.json`), which is
#: why a pin carries a build and why an unrecognised build is pinned by nothing.
CANVAS_DIGEST_FIREFOX_21 = 2735004646


def _entry(realm: str, probe_id: str, *, status: str, digest=None) -> dict:
    """One `compare_profiles`-shaped entry, in the shape the real one emits."""
    reading = {"value": {"bytes": 8192, "digest": digest, "mid": 6144}}
    entry = {
        "probe_id": probe_id,
        "realm": realm,
        "status": status,
        "expected": reading,
        "observed": reading,
    }
    if status == "colliding":
        entry["value"] = reading["value"]
    return entry


def _colliding_canvas(digest=CANVAS_DIGEST) -> list[dict]:
    return [
        _entry("window", "canvas.readback", status="colliding", digest=digest),
        _entry("worker", "canvas.readback", status="colliding", digest=digest),
    ]


# --- rule 4: every entry cites a file and a verbatim quote -------------------


def test_the_set_is_not_empty_and_every_entry_is_fully_specified() -> None:
    """A pin with a missing field is a pin nobody can audit.

    Each field answers a question a reader of a green report will ask: WHICH
    pair, at WHAT reading, on WHICH build, WHO owns the fix, and WHERE the
    decision is written down. An entry missing any of them is an unexplained
    absence wearing a reason's clothes.
    """
    assert KNOWN_POSITIONS, (
        "the known-position set is empty, so this whole file is testing "
        "nothing. If the canvas collision was genuinely fixed, the check now "
        "gates all five pairs and these tests should have been deleted with "
        "the entries — not left passing over an empty tuple."
    )

    for kp in KNOWN_POSITIONS:
        assert isinstance(kp, KnownPosition)
        assert kp.realm and kp.probe_id, f"{kp!r} names no pair"
        assert kp.digest is not None, (
            f"{kp.pair} pins no reading, so it pins a VECTOR NAME — which makes "
            "any collision on that pair acceptable forever. Rule 3."
        )
        assert kp.build, (
            f"{kp.pair} pins no build, but the collided value moves with the "
            f"engine ({CANVAS_DIGEST} on firefox-20, "
            f"{CANVAS_DIGEST_FIREFOX_21} on firefox-21/25/26)."
        )
        assert kp.owner and len(kp.owner) > 10, (
            f"{kp.pair} names no owner, so the debt belongs to nobody and will "
            "never be paid down."
        )
        assert kp.reason_path and kp.reason_quote, (
            f"{kp.pair} cites no recorded reason. Rule 4."
        )


def test_every_recorded_reason_is_still_in_the_tree() -> None:
    """⭐ THE QUOTE IS RE-READ AT ITS SOURCE, not trusted.

    `test_engine_masking_matrix.py::test_recorded_reasons_still_in_tree`'s rule,
    applied to this structure: a pin rests on a published reading, and if that
    reading is deleted or reworded the pin silently degrades into an
    unexplained exclusion — the exact confusion this mechanism exists to avoid.

    Compared with whitespace COLLAPSED on both sides, deliberately, for the same
    reason that test gives: the quotes are prose in wrapped markdown, so a
    re-wrap moves newlines without changing a word, and a test that went red on
    a re-flow would train its reader to re-quote reflexively. A WORD change
    still fails.
    """
    for kp in KNOWN_POSITIONS:
        source = REPO_ROOT / kp.reason_path
        assert source.is_file(), (
            f"{kp.pair} cites {kp.reason_path}, which does not exist. The "
            "recorded reason is the only justification for excluding this pair."
        )
        text = " ".join(source.read_text(encoding="utf-8").split())
        quote = " ".join(kp.reason_quote.split())
        assert quote in text, (
            f"{kp.pair}'s recorded reason is no longer in {kp.reason_path}:\n"
            f"  quoted: {quote!r}\n"
            "Either the reading was reworded (re-quote it, deliberately) or the "
            "decision was withdrawn — in which case DELETE the pin rather than "
            "keeping an exclusion whose premise is gone."
        )


def test_the_pinned_digest_is_the_one_the_corpus_recorded() -> None:
    """The pin must match the committed evidence, not a number someone typed.

    Re-derived from the reading files themselves rather than asserted against a
    literal, so a pin that drifted from the corpus is caught here instead of
    being discovered on a live run.
    """
    import json

    readings = sorted(
        (REPO_ROOT / "readings" / "ps135-2026-08-24").glob("reading.firefox.*.json")
    )
    assert readings, "the ps135 firefox corpus is missing; the pin rests on it"

    observed: set = set()
    for path in readings:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if snapshot.get("engine_build") != BUILD:
            continue
        for kp in KNOWN_POSITIONS:
            if kp.build != BUILD:
                continue
            entry = snapshot.get("probes", {}).get(kp.realm, {}).get(kp.probe_id)
            assert entry, (
                f"{kp.pair} is pinned on {BUILD} but the corpus recording "
                f"{path.name} does not carry it"
            )
            observed.add(entry["value"]["digest"])

    assert observed == {CANVAS_DIGEST}, (
        f"the {BUILD} corpus reads {sorted(observed)} for the pinned pairs, not "
        f"{CANVAS_DIGEST}"
    )
    for kp in KNOWN_POSITIONS:
        if kp.build == BUILD:
            assert kp.digest == CANVAS_DIGEST, (
                f"{kp.pair} pins {kp.digest!r}, which is not what the corpus "
                f"recorded on {BUILD} ({CANVAS_DIGEST})"
            )


# --- rule 1: shrink-only ----------------------------------------------------


def test_every_pin_came_out_of_a_recorded_check_level_omission() -> None:
    """⛔ RULE 1, AND IT IS THE GUARD AGAINST THIS BECOMING A HABIT.

    A pair may enter `KNOWN_POSITIONS` ONLY by moving OUT of a check-level
    omission that already existed — never by moving out of a passing lane. The
    structure exists to make a whole-check carve-out SMALLER; an entry that did
    not come from one is a NEW permission to hide, which is precisely what the
    `ci.yml` anti-allowlist stance refuses and what the noise-source rule warns
    a transplanted tolerance mechanism turns into.

    ⭐ ENFORCED PER-PAIR, BECAUSE THE RULE IS STATED PER-PAIR — and round 1 got
    this wrong in a way worth recording. It resolved each pinned pair to its
    OWNING CHECK and asserted that CHECK's name was in `DOCUMENTED_OMISSIONS` ∪
    `RETIRED_OMISSIONS`. `two-profile-unlinkability` is permanently in the
    retired set, so after this slice EVERY future pin on EVERY must-differ pair
    satisfied it for free: measured, a pin added on `window/audio.digest` — a
    DEFENDED vector measured VARYING — left that assertion PASSING. The rule's
    precondition had become unsatisfiable and its test could no longer fail,
    while the comment and the PR both presented it as live.

    So a pinned pair must be in the covered set its check's retired omission
    RECORDED (`RETIRED_OMISSION_PAIRS`), or the check must still be inside a
    live `DOCUMENTED_OMISSIONS` carve-out (nothing has shrunk yet, so a pin is
    a shrink by construction). A pair from neither is a new hole.

    ⚠️ THE COVERED SET IS NOT TRUSTED AS A LITERAL either — widening it by hand
    would re-open exactly this door one level down. It is re-derived from the
    committed corpus by
    `test_the_retired_omission_covers_exactly_the_pairs_its_evidence_recorded`.
    """
    from tests.test_ps336_launch_behaviour_venue import (
        DOCUMENTED_OMISSIONS,
        RETIRED_OMISSION_PAIRS,
        RETIRED_OMISSIONS,
    )

    pinned_pairs = {kp.pair for kp in KNOWN_POSITIONS}
    owners: dict[str, str] = {}
    for check in behaviour_checks.CHECKS:
        gated = _pairs_gated_by(check)
        for pair in pinned_pairs & gated:
            owners[pair] = check.name

    unowned = pinned_pairs - set(owners)
    assert not unowned, (
        f"{sorted(unowned)} is pinned but no check gates it, so the pin applies "
        "to nothing — either the inventory moved or the pin is dead"
    )

    for pair in sorted(pinned_pairs):
        name = owners[pair]
        if name in DOCUMENTED_OMISSIONS:
            # Still inside a live whole-check carve-out: a per-pair pin is a
            # shrink by construction, because the whole check is excluded today.
            continue
        assert name in RETIRED_OMISSIONS, (
            f"{name!r} gates the pinned pair {pair!r} but has NEVER been a "
            "documented check-level omission. Rule 1: a known position may "
            "only SHRINK an existing exclusion, never create one. This pin is "
            "carving a hole in a check that was being fully gated.\n"
            f"  pinned pairs: {sorted(pinned_pairs)}\n"
            f"  recorded omissions: live={sorted(DOCUMENTED_OMISSIONS)} "
            f"retired={sorted(RETIRED_OMISSIONS)}"
        )
        covered = RETIRED_OMISSION_PAIRS.get(name, frozenset())
        assert covered, (
            f"{name!r} is a retired omission but records no covered pairs, so "
            "rule 1 cannot be checked at the granularity it is stated at. Add "
            f"its entry to RETIRED_OMISSION_PAIRS."
        )
        assert pair in covered, (
            f"{pair!r} is pinned as a known position, but the retired "
            f"check-level omission on {name!r} did NOT rest on it — it rested "
            f"on {sorted(covered)}. Rule 1 is per-PAIR: a vector may only move "
            "OUT of an exclusion that actually covered it. This pin is a NEW "
            "permission to hide, wearing the retired omission's clothes.\n"
            f"  pinned pairs: {sorted(pinned_pairs)}"
        )


def test_the_retired_omission_covers_exactly_the_pairs_its_evidence_recorded() -> None:
    """⛔ THE COVERED SET IS A MEASUREMENT, NOT A LITERAL SOMEBODY WIDENED.

    `RETIRED_OMISSION_PAIRS` is what makes rule 1 enforceable per-pair, so a
    hand-edited entry there re-opens the same door one level down: add
    `window/audio.digest` to the covered set and a pin on a defended vector
    becomes admissible again, with every guard green.

    It is therefore re-derived here by running the REAL comparator over the
    committed firefox-20 corpus — the same evidence the omission's reason cites
    (PS-135 §8) — and set-equality is asserted in BOTH directions: a widened
    entry fails, and so does one that quietly dropped a pair it did cover.

    ⚠️ WHAT THIS DOES NOT CLAIM. The corpus is `('window', 'worker')` only —
    those readings were taken before PS-316 widened `BASELINE_REALMS`, and
    nothing re-recorded the corpus — so a pair in the child realm reads
    INCONCLUSIVE here and is correctly NOT in the covered set. That is the safe
    direction: an unmeasured pair cannot buy itself a pin. ⛔ AND IT STAYS THE
    SAFE DIRECTION NOW THAT THE BASELINE RECORDS THE REALM: the covered set is
    derived from THIS corpus, not from the baseline artifact, so widening the
    recorder did not silently admit a child-realm pair to it. Re-deriving the
    corpus on a three-realm recording is its own act, with its own review.
    """
    import itertools
    import json

    from src.services.verify.diff import compare_profiles
    from tests.test_ps336_launch_behaviour_venue import (
        RETIRED_OMISSION_PAIRS,
        RETIRED_OMISSION_PAIRS_EVIDENCE,
    )

    corpus = REPO_ROOT / RETIRED_OMISSION_PAIRS_EVIDENCE
    assert corpus.is_dir(), (
        f"{RETIRED_OMISSION_PAIRS_EVIDENCE} is gone, so the covered set rests "
        "on nothing. Re-derive it before trusting rule 1."
    )

    by_profile: dict[str, dict] = {}
    for path in sorted(corpus.glob("reading.firefox.*.json")):
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if snapshot.get("engine_build") != BUILD:
            continue
        by_profile.setdefault(snapshot.get("profile"), snapshot)

    assert len(by_profile) >= 2, (
        f"the {BUILD} corpus carries {len(by_profile)} distinct profile(s); a "
        "cross-profile collision cannot be derived from fewer than two"
    )

    # A pair counts as covered only if it collides in EVERY pairing — one
    # pairing's agreement is a coincidence, not a recorded position.
    derived: set[str] | None = None
    for a, b in itertools.combinations(sorted(by_profile), 2):
        colliding = {
            f"{e['realm']}/{e['probe_id']}"
            for e in compare_profiles(by_profile[a], by_profile[b])
            if e.get("status") == "colliding"
        }
        derived = colliding if derived is None else (derived & colliding)

    assert RETIRED_OMISSION_PAIRS["two-profile-unlinkability"] == derived, (
        "the recorded covered set does not match what the committed corpus "
        f"actually shows colliding on {BUILD}.\n"
        f"  recorded: {sorted(RETIRED_OMISSION_PAIRS['two-profile-unlinkability'])}\n"
        f"  corpus:   {sorted(derived or ())}\n"
        "A WIDENED set admits a pin on a vector the omission never rested on, "
        "which is rule 1 defeated one level down."
    )


def test_rule_1_REFUSES_a_pin_on_a_pair_the_omission_never_covered() -> None:
    """⭐ THE GUARD IS SHOWN CAPABLE OF FAILING — the round-1 defect, pinned.

    Round 1's rule-1 test could not fail for any value once
    `two-profile-unlinkability` was retired, and nothing said so. This drives
    the same assertion over a pin on `window/audio.digest` — a DEFENDED vector,
    measured VARYING on firefox-20, gated by the same check — and requires it
    to be REFUSED. If this test ever passes trivially, rule 1 has gone vacuous
    again.
    """
    from tests.test_ps336_launch_behaviour_venue import (
        DOCUMENTED_OMISSIONS,
        RETIRED_OMISSION_PAIRS,
        RETIRED_OMISSIONS,
    )

    intruder = KnownPosition(
        realm="window",
        probe_id="audio.digest",
        digest=1234567890,
        build=BUILD,
        owner="nobody — this pin is the attack",
        reason_path="readings/ps135-2026-08-24/EVIDENCE.md",
        reason_quote="two profiles agree, so the two-profile unlinkability check will",
    )

    owner = next(
        c.name
        for c in behaviour_checks.CHECKS
        if intruder.pair in _pairs_gated_by(c)
    )
    assert owner not in DOCUMENTED_OMISSIONS, (
        f"{owner!r} is back inside a live whole-check omission, so this attack "
        "is admitted for a legitimate reason and proves nothing. Re-derive it."
    )
    assert owner in RETIRED_OMISSIONS, (
        "the attack rests on the check being a RETIRED omission — the exact "
        "state that made round 1's check-granular assertion vacuous"
    )

    covered = RETIRED_OMISSION_PAIRS[owner]
    assert intruder.pair not in covered, (
        f"{intruder.pair!r} is now recorded as covered by {owner!r}'s retired "
        "omission, so rule 1 would ADMIT a pin on a vector persona ships a "
        "firefox spoof for. That is the hole, not the guard."
    )


def _pairs_gated_by(check) -> set[str]:
    """The (realm, probe) pairs a check's verdict is taken over.

    Only the cross-profile check has any, and it is identified by the inventory
    it compares rather than by its name, so renaming it does not quietly make
    this test vacuous.
    """
    from src.services.verify.probes import must_differ_probes

    if check.run is not behaviour_checks._run_two_profile_unlinkability:
        return set()
    return {f"{realm}/{probe.id}" for probe in must_differ_probes() for realm in probe.realms}


def test_a_pinned_pair_is_actually_in_the_inventory() -> None:
    """A pin on a pair nothing compares is a stale exclusion, silently inert.

    Worse than useless: it reads as an admitted gap that is still being carried,
    so nobody goes looking for the vector it names.
    """
    pairs = _pairs_gated_by(
        next(
            c
            for c in behaviour_checks.CHECKS
            if c.run is behaviour_checks._run_two_profile_unlinkability
        )
    )
    for kp in KNOWN_POSITIONS:
        assert kp.pair in pairs, (
            f"{kp.pair} is pinned but is not a must-differ pair, so the pin "
            f"excludes nothing. Known must-differ pairs: {sorted(pairs)}"
        )


def test_the_pins_do_not_cover_the_whole_inventory() -> None:
    """⭐ THE HARD FLOOR: the gate must keep gating SOMETHING.

    A known-position set grown to cover every must-differ pair is a check that
    compares nothing, and `compare_profiles`' contract reads an empty result as
    the PASS — a certificate of unlinkability nobody measured. The check's own
    falsification refuses that case at runtime (there is no live pair left to
    plant a collision on); this refuses it at the structure level, where it is
    cheaper to notice.
    """
    pairs = _pairs_gated_by(
        next(
            c
            for c in behaviour_checks.CHECKS
            if c.run is behaviour_checks._run_two_profile_unlinkability
        )
    )
    live = pairs - {kp.pair for kp in KNOWN_POSITIONS}
    assert live, (
        "every must-differ pair is a known position, so this check's verdict is "
        "taken over an empty comparison — which its comparator reads as a PASS."
    )
    assert len(live) >= len(KNOWN_POSITIONS), (
        "more pairs are pinned than are gated. That is not a bright line, but "
        "it is the point at which this stopped being a narrow exclusion:\n"
        f"  gated: {sorted(live)}\n"
        f"  pinned: {sorted(kp.pair for kp in KNOWN_POSITIONS)}"
    )


def test_the_vectors_persona_ships_firefox_spoofs_for_are_NOT_pinned() -> None:
    """⛔ THE WHOLE POINT OF THE SLICE, asserted so it cannot be undone quietly.

    `webgl.readback` and `audio.digest` are the two must-differ vectors persona
    installs Firefox spoofs for, and they are exactly what the whole-check
    exclusion un-watched. Pinning either would restore that blindness one pair
    at a time while the lane still reported itself as watching Level 2 — which
    is strictly worse than the whole-check exclusion, because the exclusion at
    least announced itself by the check's absence.
    """
    defended = {"webgl.readback", "audio.digest", "webgl.readback.childFrame"}
    pinned = {kp.probe_id for kp in KNOWN_POSITIONS}
    assert pinned.isdisjoint(defended), (
        f"{sorted(pinned & defended)} is pinned as a known position, but persona "
        "ships a Firefox spoof for it (invisible_launch.py's _install_spoof "
        "calls) and it was MEASURED VARYING on firefox-20 at PS-380. A pin "
        "there blinds a vector that works."
    )


def test_the_firefox_spoofs_the_unpinned_vectors_rest_on_are_still_installed() -> None:
    """The three unpinned pairs are only worth gating while the spoofs exist.

    Not a claim that they reach a page — that is what the live check is for —
    but the pins' ARGUMENT is "canvas has no firefox arm and these do", and if
    that stopped being true the argument would need re-making rather than
    inheriting.
    """
    source = (REPO_ROOT / "src/services/browser/invisible_launch.py").read_text(
        encoding="utf-8"
    )
    for vector in ("webgl", "audio"):
        assert f'_install_spoof("{vector}"' in source, (
            f"the firefox {vector} spoof is no longer installed, so the claim "
            "that this vector is actively defended (and therefore worth gating "
            "rather than pinning) no longer holds. Re-measure before trusting "
            "the pins' argument."
        )
    assert "firefox_canvas" not in source, (
        "a firefox canvas arm now exists, so the premise of the canvas pins "
        "(PS-135 §8: canvas 2D is not spoofed on firefox) may have expired. "
        "Re-measure; if the collision is gone, DELETE the pins."
    )


def test_a_firefox_canvas_arm_is_what_PROMPTS_deleting_the_pins() -> None:
    """⭐ THE PREMISE-EXPIRY PROMPT, AND IT IS A TEST RATHER THAN A RED LANE.

    The lane deliberately stays GREEN when a pinned collision vanishes: the
    pair rejoins the live comparison and gates normally, and failing on it
    would turn the day PS-2 ships its fix into a red — "permanently red is as
    bad as permanently green" arriving by the back door. That is a
    self-maintaining property traded away on purpose, so the prompt to delete
    a dead pin has to live somewhere that CAN fail. This is that somewhere.

    The pins' premise is one sentence: canvas 2D has no firefox spoof arm, so
    two profiles share its readback. The moment PS-2 installs one, that
    sentence is false and this test goes red naming the entries to delete —
    the same shape as `test_every_recorded_reason_is_still_in_the_tree`, which
    fails when a cited record is reworded away.

    ⚠️ ITS BOUND, STATED SO A GREEN HERE IS NOT OVER-READ. It watches OUR
    source. A collision that stops because the ENGINE changed underneath us
    leaves this test green, and is caught only by the split's
    `STALE PIN — DELETE IT` report line on a passing run. So this prompt covers
    the expected way the premise expires, not every way.
    """
    source = (REPO_ROOT / "src/services/browser/invisible_launch.py").read_text(
        encoding="utf-8"
    )
    canvas_pins = sorted(kp.pair for kp in KNOWN_POSITIONS if "canvas" in kp.probe_id)
    assert canvas_pins, (
        "no canvas pin is left, so this prompt guards nothing — if the pins "
        "were deleted, delete this test with them"
    )

    assert '_install_spoof("canvas"' not in source and "firefox_canvas" not in source, (
        "A FIREFOX CANVAS ARM NOW EXISTS. The premise the canvas known "
        "positions rest on (PS-135 §8: canvas 2D is not spoofed on firefox, so "
        "two profiles share its readback) has expired.\n"
        f"  DELETE these KNOWN_POSITIONS entries: {canvas_pins}\n"
        "  and their RETIRED_OMISSION_PAIRS covered-set entry with them.\n"
        "Then re-measure: if the collision is genuinely gone, the pairs gate "
        "normally and this lane watches all five. Do NOT re-pin them at a new "
        "digest to keep this green — that is rule 3 defeated by hand."
    )


# --- rule 3: the pin is a reading, not a vector name ------------------------


def test_a_matching_collision_is_excluded() -> None:
    """The base case: the recorded collision, on the recorded build."""
    live, excluded, stale = behaviour_checks._known_position_split(
        _colliding_canvas(), BUILD
    )

    assert live == [], f"a pinned pair reached the verdict: {live}"
    assert len(excluded) == 2
    assert stale == []
    assert all(e["known_position"].digest == CANVAS_DIGEST for e in excluded)


def test_a_DIFFERENT_collision_on_a_pinned_pair_is_a_FINDING() -> None:
    """⛔ FALSIFICATION 3. The pin is a READING, not a licence for a pair name.

    A pair that collides at a value nobody recorded is a NEW collision. Letting
    it through because the pair's NAME is on a list is exactly the allowlist
    failure `ci.yml` refuses: the mechanism would stop noticing the thing it was
    built around.
    """
    live, excluded, stale = behaviour_checks._known_position_split(
        _colliding_canvas(digest=999999999), BUILD
    )

    assert excluded == [], "a collision at an unrecorded digest was excluded"
    assert len(live) == 2, (
        "a collision the pin does not describe did not reach the verdict, so it "
        "was excused by the pair's NAME"
    )
    assert all(e["status"] == "colliding" for e in live)
    assert len(stale) == 2
    for note in stale:
        assert "999999999" in note
        assert str(CANVAS_DIGEST) in note


def test_a_pinned_pair_that_STOPPED_colliding_is_REPORTED_but_is_NOT_a_finding() -> None:
    """⛔ THE DAY PS-2 SHIPS ITS FIX, THIS LANE MUST STAY GREEN.

    A pin whose collision has been FIXED is an exclusion with no premise, and it
    blinds a vector that now works — so it has to be REPORTED, loudly, or it
    rots silently. `compare_profiles` is silent on a pair that was read and
    DIFFERED, so the absence of an entry is the only signal there is.

    But the report must not be a FINDING. An earlier draft made it one, which
    would have turned the lane red on the day the product got better, for a
    reason that is not a product defect — "permanently red is as bad as
    permanently green" arriving by the back door. Three existing tests caught
    it (`test_ps232_child_frame_unlinkability.py` and two in
    `test_canvas_readback_probe.py`, all of which drive this check over profiles
    that differ on every pair), and this is that lesson pinned so it cannot
    regress.
    """
    live, excluded, stale = behaviour_checks._known_position_split([], BUILD)

    assert excluded == []
    assert live == [], "there was nothing to compare; the split invented an entry"
    assert len(stale) == 2, (
        "a pinned pair that stopped colliding produced no report, so the dead "
        "pin would be carried forever and would blind a working vector"
    )
    for note in stale:
        assert "DIFFERED" in note
        assert "DELETE IT" in note
        assert "ps135-2026-08-24" in note, (
            "the report does not cite the premise that expired, so a reader "
            "cannot tell which decision to go and close"
        )


def test_an_INCONCLUSIVE_pinned_pair_is_not_a_known_anything() -> None:
    """An unobtained reading can neither agree nor differ, so it is not pinned.

    The module's standing rule, applied here: a reading nobody took is never a
    pass, and it is never a recorded collision either. Excluding it would hide
    a realm that failed to be entered behind a pin that describes a reading.
    """
    entries = [
        _entry("window", "canvas.readback", status="inconclusive"),
        _entry("worker", "canvas.readback", status="inconclusive"),
    ]
    live, excluded, stale = behaviour_checks._known_position_split(entries, BUILD)

    assert excluded == [], "an unread pair was excluded as a recorded collision"
    assert len(live) == 2, (
        "an unread pinned pair did not reach the verdict, so a realm that "
        "failed to be entered would hide behind a pin that describes a reading"
    )
    assert all(e["status"] == "inconclusive" for e in live)
    assert len(stale) == 2
    for note in stale:
        assert "could not READ it" in note


def test_an_UNRECOGNISED_BUILD_is_pinned_by_nothing() -> None:
    """⛔ THE BUILD AXIS IS HONOURED, NOT STATED AND IGNORED.

    The collided value MOVES with the engine — the same profile reads
    4242351214 on firefox-20 and 2735004646 on firefox-21/25/26 — so an entry
    recorded on one build says nothing about another. The safe direction for an
    unknown build is to MEASURE it, because a baseline that followed the engine
    wherever it went would be a waiver and not a pin.

    This is not hypothetical: `engine-baseline.txt` is bumped by
    engine-autoupdate.yml, so a later build WILL arrive on this lane without
    anyone editing the pins.
    """
    assert known_positions_for("firefox-21") == ()
    assert known_positions_for("firefox-26") == ()
    assert known_positions_for(None) == ()
    assert known_positions_for("") == ()

    entries = _colliding_canvas(digest=CANVAS_DIGEST_FIREFOX_21)
    live, excluded, stale = behaviour_checks._known_position_split(
        entries, "firefox-21"
    )
    assert excluded == [] and stale == []
    assert len(live) == 2, (
        "a collision on an UNRECOGNISED build was not passed through to the "
        "verdict, so a pin followed the engine to a build it was never measured "
        "on"
    )

    # And the same digest on the build it WAS recorded on is still excluded, so
    # the above is the build axis working rather than the pin being inert.
    assert len(behaviour_checks._known_position_split(_colliding_canvas(), BUILD)[1]) == 2


def test_two_pins_on_the_SAME_pair_and_build_are_REFUSED(monkeypatch) -> None:
    """A silent collapse in a structure whose whole value is legibility.

    `_known_position_split` keys its lookup on the pair, so two entries pinning
    the same pair on the same build would collapse to the LAST one: the first's
    digest could never match, and the entry would sit in `KNOWN_POSITIONS`
    being reported to a reader as if it were doing something. That is the
    quietest possible way for this set to stop meaning what it says.

    Cannot happen today (two entries, two pairs) and this is not a live defect
    — it is the failure mode named and made loud, because the alternative is a
    reader trusting a pin that is inert.
    """
    twin = KnownPosition(
        realm="window",
        probe_id="canvas.readback",
        digest=999_999_999,
        build=BUILD,
        owner="the duplicate",
        reason_path="readings/ps135-2026-08-24/EVIDENCE.md",
        reason_quote="two profiles agree, so the two-profile unlinkability check will",
    )
    monkeypatch.setattr(
        behaviour, "KNOWN_POSITIONS", KNOWN_POSITIONS + (twin,), raising=True
    )

    with pytest.raises(behaviour.BehaviourCheckError) as excinfo:
        behaviour_checks._known_position_split(_colliding_canvas(), BUILD)

    message = str(excinfo.value)
    assert twin.pair in message and str(twin.digest) in message, (
        "the refusal does not name the pair and the conflicting readings, so a "
        "reader cannot tell which entry to delete"
    )


def test_an_unpinned_collision_always_reaches_the_verdict() -> None:
    """⛔ FALSIFICATION 1, at the structural level.

    The whole deliverable: a collision on `window/webgl.readback` must reach
    the verdict. Today, with the check excluded whole from the lane, it reaches
    nothing.
    """
    entries = _colliding_canvas() + [
        _entry("window", "webgl.readback", status="colliding", digest=4242)
    ]
    live, excluded, stale = behaviour_checks._known_position_split(entries, BUILD)

    assert len(excluded) == 2
    assert stale == []
    assert [e["probe_id"] for e in live] == ["webgl.readback"], (
        "the known-position split swallowed a collision on an UNPINNED vector"
    )


# --- rule 2: excluded visibly, never forgiven -------------------------------


class _StubContext:
    """A Context whose recordings are supplied, so no browser is launched."""

    def __init__(self, entries, build=BUILD):
        self._entries = entries
        self._build = build
        self.launches = 0

    def make_profile(self, name, **kwargs):
        return type("P", (), {"name": name, "fingerprint_seed": f"seed-{name}"})()

    def record(self, profile, *, fresh=False, realms=None):
        self.launches += 1
        return {
            "engine": "firefox",
            "engine_build": self._build,
            "profile": profile.name,
            "probes": {"window": {"canvas.readback": {"value": {"digest": 1}}}},
        }


def _drive_check(monkeypatch, entries, build=BUILD):
    """Run the real check body with `compare_profiles` and the guards stubbed."""
    monkeypatch.setattr(behaviour_checks, "_readings_or_refuse", lambda *a, **k: None)
    monkeypatch.setattr(
        "src.services.verify.diff.compare_profiles",
        lambda a, b, **kw: list(entries),
    )
    return behaviour_checks._run_two_profile_unlinkability(_StubContext(entries, build))


def test_an_excluded_pair_is_NAMED_in_a_PASSING_outcome(monkeypatch) -> None:
    """⛔ RULE 2, AND THIS IS THE TEST THAT SEPARATES EXCLUDING FROM FORGIVING.

    ⚠️ FALSIFICATION 2. A clean tree must go GREEN with the canvas pair REPORTED
    as a known position rather than silently absent. A reader of this green must
    not be able to mistake it for "all five pairs differed": an excluded pair is
    visible in the report, which is the whole reason excluding is admissible
    where forgiving is not.
    """
    outcome = _drive_check(monkeypatch, _colliding_canvas())

    assert outcome.status == PASS, (
        f"a tree whose ONLY collisions are known positions did not pass: "
        f"{outcome.status} — {outcome.detail}"
    )
    assert "KNOWN POSITIONS EXCLUDED" in outcome.detail, (
        "the pass does not SAY that two pairs were excluded, so it reads as a "
        "clean five-pair green. That is forgiving wearing excluding's clothes."
    )
    assert "window/canvas.readback" in outcome.detail
    assert "worker/canvas.readback" in outcome.detail
    assert str(CANVAS_DIGEST) in outcome.detail, (
        "the excluded pair is named but its PINNED READING is not, so a reader "
        "cannot tell what would have to change for it to go red"
    )
    evidence = "\n".join(outcome.evidence)
    assert "KNOWN POSITION (excluded, not forgiven)" in evidence
    assert "ps135-2026-08-24" in evidence, (
        "the evidence does not cite the reading the exclusion rests on"
    )


def test_an_unpinned_collision_is_a_FINDING_even_beside_an_excluded_pair(
    monkeypatch,
) -> None:
    """⛔ FALSIFICATION 1, through the real check body.

    A planted collision on `window/webgl.readback` takes the check to FINDING
    and NAMES that vector, while the canvas pair is still reported as excluded.
    This is the deliverable: today, with the check out of the lane entirely,
    this collision is observed by nothing.
    """
    entries = _colliding_canvas() + [
        _entry("window", "webgl.readback", status="colliding", digest=4242)
    ]
    outcome = _drive_check(monkeypatch, entries)

    assert outcome.status == FINDING, (
        "a collision on an actively-defended vector did not go red"
    )
    blob = outcome.detail + "\n".join(outcome.evidence)
    assert "webgl.readback" in blob, "the finding does not NAME the vector"
    assert "KNOWN POSITIONS EXCLUDED" in outcome.detail, (
        "the known positions stopped being reported as soon as there was a real "
        "finding — they must be visible on every run, not only on a green"
    )


def test_a_pin_that_no_longer_describes_the_tree_is_REPORTED_both_ways(
    monkeypatch,
) -> None:
    """⭐ THE TWO DIRECTIONS ARE REPORTED ALIKE AND VERDICTED DIFFERENTLY.

    Both are dead pins and both must be reported, but they are not the same
    event and must not wear the same colour:

    * a DIFFERENT collision is a collision nobody recorded, so the pair gates
      and the check goes RED. The pin did not apply.
    * a VANISHED collision is the product getting BETTER. The pair gates (so it
      is not blinded), the dead pin is reported (so it is deleted), and the
      check stays GREEN — because failing the lane on a fix is the same defect
      as a permanently-green gate wearing the other colour.
    """
    moved = _drive_check(monkeypatch, _colliding_canvas(digest=123456))
    assert moved.status == FINDING, (
        "a collision at an unrecorded digest did not go red, so the pin excused "
        "a collision it does not describe"
    )
    evidence = "\n".join(moved.evidence)
    assert "123456" in evidence and "STALE PIN" in evidence

    gone = _drive_check(monkeypatch, [])
    assert gone.status == PASS, (
        "a pinned pair whose collision is GONE took the check red. That turns "
        f"the day PS-2 ships its fix into a red lane. Detail: {gone.detail}"
    )
    gone_evidence = "\n".join(gone.evidence)
    assert "DELETE IT" in gone_evidence, (
        "the dead pin was not reported, so it would be carried forever and "
        "would blind a vector that has started working"
    )
    assert "NO LONGER DESCRIBE THIS TREE" in gone.detail, (
        "the verdict line does not mention the dead pin, so a reader of the "
        "green never learns the exclusion has expired"
    )


def test_an_inconclusive_unpinned_pair_is_still_CANNOT_RUN(monkeypatch) -> None:
    """The three-way verdict split survives the new structure.

    An unread vector is never a pass and never a finding, and adding a
    known-position layer must not collapse that into either.
    """
    entries = _colliding_canvas() + [
        _entry("window", "webgl.readback", status="inconclusive")
    ]
    outcome = _drive_check(monkeypatch, entries)

    assert outcome.status == CANNOT_RUN, (
        f"an unread must-differ vector did not read as CANNOT_RUN: "
        f"{outcome.status}"
    )
    assert "KNOWN POSITIONS EXCLUDED" in outcome.detail, (
        "a CANNOT_RUN outcome stopped reporting the known positions"
    )


def test_nothing_adjudicates_a_verdict_DOWN(monkeypatch) -> None:
    """⛔ THE TOWARDS-2 ASYMMETRY, asserted of the CHECK rather than the gate.

    `run_behaviour_checks.py`'s rule — "every correction here can only ever move
    a verdict TOWARDS 2, never greener" — is pinned at the adjudication layer by
    `test_no_corroboration_rule_can_make_the_job_greener` and
    `test_no_rule_can_make_this_lane_greener`. This is the same rule one level
    down, where the new structure actually lives.

    The structure is safe because it removes a pair BEFORE a verdict exists, so
    there is no correction to make. The way that could break is a known position
    covering a pair that still has a LIVE collision — so the sweep asserts that
    adding ANY unpinned collision to a world of known positions moves the
    verdict AWAY from PASS, never towards it.
    """
    order = {PASS: 0, FINDING: 1, CANNOT_RUN: 2}

    baseline = _drive_check(monkeypatch, _colliding_canvas())
    assert baseline.status == PASS

    for extra in (
        _entry("window", "webgl.readback", status="colliding", digest=7),
        _entry("window", "audio.digest", status="colliding", digest=8),
        _entry("child_frame", "webgl.readback.childFrame", status="colliding", digest=9),
        _entry("window", "webgl.readback", status="inconclusive"),
        # A pinned pair colliding at an UNRECORDED reading, and a pinned pair
        # that came back UNREAD. Both must also move the verdict away from PASS
        # — they are the two ways a pin could be stretched into a blanket
        # excuse for the pair it names.
        _entry("window", "canvas.readback", status="colliding", digest=7777),
        _entry("window", "canvas.readback", status="inconclusive"),
    ):
        outcome = _drive_check(monkeypatch, [extra] + _colliding_canvas()[1:])
        assert order[outcome.status] > order[baseline.status], (
            f"adding {extra['realm']}/{extra['probe_id']} ({extra['status']}) "
            f"left the verdict at {outcome.status} — the known-position layer "
            "made a verdict GREENER than the comparison it was taken over"
        )


def test_the_report_states_the_known_positions(monkeypatch) -> None:
    """The operator report names them too, not only the outcome detail.

    The report is what a human reads on a green run, and a known position that
    appears only inside a check's verdict line is one refactor away from being
    invisible.
    """
    outcome = _drive_check(monkeypatch, _colliding_canvas())
    report = behaviour.format_report([outcome])

    assert "KNOWN POSITIONS" in report
    for kp in KNOWN_POSITIONS:
        assert kp.pair in report
        assert str(kp.digest) in report
        assert kp.build in report
        assert kp.reason_path in report
    assert "never adjudicated" in report, (
        "the report does not say that a known position is EXCLUDED rather than "
        "FORGIVEN, which is the distinction a reader needs to trust the green"
    )


def test_the_report_does_not_claim_a_VANISHED_collision_is_a_finding(
    monkeypatch,
) -> None:
    """⛔ THE REPORT MUST DESCRIBE THE BRANCH IT ACTUALLY TAKES.

    Round 1's header read "A pair that collides at a DIFFERENT reading, or has
    STOPPED colliding, is reported as a finding". The first half is true and
    tested; the second is FALSE — a vanished collision leaves the verdict at
    PASS by design. The conjunction is what made it dangerous: it welded a true
    case and a false case onto one verb, so a reader who spot-checked the true
    half came away confident about the false one — printed on every run, in the
    report a human reads to decide whether to trust the green.

    That matters more than ordinary comment drift, because the whole
    admissibility argument for this mechanism is "excluding is visible in the
    report; forgiving is invisible". The report is load-bearing EVIDENCE, so a
    sentence in it that misdescribes the mechanism is a defect in the mechanism.

    Driven against the behaviour rather than asserted as a string: the same
    input that produces the header is shown producing a PASS.
    """
    stopped = _drive_check(monkeypatch, [])
    assert stopped.status == PASS, (
        "a vanished collision is not a PASS any more; the report's claim and "
        "the code have swapped places rather than been reconciled"
    )

    report = behaviour.format_report([_drive_check(monkeypatch, _colliding_canvas())])
    header = next(
        line for line in report.splitlines() if line.startswith("KNOWN POSITIONS")
    )
    block = report[report.index(header) :]

    assert "STOPPED colliding, is reported as a finding" not in block, (
        "the report still claims a pair that stopped colliding is a FINDING. "
        "It is not: the pair rejoins the live comparison, the dead pin is "
        "reported, and the verdict is unaffected — measured one assertion up."
    )
    assert "has STOPPED colliding is NOT" in block, (
        "the report does not state that a vanished collision is NOT a finding, "
        "so a reader cannot tell what the green they are looking at means"
    )
    assert "DIFFERENT reading" in block and "FINDING" in block, (
        "the report no longer states the case that IS a finding, so the "
        "correction removed the true half along with the false one"
    )


# --- the falsification still falsifies -------------------------------------


def test_the_falsification_plants_on_a_LIVE_pair_and_checks_the_split(
    monkeypatch,
) -> None:
    """⭐ THE SELF-TEST MUST SURVIVE THE SPLIT, not merely the comparator.

    The check's verdict is taken over the pairs the split leaves LIVE. A
    falsification that asserted only the raw comparator would prove the
    COMPARATOR can see a collision while saying nothing about whether the CHECK
    still can — the permanently-green shape `run_check`'s falsify-first rule
    exists to prevent.
    """
    planted: dict = {}

    def fake_compare(a, b, **kw):
        # Whatever was planted, reported as colliding — plus the known canvas
        # pair, so the split has something real to remove.
        pairs = [
            (realm, probe_id)
            for realm, probes in b.get("probes", {}).items()
            for probe_id in probes
            if b["probes"][realm][probe_id] == a.get("probes", {}).get(realm, {}).get(probe_id)
        ]
        planted["pairs"] = pairs
        return _colliding_canvas() + [
            _entry(realm, probe_id, status="colliding", digest=4242)
            for realm, probe_id in pairs
        ]

    monkeypatch.setattr(behaviour_checks, "_readings_or_refuse", lambda *a, **k: None)
    monkeypatch.setattr("src.services.verify.diff.compare_profiles", fake_compare)

    class _Ctx(_StubContext):
        def record(self, profile, *, fresh=False, realms=None):
            self.launches += 1
            # A per-profile reading on every must-differ pair, so the plant is
            # the ONLY agreement and the split is driven over a real world.
            # Built realm-first rather than with a probe-outer comprehension:
            # several probes share the `window` realm, so a `{realm: {...} for
            # probe in ... for realm in probe.realms}` comprehension keeps only
            # the LAST probe per realm and the plant then has nothing to land on.
            from src.services.verify.probes import must_differ_probes

            probes_by_realm: dict[str, dict] = {}
            for probe in must_differ_probes():
                for realm in probe.realms:
                    probes_by_realm.setdefault(realm, {})[probe.id] = {
                        "value": {"digest": f"{profile.name}:{probe.id}"}
                    }
            return {
                "engine": "firefox",
                "engine_build": BUILD,
                "profile": profile.name,
                "probes": probes_by_realm,
            }

    proven = behaviour_checks._falsify_two_profile_unlinkability(_Ctx([]))

    assert "is reported as linkable" in proven
    assert "survives the known-position split" in proven, (
        "the falsification does not state that the plant reached the VERDICT, "
        "only that the comparator saw it"
    )
    pinned = {kp.pair for kp in KNOWN_POSITIONS}
    assert planted["pairs"], "nothing was planted"
    for realm, probe_id in planted["pairs"]:
        assert f"{realm}/{probe_id}" not in pinned, (
            f"the falsification planted on {realm}/{probe_id}, which is a KNOWN "
            "POSITION — so the plant would be excluded and the self-test would "
            "prove the check catches a defect it actually cannot see"
        )


def test_the_falsification_REFUSES_when_every_pair_is_pinned(monkeypatch) -> None:
    """⛔ THE DEGENERATE END-STATE IS A REFUSAL, NOT A PASS.

    If `KNOWN_POSITIONS` ever grew to cover the whole inventory there would be
    no live pair to plant on, and the check could not be shown capable of
    failing at all. `run_check` publishes CANNOT_RUN when a falsification
    cannot run, so this is the hard floor under the set growing one entry at a
    time: it cannot be extended to cover the inventory without the check
    refusing to publish any verdict.
    """
    from src.services.verify.probes import must_differ_probes

    everything = tuple(
        KnownPosition(
            realm=realm,
            probe_id=probe.id,
            digest=1,
            build=BUILD,
            owner="test",
            reason_path="x",
            reason_quote="y",
        )
        for probe in must_differ_probes()
        for realm in probe.realms
    )
    monkeypatch.setattr(behaviour, "KNOWN_POSITIONS", everything)

    with pytest.raises(behaviour.BehaviourCheckError) as exc:
        behaviour_checks._falsify_two_profile_unlinkability(_StubContext([]))

    assert "no live pair left" in str(exc.value)
    assert "certifies nothing" in str(exc.value)


# --- the lane actually watches Level 2 now ---------------------------------


def test_the_launch_lane_now_selects_and_requires_the_level_2_check() -> None:
    """⭐ THE DELIVERABLE, stated as an assertion rather than a PR sentence.

    `two-profile-unlinkability` is the only check anywhere that observes Level 2
    of the bar. It must be BOTH selected (so it runs) and in the floor (so its
    pass is required) — selected without the floor is a check whose result is
    discarded, and floored without selection is a gate that exits 2 forever.
    """
    import importlib.util

    runner_path = REPO_ROOT / ".github" / "scripts" / "run_launch_behaviour_checks.py"
    spec = importlib.util.spec_from_file_location("_ps380_runner", runner_path)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    assert "two-profile-unlinkability" in runner.SELECTED_CHECKS, (
        "the launch lane does not RUN the only check that observes Level 2 of "
        "the bar, so Level 2 is watched by no CI gate — the defect PS-380 "
        "exists to close"
    )
    assert "two-profile-unlinkability" in runner.EXPECTED_CHECKS, (
        "the lane runs the Level 2 check but does not REQUIRE its pass, so its "
        "verdict is discarded and the gate is green whatever it finds"
    )


def test_no_workflow_grep_for_level_2_comes_back_empty() -> None:
    """The wiring-absence probe from the ticket, inverted into a guard.

    PS-380's finding was that `git grep -nE 'compare_profiles|must_differ|
    unlinkab' -- .github/` returned 12 hits and ALL of them were prose: zero
    invocations. The lane now invokes the check by name, so this asserts the
    invocation exists rather than leaving it to the next person to re-derive.
    """
    runner = (
        REPO_ROOT / ".github" / "scripts" / "run_launch_behaviour_checks.py"
    ).read_text(encoding="utf-8")

    assert '"two-profile-unlinkability",' in runner, (
        "no CI runner names the Level 2 check, so nothing observes mutual "
        "unlinkability"
    )
    # It must be named in BOTH constants, which is two occurrences of the
    # literal — one in the selection, one in the floor.
    assert runner.count('"two-profile-unlinkability",') == 2, (
        "the Level 2 check is named once rather than twice, so it is either run "
        "without being required or required without being run"
    )
