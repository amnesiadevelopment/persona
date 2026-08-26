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

from .behaviour import (
    CANNOT_RUN,
    FINDING,
    PASS,
    BehaviourCheckError,
    Check,
    Context,
    Outcome,
    _first_readable,
    _readings_or_refuse,
    _summarise,
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


def _run_two_profile_unlinkability(ctx: Context) -> Outcome:
    from .diff import compare_profiles
    from .probes import must_differ_probes

    targets = must_differ_probes()
    a = ctx.make_profile("ps70-unlink-a")
    b = ctx.make_profile("ps70-unlink-b")
    snap_a = ctx.record(a, fresh=True)
    snap_b = ctx.record(b, fresh=True)
    _readings_or_refuse(snap_a, "profile-A")
    _readings_or_refuse(snap_b, "profile-B")

    entries = compare_profiles(snap_a, snap_b)
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

    if colliding:
        return Outcome(
            name="two-profile-unlinkability",
            surface="two profiles are genuinely two machines",
            status=FINDING,
            detail=(
                f"{len(colliding)} seed-derived vector(s) AGREE across two "
                "distinct profiles — that is a linkable identity. " + breadth
            ),
            evidence=_summarise(colliding),
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
            ),
            evidence=_summarise(inconclusive),
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
            "inventory note."
        ),
        launches=2,
    )


def _falsify_two_profile_unlinkability(ctx: Context) -> str:
    """Force two profiles to agree on a must-differ vector; require a finding.

    This is the defect the check exists for — two identities a site can link —
    modelled by copying profile A's reading over profile B's. If the comparator
    reports nothing, the unlinkability verdict is inert.
    """
    import copy

    from .diff import compare_profiles
    from .probes import must_differ_probes

    targets = must_differ_probes()
    if not targets:
        raise BehaviourCheckError(
            "the inventory declares NO must-differ vectors, so cross-profile "
            "unlinkability cannot be measured at all and no green from it "
            "would mean anything."
        )

    a = ctx.make_profile("ps70-unlink-falsify-a")
    b = ctx.make_profile("ps70-unlink-falsify-b")
    snap_a = ctx.record(a, fresh=True)
    snap_b = ctx.record(b, fresh=True)
    _readings_or_refuse(snap_a, "falsification-A")
    _readings_or_refuse(snap_b, "falsification-B")

    probe = targets[0]
    realm = probe.realms[0]
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
    return (
        f"a forced collision on {realm}/{probe.id} is reported as linkable"
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
    under test (proxy resolution, then the geography gate at process.py:249)
    runs untouched and in its real order, ahead of any socket, any display and
    any exit. A launch that is correctly REFUSED never reaches the sentinel at
    all, which is what keeps ``needs_launch=False`` honest: no browser starts
    on the refusal path, and none starts on the healthy path either.
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
)


def check_names() -> tuple[str, ...]:
    return tuple(c.name for c in CHECKS)


__all__ = ["CHECKS", "check_names"]
