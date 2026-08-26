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

# ---------------------------------------------------------------------------
# THE LIVE RECORDS. Round 1 shipped with NOTHING in tests/ asserting on the
# live read at all — which is precisely how it reached review carrying a
# measurement that contradicted its own conclusion. These paths close that.
# ---------------------------------------------------------------------------

# Round 1: scrape only, no control, hand-trimmed after the run. SUPERSEDED, and
# kept as a fixture only so its provenance defect stays pinned rather than
# quietly forgotten (see the provenance test below).
LIVE_ROUND1 = os.path.join(
    READINGS, "ps189-2026-08-26", "live", "live-creepjs.json"
)
# Round 2: the same scrape PLUS the realm controls, through two different exits.
LIVE_ROUND2 = [
    os.path.join(
        READINGS, "ps189-2026-08-26", "live-round2-a-controls", "live-creepjs.json"
    ),
    os.path.join(
        READINGS, "ps189-2026-08-26", "live-round2-b-realms", "live-creepjs.json"
    ),
]

# The card THIS profile (linux / seed 24601) is supposed to present. Written out
# in full rather than matched on a fragment: "contains Intel" would pass on a
# value that got the model wrong, and the whole point here is the exact string.
OUR_CARD = "ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)"

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


# ---------------------------------------------------------------------------
# THE LIVE RECORDS — the reconciliation, pinned.
#
# Round 1 shipped no assertion on the live read at all, and that is exactly how
# it reached review with a live measurement that contradicted its own headline.
# The review's demand was specific: have the live record answer "did our card
# reach the main thread?" WITHOUT inference. These tests hold it to that.
# ---------------------------------------------------------------------------


def live_records(path: str) -> "list[dict]":
    return load(path)["records"]


@pytest.mark.parametrize("path", LIVE_ROUND2, ids=["run_a_controls", "run_b_realms"])
def test_the_live_page_realm_returns_our_card_not_the_host(path):
    """THE CONTROL ROUND 1 LACKED, and the reason the finding survives review.

    Taken on the LIVE creepjs origin, in the same run as the scrape, moments
    after it. If our layer did not reach the live main thread — the review's
    hypothesis (a), a second uncovered surface — this is the assertion that
    would fail. It does not: the main thread returns the profile's card.

    That is what makes the SwiftShader string creepjs renders in its
    main-thread WebGL section a DERIVED row rather than a live read, and it is
    measured on the origin where the defect was found rather than on loopback
    (PS-97, PS-182: an internal buffer differing is not evidence about a page).
    """
    for rec in live_records(path):
        control = rec.get("page_realm_control")
        assert control and control.get("ok"), (
            "the live record must carry a page-realm control that was actually "
            f"taken; got {control!r}. Without it the record cannot answer "
            "'did our card reach the main thread?' except by inference."
        )
        assert control["value"]["unmasked_renderer"] == OUR_CARD


@pytest.mark.parametrize("path", LIVE_ROUND2, ids=["run_a_controls", "run_b_realms"])
def test_every_probed_live_realm_agrees_with_the_page(path):
    """No page-side realm on the live origin carries the host value.

    ``page_webgl2`` and the iframe realms are here because they are the two
    classic routes around a MAIN-world patch: a patch reaching only
    ``WebGLRenderingContext`` would look clean to a WebGL1 probe while creepjs
    read the other prototype, and a child realm is the standard escape hatch.
    Both are checked rather than assumed, LIVE rather than on loopback.
    """
    for rec in live_records(path):
        probed = {"page": rec["page_realm_control"]["value"]["unmasked_renderer"]}
        worker = rec.get("worker_realm_control")
        if worker and worker.get("ok") and worker["value"].get("unmasked_renderer"):
            probed["worker"] = worker["value"]["unmasked_renderer"]
        extra = rec.get("extra_realm_controls")
        if extra and extra.get("ok"):
            for name, reading in (extra["value"] or {}).items():
                if isinstance(reading, dict) and reading.get("unmasked_renderer"):
                    probed[name] = reading["unmasked_renderer"]

        assert len(probed) >= 2, (
            "a run that probed only one realm cannot support 'every realm "
            f"agrees'; probed {sorted(probed)}"
        )
        disagreeing = {n: v for n, v in probed.items() if v != OUR_CARD}
        assert not disagreeing, (
            "every page-side realm probed live must carry the profile's card; "
            f"these did not: {disagreeing}"
        )


@pytest.mark.parametrize("path", LIVE_ROUND2, ids=["run_a_controls", "run_b_realms"])
def test_the_live_page_still_renders_the_host_card_despite_the_controls(path):
    """The DEFECT half, asserted beside the control half.

    This is the pair that makes the finding honest. The test above proves our
    card reaches every page-side realm; this one proves creepjs nonetheless
    renders the host's SwiftShader — and that our card appears NOWHERE in the
    page text. Assert only one of the two and the record tells a comfortable
    half-truth in whichever direction that one points.

    THIS TEST IS EXPECTED TO GO RED WHEN THE ESCALATED FIX LANDS. That is its
    purpose: when a remedy authors the service-worker realm, the host string
    should leave this page. If you are reading this because it failed, check
    whether our card now appears in ``page_text`` — and if so, delete this test
    and update EVIDENCE.md §4, which states the leak was still live.
    """
    for rec in live_records(path):
        text = rec.get("page_text")
        assert text, (
            "the live record must retain the FULL page text — round 1 dropped "
            "it for a 24% excerpt, which is why its claims were not "
            "re-checkable at review (EVIDENCE.md §4.3)"
        )
        assert len(text) == rec["page_text_chars"], (
            "page_text must be the whole text the run measured, not a trim: "
            f"retained {len(text)} of a declared {rec['page_text_chars']}"
        )

        occurrences = rec.get("angle_occurrences") or []
        assert occurrences, "the scrape must have found the rendered gpu rows"
        assert all(o["software_rasteriser"] for o in occurrences), (
            "every ANGLE row creepjs rendered should still be the host's "
            f"software rasteriser; got {[o['value_line'] for o in occurrences]}"
        )
        assert OUR_CARD not in text, (
            "our card is not expected anywhere in the live page text — if it "
            "now is, the leak may be fixed; see this test's docstring"
        )


@pytest.mark.parametrize("path", LIVE_ROUND2, ids=["run_a_controls", "run_b_realms"])
def test_every_live_record_proves_its_own_exit(path):
    """PS-10: an exit is observed per record and never written as a constant.

    The two round-2 runs reached the SAME verdict through two DIFFERENT ASNs,
    which is what makes §4.1 a reproduction rather than a single observation —
    so the ASNs are asserted to be real and present, not asserted to be any
    particular value.
    """
    for rec in live_records(path):
        exit_obs = rec.get("exit") or {}
        for field in ("ip", "city", "org"):
            assert exit_obs.get(field), (
                f"every live record must carry its own observed {field} "
                f"(PS-186 measured 5 distinct ASNs across 8 records); got {exit_obs!r}"
            )
        assert exit_obs["org"].startswith("AS"), (
            f"the ASN must be the observed one, got {exit_obs['org']!r}"
        )


def test_the_round_one_live_record_carries_a_disclosed_provenance_defect():
    """The hand-edit, pinned so it cannot be quietly forgotten.

    ``page_text_excerpt_around_scope_label`` is emitted by NO instrument in this
    repository — the script writes ``page_text``. So the round-1 record was
    edited after the run, keeping ~24% of the page text it measured. Round 2
    supersedes it; it stays committed because deleting it would erase the
    evidence of the defect rather than fix it.

    This test asserts the record is STILL the defective artefact it is described
    as in EVIDENCE.md §4.3. If someone regenerates it properly, this goes red —
    delete it and drop §4.3's "kept as evidence" note.
    """
    rec = live_records(LIVE_ROUND1)[0]
    assert "page_text_excerpt_around_scope_label" in rec, (
        "round 1's record is described in EVIDENCE.md §4.3 as carrying a "
        "hand-made key; it no longer does — update or delete that section"
    )
    assert "page_text" not in rec, (
        "round 1's record is described as having had its full page_text "
        "dropped; it now has one — update or delete EVIDENCE.md §4.3"
    )
    excerpt = rec["page_text_excerpt_around_scope_label"]
    assert len(excerpt) < rec["page_text_chars"], (
        "the excerpt is supposed to be a TRIM of the measured page text"
    )


def test_the_round_two_records_are_instrument_produced():
    """The counterpart: round 2's records carry only keys the script emits.

    The round-1 defect was invisible because nothing checked that a record's
    shape came from the instrument that claims to have produced it. This is
    that check, and it is why the hand-made key cannot recur unnoticed.
    """
    emitted = {
        "arm", "seed", "url", "layer_installed", "page_text_chars",
        "angle_occurrences", "page_text", "page_realm_control",
        "worker_realm_control", "extra_realm_controls", "argv", "exit", "error",
    }
    for path in LIVE_ROUND2:
        for rec in live_records(path):
            unexpected = set(rec) - emitted
            assert not unexpected, (
                f"{os.path.relpath(path, READINGS)} carries key(s) no "
                f"instrument emits: {sorted(unexpected)} — a record whose shape "
                "did not come from the script is a hand-edited record"
            )
