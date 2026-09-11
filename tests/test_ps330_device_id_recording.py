"""AC5 — the ``deviceId``/``groupId`` RECORDING decision, pinned.

Split out of ``test_ps330_ff_mediadevices_live.py`` on purpose. That file
launches a real browser and is therefore SKIPPED wherever the engine, the
display or the launcher is missing — which is most hosts. This decision is a
static claim about the probe inventory and needs none of those, so pinning it
there would have left it unguarded on precisely the hosts that cannot run it.

The reasoning is in the test's own docstring.
"""

def test_device_ids_are_read_test_locally_and_not_recorded_into_the_probe():
    """⭐ AC5, settled by ADOPTING the position PS-320 recorded, not by omission.

    The question is whether ``deviceId``/``groupId`` — which this suite reads
    live, and which nothing had read on the Firefox arm before — should become
    part of the RECORDED probe inventory in ``probes.py``.

    THE ANSWER IS NO, and it is the same answer PS-320 reached on the Chromium
    arm, for reasons that transfer intact rather than by analogy:

      * ``devices.kindCounts`` stays ``WINDOW_ONLY``. Widening it to ``BOTH``
        would add a row that is null BY CONSTRUCTION on every engine forever
        (``navigator.mediaDevices`` is undefined in a worker) — "a widened
        baseline that records nothing, which is worse than no row because it
        reads like coverage".
      * ``CHILD_FRAME`` is the realm that would be worth recording, and since
        PS-316 the baseline DOES record it (``realms`` is
        ``["window","worker","child_frame"]``). That removes the mechanical
        obstacle this bullet used to name, and not the argument: reaching the
        child realm from here still means ``ALL_REALMS``, which drags in the
        worker realm the bullet above rules out — and the measurement below
        rules the vector out on its own terms regardless of realm.

    ⭐ AND FIREFOX ADDS A REASON OF ITS OWN, which is this ticket's measurement
    rather than PS-320's: the ids are the EMPTY STRING on every profile. A
    recorded vector that is a compile-time constant across every seed and every
    host is not a fingerprint dimension — it would enlarge the baseline while
    carrying no information, and ``compare_profiles`` would never walk it
    anyway (``devices.kindCounts`` carries no ``variance=`` and therefore
    defaults to ``SHARED``, which "produces silence rather than a false leak
    report"). Recording a constant as though it were a reading is the shape
    this project has repeatedly paid for.

    So the ids are read TEST-LOCALLY — here, and in PS-320's
    ``test_ps320_enumerate_devices_realm.py`` on the other engine — and the
    probe is unchanged. This test pins that decision so a later widening has to
    be deliberate rather than incidental.

    ⛔ NOT A CLAIM THAT THE IDS DO NOT MATTER. They matter enough to be
    asserted, above, on a live launch; they are simply not worth a row in a
    file an operator may share when their measured value is "".
    """
    from src.services.verify.probes import PROBES, SHARED, WINDOW_ONLY

    probe = next(p for p in PROBES if p.id == "devices.kindCounts")
    assert probe.realms == WINDOW_ONLY, (
        "devices.kindCounts was widened out of WINDOW_ONLY. PS-320 recorded "
        "why it should not be (worker is null by construction; child_frame is "
        "unrecordable until PS-316), and PS-330 adopted that position for the "
        "Firefox arm. Restate both if this is deliberate."
    )
    assert probe.variance == SHARED, (
        "devices.kindCounts is now classified, so compare_profiles will walk "
        "it. On Firefox this vector is a measured CONSTANT — see "
        "readings/ps330-2026-09-07 — so classifying it as must-differ would "
        "report a leak on every profile pair."
    )
    # ⛔ The label restriction, pinned rather than trusted: the probe records
    # kind counts only, and a future edit that starts recording labels into a
    # shareable file must fail here.
    assert "label" not in probe.expr, (
        "the device probe now touches `label`. Labels are user-identifying and "
        "are deliberately NOT recorded into a file the operator may share."
    )
