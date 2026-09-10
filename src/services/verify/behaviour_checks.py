"""The behavioural checks themselves: one sequence of real product operations
each, and one planted defect each that proves the check can go red.

Read :mod:`behaviour` first for the verdict vocabulary and the safety guard.
Every check here follows the same contract:

``run``      perform the real sequence; return an :class:`Outcome`.
``falsify``  break the thing deliberately; return the line describing the defect
             this run PROVED the check catches, or raise if it went unnoticed.

The falsifications are not fixtures. Each one perturbs the world (or this run's
own recording) in the way the surface actually fails, so a check that has
stopped looking is caught on the run where it stopped rather than a month later.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import time

from .behaviour import (
    CANNOT_RUN,
    FINDING,
    PASS,
    SUN_PATH_LIMIT,
    BehaviourCheckError,
    Check,
    Context,
    KnownPosition,
    Outcome,
    _first_readable,
    _readings_or_refuse,
    _summarise,
    known_positions_for,
)

# --- the first-launch transient, and why every launch check discards one -----
#
# MEASURED ON THIS TREE, NOT ASSUMED. Recording a profile twice with NOTHING
# happening in between reports two vectors as "changed":
#
#     window/window.innerSize     1152x808  -> 1920x1032  (the Xvfb screen)
#     window/mobile.pointerMedia  prefers-color-scheme: dark -> false
#
# A THIRD recording is then byte-identical to the second. So the difference is
# the profile's FIRST launch initialising — the window settling to the display
# and Firefox settling a pref — and it is over by the second launch.
#
# This matters more than a tuning detail, because a naive restart check
# (record on first launch, restart, record again) reports those two vectors as
# a moved identity and produces a CONFIDENT FALSE FINDING on a healthy tree.
# It did exactly that here before the control was run. A false red on "the
# profile re-rolled its machine" is expensive — it is the loudest signal this
# product has — and it would have been handed to whichever direction owns the
# masking layer as a real defect.
#
# The cure is to make the comparison answer the question actually being asked.
# "Restart continuity" is a claim about launches 2..N of a profile, not about
# the transition from an empty data dir to an initialised one. So every
# launch-backed check below burns one SETTLING launch and discards it, and the
# recordings it compares are both post-settle. Nothing is excluded from the
# comparison and no vector is blinded: a genuine re-roll still moves these
# vectors and is still caught. Only the transient is removed.
#
# Deliberately NOT solved by adding these two probes to an ignore list. That
# would blind the check to a real re-roll of the window geometry — a vector a
# site reads directly — for the sake of a transient that a single extra launch
# removes outright.


def _settle(ctx: Context, profile):
    """Burn the first launch so the profile's data dir is initialised.

    Returns nothing: the reading is DISCARDED on purpose. See the note above
    for the measurement behind this and the false finding it prevents.
    """
    ctx.record(profile, fresh=True)

# --- 1. restart continuity --------------------------------------------------
#
# Level 2 of the bar, and it has never been observed from outside. The
# instrument to do it exists; nothing drove it across a restart.


def _run_restart_continuity(ctx: Context) -> Outcome:
    from .diff import diff_snapshots

    profile = ctx.make_profile("ps70-restart")

    # Burn the first launch: the transition from an empty data dir to an
    # initialised one is not what "restart continuity" asks about, and it moves
    # two vectors on a HEALTHY tree (see the note at the top of this file).
    _settle(ctx, profile)

    # Both recordings are now post-settle restarts of the same profile over its
    # own on-disk state — a genuine restart, not a second first-launch. That
    # distinction is the whole check: a re-roll under a live cookie jar is the
    # defect class.
    before = ctx.record(profile, fresh=False)
    after = ctx.record(profile, fresh=False)

    _readings_or_refuse(before, "first-launch")
    _readings_or_refuse(after, "restart")

    entries = diff_snapshots(before, after)
    total = sum(len(r) for r in before.get("probes", {}).values())
    if entries:
        return Outcome(
            name="restart-continuity",
            surface="a profile is the same observed identity after a restart",
            status=FINDING,
            detail=(
                f"{len(entries)} vector(s) MOVED across a restart of the same "
                "profile over its own data dir. A profile that presents a "
                "different machine after a restart is linkable across its own "
                "sessions."
            ),
            evidence=_summarise(entries),
            launches=3,
        )
    return Outcome(
        name="restart-continuity",
        surface="a profile is the same observed identity after a restart",
        status=PASS,
        detail=(
            f"all {total} readings across both realms were identical before and "
            "after a real restart over the same data dir (one settling launch "
            "discarded first)."
        ),
        launches=3,
    )


def _falsify_restart_continuity(ctx: Context) -> str:
    """Plant a moved reading in a real recording; require the diff to see it.

    Uses ``engine_gate``'s planting helpers rather than new ones — the defect
    being modelled ("a vector moved") is identical, and a second implementation
    of it could drift from the one the engine gate is trusted on.
    """
    from .diff import diff_snapshots
    from .engine_gate import plant_absent_probe, plant_moved_reading

    profile = ctx.make_profile("ps70-restart-falsify")
    snapshot = ctx.record(profile, fresh=True)
    _readings_or_refuse(snapshot, "falsification")
    realm, probe_id = _first_readable(snapshot)

    moved = diff_snapshots(snapshot, plant_moved_reading(snapshot, realm, probe_id))
    if not any(
        e.get("probe_id") == probe_id and e.get("status") == "changed" for e in moved
    ):
        raise BehaviourCheckError(
            f"a deliberately MOVED reading ({realm}/{probe_id}) was not reported "
            "as changed. The continuity comparator is not detecting movement, "
            "so its green verdict is meaningless."
        )

    # The sharper defect: a comparator looping the INTERSECTION cannot see a
    # probe that disappears — it silently stops checking it and stays green.
    absent = diff_snapshots(snapshot, plant_absent_probe(snapshot, realm, probe_id))
    if not any(
        e.get("probe_id") == probe_id and e.get("status") in ("removed", "changed")
        for e in absent
    ):
        raise BehaviourCheckError(
            f"probe {realm}/{probe_id} present on one side and ABSENT on the "
            "other was not reported. A check that silently stops looking at a "
            "vanished vector is how a gate goes green while no longer looking."
        )
    return (
        f"a moved reading and an absent probe ({realm}/{probe_id}) are both "
        "reported red by the continuity comparator"
    )


# --- 2. two profiles are genuinely two machines -----------------------------


def _known_position_split(entries: list[dict], build: "str | None"):
    """Split a comparison into (live, excluded, stale) by known position.

    PS-380, and this is where rules 2 and 3 are executed. See
    ``behaviour.KNOWN_POSITIONS`` for all four.

    ⭐ THE PIN'S ONLY POWER IS TO REMOVE AN EXACTLY-MATCHING RECORDED COLLISION.
    Everything else flows through to the verdict untouched. That is the whole
    rule, and it is deliberately the narrowest one that works:

    * ``excluded`` — the pinned pairs whose reading MATCHES the pin. These do
      not reach the verdict, and they are REPORTED on every run, which is what
      makes this an exclusion rather than a waiver (rule 2).
    * ``live`` — everything else, INCLUDING a pinned pair whose reading does not
      match. A pair that collides at an unrecorded digest is a collision the pin
      does not describe, so it is compared exactly as if it were never pinned —
      which is how it reaches the verdict as the finding it is (rule 3). Same
      for a pinned pair that came back INCONCLUSIVE: nobody read it, which is
      never a recorded anything.
    * ``stale`` — NOTES, not entries, and never a status of their own. A pin
      whose premise no longer holds is reported loudly so it gets deleted.

    ⛔ A VANISHED COLLISION IS NOT A FINDING, AND GETTING THAT WRONG WOULD
    RE-CREATE THE DEFECT THIS WHOLE SLICE EXISTS TO REMOVE. When PS-2 fixes the
    canvas collision the pinned pairs will simply DIFFER, and an earlier draft
    of this function reported that as a FINDING — turning the lane red on the
    day the product got better, for a reason that is not a product defect. That
    is "permanently red is as bad as permanently green" arriving by the back
    door. A pair whose collision is gone REJOINS the live comparison and gates
    normally (so the vector is not blinded), the dead pin is reported (so it
    gets deleted), and the gate stays green because the product is fine.
    Measured: three existing tests — `test_ps232_child_frame_unlinkability.py::
    test_the_live_lane_records_every_realm_the_comparator_will_walk` and two in
    `test_canvas_readback_probe.py` — drive this check over profiles that differ
    on every pair, and they are what caught it.

    ⛔ AND THE DIRECTION IS STILL SAFE. `live` is the verdict's input, and a pin
    only ever REMOVES an exactly-matching recorded collision from it. Every
    other path ADDS the pair back, which can make the verdict redder or leave it
    equal and can never make it greener — the towards-2 asymmetry, one level
    below the adjudicator that states it.
    """
    known: dict[str, KnownPosition] = {}
    for kp in known_positions_for(build):
        # ⚠️ TWO ENTRIES PINNING THE SAME PAIR ON THE SAME BUILD would silently
        # collapse to the last one under a plain dict comprehension, so the
        # second pin's digest would be the only one that could ever match and
        # the first would be inert without saying so. Cannot happen today (two
        # entries, two pairs), but a silent collapse in a structure whose whole
        # value is legibility is exactly the rot the four rules exist against.
        if kp.pair in known:
            raise BehaviourCheckError(
                f"two known positions pin {kp.pair!r} on build {kp.build!r} "
                f"({known[kp.pair].digest!r} and {kp.digest!r}). A pair has ONE "
                "recorded reading per build; keeping both would silently make "
                "one of them inert. Delete the stale entry."
            )
        known[kp.pair] = kp
    if not known:
        return list(entries), [], []

    seen: set[str] = set()
    live: list[dict] = []
    excluded: list[dict] = []
    stale: list[str] = []

    for entry in entries:
        pair = f"{entry.get('realm')}/{entry.get('probe_id')}"
        kp = known.get(pair)
        if kp is None:
            live.append(entry)
            continue
        seen.add(pair)
        if entry.get("status") != "colliding":
            # INCONCLUSIVE on a pinned pair. Not the recorded position: nobody
            # obtained the reading, so the premise was not observed at all. It
            # goes back into the verdict, where an unread must-differ vector is
            # CANNOT_RUN — never a pass and never a pinned excuse.
            live.append(entry)
            stale.append(
                f"STALE PIN: {pair} is pinned at digest {kp.digest!r} on "
                f"{kp.build}, but this run could not READ it. An unobtained "
                "reading is never a recorded collision, so the pair was "
                "compared normally and the pin did not apply."
            )
            continue
        observed = _collision_digest(entry)
        if observed == kp.digest:
            excluded.append(dict(entry, known_position=kp))
            continue
        # A DIFFERENT collision. The pin names a reading, not a vector, so this
        # is an uncovered collision and reaches the verdict as one.
        live.append(entry)
        stale.append(
            f"STALE PIN: {pair} collides at {observed!r}, but the known "
            f"position for {kp.build} pins {kp.digest!r} "
            f"({kp.reason_path}). A DIFFERENT collision is not the recorded "
            "one, so the pin did not apply and this is reported as a finding."
        )

    # A pinned pair that produced NO entry has stopped colliding: the comparator
    # is silent only on a pair that was read on both sides and DIFFERED. That is
    # GOOD NEWS about the product and bad news about the pin, so it is reported
    # loudly and costs the verdict nothing — see the docstring for why making it
    # a finding would be the same defect wearing the other colour.
    for pair, kp in known.items():
        if pair in seen:
            continue
        stale.append(
            f"STALE PIN — DELETE IT: {pair} is pinned at digest {kp.digest!r} "
            f"on {kp.build}, but the two profiles DIFFERED on it. The "
            f"collision is GONE: its premise ({kp.reason_path}) has expired, "
            f"{kp.owner} can close its side, and the pair is now gating "
            "normally. Remove the entry — a pin left in place would blind a "
            "vector that has started working."
        )

    return live, excluded, stale


def _collision_digest(entry: dict):
    """The reading two profiles share, unwrapped to the value the pin names.

    ``compare_profiles`` carries the shared reading on ``value``; a probe's
    reading is a dict, and the digest inside it is what a pin can be compared
    against. Anything else is returned verbatim so an unexpected shape fails
    the comparison rather than matching it by accident.
    """
    value = entry.get("value")
    if isinstance(value, dict):
        if "digest" in value:
            return value["digest"]
        return tuple(sorted(value.items()))
    return value


def _run_two_profile_unlinkability(ctx: Context) -> Outcome:
    from .diff import compare_profiles
    from .probes import must_differ_probes, must_differ_realms

    targets = must_differ_probes()
    a = ctx.make_profile("ps70-unlink-a")
    b = ctx.make_profile("ps70-unlink-b")
    # RECORD WHAT THE COMPARATOR WALKS. `compare_profiles` is inventory-driven,
    # so it asks about every realm a must-differ vector declares; a realm this
    # recording skipped reads ABSENT -> unread -> INCONCLUSIVE forever, and the
    # branch below turns one permanently-inconclusive pair into CANNOT_RUN. The
    # realm set is derived from the same inventory the comparator walks, so the
    # two cannot drift — see `probes.must_differ_realms`.
    realms = must_differ_realms()
    snap_a = ctx.record(a, fresh=True, realms=realms)
    snap_b = ctx.record(b, fresh=True, realms=realms)
    _readings_or_refuse(snap_a, "profile-A")
    _readings_or_refuse(snap_b, "profile-B")

    all_entries = compare_profiles(snap_a, snap_b)
    # PS-380. A known position is removed BEFORE a verdict exists, and what was
    # removed is stated in the outcome. The pin's only power is to remove an
    # EXACTLY-MATCHING recorded collision; everything else flows through to the
    # verdict, so an uncovered collision on a pinned pair is still a finding and
    # an unread one is still CANNOT_RUN.
    entries, excluded, stale = _known_position_split(
        all_entries, snap_a.get("engine_build")
    )
    colliding = [e for e in entries if e.get("status") == "colliding"]
    inconclusive = [e for e in entries if e.get("status") == "inconclusive"]

    # Stated in the verdict, every run, rather than buried in a report nobody
    # opens: this outcome is only as strong as the must-differ inventory, and
    # today that inventory is ONE vector. A reader who is not told this will
    # over-read a green.
    breadth = (
        f"compared {len(targets)} must-differ vector(s): "
        f"{', '.join(p.id for p in targets)}"
        f" (engine {snap_a.get('engine')!r} build "
        f"{snap_a.get('engine_build')!r})"
    )
    # ⭐ NAMED ON EVERY RUN, PASS OR FAIL, and that is rule 2 in force: an
    # excluded pair is visible in the report, where a forgiven one would be
    # invisible. A reader must never be able to mistake this green for "every
    # pair differed".
    known_note = ""
    known_evidence: list[str] = []
    if excluded:
        known_note = (
            " KNOWN POSITIONS EXCLUDED FROM THIS COMPARISON (reported, not "
            f"silenced): {len(excluded)} pair(s) — "
            + "; ".join(
                f"{e['known_position'].pair} at digest "
                f"{e['known_position'].digest!r} on "
                f"{e['known_position'].build}, owned by "
                f"{e['known_position'].owner}"
                for e in excluded
            )
            + ". Each is pinned to a recorded reading, so a DIFFERENT "
            "collision on the same pair is reported as a finding."
        )
        known_evidence = [
            f"KNOWN POSITION (excluded, not forgiven): {e['known_position'].pair}"
            f" — collides at {e['known_position'].digest!r} on "
            f"{e['known_position'].build}; recorded in "
            f"{e['known_position'].reason_path}; owned by "
            f"{e['known_position'].owner}"
            for e in excluded
        ]
    # A dead pin is reported in the SAME breath whatever the verdict is, and it
    # does NOT colour it: a pin whose collision is gone is good news about the
    # product, and failing the lane on it would turn a fix into a red.
    if stale:
        known_note += (
            f" ⚠️ {len(stale)} KNOWN-POSITION PIN(S) NO LONGER DESCRIBE THIS "
            "TREE — see the evidence; a pin that did not apply blinds nothing "
            "(the pair was compared normally) but must be deleted."
        )
        known_evidence = known_evidence + list(stale)

    if colliding:
        return Outcome(
            name="two-profile-unlinkability",
            surface="two profiles are genuinely two machines",
            status=FINDING,
            detail=(
                f"{len(colliding)} seed-derived vector(s) AGREE across two "
                "distinct profiles — that is a linkable identity. " + breadth
                + known_note
            ),
            evidence=known_evidence + _summarise(colliding),
            launches=2,
        )
    if inconclusive:
        return Outcome(
            name="two-profile-unlinkability",
            surface="two profiles are genuinely two machines",
            status=CANNOT_RUN,
            detail=(
                f"{len(inconclusive)} must-differ vector(s) could not be read on "
                "at least one side, so distinctness was not established. "
                "Holding one profile's digest and not the other's is one "
                "reading and one hole, never evidence the two differ. " + breadth
                + known_note
            ),
            evidence=known_evidence + _summarise(inconclusive),
            launches=2,
        )
    return Outcome(
        name="two-profile-unlinkability",
        surface="two profiles are genuinely two machines",
        status=PASS,
        detail=(
            "two distinct profiles differ on every seed-derived vector that was "
            "compared. " + breadth + ". NOTE: the must-differ inventory is "
            "narrow, so this is a real but NARROW pass — see the report's "
            "inventory note." + known_note
        ),
        evidence=known_evidence,
        launches=2,
    )


def _falsify_two_profile_unlinkability(ctx: Context) -> str:
    """Force two profiles to agree on a must-differ vector; require a finding.

    This is the defect the check exists for — two identities a site can link —
    modelled by copying profile A's reading over profile B's. If the comparator
    reports nothing, the unlinkability verdict is inert.

    ⭐ PS-380: THE PLANT GOES ON A *LIVE* PAIR, AND THE SELF-TEST RUNS THROUGH
    THE KNOWN-POSITION SPLIT RATHER THAN AROUND IT. This is the whole safety
    argument for the split existing. The check's verdict is now taken over the
    pairs the split leaves LIVE, so a falsification that asserted only the raw
    comparator would prove the COMPARATOR can see a collision while saying
    nothing about whether the CHECK still can — exactly the permanently-green
    shape this module's falsify-first rule exists to prevent. If a future entry
    ever pinned the pair this plants on, the comparator would still report it
    and the check would still be blind.

    And the degenerate end-state is refused outright: if EVERY must-differ pair
    were a known position there would be no live pair to plant on, the check
    could not be shown capable of failing at all, and this raises rather than
    returning a sentence. That is the hard floor under ``KNOWN_POSITIONS``
    growing one entry at a time — the set cannot be extended to cover the
    inventory without this check refusing to publish a verdict.
    """
    import copy

    from .diff import compare_profiles
    from .probes import must_differ_probes, must_differ_realms

    targets = must_differ_probes()
    if not targets:
        raise BehaviourCheckError(
            "the inventory declares NO must-differ vectors, so cross-profile "
            "unlinkability cannot be measured at all and no green from it "
            "would mean anything."
        )

    a = ctx.make_profile("ps70-unlink-falsify-a")
    b = ctx.make_profile("ps70-unlink-falsify-b")
    # Same realm set as the verdict lane, and for a sharper reason than
    # symmetry: this falsification PLANTS a collision on the first LIVE
    # (realm, probe) pair. Were the recording narrower than the inventory, a
    # target whose only realm this recording skipped would have nothing to
    # plant ONTO — the KeyError below — so the self-test would fail to run on
    # exactly the vector the check was extended to cover, and `run_check`
    # publishes CANNOT_RUN when a falsification cannot run.
    realms = must_differ_realms()
    snap_a = ctx.record(a, fresh=True, realms=realms)
    snap_b = ctx.record(b, fresh=True, realms=realms)
    _readings_or_refuse(snap_a, "falsification-A")
    _readings_or_refuse(snap_b, "falsification-B")

    build = snap_a.get("engine_build")
    pinned = {kp.pair for kp in known_positions_for(build)}
    candidates = [
        (r, p)
        for p in targets
        for r in p.realms
        if f"{r}/{p.id}" not in pinned
    ]
    if not candidates:
        raise BehaviourCheckError(
            "every must-differ pair on this build is a KNOWN POSITION, so "
            "there is no live pair left to plant a collision on and this "
            "check cannot be shown capable of failing at all. A known-position "
            "set that has grown to cover the whole inventory is a gate that "
            "certifies nothing — shrink it, or fix the vectors it pins "
            f"(build {build!r}, pinned {sorted(pinned)})."
        )
    realm, probe = candidates[0]
    planted = copy.deepcopy(snap_b)
    try:
        planted["probes"][realm][probe.id] = copy.deepcopy(
            snap_a["probes"][realm][probe.id]
        )
    except KeyError as exc:  # pragma: no cover - defensive
        raise BehaviourCheckError(
            f"could not plant a collision on {realm}/{probe.id}: {exc}"
        ) from exc

    entries = compare_profiles(snap_a, planted)
    if not any(
        e.get("probe_id") == probe.id and e.get("status") == "colliding"
        for e in entries
    ):
        raise BehaviourCheckError(
            f"two profiles were made to AGREE on {realm}/{probe.id} and the "
            "comparator did not report a collision. The unlinkability check is "
            "inert; its green certifies nothing."
        )

    # ⛔ AND THE SPLIT MUST NOT SWALLOW IT. The verdict is taken over `live`, so
    # a planted collision that the split removed would leave the check green
    # over a real leak. Proving the comparator saw it is not enough; this
    # proves the pair the verdict is COMPUTED from still carries it.
    live, _excluded, _expired = _known_position_split(entries, build)
    if not any(
        e.get("probe_id") == probe.id
        and e.get("realm") == realm
        and e.get("status") == "colliding"
        for e in live
    ):
        raise BehaviourCheckError(
            f"a forced collision on {realm}/{probe.id} was reported by the "
            "comparator and then REMOVED by the known-position split, so the "
            "verdict would be taken over a world that no longer contains it. A "
            "known position must never cover a pair this check still gates."
        )
    return (
        f"a forced collision on {realm}/{probe.id} is reported as linkable, "
        "and survives the known-position split into the verdict"
    )


# --- 3. a benign edit does not move the presented machine -------------------
#
# The class PS-45 and PS-54 both live in. A rename is the sharp case: the whole
# derived identity hangs off the seed, and the seed used to be crc32(name)
# recomputed on every read — so a rename re-rolled the presented machine under a
# live cookie jar. add_profile now FREEZES the seed at creation, which is
# exactly the behaviour this check observes from outside for the first time.


def _run_benign_edit_stability(ctx: Context) -> Outcome:
    from .diff import diff_snapshots

    original = "ps70-edit-before"
    renamed = "ps70-edit-after"
    profile = ctx.make_profile(original)
    # Discard the first-launch transient before the pre-edit reading, or the
    # settling that happens on ANY first launch is attributed to the edit.
    _settle(ctx, profile)
    before = ctx.record(profile, fresh=False)
    _readings_or_refuse(before, "pre-edit")

    pm = ctx.manager()
    # A rename plus a cosmetic note: neither is a request to change the
    # presented machine, so neither may change it.
    if not pm.update_profile(original, renamed, new_notes="cosmetic edit"):
        raise BehaviourCheckError(
            f"the rename {original!r} -> {renamed!r} was refused, so the edit "
            "this check exists to observe never happened."
        )
    edited = pm.profiles.get(renamed)
    if edited is None:
        raise BehaviourCheckError("the renamed profile is not in the store")

    after = ctx.record(edited, fresh=False)
    _readings_or_refuse(after, "post-edit")

    # Probe evidence only: the `profile` header legitimately changed (that IS
    # the edit), and include_meta would report the rename itself as a diff.
    entries = diff_snapshots(before, after)
    seed_note = (
        f"seed {profile.fingerprint_seed} -> {edited.fingerprint_seed}"
    )
    if entries:
        return Outcome(
            name="benign-edit-stability",
            surface="an edit that should not change the presented machine, does not",
            status=FINDING,
            detail=(
                f"{len(entries)} vector(s) MOVED after a rename + note edit "
                f"({seed_note}). Nothing about a rename asks the presented "
                "machine to change; anything that moves is a re-roll under the "
                "profile's live data dir."
            ),
            evidence=_summarise(entries),
            launches=2,
        )
    return Outcome(
        name="benign-edit-stability",
        surface="an edit that should not change the presented machine, does not",
        status=PASS,
        detail=(
            f"a rename ({original!r} -> {renamed!r}) plus a note edit moved "
            f"nothing a page can read ({seed_note})."
        ),
        launches=2,
    )


def _falsify_benign_edit_stability(ctx: Context) -> str:
    """Prove the check would SEE an identity that moved under an edit.

    Modelled the way the real defect behaved: a profile whose seed differs
    presents a different machine. Rather than perturb a recording, this records
    a genuinely DIFFERENT profile and requires the comparator to report the
    move — i.e. the check is shown catching a real re-rolled identity, not a
    doctored JSON.
    """
    from .diff import diff_snapshots

    a = ctx.make_profile("ps70-edit-falsify-a")
    b = ctx.make_profile("ps70-edit-falsify-b")
    if a.fingerprint_seed == b.fingerprint_seed:  # pragma: no cover - defensive
        raise BehaviourCheckError(
            "the two falsification profiles minted the SAME seed, so a moved "
            "identity cannot be modelled."
        )
    snap_a = ctx.record(a, fresh=True)
    snap_b = ctx.record(b, fresh=True)
    _readings_or_refuse(snap_a, "falsification-A")
    _readings_or_refuse(snap_b, "falsification-B")

    entries = diff_snapshots(snap_a, snap_b)
    if not entries:
        raise BehaviourCheckError(
            "two profiles with DIFFERENT seeds produced byte-identical "
            "readings, so this check cannot distinguish a re-rolled identity "
            "from a stable one. Its green means nothing."
        )
    return (
        f"a genuinely re-rolled identity (seeds {a.fingerprint_seed} vs "
        f"{b.fingerprint_seed}) is reported as {len(entries)} moved vector(s)"
    )


# --- 4. a proxy assignment survives an unrelated edit -----------------------
#
# Needs NO exit, deliberately. The ticket's rule: a check that does not need the
# network must not require it, because the link has been down repeatedly and a
# suite that cannot run without it is the suite nobody runs. Here a proxy is a
# thing that gets ASSIGNED and must stay assigned.


def _proxy_store():
    from ..proxy.store import ProxyStore

    return ProxyStore()


def _run_proxy_assignment_survives_edit(ctx: Context) -> Outcome:
    pm = ctx.manager()
    store = _proxy_store()
    store.add("ps70-proxy", "socks5h://198.51.100.7:1080")

    name = "ps70-proxy-holder"
    ctx.make_profile(name, proxy="ps70-proxy")
    if pm.profiles[name].proxy != "ps70-proxy":
        raise BehaviourCheckError("the proxy was not assigned at creation")

    # A string of edits that say NOTHING about the proxy. Each used to be able
    # to clear it as a side effect, because absence and emptiness were the same
    # statement — the defect proxy_assignment.py was written to end.
    renamed = "ps70-proxy-holder-2"
    if not pm.update_profile(name, renamed, new_notes="unrelated note"):
        raise BehaviourCheckError("the unrelated edit was refused")
    if not pm.update_profile(renamed, renamed, new_search_engine="google"):
        raise BehaviourCheckError("the second unrelated edit was refused")

    still = pm.profiles[renamed].proxy
    if still != "ps70-proxy":
        return Outcome(
            name="proxy-assignment-survives-edit",
            surface="a proxy assignment survives an unrelated edit",
            status=FINDING,
            detail=(
                "the proxy assignment did NOT survive an unrelated edit: "
                f"expected 'ps70-proxy', found {still!r}. A silently cleared "
                "assignment launches DIRECT on the operator's real IP, and the "
                "launch guard has nothing left to refuse."
            ),
            evidence=[f"after rename + note + search-engine edit: proxy={still!r}"],
        )
    return Outcome(
        name="proxy-assignment-survives-edit",
        surface="a proxy assignment survives an unrelated edit",
        status=PASS,
        detail=(
            "the assignment survived a rename, a note edit and a search-engine "
            "edit — three edits that say nothing about the proxy."
        ),
        evidence=[f"proxy still {still!r} after 3 unrelated edits"],
    )


def _falsify_proxy_assignment_survives_edit(ctx: Context) -> str:
    """Clear the assignment for real; require the check's predicate to notice.

    ``PROXY_NONE`` is the one input that legitimately clears a proxy, so it
    models the post-condition failing exactly as the historical bug did — the
    profile ends up with no proxy after an edit — without faking anything.
    """
    from ..profile.proxy_assignment import PROXY_NONE

    pm = ctx.manager()
    store = _proxy_store()
    store.add("ps70-proxy-falsify", "socks5h://198.51.100.9:1080")
    name = "ps70-proxy-falsify-holder"
    ctx.make_profile(name, proxy="ps70-proxy-falsify")

    if not pm.update_profile(name, name, new_proxy=PROXY_NONE):
        raise BehaviourCheckError("could not clear the proxy for the falsification")
    cleared = pm.profiles[name].proxy
    if cleared is not None:
        raise BehaviourCheckError(
            "the falsification could not produce a cleared assignment "
            f"(proxy={cleared!r}), so the check was never shown catching one."
        )
    return (
        "a genuinely cleared assignment reads back as None — the condition the "
        "check tests for is observable, so its pass is not vacuous"
    )


# --- 5. a launch REFUSES when the geography is broken -----------------------


class _ReachedEngineSpawn(Exception):
    """Raised in place of the engine spawn: the launch path traversed every
    guard and was about to start a browser. Carries the timezone the launch was
    about to hand the engine."""


def _launch_outcome(profile) -> str:
    """Drive the REAL public launch entry point and report where it stopped.

    Returns the timezone the launch was about to declare to the engine, or
    propagates the refusal the launch raised.

    This calls ``spawn_browser`` — the entry point the UI and the REST lane
    both go through — rather than an internal helper. Asserting that a private
    function raises IS the shape of a unit test, and it comes apart from the
    product in ways that are not hypothetical: a refactor that resolves the
    timezone AFTER the engine spawns, or that swallows the error anywhere
    between the helper and the launch, would leave such an assertion green
    while the product launched on the operator's real timezone.

    ONLY the engine spawn is replaced, by a sentinel. It sits BEYOND the last
    guard, so it cannot mask a refusal that should have happened — every gate
    under test (proxy resolution, then BOTH geography gates that ``spawn_browser``
    asks before it does any launch work: the timezone half and, since PS-240,
    the locale half) runs untouched and in its real order, ahead of any socket,
    any display and any exit. A launch that is correctly REFUSED never reaches
    the sentinel at all, which is what keeps ``needs_launch=False`` honest: no
    browser starts on the refusal path, and none starts on the healthy path
    either.

    ⚠️ The private gate helpers are deliberately NOT named here.
    ``test_the_module_does_not_reach_for_the_private_timezone_helper`` greps
    this module's whole source for that symbol, docstrings included, and it is
    right to: a check that reaches for the private helper instead of driving
    ``spawn_browser`` goes green while the product launches on the operator's
    real timezone. Writing the name in prose trips a guard that cannot tell
    prose from a call — so the gates are described, not named.
    """
    from ..browser import invisible_launch
    from ..browser.process import spawn_browser

    original = invisible_launch.spawn

    def _sentinel(cfg, **kwargs):
        raise _ReachedEngineSpawn(cfg.get("timezone", ""))

    invisible_launch.spawn = _sentinel
    try:
        proc = spawn_browser(profile)
    except _ReachedEngineSpawn as reached:
        return str(reached)
    finally:
        invisible_launch.spawn = original
    # A launch that got past the sentinel is not the path this check believes
    # it is driving; never leave a real engine running behind a check.
    # PS-192: the GROUP, not the handle — spawn_browser returns a wrapper pid
    # whose engine tree is what actually survives a bare terminate().
    with contextlib.suppress(Exception):
        from ..browser.process_group import reap_process_group

        reap_process_group(proc, timeout=10)
    raise BehaviourCheckError(
        "spawn_browser returned a live handle without reaching the engine "
        "spawn sentinel, so this check no longer drives the path it claims to."
    )


def _run_launch_refuses_broken_geography(ctx: Context) -> Outcome:
    from ..proxy.errors import GeographyDisprovenError, GeographyUnknownError

    pm = ctx.manager()
    store = _proxy_store()
    store.add("ps70-geo", "socks5h://198.51.100.11:1080")
    # A proxy that WAS checked successfully: it has geography on file.
    store.mark_checked(
        "ps70-geo", "PL", "Poland", ip="198.51.100.11", timezone="Europe/Warsaw"
    )
    name = "ps70-geo-holder"
    ctx.make_profile(name, proxy="ps70-geo")
    profile = pm.profiles[name]

    healthy = _launch_outcome(profile)
    if healthy != "Europe/Warsaw":
        return Outcome(
            name="launch-refuses-broken-geography",
            surface="a launch refuses when the geography is broken",
            status=FINDING,
            detail=(
                "a launch with VERIFIED proxy geography did not carry its "
                f"exit's zone to the engine (got {healthy!r}, expected "
                "'Europe/Warsaw')."
            ),
        )

    # Now break the geography deliberately: the check FAILED, so the stored zone
    # is contradicted by the product's own most recent evidence.
    store.mark_check_failed("ps70-geo")
    try:
        leaked = _launch_outcome(profile)
    except GeographyDisprovenError as exc:
        return Outcome(
            name="launch-refuses-broken-geography",
            surface="a launch refuses when the geography is broken",
            status=PASS,
            detail=(
                "with the proxy's last check FAILED, spawn_browser refused "
                "rather than proceeding with a zone the latest evidence "
                "disproves. Observed by driving the public launch entry point "
                "until it was about to start an engine — not by asserting "
                "that an internal helper raises. The refusal names the "
                "SPECIFIC cause (the check failed) rather than the generic "
                "one (never checked), which is the distinction the product "
                "went to trouble to keep."
            ),
            evidence=[
                f"verified -> launch declared {healthy!r} to the engine",
                f"after mark_check_failed -> spawn_browser raised {type(exc).__name__}",
            ],
        )
    except GeographyUnknownError as exc:
        # The parent class. The launch DID fail closed — no leak — but it
        # reports "never checked" for a proxy that WAS checked and failed,
        # sending the operator after the wrong remedy.
        return Outcome(
            name="launch-refuses-broken-geography",
            surface="a launch refuses when the geography is broken",
            status=FINDING,
            detail=(
                "the launch refused (so nothing leaked), but as "
                f"{type(exc).__name__} — the 'never checked' cause — for a "
                "proxy that WAS checked and whose check FAILED. The two are "
                "deliberately distinct (errors.py: GeographyDisprovenError "
                "subclasses GeographyUnknownError precisely so the cause can "
                "be stated truthfully); collapsing them tells the operator to "
                "check a proxy they already checked."
            ),
            evidence=[
                f"after mark_check_failed -> {type(exc).__name__} "
                "(expected GeographyDisprovenError)"
            ],
        )
    return Outcome(
        name="launch-refuses-broken-geography",
        surface="a launch refuses when the geography is broken",
        status=FINDING,
        detail=(
            "the launch path did NOT refuse a proxy whose last check failed: "
            f"spawn_browser carried {leaked!r} to the engine. Declaring a "
            "location the product's own latest evidence contradicts is "
            "exactly the incoherence the refusal exists to prevent."
        ),
        evidence=[f"after mark_check_failed -> {leaked!r} (expected a refusal)"],
    )


def _falsify_launch_refuses_broken_geography(ctx: Context) -> str:
    """Show the refusal is CONDITIONAL and CAUSALLY SPECIFIC.

    A guard that refuses everything would pass the check above while being
    useless — every profile would be unlaunchable. So the falsification proves
    both negatives: a healthy proxy must NOT be refused, an unchecked one must
    be, and the two refusing states must not be reported as the same cause.
    """
    from ..proxy.errors import GeographyDisprovenError, GeographyUnknownError

    pm = ctx.manager()
    store = _proxy_store()
    store.add("ps70-geo-falsify", "socks5h://198.51.100.13:1080")
    store.mark_checked(
        "ps70-geo-falsify", "DE", "Germany", ip="198.51.100.13", timezone="Europe/Berlin"
    )
    name = "ps70-geo-falsify-holder"
    ctx.make_profile(name, proxy="ps70-geo-falsify")
    profile = pm.profiles[name]

    try:
        zone = _launch_outcome(profile)
    except GeographyUnknownError as exc:
        raise BehaviourCheckError(
            "a launch with VERIFIED proxy geography was refused "
            f"({type(exc).__name__}). A guard that refuses everything makes "
            "every profile unlaunchable and its 'refusal' proves nothing."
        ) from exc
    if zone != "Europe/Berlin":
        raise BehaviourCheckError(
            f"a verified proxy launched declaring {zone!r} rather than its "
            "exit's zone"
        )

    # The other refusing state: never successfully checked, so no geography.
    store.add("ps70-geo-unchecked", "socks5h://198.51.100.15:1080")
    unchecked_name = "ps70-geo-unchecked-holder"
    ctx.make_profile(unchecked_name, proxy="ps70-geo-unchecked")
    try:
        zone2 = _launch_outcome(pm.profiles[unchecked_name])
    except GeographyDisprovenError as exc:
        raise BehaviourCheckError(
            "a proxy that was NEVER checked was refused as "
            f"{type(exc).__name__} — 'the check failed' — conflating the two "
            "causes in the opposite direction. The check above would then be "
            "unable to tell a disproven geography from an absent one."
        ) from exc
    except GeographyUnknownError:
        return (
            "the refusal is conditional AND causally specific: a VERIFIED "
            "proxy launches declaring its exit's zone (Europe/Berlin), an "
            "UNCHECKED one is refused as GeographyUnknownError, and only a "
            "DISPROVEN one raises GeographyDisprovenError"
        )
    raise BehaviourCheckError(
        "a proxy that was never successfully checked was NOT refused: the "
        f"launch declared {zone2!r}. Deriving a zone from the host would "
        "declare the operator's real location inside the tunnel."
    )


# --- 6. a certificate's key material does not outlive the session -----------


_KEY_MATERIAL = ("persona-mtls-deadbeef.pem", "term_leaf.key", "term_leaf.crt")


def _run_certificate_key_material(ctx: Context) -> Outcome:
    from ..browser.process import _cert_session_for

    name = "ps70-cert"
    profile = ctx.make_profile(name)
    profile_dir = ctx.data_dir(name)
    work = os.path.join(profile_dir, ".persona-mtls")
    os.makedirs(work, exist_ok=True)
    for fname in _KEY_MATERIAL:
        with open(os.path.join(work, fname), "w", encoding="utf-8") as fh:
            fh.write("-----BEGIN PRIVATE KEY-----\nps70\n-----END PRIVATE KEY-----\n")

    planted = sorted(os.listdir(work))

    # The profile has NO certificate assigned — the state an operator reaches by
    # unassigning one. Nothing will start a session, so this is the last chance
    # to clear the previous session's residue.
    session = _cert_session_for(profile, profile_dir, None)
    if session is not None:  # pragma: no cover - defensive
        try:
            session.stop()
        except Exception:
            pass

    left = sorted(os.listdir(work)) if os.path.isdir(work) else []
    if left:
        return Outcome(
            name="certificate-key-material",
            surface="a certificate's key material does not outlive the session",
            status=FINDING,
            detail=(
                f"{len(left)} piece(s) of key material survived an unassigned "
                "certificate. The operator's DECRYPTED private key is written "
                "unencrypted, so anything left here outlives the session it "
                "belonged to, on disk, indefinitely."
            ),
            evidence=[f"planted: {planted}", f"still present: {left}"],
        )
    return Outcome(
        name="certificate-key-material",
        surface="a certificate's key material does not outlive the session",
        status=PASS,
        detail=(
            "with no certificate assigned, every piece of planted key material "
            "was swept from the profile's .persona-mtls directory."
        ),
        evidence=[f"planted: {planted}", "still present: []"],
    )


def _falsify_certificate_key_material(ctx: Context) -> str:
    """Show the check reads a real directory and would REPORT a survivor.

    Two halves. First: an unrelated file in the same directory must NOT be
    swept — a check that passed because something deleted the whole tree would
    be measuring the wrong thing. Second: that surviving file is exactly what
    the check's predicate reports, so a genuine leak is observable.
    """
    from ..browser.process import _cert_session_for

    name = "ps70-cert-falsify"
    profile = ctx.make_profile(name)
    profile_dir = ctx.data_dir(name)
    work = os.path.join(profile_dir, ".persona-mtls")
    os.makedirs(work, exist_ok=True)
    decoy = os.path.join(work, "unrelated-note.txt")
    with open(decoy, "w", encoding="utf-8") as fh:
        fh.write("not key material")

    _cert_session_for(profile, profile_dir, None)

    if not os.path.exists(decoy):
        raise BehaviourCheckError(
            "the sweep removed an UNRELATED file, so a green from this check "
            "could come from something deleting the directory wholesale rather "
            "than from key material being handled correctly."
        )
    survivors = sorted(os.listdir(work))
    if not survivors:
        raise BehaviourCheckError(
            "the check's own predicate (listing the directory) reported nothing "
            "while a file demonstrably exists — it cannot observe a survivor."
        )
    return (
        "a survivor in .persona-mtls IS observed by the check's predicate "
        f"({survivors}), and the sweep is targeted rather than a blanket delete"
    )


# --- 7. deleting is recoverable and wiping is not ---------------------------


def _trash_entry_for(pm, name: str):
    entry = pm._trash().find("profile", name)
    if entry is None:
        raise BehaviourCheckError(
            f"no trash entry for {name!r} after delete — the trash bin did not "
            "receive the profile, so there is nothing to restore."
        )
    return entry


def _run_trash_restore_and_wipe(ctx: Context) -> Outcome:
    from .diff import diff_snapshots

    pm = ctx.manager()
    name = "ps70-trash"
    profile = ctx.make_profile(name)

    # Discard the first-launch transient first, or the settling that happens on
    # ANY first launch is attributed to the trash round-trip.
    _settle(ctx, profile)
    # Record BEFORE the delete so "came back whole" is a claim about the
    # observed identity, not merely about a row reappearing in a list.
    before = ctx.record(profile, fresh=False)
    _readings_or_refuse(before, "pre-delete")

    if not pm.delete_profile(name):
        raise BehaviourCheckError(f"delete_profile({name!r}) returned False")
    if name in pm.profiles:
        return Outcome(
            name="trash-restore-and-wipe",
            surface="deleting is recoverable and wiping is not",
            status=FINDING,
            detail="the profile was still in the store after being deleted.",
            launches=1,
        )

    entry = _trash_entry_for(pm, name)
    restored_ok, reason = pm.restore_profile(entry)
    if not restored_ok:
        return Outcome(
            name="trash-restore-and-wipe",
            surface="deleting is recoverable and wiping is not",
            status=FINDING,
            detail=f"restore was refused: {reason}",
            launches=1,
        )
    revived = pm.profiles.get(name)
    if revived is None:
        return Outcome(
            name="trash-restore-and-wipe",
            surface="deleting is recoverable and wiping is not",
            status=FINDING,
            detail="restore reported success but the profile is not in the store.",
            launches=1,
        )

    # "Whole" means the SAME OBSERVED MACHINE, over the restored data dir.
    after = ctx.record(revived, fresh=False)
    _readings_or_refuse(after, "post-restore")
    moved = diff_snapshots(before, after)
    if moved:
        return Outcome(
            name="trash-restore-and-wipe",
            surface="deleting is recoverable and wiping is not",
            status=FINDING,
            detail=(
                f"the profile came back, but {len(moved)} vector(s) MOVED: a "
                "trash bin that returns a different machine hands back the "
                "cookie jar under a changed identity."
            ),
            evidence=_summarise(moved),
            launches=2,
        )

    # --- the other half: a wipe must genuinely wipe -------------------------
    live_dir = ctx.data_dir(name)
    wiped = pm.wipe_all_profiles()
    remaining_profiles = list(pm.profiles.keys())
    remaining_trash = pm._trash().list("profile")
    dir_left = os.path.isdir(live_dir)

    problems = []
    if remaining_profiles:
        problems.append(f"profiles still in the store: {remaining_profiles}")
    if remaining_trash:
        problems.append(
            f"{len(remaining_trash)} profile entr(ies) survived in the TRASH — a "
            "panic wipe that quietly parks logged-in profiles in a recoverable "
            "store is the interface claiming a protection the code does not give"
        )
    if dir_left:
        problems.append(f"the data dir survived the wipe: {live_dir}")
    if problems:
        return Outcome(
            name="trash-restore-and-wipe",
            surface="deleting is recoverable and wiping is not",
            status=FINDING,
            detail="the wipe did not wipe.",
            evidence=problems,
            launches=2,
        )

    return Outcome(
        name="trash-restore-and-wipe",
        surface="deleting is recoverable and wiping is not",
        status=PASS,
        detail=(
            f"delete -> restore returned the SAME observed machine (all "
            f"{sum(len(r) for r in before.get('probes', {}).values())} readings "
            f"identical), and a subsequent wipe removed all {wiped} profile(s), "
            "their data dirs and the trash."
        ),
        launches=2,
    )


def _falsify_trash_restore_and_wipe(ctx: Context) -> str:
    """Destroy the parked material, then require the restore to fail loudly.

    The failure that matters is a trash bin that reports success while handing
    back an empty profile. So this deletes the parked data dir before restoring
    and requires the outcome to be visible — either a refused restore or a
    restored profile whose data dir is demonstrably absent.
    """
    import shutil

    pm = ctx.manager()
    name = "ps70-trash-falsify"
    ctx.make_profile(name)
    # Give the profile a data dir to lose.
    live_dir = ctx.data_dir(name)
    os.makedirs(live_dir, exist_ok=True)
    with open(os.path.join(live_dir, "cookies.sqlite"), "w", encoding="utf-8") as fh:
        fh.write("jar")

    if not pm.delete_profile(name):
        raise BehaviourCheckError("delete_profile returned False in falsification")
    entry = _trash_entry_for(pm, name)
    parked = entry.material_path
    if not parked or not os.path.isdir(parked):
        raise BehaviourCheckError(
            "the trash entry carries no parked data dir, so 'came back whole' "
            "could never be distinguished from 'came back empty'."
        )

    # Destroy the parked material behind the trash bin's back.
    shutil.rmtree(parked, ignore_errors=True)
    restored_ok, _reason = pm.restore_profile(entry)
    jar = os.path.join(ctx.data_dir(name), "cookies.sqlite")
    if restored_ok and os.path.exists(jar):
        raise BehaviourCheckError(
            "the restore reported success AND produced a data dir that should "
            "not exist — the check cannot distinguish a whole restore from a "
            "hollow one."
        )
    return (
        "with the parked material destroyed, the restore does not silently "
        f"produce a whole profile (restored_ok={restored_ok}, data present="
        f"{os.path.exists(jar)}) — a hollow restore is observable"
    )


# --- 8. no process survives a closed session --------------------------------
#
# PS-347. PS-192 was the product's most expensive failure: a launch left ~35
# engine processes alive per run, and one chromium burned 361% CPU for 12.5
# hours on a user's workstation. Teardown signalled only the pid it held; every
# descendant was reparented and became unreachable from any handle we ever had.
#
# The fix landed (`process_group.py`) and is good. NOTHING CHECKED THAT IT
# STAYS FIXED: before this check the suite had seven checks and not one of them
# counted processes, so PS-192's class of defect would return silently.
#
# ⚠️ THIS CHECK ASKS THE OPERATING SYSTEM, NOT A RETURN VALUE. `terminate`
# reports success on a teardown that reaped a wrapper and orphaned nine
# descendants — that IS the defect, and PS-204 is the same shape one level
# down (`close=all-pids-exit` asserted against a pid set captured once). So the
# evidence here is `process_group_survivors`, which walks the live process
# table.
#
# ⚠️ AND IT COUNTS THE PEAK BEFORE IT IS ALLOWED TO COUNT THE SURVIVORS. A
# survivor count of zero because nothing was ever launched is PS-347's own
# defect reproduced inside its fix, and this project has produced that exact
# shape repeatedly — four distinct ways to reach `peak=1, survivors=0` from a
# browser that never started, byte-identical to a clean teardown. The survivor
# count alone cannot tell them apart; only the PEAK TREE SIZE can. So the check
# refuses to publish any verdict until it has observed a real multi-process
# tree ALIVE in the group it created (`_MIN_LIVE_TREE`), and a launch that
# never got there is CANNOT_RUN, never a pass.
#
# SCOPE OF THIS FIRST SLICE, stated rather than implied: ONE engine (chromium)
# on ONE platform (Linux). Chromium is the arm the defect was measured on — it
# is a WRAPPER launch (fpchrome.AppImage) above a multi-process browser, which
# is the shape that leaks; a direct, single-process launch does not leak on
# `terminate()` at all, so a measurement taken there would be vacuous. Both
# engines on every shipped platform is the roadmap's full bar and a later
# widening.

#: How many live processes must be observed IN THE LAUNCHED GROUP before this
#: check will assert anything about survivors.
#:
#: Three, not one, and the number is the guard rather than a tuning knob. One
#: is what a wrapper that started and immediately died looks like, and it is
#: also what several ways of not-launching look like. A real persona chromium
#: session is a wrapper above a browser above a zygote/gpu/renderer fan-out —
#: measured at 10 members on the tree this check was falsified against — so
#: three is comfortably below the real shape and far above every way of
#: launching nothing.
_MIN_LIVE_TREE = 3

#: The tree must also be SETTLED — this many consecutive samples at the same
#: size — before anything is asserted about it.
#:
#: ⭐ MEASURED, NOT CHOSEN. Without this the grow loop stopped at the first
#: sample past `_MIN_LIVE_TREE`, which on a real launch is a tree part-way
#: through starting: the falsification caught its own harness doing exactly
#: that (peak 4 of an eventual 10) and REFUSED, because signalling the held pid
#: of a half-started chromium takes the whole thing down with it and leaves
#: zero survivors. That is a true fact about a browser that has not finished
#: starting, and it is not the shape PS-192 is about — the leak is the settled
#: zygote/gpu/renderer fan-out being reparented. So the check waits for the
#: size to stop moving rather than for it to cross a bar. Measured on this
#: engine: the tree reaches 10 within ~7s of the launch and stays there.
_STABLE_SAMPLES = 8

#: Interval between tree samples, in seconds.
_SAMPLE_INTERVAL = 0.25

#: How long to wait for the launched tree to reach `_MIN_LIVE_TREE` and settle.
_TREE_GROW_TIMEOUT = 90.0

#: How long a torn-down tree is given to finish leaving before its members are
#: called survivors. Not a loophole: the teardown's own escalation has already
#: run to SIGKILL by this point, so anything still here is genuinely orphaned
#: rather than mid-exit.
_TEARDOWN_GRACE = 5.0

#: THE PROFILE NAMES THIS CHECK LAUNCHES CHROMIUM UNDER — named here, once,
#: because they are the only names in this module whose LENGTH is a
#: correctness property rather than a label.
#:
#: Chromium's process singleton binds a UNIX socket under the profile, and
#: ``behaviour.SUN_PATH_LIMIT`` is a hard wall the engine enforces by exiting
#: FATAL mid-launch. Every OTHER check here launches FIREFOX (see
#: ``UNCOVERED_SURFACES``), which binds no such socket, so these two are the
#: names a scratch home has to leave room for.
#:
#: ⛔ LENGTHENING EITHER OF THESE IS A BEHAVIOUR CHANGE, NOT A RENAME. The
#: budget is asserted against them by
#: ``tests/test_behaviour_checks.py::TestSingletonSocketBudget``, so a name
#: that no longer fits under the CLI's own default home turns that test red
#: rather than turning this check into a silent CANNOT RUN.
SOCKET_BOUND_PROFILE_NAMES: "tuple[str, ...]" = ("p347a", "p347b")


def longest_socket_bound_profile_name() -> int:
    """The budget a scratch home must leave for this module's chromium names.

    Derived from the registry rather than typed as a number, so a home is
    sized against the names the checks ACTUALLY create — a hand-copied figure
    is a figure that goes stale the first time a name changes.
    """
    return max(len(n) for n in SOCKET_BOUND_PROFILE_NAMES)



def _survivors_or_refuse(pgid: int) -> "list[int]":
    """The live members of ``pgid``, or a refusal saying we could not look.

    ``process_group_survivors`` RAISES when psutil is unavailable, deliberately
    — "nothing survived" and "I was unable to check" must never render as the
    same value, and PS-192's reviewer once measured the product path as CLEAN
    in a container where psutil was absent while ``ps`` showed three live
    processes. This translates that raise into the class the harness reports as
    CANNOT_RUN, so a broken instrument can never emit a green.
    """
    from ..browser.process_group import process_group_survivors

    try:
        return process_group_survivors(pgid)
    except Exception as exc:
        raise BehaviourCheckError(
            f"the survivor count could not be taken: {exc}"
        ) from exc


def _survivor_profile(ctx: Context, name: str):
    """A profile whose launch is the WRAPPER, multi-process shape that leaks.

    ``os_type='linux'`` rather than this module's ``windows`` default, and that
    is load-bearing rather than incidental: windows+desktop is the one
    combination that resolves to firefox (see UNCOVERED_SURFACES), and firefox
    is not the arm PS-192 was measured on.

    ⚠️ THE NAME IS DELIBERATELY SHORT, AND ITS LENGTH IS MEASURED RATHER THAN
    STYLISTIC — see :data:`SOCKET_BOUND_PROFILE_NAMES`, which is where the two
    names live and where the arithmetic that sizes them is stated. Chromium's
    process singleton binds a UNIX socket at
    ``<user-data-dir>/.persona-tmp/org.chromium.Chromium.XXXXXX/
    SingletonSocket``, and ``sun_path`` is 108 bytes. The profile name is a
    path COMPONENT of that, under a scratch PERSONA_HOME, so a descriptive
    name like ``ps347-survivors-falsify`` pushed it over: chromium exited FATAL
    "Socket path too long" ~6s in, which reads from outside as a tree that
    started and then vanished. That is a launch this check must never mistake
    for a teardown — and it did not (the settle guard refused it) — but the
    cure is the short name rather than a looser guard.

    ⭐ AND SHORTENING THE NAME IS ONLY HALF THE CURE, which the first attempt
    at this check got wrong: ``ps347-live`` (10 bytes) fits under a home an
    operator names by hand and does NOT fit under the one the CLI provisioned
    itself (31 bytes, leaving 4), so the check was green under
    ``--home /tmp/x`` and reported CANNOT RUN under its own documented
    invocation. The home is now sized against these names
    (``behaviour.default_scratch_home``) and the pair is asserted together by
    ``TestSingletonSocketBudget`` — a budget checked on one side only is a
    budget that fails on the other.

    ⭐ AND IT REFUSES BEFORE LAUNCHING when the operator's own ``--home`` is
    too long for the name, which the CLI's sizing cannot cover: ``--home`` is
    an arbitrary path this module never chose. Unguarded, that case reaches the
    engine and comes back as FATAL several seconds in — a tree that grew to 5
    and vanished — which the settle guard correctly refuses but can only
    describe as "no browser tree was observed running". The operator is then
    told the launch did not settle, when the actionable fact is that their home
    path is N bytes too long. Measuring it here converts an opaque symptom into
    the sentence that names the cure.
    """
    from .behaviour import (
        profile_name_budget,
        singleton_socket_is_bound,
        singleton_socket_length,
    )

    budget = profile_name_budget(ctx.home)
    # Scoped to the platforms whose engine binds a UNIX socket — see
    # `singleton_socket_is_bound`. Windows uses a named mutex and has no
    # sun_path, so this arithmetic describes nothing there.
    if singleton_socket_is_bound() and len(name) > budget:
        raise BehaviourCheckError(
            f"the scratch home {ctx.home!r} is too long to launch chromium "
            f"under: profile {name!r} puts its process-singleton socket at "
            f"{singleton_socket_length(ctx.home, name)} bytes, and the limit "
            f"is {SUN_PATH_LIMIT}. The engine does not degrade here — it exits "
            "FATAL 'Socket path too long' seconds into the launch, which looks "
            "from outside like a browser that started and then vanished, so "
            "NOTHING would be measured. This home leaves "
            f"{budget} byte(s) for a profile name. Use a shorter --home, or "
            "omit --home and let the harness provision one that fits."
        )
    return ctx.make_profile(name, os_type="linux", engine="chromium")


def _sweep_group(pgid: "int | None") -> None:
    """Kill the group this check created. Independent of the code under test.

    ⚠️ NOT ``reap_process_group``, and that is the whole reason this exists.
    Cleanup that goes through the teardown being MEASURED is cleanup that
    stops working exactly when the measurement goes red — which is not
    hypothetical: the first sabotage run of this check (``recorded_group``
    forced to None, the pre-PS-192 shape) correctly reported 9 survivors and
    then leaked all 18 of them from BOTH arms, because its own ``finally``
    called the broken reaper. A gate that leaves a leak behind when it detects
    a leak is worse than no gate on a shared runner.

    ⚠️ AND IT IS ANCHORED ON THE GROUP THIS CHECK RECORDED, NEVER ON A NAME.
    A sweep matching "every process called chrome" can reap a browser this
    check never launched — or, on a self-hosted runner, the agent itself.
    ``signallable_group`` is reused rather than restated because it owns the
    self-kill rule (it refuses a pgid equal to our own group), and it is a pure
    guard rather than part of the teardown escalation, so reusing it does not
    reintroduce the dependency above.
    """
    if pgid is None:
        return
    import signal

    from ..browser.process_group import signallable_group

    target = signallable_group(pgid)
    if target is None:
        return
    with contextlib.suppress(Exception):
        os.killpg(target, getattr(signal, "SIGKILL", 9))


def _drain(proc) -> None:
    """Consume the browser's stdout, exactly as the product's launcher does.

    ⭐ NOT OPTIONAL, AND MEASURED. ``spawn_browser`` gives the engine a PIPE,
    and the launcher immediately starts ``_monitor_process`` to read it. This
    check drives ``spawn_browser`` directly (the public entry point, for
    ``_launch_outcome``'s reason), so it must supply that reader itself — with
    nobody draining, chromium fills the 64KB pipe buffer within seconds and
    dies. Observed here as a tree that reached 10 and collapsed to 0 without
    anything asking it to, which the settle guard correctly refused to measure.
    A browser killed by our own unread pipe is an artefact of the harness, not
    a fact about the product's teardown.
    """
    import threading

    def _pump() -> None:
        stream = getattr(proc, "stdout", None)
        if stream is None:
            return
        with contextlib.suppress(Exception):
            for _ in stream:
                pass

    thread = threading.Thread(target=_pump, daemon=True)
    thread.start()


def _launch_and_grow(
    ctx: Context, profile
) -> "tuple[subprocess.Popen, int, int]":
    """Launch through the PRODUCT's own entry point and watch the tree appear.

    Returns ``(proc, pgid, peak)``. Raises :class:`BehaviourCheckError` when no
    group could be recorded or the tree never reached :data:`_MIN_LIVE_TREE` —
    both of which are "nothing was measured", never "nothing survived".

    ⭐ EVERY FAILING PATH OUT OF HERE SWEEPS THE TREE IT LAUNCHED. That is a
    contract the caller depends on and cannot supply itself: the caller's own
    ``try``/``finally`` opens only once this function has RETURNED, so a raise
    from inside here — most importantly the instrument raising, not the product
    — happens in a window nothing else guards.

    ``spawn_browser`` is the entry point the UI and the REST lane both go
    through, for the reason ``_launch_outcome`` states about the geography
    check: asserting against an internal helper is the shape of a unit test and
    comes apart from the product exactly where it matters.
    """
    from ..browser.process import spawn_browser
    from ..browser.process_group import recorded_group

    ctx.launches += 1
    proc = spawn_browser(profile)

    # ⭐ FROM HERE A REAL TREE IS RUNNING, so every way out of this function
    # must sweep it — INCLUDING the ways that are the INSTRUMENT failing
    # rather than the product. `_survivors_or_refuse` is *designed* to raise
    # (psutil absent is "I could not look", never "nothing survived"), and an
    # unguarded raise would propagate past the caller's own `try:` — which has
    # not been entered yet — and leave the launched tree alive behind a report
    # that reads CANNOT RUN. Measured before the guard, on a real chromium
    # wrapper launch with the instrument broken mid-sampling: 10 live
    # processes still running after the check gave up. A gate that leaks when
    # its instrument breaks is the ticket's second ⛔ met on the happy path
    # only.
    try:
        _drain(proc)

        # THE GROUP IS READ FROM THE HANDLE, NOT GUESSED, and it is the only
        # thing this check will ever signal or count. Anchoring on the group
        # rather than on a command-line substring is what keeps the check from
        # reaping unrelated processes on a shared runner — PS-185's worker lost
        # two cycles to a `pkill -f chromium` that matched its own command
        # line, and a group kill aimed at a non-leader kills the caller.
        pgid = recorded_group(proc)
    except BaseException:
        # No group has been NAMED yet, so a sweep has nothing to anchor on.
        # Ask once more — `recorded_group` only reads the handle — and fall
        # back to the single pid we hold, which is the same rule the
        # `pgid is None` branch below states: a group we cannot name is a
        # group we must not guess at.
        stray: "int | None" = None
        with contextlib.suppress(Exception):
            stray = recorded_group(proc)
        if stray is None:
            with contextlib.suppress(Exception):
                proc.kill()
        else:
            _sweep_group(stray)
        raise

    if pgid is None:
        # The most this may safely reach is the one pid it holds — which is
        # exactly the reason it refuses to measure from here. Nothing else is
        # signalled, because a group we cannot name is a group we must not
        # guess at.
        with contextlib.suppress(Exception):
            proc.kill()
        raise BehaviourCheckError(
            "the launch recorded no process group, so this check has nothing "
            "it may safely count or signal. A survivor count taken any other "
            "way (a name match, a command-line substring) can reap unrelated "
            "processes on a shared runner, so nothing is counted instead. "
            "NOTE: only the single held pid was signalled on the way out, so a "
            "descendant tree may have been left behind — say so rather than "
            "guess at a group id."
        )

    # ⭐ EVERYTHING FROM HERE IS GUARDED, because the group now exists and a
    # live tree is running under it. `except BaseException: … raise` rather
    # than `finally:` deliberately — the SUCCESS path hands the live tree to
    # the caller, which is the whole point of this function, so a `finally`
    # would sweep the tree the caller is about to measure. This also covers a
    # KeyboardInterrupt or a timeout landing inside the ~90s sampling window.
    try:
        peak = 0
        stable = 0
        last = -1
        deadline = time.monotonic() + _TREE_GROW_TIMEOUT
        while time.monotonic() < deadline:
            size = len(_survivors_or_refuse(pgid))
            peak = max(peak, size)
            # SETTLED, not merely large. A tree sampled mid-startup is smaller
            # than the one that leaks, and tearing that down does not orphan
            # anything — see `_STABLE_SAMPLES`.
            stable = (
                stable + 1 if size == last and size >= _MIN_LIVE_TREE else 0
            )
            last = size
            if stable >= _STABLE_SAMPLES:
                break
            time.sleep(_SAMPLE_INTERVAL)

        if stable < _STABLE_SAMPLES:
            # The precondition failed, so NOTHING is asserted about survivors.
            # This is the branch that keeps the check from being unfailable.
            # The sweep is the guard's job now, not this branch's.
            raise BehaviourCheckError(
                f"the launched group {pgid} never held a SETTLED tree of at "
                f"least {_MIN_LIVE_TREE} live processes (peak {peak}, last "
                f"{last}) within {_TREE_GROW_TIMEOUT:.0f}s, so no browser tree "
                "was observed running. A survivor count of zero taken from "
                "here would certify a teardown that had nothing to tear down "
                "— which is precisely the defect this check exists to "
                "prevent, reproduced inside it. Nothing was measured."
            )
    except BaseException:
        _sweep_group(pgid)
        raise
    return proc, pgid, peak


def _run_no_process_survives_a_closed_session(ctx: Context) -> Outcome:
    from ..browser.process import terminate

    name = SOCKET_BOUND_PROFILE_NAMES[0]
    profile = _survivor_profile(ctx, name)
    proc, pgid, peak = _launch_and_grow(ctx, profile)

    try:
        # THE PRODUCT'S OWN TEARDOWN, not this module's. `terminate` is what
        # every close path in the launcher calls (stop_profile, the abort path,
        # shutdown_all), so a regression in it is caught here rather than in a
        # teardown written for the check.
        terminate(proc, name, timeout=10)

        deadline = time.monotonic() + _TEARDOWN_GRACE
        survivors = _survivors_or_refuse(pgid)
        while survivors and time.monotonic() < deadline:
            time.sleep(0.25)
            survivors = _survivors_or_refuse(pgid)

        if survivors:
            return Outcome(
                name="no-process-survives-a-closed-session",
                surface="a closed session leaves no process running",
                status=FINDING,
                detail=(
                    f"{len(survivors)} process(es) of a peak {peak}-process "
                    f"tree were STILL RUNNING {_TEARDOWN_GRACE:.0f}s after the "
                    "product's own teardown returned. They are reparented to "
                    "init and unreachable from any handle persona holds, so "
                    "they accumulate for the life of the machine — PS-192 "
                    "measured this at ~35 per launch and at 361% CPU for 12.5 "
                    "hours on a user's workstation."
                ),
                evidence=[
                    f"launched group {pgid}: peak {peak} live process(es)",
                    f"after terminate(): {len(survivors)} alive — pids "
                    f"{survivors}",
                ],
                launches=1,
            )
        return Outcome(
            name="no-process-survives-a-closed-session",
            surface="a closed session leaves no process running",
            status=PASS,
            detail=(
                f"a real launch grew to {peak} live processes in its own "
                f"group ({pgid}), and the product's teardown left ZERO of them "
                "running — counted from the operating system's process table, "
                "not from the teardown's return value. NOTE: chromium on "
                "Linux only (the wrapper launch PS-192 was measured on); the "
                "firefox arm and the other platforms are not observed here."
            ),
            evidence=[
                f"peak live tree: {peak} process(es) in group {pgid}",
                "survivors after terminate(): 0",
            ],
            launches=1,
        )
    finally:
        # Never leave a live tree behind, on ANY path out of here — above all
        # the FINDING path, where by construction something is still running
        # and the product's own reaper is the thing under suspicion. Scoped to
        # the group this check created and nothing else. See `_sweep_group`.
        _sweep_group(pgid)


def _falsify_no_process_survives_a_closed_session(ctx: Context) -> str:
    """Tear a REAL tree down the pre-PS-192 way; require the check to see it.

    The defect being modelled is the one the fix removed: signal only the pid
    we hold, and let every descendant be reparented. If the check's predicate
    reports zero survivors after that, it cannot observe a leak at all and its
    green certifies nothing.

    ⚠️ THE SIGNAL IS SENT WITH ``os.kill`` ON THE HELD PID, NOT WITH
    ``proc.terminate()``. That is not pedantry: on persona's Linux FORK path
    the handle's own ``kill()`` is group-aware (it IS the PS-192 fix), so a
    "pre-fix shape" control built on the handle would be measuring the fix,
    return a comfortable zero, and certify nothing. Addressing the pid directly
    cannot be group-aware by accident.
    """
    import signal

    name = SOCKET_BOUND_PROFILE_NAMES[1]
    profile = _survivor_profile(ctx, name)
    proc, pgid, peak = _launch_and_grow(ctx, profile)

    try:
        # The defect, exactly: the one pid we hold, escalated, and nothing else.
        with contextlib.suppress(Exception):
            os.kill(proc.pid, getattr(signal, "SIGTERM", 15))
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)
        with contextlib.suppress(Exception):
            os.kill(proc.pid, getattr(signal, "SIGKILL", 9))
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)
        time.sleep(_TEARDOWN_GRACE)

        survivors = _survivors_or_refuse(pgid)
        if not survivors:
            raise BehaviourCheckError(
                "signalling ONLY the held pid — the exact pre-PS-192 defect — "
                f"left NOTHING alive in group {pgid} (peak {peak}). Either the "
                "launch was not the wrapper, multi-process shape this check "
                "believes it is driving, or the predicate cannot observe a "
                "survivor. Its green would certify nothing either way."
            )
        return (
            f"a real {peak}-process tree torn down the pre-PS-192 way (the "
            f"held pid only) leaves {len(survivors)} process(es) alive, and "
            "the check's own predicate REPORTS them — so a returning leak is "
            "observable rather than assumed absent"
        )
    finally:
        # The falsification deliberately created orphans — that IS its result —
        # so it must sweep them itself, by GROUP and WITHOUT the reaper it is
        # modelling the absence of. A self-test that leaks is the defect it is
        # testing. See `_sweep_group`.
        _sweep_group(pgid)


# --- 9. every out-of-perimeter launch artifact is enumerated ----------------
#
# PS-355 / PS-8 DoD#3. The ONLY check here that observes the TREE rather than a
# running product, and that is the point rather than a compromise: the surface
# under test is "does the code write somewhere the inventory does not know
# about", which is a property of the source and is answered before a browser
# exists. It is `needs_launch=False` for that reason and not to dodge the venue.
#
# ⛔ WHAT MAKES THIS DIFFERENT FROM THE LIST IT READS. A hand-written inventory
# with nothing checking it is the "check that cannot fail" PS-8 names — six
# defects of one class were each found by a person reading code, and the
# seventh would have been too. The comparison below is the deliverable; the
# list is its input. The check runs BOTH directions deliberately: an unlisted
# write site is a finding, and so is a listed site that has vanished, because
# an inventory that can be satisfied by deleting its own entries certifies
# nothing.


def _run_launch_perimeter_inventory(ctx: Context) -> Outcome:
    from .launch_perimeter import PERIMETER_ARTIFACTS, validate_inventory
    from .launch_perimeter_scan import (
        missing_removal_sites,
        repo_root,
        scan_launch_surface,
    )

    problems = validate_inventory()
    if problems:
        # A malformed inventory cannot be the input to anything, and this is
        # NOT a finding about the product — it is this check being unable to
        # say anything, which is exactly what CANNOT_RUN is for.
        raise BehaviourCheckError(
            "the inventory is structurally invalid, so nothing can be compared "
            "against it: " + "; ".join(problems)
        )

    root = repo_root()
    try:
        sites = scan_launch_surface(root=root)
    except (FileNotFoundError, SyntaxError) as exc:
        raise BehaviourCheckError(
            f"the launch surface could not be scanned: {exc}"
        ) from exc

    accounted = {a.site for a in PERIMETER_ARTIFACTS if a.detected}
    found = {s.site for s in sites}
    by_site = {s.site: s for s in sites}

    unaccounted = sorted(found - accounted)
    vanished = sorted(accounted - found)

    # ⭐ THE SECOND HALF, AND IT IS NOT DECORATION. Three of the six historical
    # defects were a MISSING REMOVAL rather than a new write — PS-16 above all,
    # which added no write site and removed none. A gate reading only the two
    # sets above passes it. Measured: this one did, before this loop existed.
    broken_reach: list[tuple[str, list[str]]] = []
    total_reach = 0
    for artifact in PERIMETER_ARTIFACTS:
        if not artifact.removal_sites:
            continue
        total_reach += len(artifact.removal_sites)
        gone = missing_removal_sites(artifact.removal_sites, root=root)
        if gone:
            broken_reach.append((artifact.site, gone))

    evidence = [
        f"launch surface: {len(_surface_modules())} module(s) scanned",
        f"inventory: {len(PERIMETER_ARTIFACTS)} artifact(s), "
        f"{len(accounted)} statically checkable",
        f"write sites found: {len(found)}",
        f"removal paths verified: {total_reach}",
    ]

    if unaccounted or vanished or broken_reach:
        lines = list(evidence)
        for site in unaccounted:
            s = by_site[site]
            lines.append(
                f"UNACCOUNTED: {s.path}:{s.lineno} in {s.symbol} — "
                f"[{s.kind}] {s.detail}"
            )
        for site in vanished:
            lines.append(
                f"VANISHED: {site} is in the inventory but the scan no longer "
                "finds it"
            )
        for owner, gone in broken_reach:
            for edge in gone:
                lines.append(f"UNREACHED: {owner} declares {edge}, which is gone")
        return Outcome(
            name="launch-perimeter-inventory",
            surface="every out-of-perimeter launch artifact is enumerated",
            status=FINDING,
            detail=(
                f"{len(unaccounted)} write site(s) outside a profile's own "
                f"directory are not in the inventory, {len(vanished)} "
                "inventory entry(ies) name a site the scan cannot find, and "
                f"{sum(len(g) for _, g in broken_reach)} declared removal "
                "path(s) no longer exist. An unlisted site is the seventh "
                "instance of the class that produced PS-16, PS-57, PS-129, "
                "PS-175, PS-234 and PS-283 — each of which was found by a "
                "person reading code. A MISSING REMOVAL PATH is that class's "
                "other half: PS-16 stranded a profile name on the host by "
                "dropping ONE call, adding no write site anywhere. Either add "
                "the artifact to PERIMETER_ARTIFACTS with its platform column "
                "and its removal path (or its reason), or restore the reach."
            ),
            evidence=lines,
        )

    return Outcome(
        name="launch-perimeter-inventory",
        surface="every out-of-perimeter launch artifact is enumerated",
        status=PASS,
        detail=(
            "every write site the scan finds outside a profile's own directory "
            "is accounted for in PERIMETER_ARTIFACTS, every inventory entry "
            "claiming a static site still resolves to one, and every declared "
            "removal path still exists in the tree. NOTE: this observes the "
            "SOURCE across a traced launch surface, so a write reached only "
            "dynamically, or from a module not on that surface, is not seen; "
            "and a removal path is checked for EXISTENCE, not for working — "
            "see UNCOVERED_SURFACES."
        ),
        evidence=evidence,
    )


def _surface_modules():
    from .launch_perimeter import LAUNCH_SURFACE

    return LAUNCH_SURFACE


def _falsify_launch_perimeter_inventory(ctx: Context) -> str:
    """Plant a real out-of-perimeter write and require the check to name it.

    ⭐ THE PLANTED DEFECT IS A REAL MODULE ON A REAL SCAN, NOT A STUBBED
    PREDICATE. A temporary package is written to disk carrying the exact shape
    of PS-57 — ``tempfile.mkstemp()`` with no ``dir=``, which is how the mTLS
    scratch file landed in the host's temp dir — and the scanner is pointed at
    it as an extra surface module. Faking the comparison (handing the check a
    doctored set) would prove only that set arithmetic works; this proves the
    PARSER sees the shape, which is the half that can rot.

    Two halves, because one alone is not enough:

    1. The planted site must be FOUND and reported as unaccounted. A scanner
       that has stopped parsing reports nothing and its green is vacuous.
    2. The SAME file with the ``dir=`` keyword — PS-57's actual fix — must NOT
       be reported. Without this, a scanner that flagged every line would pass
       half 1 while being useless: it would fire on any write at all rather
       than on an out-of-perimeter one.

    ⭐ AND A THIRD HALF, WHICH IS THE ONE THIS CHECK ORIGINALLY FAILED. The
    write scan cannot see PS-16 at all: that defect DELETED a removal call and
    added no write anywhere. So the reach reader is falsified separately —
    against the real ``manager.py``, with the real declared edges — because a
    reach check that cannot notice a missing caller is exactly the half of this
    gate that would silently rot.
    """
    import tempfile

    from .launch_perimeter_scan import missing_removal_sites, scan_module

    defect = (
        "import tempfile\n"
        "\n"
        "\n"
        "def _stash_the_password():\n"
        "    # The PS-57 shape exactly: no dir=, so this lands in the host's\n"
        "    # shared temp dir where nothing persona owns can reach it.\n"
        "    fd, path = tempfile.mkstemp(prefix='persona-mtls-nsspw-')\n"
        "    return path\n"
    )
    fixed = defect.replace(
        "tempfile.mkstemp(prefix='persona-mtls-nsspw-')",
        "tempfile.mkstemp(prefix='persona-mtls-nsspw-', dir=profile_dir)",
    )

    with tempfile.TemporaryDirectory(prefix="ps355-falsify-") as tmp:
        import os as _os

        defect_rel = "planted_defect.py"
        fixed_rel = "planted_fixed.py"
        with open(_os.path.join(tmp, defect_rel), "w", encoding="utf-8") as fh:
            fh.write(defect)
        with open(_os.path.join(tmp, fixed_rel), "w", encoding="utf-8") as fh:
            fh.write(fixed)

        planted = scan_module(defect_rel, root=tmp)
        if not planted:
            raise BehaviourCheckError(
                "the scanner found NOTHING in a module that calls "
                "tempfile.mkstemp() with no dir= — the exact shape PS-57 "
                "fixed. It cannot observe an out-of-perimeter write, so its "
                "green certifies nothing."
            )
        kinds = {s.kind for s in planted}
        if "host-temp" not in kinds:
            raise BehaviourCheckError(
                f"the planted host-temp write was classified as {kinds!r} "
                "rather than host-temp, so the check would not report it as a "
                "write outside the perimeter."
            )
        # The planted site is by construction absent from the inventory, which
        # is the FINDING arm: confirm the comparison reports it rather than
        # merely that the scan saw it.
        from .launch_perimeter import inventory_sites

        if planted[0].site in inventory_sites():
            raise BehaviourCheckError(  # pragma: no cover - defensive
                "the planted site collides with a real inventory entry, so it "
                "could not demonstrate the unaccounted arm."
            )

        scoped = scan_module(fixed_rel, root=tmp)
        if any(s.kind == "host-temp" for s in scoped):
            raise BehaviourCheckError(
                "the SAME call with dir= — PS-57's actual fix — was still "
                "reported as a host-temp write. The check fires on any write "
                "rather than on an out-of-perimeter one, so a green from it "
                "says nothing about the perimeter."
            )

    # --- the reach half, falsified on a PLANTED PAIR -----------------------
    #
    # ⚠️ PS-16 MODELLED AS A BEFORE/AFTER PAIR, AND THE PAIR IS WHY. Its defect
    # was the ABSENCE of `update_profile -> _remove_window_entry`, so the
    # reproduction is two modules identical but for that one call, with the
    # reader required to tell them apart. A reader that answers "nothing
    # missing" for the reverted one cannot notice a deleted caller; one that
    # answers "missing" for the fixed one fires on any edge and its red is
    # worthless.
    #
    # ⛔ DELIBERATELY NOT ASSERTED AGAINST THE REAL manager.py, and the reason
    # is a measured one rather than a preference. A control pinned to a live
    # inventory edge INVERTS on precisely the tree this check exists to catch:
    # revert PS-16 for real and the falsification itself fails, so the run
    # reports CANNOT_RUN ("this check is broken") instead of FINDING ("the
    # removal path is gone") — the true statement in the words reserved for the
    # wrong one. Observed while building this check. The planted pair says the
    # same thing about the READER without borrowing the tree's health.
    with tempfile.TemporaryDirectory(prefix="ps355-reach-") as tmp:
        import os as _os

        fixed_mod = (
            "class ProfileManager:\n"
            "    def update_profile(self, original_name, new_name):\n"
            "        self._rename_data_dir(original_name, new_name)\n"
            "        # The PS-16 fix, in one line.\n"
            "        self._remove_window_entry(original_name)\n"
            "        return True\n"
        )
        reverted_mod = fixed_mod.replace(
            "        # The PS-16 fix, in one line.\n"
            "        self._remove_window_entry(original_name)\n",
            "",
        )
        with open(_os.path.join(tmp, "fixed_mgr.py"), "w", encoding="utf-8") as fh:
            fh.write(fixed_mod)
        with open(_os.path.join(tmp, "reverted_mgr.py"), "w", encoding="utf-8") as fh:
            fh.write(reverted_mod)

        edge = "update_profile->_remove_window_entry"
        if missing_removal_sites((f"fixed_mgr.py:{edge}",), root=tmp):
            raise BehaviourCheckError(
                "the reach reader reported a removal call that IS present as "
                "missing. It fires on any edge rather than on an absent one, "
                "so its red would mean nothing."
            )
        if not missing_removal_sites((f"reverted_mgr.py:{edge}",), root=tmp):
            raise BehaviourCheckError(
                "the reach reader reported NOTHING missing from a module with "
                "the PS-16 removal call deleted — the exact defect that "
                "stranded a profile name on the host while adding no write "
                "site anywhere. It cannot detect a deleted removal path, so "
                "this check's green says nothing about whether anything still "
                "reaches these artifacts."
            )

    return (
        "a planted module calling tempfile.mkstemp() with NO dir= (the PS-57 "
        f"shape) is found and classified host-temp at {planted[0].symbol!r}, "
        "is absent from the inventory and would be reported UNACCOUNTED — "
        "while the identical call WITH dir= is not reported at all; AND on a "
        "planted PS-16 pair the reach reader reports the removal call as "
        "missing when it is deleted and present when it is not, so a deleted "
        "removal path — the half no write scan can see — is observable too"
    )


# --- the registry -----------------------------------------------------------

CHECKS: tuple[Check, ...] = (
    Check(
        name="restart-continuity",
        surface="a profile is the same observed identity after a restart",
        needs_launch=True,
        run=_run_restart_continuity,
        falsify=_falsify_restart_continuity,
    ),
    Check(
        name="two-profile-unlinkability",
        surface="two profiles are genuinely two machines",
        needs_launch=True,
        run=_run_two_profile_unlinkability,
        falsify=_falsify_two_profile_unlinkability,
    ),
    Check(
        name="benign-edit-stability",
        surface="an edit that should not change the presented machine, does not",
        needs_launch=True,
        run=_run_benign_edit_stability,
        falsify=_falsify_benign_edit_stability,
    ),
    Check(
        name="proxy-assignment-survives-edit",
        surface="a proxy assignment survives an unrelated edit",
        needs_launch=False,
        run=_run_proxy_assignment_survives_edit,
        falsify=_falsify_proxy_assignment_survives_edit,
    ),
    Check(
        name="launch-refuses-broken-geography",
        surface="a launch refuses when the geography is broken",
        needs_launch=False,
        run=_run_launch_refuses_broken_geography,
        falsify=_falsify_launch_refuses_broken_geography,
    ),
    Check(
        name="certificate-key-material",
        surface="a certificate's key material does not outlive the session",
        needs_launch=False,
        run=_run_certificate_key_material,
        falsify=_falsify_certificate_key_material,
    ),
    Check(
        name="trash-restore-and-wipe",
        surface="deleting is recoverable and wiping is not",
        needs_launch=True,
        run=_run_trash_restore_and_wipe,
        falsify=_falsify_trash_restore_and_wipe,
    ),
    Check(
        name="no-process-survives-a-closed-session",
        surface="a closed session leaves no process running",
        # ⛔ NOT the needs_launch=False lane, whatever the venue situation is.
        # This check needs a real browser BY CONSTRUCTION — there is nothing to
        # count without one — and the launch-backed lane's missing execution
        # venue is PS-336's problem, never a reason to mislabel a check into a
        # lane it cannot honestly run in. A survivor count taken with no launch
        # is the vacuous zero this whole check exists to refuse.
        needs_launch=True,
        run=_run_no_process_survives_a_closed_session,
        falsify=_falsify_no_process_survives_a_closed_session,
    ),
    Check(
        name="launch-perimeter-inventory",
        surface="every out-of-perimeter launch artifact is enumerated",
        # ⛔ NOT A DODGE OF THE LAUNCH VENUE. This check's surface is the
        # SOURCE — "does the code write somewhere the inventory does not know
        # about" — which is answered before a browser exists and would be
        # answered identically with one running. A launch is what PRODUCED the
        # inventory (it was measured, not grepped); it is not what checks it.
        needs_launch=False,
        run=_run_launch_perimeter_inventory,
        falsify=_falsify_launch_perimeter_inventory,
    ),
)


def check_names() -> tuple[str, ...]:
    return tuple(c.name for c in CHECKS)


__all__ = [
    "CHECKS",
    "SOCKET_BOUND_PROFILE_NAMES",
    "check_names",
    "longest_socket_bound_profile_name",
]
