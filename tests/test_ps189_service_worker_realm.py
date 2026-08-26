"""PS-189: the ServiceWorker realm has no author, and the gate can see it.

WHAT THIS SUITE PINS
--------------------
PS-186's sweep found ``chromium / linux`` presenting the container's real
SwiftShader to creepjs, and ``macos`` handing different cards to different
checkers. PS-189 settled BY MEASUREMENT that those are ONE defect: the
``ServiceWorkerGlobalScope`` is authored by NEITHER of the two identity authors,
so it falls through to whoever is left — the ENGINE where the engine spoofs that
arm (macos), and the HOST where it does not (linux).

Every fixture below is a COMMITTED RECORD from ``readings/ps189-2026-08-26/``,
derived verbatim from the realm sweep by that directory's own ``derive.py``. No
value here was invented for a test: the linux service worker really did report
SwiftShader while eleven sibling realms in the SAME LAUNCH reported the
profile's Mesa card.

THE GATE IS PROVEN BOTH RED AND GREEN, which is the ticket's own requirement and
the reason the windows cells are fixtures rather than an afterthought:

    linux/24601, linux/5150   -> HOST_LEAK       (exit 1)
    macos/24601, macos/5150   -> CONTRADICTION   (exit 1)
    windows/24601, /5150      -> CONSISTENT      (exit 0)

⚠️ WINDOWS IS NOT A CONTROL FOR REALM COVERAGE, and this suite says so in a test
rather than in a comment. Windows is clean because
``ENGINE_AUTHORED_IDENTITY_ARMS`` stands our layer down entirely there, leaving
the ENGINE as the single author of every realm — so its green reading is
evidence about AUTHORSHIP COUNT, never about whether our layer reaches a service
worker. Reading it as the latter is what let this defect survive PS-161.

WHY A STRUCTURAL TEST SITS BESIDE THE RECORD-BASED ONES
--------------------------------------------------------
The records prove the defect EXISTED on the day it was measured. They cannot
fail if someone later teaches ``worker_wrap`` to chain ``ServiceWorker``,
because a committed record never changes. So the structural test below asserts
the CURRENT STATE of the module's coverage and is written to go RED when the
blind spot is closed — with a message saying what to do about it. A test that
pins a known gap must announce its own obsolescence, or it becomes the thing
that argues against the fix.
"""

from __future__ import annotations

import json
import os

import pytest

from src.services.verify.checker_cli import main
from src.services.verify.matrix_consistency import (
    CONSISTENT,
    CONTRADICTION,
    HOST_LEAK,
    consistency_pass,
    contradictions,
    host_leaks,
)

READINGS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "readings"
)
DERIVED = os.path.join(READINGS, "ps189-2026-08-26", "derived-matrix")

# The realm sweep itself, which the derived records are built from. Read
# directly by the tests that assert on WHICH realm disagreed — the derived
# record deliberately flattens realms into `checker` rows, so the sweep is the
# place the per-realm question is still askable.
SWEEP = os.path.join(READINGS, "ps189-2026-08-26", "realm-gpu.json")
SWEEP_LAYER_OFF = os.path.join(
    READINGS, "ps189-2026-08-26", "realm-gpu-layer-off.json"
)

LINUX = [
    os.path.join(DERIVED, "realm-matrix.chromium.linux.seed24601.json"),
    os.path.join(DERIVED, "realm-matrix.chromium.linux.seed5150.json"),
]
MACOS = [
    os.path.join(DERIVED, "realm-matrix.chromium.macos.seed24601.json"),
    os.path.join(DERIVED, "realm-matrix.chromium.macos.seed5150.json"),
]
WINDOWS = [
    os.path.join(DERIVED, "realm-matrix.chromium.windows.seed24601.json"),
    os.path.join(DERIVED, "realm-matrix.chromium.windows.seed5150.json"),
]

# The realm the page never builds and the browser starts on its own.
SERVICE_WORKER = "service_worker"


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def cells(path: str) -> "list[dict]":
    """Every cell of a realm sweep."""
    return load(path)["records"]


def realm_identities(cell: dict) -> "dict[str, str]":
    """``{realm name: unmasked renderer}`` for the realms that read one."""
    realms = (cell.get("reading") or {}).get("realms") or {}
    out = {}
    for name, reading in realms.items():
        if isinstance(reading, dict) and reading.get("unmasked_renderer"):
            out[name] = reading["unmasked_renderer"]
    return out


# ---------------------------------------------------------------------------
# The gate's verdict on the committed records — RED on the two broken arms.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", LINUX, ids=["seed24601", "seed5150"])
def test_the_linux_arm_is_flagged_as_a_host_leak(path):
    """The container's REAL software rasteriser reached a realm. Invariant #0.

    This is the more serious of the two faces: not a spoof disagreeing with a
    spoof, but the machine underneath showing through — and PS-186 recorded it
    with ZERO adverse verdicts fired, which is worse rather than better,
    because nothing in the verdict layer will ever catch it.
    """
    entries = consistency_pass(load(path))
    leaks = host_leaks(entries)

    assert [e["vector"] for e in leaks] == ["gpu_claimed"], (
        "the linux record must be flagged as a HOST LEAK — a software "
        "rasteriser is the host, not a graphics card"
    )
    assert leaks[0]["classification"] == HOST_LEAK


@pytest.mark.parametrize("path", MACOS, ids=["seed24601", "seed5150"])
def test_the_macos_arm_is_flagged_as_a_self_contradiction(path):
    """One profile, one launch, two Apple cards — the PS-155/PS-161 class.

    Ranked BELOW the linux leak deliberately: both values are plausible
    consumer GPUs, so nothing about the host is disclosed. It is still a hard
    tell, because no real machine changes graphics card between its page and
    its service worker.
    """
    entries = consistency_pass(load(path))
    found = contradictions(entries)

    assert [e["vector"] for e in found] == ["gpu_claimed"]
    assert found[0]["classification"] == CONTRADICTION
    # And it is NOT the louder alarm: no host value is present on this arm.
    assert host_leaks(entries) == [], (
        "macos leaks nothing about the host — both values are real Apple "
        "silicon, which is exactly why it ranks below the linux cell"
    )


# ---------------------------------------------------------------------------
# ...and GREEN on the arm that has a single author. Both halves, as required.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", WINDOWS, ids=["seed24601", "seed5150"])
def test_the_windows_arm_is_clean_across_every_realm(path):
    """The green half of "proven both red and green".

    ⚠️ READ THIS AS AN AUTHORSHIP-COUNT RESULT, NOT A REALM-COVERAGE ONE. On
    windows our layer stands down entirely, so the engine authors every realm
    including the service worker and there is nobody to disagree with. This
    cell would look identical if our realm coverage were far worse than it is.
    """
    entries = consistency_pass(load(path))

    assert host_leaks(entries) == []
    assert contradictions(entries) == []
    gpu = [e for e in entries if e["vector"] == "gpu_claimed"]
    assert [e["classification"] for e in gpu] == [CONSISTENT]


@pytest.mark.parametrize(
    "path,expected_exit",
    [(p, 1) for p in LINUX + MACOS] + [(p, 0) for p in WINDOWS],
)
def test_the_cli_exit_code_matches_the_verdict(path, expected_exit, capsys):
    """The gate is only useful if a CI job can act on it.

    ``_cmd_consistency`` exits 1 on findings and 0 on a clean record, so the
    six committed cells drive the exit code both ways.
    """
    assert main(["consistency", path]) == expected_exit
    capsys.readouterr()


# ---------------------------------------------------------------------------
# WHICH realm disagreed. The derived record flattens realms into checker rows,
# so these read the sweep itself.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("arm", ["linux", "macos"])
def test_exactly_one_realm_disagrees_and_it_is_the_service_worker(arm):
    """THE FINDING, stated as narrowly as the measurement supports.

    Not "our masking is broken" — eleven realms in the same launch, at the same
    instant, carry the profile's authored card. Exactly one does not, and it is
    the one realm the page never constructs.
    """
    for cell in cells(SWEEP):
        if cell["arm"] != arm:
            continue
        identities = realm_identities(cell)
        assert SERVICE_WORKER in identities, "the service worker must be read"

        sw_value = identities[SERVICE_WORKER]
        others = {n: v for n, v in identities.items() if n != SERVICE_WORKER}

        assert len(set(others.values())) == 1, (
            f"{arm}/seed{cell['seed']}: the page-reachable realms must agree "
            f"with each other, got {sorted(set(others.values()))}"
        )
        assert sw_value not in set(others.values()), (
            f"{arm}/seed{cell['seed']}: the service worker must be the realm "
            "that disagrees"
        )
        assert len(others) >= 10, (
            "the sweep must actually cover the page-reachable realms, or "
            "'only one realm disagreed' is a claim about a probe that barely "
            f"looked (covered {len(others)})"
        )


@pytest.mark.parametrize("path", LINUX, ids=["seed24601", "seed5150"])
def test_only_the_service_worker_row_carries_the_host_value(path):
    """The leak is confined to one realm, and the record can prove which.

    Distinguishes the two shapes the gate itself warns are different: a record
    where every checker leaks in unison (the population is simply leaking) from
    one where a single reader does. This is the second — which is why the
    remedy is about a realm rather than about the whole layer.
    """
    record = load(path)
    leaking = [
        row["checker"]
        for row in record["readings"]
        if row.get("item") == "gpu_renderer"
        and "swiftshader" in str(row.get("value", "")).lower()
    ]
    assert leaking == [f"realm:{SERVICE_WORKER}"], (
        f"exactly the service worker realm should carry the host value, "
        f"got {leaking}"
    )


def test_windows_service_worker_agrees_with_its_siblings():
    """The contrast that makes the finding a REALM story, not an ARM story.

    Same probe, same instant, same twelve realms — and on windows the service
    worker agrees. So the service worker is not unreadable, and the probe is
    not manufacturing a difference: the realm has an author there.
    """
    for cell in cells(SWEEP):
        if cell["arm"] != "windows":
            continue
        identities = realm_identities(cell)
        assert SERVICE_WORKER in identities
        assert len(set(identities.values())) == 1, (
            f"windows/seed{cell['seed']}: every realm should carry ONE "
            f"identity, got {sorted(set(identities.values()))}"
        )


# ---------------------------------------------------------------------------
# Why the obvious fix is not the fix. Measured, so it cannot be re-proposed
# from first principles without meeting the measurement.
# ---------------------------------------------------------------------------


def test_deferring_linux_to_the_engine_would_leak_in_every_realm():
    """The layer-OFF control, which forecloses "just defer like windows does".

    With our layer off, the engine's linux arm reports SwiftShader in ALL
    twelve realms — it does not author that arm at all. So handing linux to the
    engine would not fix the service worker; it would spread the leak to the
    other eleven realms, turning a one-realm defect into a total one.
    """
    seen = 0
    for cell in cells(SWEEP_LAYER_OFF):
        if cell["arm"] != "linux":
            continue
        seen += 1
        identities = realm_identities(cell)
        assert identities, "the layer-off linux cell must have been read"
        assert all("swiftshader" in v.lower() for v in identities.values()), (
            "with the layer off, the engine's linux arm is expected to report "
            f"the host rasteriser in every realm, got {sorted(set(identities.values()))}"
        )
    assert seen, "the layer-off sweep must contain a linux cell"


def test_the_macos_service_worker_value_is_the_engines_own():
    """Attribution, established by a CONTROL rather than by inspection.

    The macos service-worker value must be a value the ENGINE produces with our
    layer OFF — that is what identifies the second author as the engine rather
    than as some third path of our own. It is also why macos and linux are ONE
    defect: the same unauthored realm, falling through to whoever is left.
    """
    engine_values = {
        v
        for cell in cells(SWEEP_LAYER_OFF)
        if cell["arm"] == "macos"
        for v in realm_identities(cell).values()
    }
    assert engine_values, "the layer-off macos cells must have been read"

    for cell in cells(SWEEP):
        if cell["arm"] != "macos":
            continue
        sw_value = realm_identities(cell).get(SERVICE_WORKER)
        assert sw_value in engine_values, (
            f"macos/seed{cell['seed']}: the service worker's value should be "
            f"one the engine itself produces ({sorted(engine_values)}), got "
            f"{sw_value!r}"
        )


# ---------------------------------------------------------------------------
# The structural pin, written to announce its own obsolescence.
# ---------------------------------------------------------------------------


def test_worker_wrap_does_not_chain_the_service_worker():
    """The CURRENT coverage of the realm chaining, pinned as it actually is.

    THIS TEST IS EXPECTED TO FAIL WHEN THE BLIND SPOT IS CLOSED. That is its
    purpose: the committed records above can never fail after a fix (a record
    is a historical fact), so without this the repo would carry no signal that
    the gap had been closed and the PS-189 documentation had gone stale.

    IF YOU ARE READING THIS BECAUSE IT WENT RED: good — something now reaches
    the service worker realm. Delete this test, and update the PS-189 blind-spot
    sections in ``worker_wrap``'s and ``gpu_ext``'s headers, which state the
    realm is unauthored.
    """
    from src.services.browser import worker_wrap

    js = worker_wrap.realm_bootstrap_js("applyPatch")

    assert "G.Worker" in js, "sanity: the dedicated Worker is chained"
    assert "G.SharedWorker" in js, "sanity: the SharedWorker is chained"
    assert "ServiceWorker" not in js, (
        "worker_wrap now mentions ServiceWorker — if the realm is genuinely "
        "covered, delete this test and update the PS-189 blind-spot sections "
        "in worker_wrap.py and gpu_ext.py, which state that it is not"
    )


def test_the_blind_spot_is_documented_where_a_reader_would_look():
    """A silent gap is the failure mode; an announced one is a known bound.

    PS-186's worker could not tell whether the linux cell had been tried and
    failed or never tried at all. Both modules that a reader would consult now
    state the gap explicitly, so the next reader inherits the measurement
    rather than the mystery.
    """
    from src.services.browser import gpu_ext, worker_wrap

    for module in (worker_wrap, gpu_ext):
        doc = module.__doc__ or ""
        assert "PS-189" in doc, f"{module.__name__} must cite the ticket"
        assert "ServiceWorker" in doc or "service worker" in doc, (
            f"{module.__name__} must name the uncovered realm"
        )
