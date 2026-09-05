"""The child-frame realm: a THIRD realm in the verify inventory (PS-210).

Why this file exists, and why it drives a real browser rather than a fake
``evaluate``:

The harness observed exactly two realms — ``window`` and ``worker`` — and that
was a structural bound, not a short list. The last two defects persona found
were both REALM defects and neither was findable here. PS-193 is the sharper
one: a real checker took a phantom iframe by INDEXED access (``self[N]``) while
persona's chain hooked the ``contentWindow`` accessor, which indexed access
never invokes. The hook never fired, an unperturbed WebGL buffer went out, and
every check stayed green.

So the two things this file must actually establish are:

1. a probe declaring the new realm produces a READING THAT CAME BACK FROM THAT
   REALM — not source text, not "a helper was called"; and
2. the realm is entered by indexed access, and the ``contentWindow`` accessor
   is NEVER invoked getting there.

Both are asserted against a live engine. The suite's existing ``_fake_evaluate``
pattern cannot establish either: canned data keyed by probe id would satisfy the
letter of both while proving nothing about whether a child realm was entered at
all — which is precisely the defect class this realm was added to observe.

THE NEGATIVE CONTROL IS LOAD-BEARING, and it is why the new probe reads frame
identity rather than a fingerprint vector. Every other probe in the inventory is
seed-derived and deterministic, so a child realm agreeing with the window realm
is the EXPECTED reading whether or not the frame was ever entered — agreement
would be evidence of nothing. Frame identity cannot agree: a realm that is its
own top and a realm that is not are distinguishable by construction. The
divergence test below is therefore the control that shows these assertions
could have failed.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile

import pytest

from src.services.verify import diff, probes, runner, snapshot

# The engine used as a READBACK CHANNEL, not as the product's launch path.
#
# Deliberately worded so it collides with NO capability in conftest.py:
# `ui_driver` owns "chromium not runnable here" and `browser_chromium` owns
# "chromium engine not runnable here". Capability matching is substring
# matching, so this reason must contain neither — this is a plain system
# browser used to read a JS expression, not fingerprint-chromium and not the
# flet UI driver.
_SKIP_REASON = "no system chromium binary available to read the child-frame realm"

_CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser")

# One page load per evaluated expression. Generous: this bounds a whole browser
# start, not a latency budget.
_PAGE_TIMEOUT_S = 60

# The one-off usability probe below gets a tighter bound than a real reading:
# its whole job is to answer "does this binary render at all", and a browser
# that cannot answer `1 + 1` inside this is not a readback channel.
_SMOKE_TIMEOUT_S = 45

# A browser test pays a whole cold browser START per page load, and two of the
# tests below legitimately load twice (determinism needs two records; the
# divergence control needs two realms). The suite-wide 120s bound is sized for
# ordinary tests, and 2 x _PAGE_TIMEOUT_S does not fit inside it — so these
# declare their own. This is a BUDGET, not a latency expectation: a working
# chromium returns in seconds, and the gate above has already established that
# this one works at all.
_BROWSER_TEST_TIMEOUT_S = 300

# Resolved once, on first use, and remembered. `None` means "not asked yet".
_USABILITY: tuple | None = None


def _chromium_usability() -> tuple:
    """``(usable, reason)`` — does this chromium actually RENDER?

    THE GATE THIS REPLACES ASKED THE WRONG QUESTION, and CI proved it. Skipping
    on ``shutil.which(...) is None`` asks whether a FILE EXISTS. On the hosted
    ubuntu runner a ``/usr/bin/chromium`` exists and then never renders: eight
    consecutive launches returned nothing before their timeout, so four tests in
    this file failed for want of a working browser while macOS — which has no
    binary at all — skipped cleanly. A binary that cannot render once is not a
    readback channel, and reporting that as a defect in the realm code is
    exactly the "gate that fails for the wrong reason" ci.yml warns about:
    chromium is provisioned NOWHERE in CI, deliberately, and `browser_chromium`
    is a named, unprovisioned capability gap rather than an oversight.

    So the question asked here is the honest one — *can this thing render?* —
    answered by rendering. The probe is a trivial page with no iframe and no
    realm machinery, so a failure can only mean the browser did not work: it
    cannot be confused with a defect in the code under test.

    THE WEAKNESS THIS DELIBERATELY ACCEPTS: a browser that renders a trivial
    page and then fails on a real one still FAILS, and should — that is a
    genuine finding about the realm, not an environment fact, and it must not
    be silently skipped. This gate only ever forgives a browser that could not
    render *anything*.
    """
    global _USABILITY
    if _USABILITY is not None:
        return _USABILITY

    if _CHROMIUM is None:
        _USABILITY = (False, _SKIP_REASON)
        return _USABILITY

    payload, diagnostic = _try_render(
        _page("1 + 1"), timeout_s=_SMOKE_TIMEOUT_S
    )
    if payload is None or payload.get("value") != 2:
        detail = diagnostic or f"it returned {payload!r} for `1 + 1`"
        _USABILITY = (
            False,
            # Worded to stay UNCLASSIFIED by conftest's capability matching,
            # exactly as _SKIP_REASON is. Capability matching is SUBSTRING
            # matching, and this must not be mistaken either for `ui_driver`'s
            # "chromium not runnable here" (a different chromium: the one the
            # flet UI driver attaches to) or for `browser_chromium`'s
            # "chromium engine not runnable here" (fingerprint-chromium, the
            # product's own engine). This is a plain system browser used to
            # read one JS expression, and no capability declares it.
            f"the system chromium at {_CHROMIUM} cannot render a page here, "
            f"so it is not a usable readback channel: {detail}",
        )
        return _USABILITY

    _USABILITY = (True, "")
    return _USABILITY


@pytest.fixture(scope="session")
def usable_chromium():
    """Skip unless a system chromium is present AND able to render."""
    usable, reason = _chromium_usability()
    if not usable:
        pytest.skip(reason)


# Kept as a decorator so every test below reads the same as before; the
# substance moved from "the file exists" to "the browser works".
requires_chromium = pytest.mark.usefixtures("usable_chromium")


def _js_string(text: str) -> str:
    """A JS string literal safe to embed inside a ``<script>`` block."""
    return json.dumps(text).replace("</", "<\\/")


def _page(expression: str, *, preamble: str = "", extra: str = "{}") -> str:
    """An HTML document that evaluates ``expression`` and publishes its value.

    The result is base64-encoded before it reaches the DOM. That is not
    decoration: ``--dump-dom`` returns serialised HTML, so a raw JSON payload
    containing ``<`` or ``&`` would come back entity-escaped and the reading
    would be silently mangled on its way to the assertion.
    """
    return (
        "<!doctype html><html><body><pre id='out'>PENDING</pre><script>\n"
        + preamble
        + "\nvar __expr = " + _js_string(expression) + ";\n"
        "function __publish(payload){\n"
        "  var json = JSON.stringify(payload);\n"
        "  document.getElementById('out').textContent = 'RESULT:' +\n"
        "    btoa(unescape(encodeURIComponent(json)));\n"
        "}\n"
        "try{\n"
        "  Promise.resolve((new Function('return (' + __expr + ')'))()).then(\n"
        "    function(v){ __publish({ok:true, value:(v===undefined?null:v),"
        " extra:(" + extra + ")}); },\n"
        "    function(e){ __publish({ok:false, error:String(e),"
        " extra:(" + extra + ")}); });\n"
        "}catch(e){ __publish({ok:false, error:String(e), extra:(" + extra + ")}); }\n"
        "</script></body></html>\n"
    )


def _render(html: str, *, timeout_s: int = _PAGE_TIMEOUT_S) -> dict:
    """Load ``html`` in a real browser and return the published payload."""
    payload, diagnostic = _try_render(html, timeout_s=timeout_s)
    if payload is None:
        pytest.fail(diagnostic)
    return payload


def _try_render(html: str, *, timeout_s: int) -> tuple:
    """``(payload, None)`` on a successful read, ``(None, diagnostic)`` otherwise.

    Split out of :func:`_render` so the usability gate below can ask "does this
    binary render at all?" and get an ANSWER rather than a test failure. A
    browser that cannot be launched is a fact about the environment; only a
    browser that CAN be launched and then misbehaves is a fact about the code.
    """
    workdir = tempfile.mkdtemp(prefix="ps210-")
    try:
        page_path = os.path.join(workdir, "page.html")
        with open(page_path, "w", encoding="utf-8") as handle:
            handle.write(html)
        try:
            proc = subprocess.run(
                [
                    _CHROMIUM,
                    "--headless",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--user-data-dir=" + os.path.join(workdir, "profile"),
                    # Lets the page's promise chain settle before the DOM is
                    # dumped.
                    "--virtual-time-budget=5000",
                    "--dump-dom",
                    "file://" + page_path,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return None, (
                f"chromium did not return within {timeout_s}s — the browser "
                "never rendered, so nothing was read."
            )
        except OSError as exc:  # binary present but not executable here
            return None, f"chromium could not be launched: {exc}"

        marker = "RESULT:"
        start = proc.stdout.find(marker)
        if start == -1:
            return None, (
                "the page never published a result — the readback channel "
                f"itself failed.\nstdout:\n{proc.stdout[:2000]}\n"
                f"stderr:\n{proc.stderr[:2000]}"
            )
        blob = proc.stdout[start + len(marker):]
        blob = blob.split("<")[0].strip()
        return json.loads(base64.b64decode(blob).decode("utf-8")), None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _evaluate(*, preamble: str = "", extra: str = "{}") -> tuple:
    """An ``Evaluate`` for :func:`runner.run_probes`, plus the extras sidecar.

    Returns ``(evaluate, sink)``. ``sink`` collects the ``extra`` payload from
    each page load, which is how the AC3 tripwire below reports its
    ``contentWindow`` hit count back to the test.
    """
    sink: list[dict] = []

    def evaluate(expression: str):
        payload = _render(_page(expression, preamble=preamble, extra=extra))
        sink.append(payload.get("extra") or {})
        if not payload.get("ok"):
            raise RuntimeError(f"page evaluation failed: {payload.get('error')}")
        return payload["value"]

    return evaluate, sink


# The tripwire. Replaces the `contentWindow` accessor with a COUNTING getter
# that still delegates, so the page behaves identically and the only observable
# difference is that we learn whether the accessor was consulted.
_COUNT_CONTENT_WINDOW = (
    "var __cwHits = 0;\n"
    "var __d = Object.getOwnPropertyDescriptor("
    "HTMLIFrameElement.prototype, 'contentWindow');\n"
    "Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {\n"
    "  configurable: true,\n"
    "  get: function(){ __cwHits++; return __d.get.call(this); }\n"
    "});\n"
)


def _frame_identity_probe() -> probes.Probe:
    wanted = [p for p in probes.PROBES if probes.CHILD_FRAME in p.realms]
    assert wanted, "no probe declares the child_frame realm"
    return wanted[0]


# --- AC1: a reading that came back FROM the realm -----------------------------


@requires_chromium
def test_run_probes_returns_a_reading_from_inside_the_child_frame_realm():
    """AC1. ``run_probes`` executes the third realm and a READING comes back.

    The assertion is on the reading's CONTENT, and specifically on content that
    only the child realm can produce: ``selfIsTop`` false and a frame depth of
    one. A harness that never entered the frame cannot manufacture those — the
    top realm is its own top, at depth zero.
    """
    evaluate, _ = _evaluate()

    results = runner.run_probes(evaluate, (probes.CHILD_FRAME,))

    assert probes.CHILD_FRAME in results
    probe = _frame_identity_probe()
    entry = results[probes.CHILD_FRAME][probe.id]

    assert "error" not in entry, f"the child realm was not read: {entry}"
    reading = entry["value"]

    # This is the realm speaking about itself.
    assert reading["selfIsTop"] is False
    assert reading["frameDepth"] == 1
    assert reading["hasParent"] is True
    assert reading["hasTop"] is True


@requires_chromium
@pytest.mark.timeout(_BROWSER_TEST_TIMEOUT_S)
def test_the_child_frame_reading_is_deterministic_across_two_records():
    """AC7. Two records of the same profile must be byte-identical.

    ``probes.py`` refuses an unstable probe outright, so this is the new
    record's entrance exam rather than a nicety.
    """
    evaluate, _ = _evaluate()
    probe = _frame_identity_probe()

    first = runner.run_probes(evaluate, (probes.CHILD_FRAME,))
    second = runner.run_probes(evaluate, (probes.CHILD_FRAME,))

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert "error" not in first[probes.CHILD_FRAME][probe.id]


# --- AC3: reached by INDEXED access, not the contentWindow accessor -----------


@requires_chromium
def test_the_child_realm_is_entered_without_ever_invoking_contentWindow():
    """AC3, asserted BEHAVIOURALLY — the whole point of this realm.

    ``HTMLIFrameElement.prototype.contentWindow`` is replaced with a counting
    getter that still delegates. The run must come back with BOTH halves:

    * a genuine child-realm reading (so we know the frame was really entered), and
    * ``contentWindow`` hit count of zero (so we know it was not entered that way).

    Neither half alone is evidence. A zero count on its own is equally
    satisfied by a harness that never reached the frame at all, which is why
    this asserts the reading in the same breath.

    This is the exact discrimination PS-193 turned on: a checker reaching a
    frame by index is invisible to an accessor hook, so a harness that could
    only see the accessor path returned a clean reading over a live defect.
    """
    evaluate, sink = _evaluate(
        preamble=_COUNT_CONTENT_WINDOW,
        extra="{contentWindowHits: __cwHits}",
    )

    results = runner.run_probes(evaluate, (probes.CHILD_FRAME,))
    entry = results[probes.CHILD_FRAME][_frame_identity_probe().id]

    # Half one: the frame really was entered.
    assert "error" not in entry, f"the child realm was not read: {entry}"
    assert entry["value"]["selfIsTop"] is False
    assert entry["value"]["frameDepth"] == 1

    # Half two: and not through the accessor.
    assert sink, "the tripwire never reported"
    hits = [s.get("contentWindowHits") for s in sink]
    assert hits == [0] * len(hits), (
        "the child realm was reached through the contentWindow accessor "
        f"({hits} invocations). That is the PS-193 blind spot: a checker using "
        "indexed access would bypass any hook on that path."
    )


def test_the_shipped_child_frame_expression_uses_indexed_access():
    """A cheap SOURCE-LEVEL tripwire against a future refactor to the accessor.

    Explicitly the WEAKER of the two AC3 checks and not a substitute for it —
    the behavioural test above is the evidence. This one exists so that a later
    "simplification" to ``frame.contentWindow`` fails here loudly and
    immediately, including in an environment with no browser to run the real
    check.
    """
    expression = runner.child_frame_expression(
        probes.probes_for_realm(probes.CHILD_FRAME)
    )
    assert "self[idx]" in expression
    assert "contentWindow" not in expression


# --- The negative control: these realms MUST disagree -------------------------


@requires_chromium
@pytest.mark.timeout(_BROWSER_TEST_TIMEOUT_S)
def test_window_and_child_frame_disagree_and_diff_realms_reports_it():
    """AC5, and the control that makes every assertion above non-vacuous.

    Reads the SAME probe in the window realm and in the child realm, builds a
    real snapshot from both, and asserts the comparator reports a divergence.

    This is the test that proves the others could have failed. Our probes are
    seed-derived, so on any ordinary vector the two realms would agree by
    construction — with the frame entered or not. Frame identity is chosen
    precisely because agreement here is impossible, so a passing divergence is
    positive evidence that two DISTINCT realms were entered.
    """
    evaluate, _ = _evaluate()
    probe = _frame_identity_probe()

    results = runner.run_probes(evaluate, (probes.WINDOW, probes.CHILD_FRAME))

    window_entry = results[probes.WINDOW][probe.id]
    child_entry = results[probes.CHILD_FRAME][probe.id]
    assert "error" not in window_entry, window_entry
    assert "error" not in child_entry, child_entry

    # The realms genuinely disagree about where they sit.
    assert window_entry["value"]["selfIsTop"] is True
    assert window_entry["value"]["frameDepth"] == 0
    assert child_entry["value"]["selfIsTop"] is False
    assert child_entry["value"]["frameDepth"] == 1

    snap = snapshot.build_snapshot(
        results,
        engine="chromium",
        profile="ps210-control",
        realms=(probes.WINDOW, probes.CHILD_FRAME),
    )
    entries = diff.diff_realms(snap, probes.WINDOW, probes.CHILD_FRAME)

    reported = [e for e in entries if e["probe_id"] == probe.id]
    assert reported, "diff_realms reported agreement over a real divergence"
    assert reported[0]["status"] == diff.CHANGED


# --- AC4: an unreachable realm is an ERROR, never agreement -------------------


def test_a_child_realm_that_cannot_be_entered_is_recorded_as_an_error():
    """AC4. A realm with no document to host a frame errors every probe.

    The failure the harness reports must be attributable, not absent: an absent
    reading compares as agreement downstream, which is the PS-21 rule this AC
    encodes.
    """

    def evaluate(_expression):
        return {"__harness_error": "TypeError: this realm has no document"}

    results = runner.run_probes(evaluate, (probes.CHILD_FRAME,))
    entries = results[probes.CHILD_FRAME]

    assert entries, "an unreachable realm produced no entries at all"
    for probe in probes.probes_for_realm(probes.CHILD_FRAME):
        assert "error" in entries[probe.id]
        assert "value" not in entries[probe.id]
        assert "ChildFrameHarness" in entries[probe.id]["error"]


def test_a_transport_failure_in_the_child_realm_errors_every_declared_probe():
    """AC4. The transport itself failing is recorded, not swallowed."""

    def evaluate(_expression):
        raise RuntimeError("transport went away")

    results = runner.run_probes(evaluate, (probes.CHILD_FRAME,))

    for probe in probes.probes_for_realm(probes.CHILD_FRAME):
        entry = results[probes.CHILD_FRAME][probe.id]
        assert "value" not in entry
        assert "transport went away" in entry["error"]


def test_an_errored_child_realm_never_compares_as_agreement():
    """AC4 + AC5 together: an unread realm is inconclusive, never a pass.

    The distinction that matters: silence must not be mistaken for consensus.
    """
    probe = _frame_identity_probe()
    snap = {
        "probes": {
            probes.WINDOW: {probe.id: {"value": {"selfIsTop": True}}},
            probes.CHILD_FRAME: {
                probe.id: {"error": "ChildFrameHarness: TimeoutError"}
            },
        }
    }

    entries = diff.diff_realms(snap, probes.WINDOW, probes.CHILD_FRAME)
    reported = [e for e in entries if e["probe_id"] == probe.id]

    assert reported, "a realm that errored was reported as agreement"
    assert reported[0]["status"] in (diff.CHANGED, diff.INCONCLUSIVE)


# --- AC5: a realm ABSENT from the snapshot is reported, never skipped ---------


def test_a_realm_absent_from_the_snapshot_is_reported_not_silently_skipped():
    """AC5 / Collision 3. The comparator must not agree over zero readings.

    ``_realm`` answers ``{}`` both for a realm that read nothing and for a realm
    the snapshot has never heard of. Those are opposite facts, and the key
    intersection downstream used to turn the second into an empty result — i.e.
    silent agreement. That is not a hypothetical: it is what every snapshot
    recorded before this realm existed looks like.
    """
    probe = _frame_identity_probe()
    snap = {
        "probes": {
            probes.WINDOW: {probe.id: {"value": {"selfIsTop": True}}},
            # child_frame is simply not in this document — an older snapshot.
        }
    }

    entries = diff.diff_realms(snap, probes.WINDOW, probes.CHILD_FRAME)

    assert entries, (
        "a snapshot missing the child_frame realm entirely compared as "
        "agreement — the comparator reported consensus over zero readings"
    )
    reported = [e for e in entries if e["probe_id"] == probe.id]
    assert reported
    assert reported[0]["status"] == diff.INCONCLUSIVE
    assert "child_frame" in reported[0]["detail"]

    # And it must COUNT, so a caller gating on the inconclusive tally cannot
    # exit 0 on a comparison that read nothing.
    assert diff.inconclusive_count(entries) >= 1


def test_an_absent_realm_is_reported_from_either_side():
    """AC5. The guard is symmetric — order of arguments must not decide it."""
    snap = {"probes": {probes.WINDOW: {"realm.kind": {"value": {"a": 1}}}}}

    left_missing = diff.diff_realms(snap, probes.CHILD_FRAME, probes.WINDOW)
    right_missing = diff.diff_realms(snap, probes.WINDOW, probes.CHILD_FRAME)

    assert left_missing, "an absent LEFT realm compared as agreement"
    assert right_missing, "an absent RIGHT realm compared as agreement"
    assert diff.inconclusive_count(left_missing) >= 1
    assert diff.inconclusive_count(right_missing) >= 1


# --- AC7: the two existing realms are untouched -------------------------------


def test_the_window_worker_comparison_is_unchanged_by_the_new_realm():
    """AC7. Adding a realm must not perturb the pair that already worked.

    Note this test is EXPECTED to stay green under AC6's falsification revert —
    it asserts unchanged behaviour, so a revert of the realm addition must not
    make it fail. It is the control on the falsification, not a subject of it.
    """
    snap = {
        "probes": {
            probes.WINDOW: {"realm.kind": {"value": {"hasDocument": True}}},
            probes.WORKER: {"realm.kind": {"value": {"hasDocument": False}}},
        }
    }

    entries = diff.diff_realms(snap, probes.WINDOW, probes.WORKER)

    assert [e["status"] for e in entries] == [diff.CHANGED]
    assert entries[0]["probe_id"] == "realm.kind"


# PS-232. ONE definition of the child-frame guard, called both by the guard
# test below and by the test that proves the guard still bites.
#
# Round 2 review caught the previous spelling: the proof test carried its own
# copy of this literal and its own copy of the predicate, so it exercised a
# COPY and never read the real one. Adding "webgl.readback" here — which
# re-permits exactly the widening PS-210 installed the guard against — left
# that proof green. Two copies of a literal is the same drift hazard
# test_the_two_records_share_one_expression_so_they_cannot_drift exists to
# prevent one file away; a guard's proof must fail when the GUARD goes soft,
# which it can only do by reading the guard itself.
#
# PS-247 added the two residue twins. THIS IS THE MOST DANGEROUS EDIT IN THAT
# SLICE and it is made deliberately, not as a test fix to turn a red green.
# What the guard forbids is a record that PRE-DATES the child-frame realm
# silently gaining it; what this set holds is the records that were BORN with
# the realm, for which the guard's predicate is not merely inapplicable but
# actively wrong — `_child_frame_guard_violations` flags on TWO clauses, and a
# CHILD_FRAME_ONLY record trips both (it declares the realm, and its realms
# tuple is neither BOTH nor WINDOW_ONLY). So membership here is what makes such
# a record legal at all, and every id added must be a genuinely new record.
#
# `realm.bootMarkers.childFrame` and `realm.seedRecoverable.childFrame` are new
# records introduced BY this realm — they are not the pre-existing
# `realm.bootMarkers` / `realm.seedRecoverable`, which remain BOTH, remain
# subject to this guard, and are untouched. `webgl.readback` is still NOT here
# and is still subject, which is the property
# test_the_guard_still_covers_the_record_the_realm_was_forbidden_on pins.
_REALM_NATIVE_IDS = frozenset(
    {
        "realm.frameIdentity",
        "webgl.readback.childFrame",
        "realm.bootMarkers.childFrame",
        "realm.seedRecoverable.childFrame",
    }
)


def _records_predating_the_child_frame_realm(inventory):
    """The records the guard applies to: every one that PRE-DATES this realm."""
    return [p for p in inventory if p.id not in _REALM_NATIVE_IDS]


def _child_frame_guard_violations(inventory):
    """Records breaking the guard: a PRE-EXISTING record declaring the realm.

    Empty means the invariant holds. Returns the offenders rather than a bool
    so the guard test can name them, keeping one definition of the predicate
    instead of trading shared code for a readable failure.
    """
    return [
        p
        for p in _records_predating_the_child_frame_realm(inventory)
        if probes.CHILD_FRAME in p.realms
        or p.realms not in (probes.BOTH, probes.WINDOW_ONLY)
    ]


def test_the_existing_probe_records_kept_their_realms():
    """AC7. Every pre-existing record keeps exactly the realms it declared.

    The new realm arrives as a NEW record, so no existing vector silently
    started being evaluated somewhere it was never validated.

    Deliberately does NOT assert the inventory SIZE. ``probes.py``'s stated
    contract is that "adding a vector MUST mean adding a record to PROBES and
    nothing else" — a count pinned here would break that, reddening a file
    named for the child-frame realm when someone adds an unrelated vector they
    never touched. The per-record loop below is the assertion the docstring
    promises, it scales, and it is what catches an existing probe silently
    gaining the realm. A DROPPED probe is owned by
    test_verify_baseline.py::test_a_probe_dropped_from_the_inventory_is_caught.
    """
    # PS-232. The set of records this realm ARRIVED with, rather than the one
    # literal id PS-210 could name when it was the only one. The invariant is
    # unchanged and is stated in the docstring above — a PRE-EXISTING vector
    # must not silently gain the realm — but the old spelling could not express
    # it: excluding a single hardcoded id made "declares child_frame" and "is
    # realm.frameIdentity" the same predicate, so it refused a NEW record too.
    #
    # That is stricter than this test's own docstring, and stricter in a
    # direction that defeats the realm's purpose. `compare_profiles` builds its
    # work list from `must_differ_probes() x probe.realms`, so the realm can
    # only enter the unlinkability question if some INDEPENDENT record declares
    # it — and the only id the old spelling permitted, realm.frameIdentity, is
    # SHARED and must stay so (frame position is not seed-derived; classifying
    # it INDEPENDENT would report every pair of profiles as COLLIDING). So the
    # old spelling did not merely forbid widening, it forbade the child realm
    # from ever being on the must-differ axis at all, by any means.
    #
    # WHAT IS STILL FORBIDDEN, and the reason this is not a relaxation: every
    # record that existed BEFORE this realm did keeps exactly the realms it
    # declared. Widening `webgl.readback` to (WINDOW, CHILD_FRAME) — the shape
    # PS-232 first tried and PS-210 installed this guard against — still fails
    # here, and test_widening_a_pre_existing_record_is_still_caught below
    # proves it, by driving THIS SAME predicate rather than a copy of it.
    pre_existing = _records_predating_the_child_frame_realm(probes.PROBES)
    assert pre_existing, "premise: there are records older than this realm"

    offenders = _child_frame_guard_violations(probes.PROBES)
    assert not offenders, (
        "a record that pre-dates the child-frame realm declares it: "
        + ", ".join(f"{p.id}{p.realms}" for p in offenders)
    )


def test_widening_a_pre_existing_record_is_still_caught():
    """The guard above did not go soft — PS-232 proves it still bites.

    Amending a guard is the moment to demonstrate it still catches what it was
    installed for, rather than to assert that it does. This rebuilds the exact
    shape PS-210 forbade (a PRE-EXISTING record gaining the realm) and drives
    ``_child_frame_guard_violations`` — **the predicate the guard test itself
    calls, not a copy of it** — so the amendment cannot widen into a no-op
    without this test going red.

    Round 2 review measured why that distinction is the whole test: the earlier
    spelling re-implemented the predicate locally with its own copy of the
    grandfathered literal, so re-permitting `webgl.readback` in the REAL set
    (which allows precisely the widening PS-210 forbade) left this green. A
    guard that has gone soft looks exactly like a guard that is working, so the
    proof has to read the guard.
    """
    live = next(p for p in probes.PROBES if p.id == "webgl.readback")
    assert live.realms == probes.WINDOW_ONLY, "premise: this record is window-only"

    # The real inventory passes...
    assert not _child_frame_guard_violations(probes.PROBES)

    # ...and the forbidden shape does not, on either spelling of widening.
    for widened in ((probes.WINDOW, probes.CHILD_FRAME), probes.EVERY_REALM):
        mutated = tuple(
            probes.Probe(p.id, widened, p.expr, note=p.note, variance=p.variance)
            if p.id == "webgl.readback"
            else p
            for p in probes.PROBES
        )
        offenders = _child_frame_guard_violations(mutated)
        assert [p.id for p in offenders] == ["webgl.readback"], (
            f"a pre-existing record widened to {widened} was NOT caught — "
            "the amended guard has gone soft on the very shape it exists for"
        )


def test_the_guard_still_covers_the_record_the_realm_was_forbidden_on():
    """The grandfathered set cannot quietly grow to cover a pre-existing record.

    ``_child_frame_guard_violations`` only bites records OUTSIDE
    ``_REALM_NATIVE_IDS``, so widening that set is the one edit that disarms the
    guard while every assertion above still reads true — the exact mutation
    round 2 review landed and measured green. Pinning membership directly is
    what makes that edit loud: the ids exempt from the guard are the records
    that were BORN with the realm — `realm.frameIdentity` and the
    `*.childFrame` twins — and `webgl.readback` is not one of them. Named as a
    SET rather than as a COUNT deliberately: PS-247 grew the exemption from two
    ids to four, which falsified the previous spelling of this sentence, and a
    count re-breaks on the next child-realm record while a description does not.
    """
    live_ids = {p.id for p in probes.PROBES}
    assert _REALM_NATIVE_IDS <= live_ids, "premise: exempt ids are real records"

    # The pre-existing vector the realm was forbidden on must remain SUBJECT to
    # the guard — i.e. never exempt from it.
    assert "webgl.readback" not in _REALM_NATIVE_IDS
    assert "webgl.readback" in {
        p.id for p in _records_predating_the_child_frame_realm(probes.PROBES)
    }

    # PS-247. The exemption grew by two ids, so the same property is pinned for
    # the two records those twins were derived FROM. The hazard is precise: an
    # author reaching for "make the guard accept my child-realm record" could
    # exempt `realm.bootMarkers` instead of `realm.bootMarkers.childFrame` —
    # one character of difference, every assertion above still true, and the
    # residue vector the six historical leaks were caught by would be free to
    # silently gain a realm it was never validated in.
    for pre_existing in ("realm.bootMarkers", "realm.seedRecoverable"):
        assert pre_existing not in _REALM_NATIVE_IDS
        assert pre_existing in {
            p.id for p in _records_predating_the_child_frame_realm(probes.PROBES)
        }
        record = next(p for p in probes.PROBES if p.id == pre_existing)
        assert record.realms == probes.BOTH, (
            f"{pre_existing} silently gained a realm — it must stay "
            "(window, worker); the child realm belongs to its twin"
        )

    # And every exempt id is a REAL record declaring ONLY the child realm.
    # An exemption for anything else is bookkeeping that disarms the guard.
    for native in _REALM_NATIVE_IDS - {"realm.frameIdentity"}:
        record = next(p for p in probes.PROBES if p.id == native)
        assert record.realms == probes.CHILD_FRAME_ONLY, (
            f"{native} is exempt from the guard but is not a child-realm-only "
            "record — the exemption is covering a widening"
        )
