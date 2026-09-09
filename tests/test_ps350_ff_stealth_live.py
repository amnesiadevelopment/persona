"""What a STOCK Firefox exposes on the two APIs ``stealth_ext`` adds — and why
persona ships no Firefox stealth spoof.

PS-350. Read this file's reason for existing before changing it.

THE QUESTION, AND WHY ONLY A CONTROL COULD SETTLE IT
----------------------------------------------------
``stealth_ext.py`` re-adds two Chrome-only Navigator APIs that CreepJS counts
toward "like headless" — ``navigator.connection.downlinkMax`` and
``ServiceWorkerRegistration.prototype.index`` — and Chromium installs it
unconditionally. Firefox's launch path reaches none of it: ``spawn_browser``
returns on the Firefox arm before the extension list is assembled, and
``downlinkMax``/``ContentIndex`` appear zero times across
``invisible_launch.py``.

``tests/test_engine_masking_matrix.py`` recorded that cell as
``position_not_established``, and its note named the exact gap:

    A plausible position is 'not applicable — Firefox exposes neither API, so a
    real Firefox is missing them too' — but plausible is not recorded, and this
    file will not mint a position the tree does not hold.

⭐ THAT SENTENCE IS A CLAIM ABOUT TWO BROWSERS, AND THE TREE ONLY HELD ONE.
The 20 committed Firefox artifacts under ``readings/`` answer the first half
unanimously — persona's Firefox exposes neither API, in both realms, across
four engine builds — and every one of them is persona's own engine. They cannot
answer the second half at any sample size, and more of them would not help. So
the corpus is not the instrument here: a STOCK Firefox is, and nothing in this
project had ever read one.

WHAT THE READING FOUND
----------------------
``readings/ps350-2026-09-09/`` — a stock Firefox 151.0 and persona's
``firefox-29`` engine (also 151.0, so NOT a version comparison), read on the
same host in the same run over the same channel against the same page, plus the
product's own ``spawn_browser`` path. **12/12 rows identical on all three
reading arms, in BOTH realms**, and identical to the committed corpus.

⭐⭐ AND THE SHARPER FINDING, which is what makes this cell ``NOT_APPLICABLE``
rather than merely "not needed": HALF THE CHROMIUM VECTOR CANNOT FIRE ON THIS
ENGINE AT ALL. ``stealth_ext``'s downlinkMax shim is guarded
``if (conn && !('downlinkMax' in conn))``, and Firefox exposes no
``navigator.connection`` for it to hang off — so the shim is a STRUCTURAL
NO-OP, not an unnecessary one. That is measured rather than argued: the reveal
control installs ``stealth_ext``'s own two shims and moves ``contentIndex`` to
``hasIndex: true`` while leaving ``stealth.connection`` at ``null``, and a
second control that MANUFACTURES the whole ``connection`` object moves the same
probe to ``{present: true, hasDownlinkMax: true, downlinkMax: "Infinity"}``.

WHY THESE TESTS LAUNCH A REAL BROWSER
-------------------------------------
Because the claim is about WHAT A PAGE RECEIVES on a browser persona does not
ship, and no source-side oracle can reach it. A test asserting that
``downlinkMax`` is absent from persona's launch path asserts the half that was
never in doubt, and would pass identically whether a real Firefox exposed these
APIs or not — which is the half the cell's position actually rests on. That is
this project's own recurring failure mode (PS-11: *"tests that assert on what
was written, not on what happens"*), and the sibling live suites
``test_ps312_ff_geolocation_live`` and ``test_ps330_ff_mediadevices_live`` exist
for exactly the same reason on their own vectors.

⛔ THE STOCK ARM IS A CONTROL AND IS NOT THE PRODUCT. Nothing it reports may be
attributed to persona's behaviour in EITHER direction — the
``readings/ps159-2026-08-25`` rule. It is launched directly by
``scripts/ps350_stealth_control.py``, deliberately never through the product's
engine resolver.

THE CONTROLS ARE NOT OPTIONAL
-----------------------------
A browser that never started and a browser that exposes nothing produce
byte-identical rows: every probe reads "absent" and the two arms "agree". So
each arm proves its channel (``1+1``), its origin (a REAL ``http://127.0.0.1``
document, never ``data:`` or ``about:blank`` — see the opaque-origin trap
below), its secure context, its engine family and that the document actually
RENDERED, before a single stealth row is read. The harness refuses to emit an
arm whose gates did not pass.

⚠️ THE OPAQUE-ORIGIN TRAP, recorded because two earlier attempts at this
measurement hit it. On ``data:`` and ``about:blank`` the origin is opaque and
service workers are unavailable, so ``ServiceWorkerRegistration`` reads
``undefined`` on a browser that exposes it perfectly well — a clean,
reproducible, FALSE divergence on a CreepJS-counted row. Every arm here reads a
real loopback document and asserts ``isSecureContext``.

⚠️ THE IDENTITY TRAP, which is this ticket's own. A first run produced an arm
disagreeing with three fresh isolation runs of the SAME binary on one row, and
nothing in the artifact could adjudicate which binary had answered.
``navigator.buildID`` cannot: the engine PINS it, and both binaries report
``20181001000000`` — the one identifier a page could offer is exactly the one
this engine spoofs. The harness now records the answering process's install
directory off ``/proc`` and DISCARDS any arm that did not run what its label
names.

SKIPPED, NEVER SILENTLY PASSED, wherever the engine, a display, the launcher or
the stock control binary is missing. An absent instrument must not read as a
clean bill of health — and note that this suite needs one thing its siblings do
not: an upstream Firefox, which no CI job downloads today. It therefore SKIPS on
CI and runs where a control has been provisioned. The committed reading under
``readings/ps350-2026-09-09/`` is what CI reads instead, and
``test_the_committed_reading_still_says_what_the_cell_claims`` re-reads it on
every run with no browser at all.
"""

import json
import os
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
READING = REPO_ROOT / "readings" / "ps350-2026-09-09" / "reading.json"

#: The rows this cell is about, and the values the reading established for
#: them. Written as DATA so a test cannot quietly assert a different set than
#: the one the record states.
EXPECTED_ROWS = {
    "stealth.connection": None,
    "stealth.contentIndex": {"hasIndex": False},
    "apiPresence[navigator.connection]": "undefined",
    "apiPresence[NetworkInformation]": "undefined",
    "apiPresence[ServiceWorkerRegistration]": "function",
}

#: The three arms that are READINGS. The two control arms are deliberately not
#: in this tuple: they carry INJECTED state and comparing them as readings
#: would be comparing the instrument with itself.
READING_ARMS = (
    "A1_stock_control",
    "A2_persona_engine_bare",
    "B_persona_product_path",
)


# --- the offline half: no browser, runs everywhere including CI --------------


def _committed_reading() -> dict:
    if not READING.exists():
        pytest.fail(
            f"the committed reading is gone from {READING}. The "
            f"firefox:stealth cell cites it; restore it or restate the cell."
        )
    return json.loads(READING.read_text(encoding="utf-8"))


def _arm(reading: dict, label: str) -> dict:
    for arm in reading["arms"]:
        if arm["arm"] == label:
            return arm
    pytest.fail(f"the committed reading carries no arm named {label!r}")


def test_the_committed_reading_still_says_what_the_cell_claims():
    # The anti-rot guard, and the one test here that needs no browser. The
    # matrix cell rests on this artifact; an artifact that changed its answer
    # while the cell kept citing it is the exact silent degradation this whole
    # matrix exists to prevent.
    reading = _committed_reading()
    for label in READING_ARMS:
        arm = _arm(reading, label)
        assert not arm.get("unobtained"), (
            f"the committed reading's {label!r} arm was never obtained "
            f"({arm.get('unobtained')!r}), so it is not evidence for anything"
        )
        for realm in ("window", "worker"):
            rows = reading["rows"][label][realm]
            assert rows == EXPECTED_ROWS, (
                f"{label}/{realm} in the committed reading no longer matches "
                f"what the firefox:stealth cell claims. RE-MEASURE with "
                f"scripts/ps350_stealth_control.py, then restate the cell."
            )


def test_the_committed_reading_was_taken_in_a_secure_non_opaque_context():
    # The opaque-origin trap, asserted rather than trusted. On data: or
    # about:blank the service-worker surface is legitimately absent, so a
    # reading taken there would record a FALSE absence on
    # ServiceWorkerRegistration and every conclusion drawn from it would be
    # about the CONTEXT rather than about either browser.
    reading = _committed_reading()
    for label in READING_ARMS:
        gates = _arm(reading, label)["gates"]
        assert gates["secure_context"] is True, f"{label} was not a secure context"
        assert str(gates["origin"]).startswith("http://127.0.0.1"), (
            f"{label} was read on origin {gates['origin']!r}, not a real "
            f"loopback origin"
        )
        assert gates["origin"] != "null", f"{label} was read on an OPAQUE origin"


def test_the_committed_reading_proves_its_own_channel_and_render():
    # The dead-channel trap. A browser that never started reports every row as
    # absent, which is byte-identical to a perfect match — so the gates are
    # asserted as evidence, not merely recorded.
    reading = _committed_reading()
    for label in READING_ARMS:
        gates = _arm(reading, label)["gates"]
        assert gates["channel_arith"] == 2, f"{label}'s eval channel never answered"
        assert "Gecko" in str(gates["user_agent"]), f"{label} is not a Gecko browser"
        assert gates["layout_width"] > 0, (
            f"{label}'s document parsed but never RENDERED, so its readings "
            f"describe a page nobody laid out"
        )
        assert gates["worker_available"] is True


def test_the_reveal_controls_prove_the_probes_are_live():
    # ⭐ THE TEST THAT MAKES EVERY NULL ABOVE MEAN SOMETHING. Five absences
    # agreeing across two binaries is exactly the shape a probe that reports
    # absence unconditionally would produce, so the reading carries two
    # mutations in the OPPOSITE direction and this asserts they moved the rows.
    #
    # The two are NOT redundant, and the difference between them is the cell's
    # actual finding. R installs stealth_ext's OWN shims and moves contentIndex
    # while leaving connection null — because that shim is guarded on
    # `conn &&` and Firefox has no navigator.connection to hang it off. M
    # manufactures the whole object and moves the connection row too. So:
    # the connection probe IS live, and stealth_ext's downlinkMax half is a
    # STRUCTURAL NO-OP on this engine rather than an unnecessary one.
    reading = _committed_reading()

    reveal = _arm(reading, "R_reveal_control_stock")
    assert not reveal.get("unobtained")
    assert reveal["reveal_installed"] == "reveal-installed"
    assert reading["rows"]["R_reveal_control_stock"]["window"][
        "stealth.contentIndex"
    ] == {"hasIndex": True}, (
        "installing stealth_ext's OWN ContentIndex shim did not move the "
        "contentIndex probe, so that probe reports 'absent' unconditionally "
        "and every reading above is worthless. Fix the instrument."
    )

    manufacture = _arm(reading, "M_manufacture_control_stock")
    assert not manufacture.get("unobtained")
    assert manufacture["manufacture_installed"] == "manufactured"
    moved = reading["rows"]["M_manufacture_control_stock"]["window"][
        "stealth.connection"
    ]
    assert isinstance(moved, dict) and moved.get("hasDownlinkMax") is True, (
        "manufacturing a navigator.connection carrying downlinkMax did not "
        "move the connection probe, so that probe reports null unconditionally "
        "and the cell's connection half rests on nothing. Fix the instrument."
    )
    # ...and the half that did NOT move under R is the finding, not a gap.
    assert (
        reading["rows"]["R_reveal_control_stock"]["window"]["stealth.connection"]
        is None
    ), (
        "stealth_ext's own downlinkMax shim now moves the connection probe on "
        "Firefox, which would mean the engine grew a navigator.connection. The "
        "cell's 'structurally inapplicable' claim rests on it NOT having one — "
        "RE-MEASURE and restate."
    )


def test_the_reading_names_its_shelf_life():
    # AC6, asserted rather than assumed. A recorded position is only honest
    # while a reader can tell WHAT it was read on — and per this ticket's
    # Correction 1 that must include the HOST PLATFORM, not only the engine
    # build: PS-314 recorded a row of this very probe (the WebSerial pair)
    # moving between two artifacts whose engine_build was IDENTICAL, because
    # WebSerial is gated on the host. A record naming only the build would
    # attribute a host-gated row to the engine.
    env = _committed_reading()["environment"]
    for key in (
        "host_platform",
        "host_machine",
        "engine_build",
        "stock_version",
        "engine_version",
        "stock_sha256",
        "engine_sha256",
    ):
        assert env.get(key), f"the reading does not record {key!r}"
    # The version confound is REMOVED rather than merely disclosed: both arms
    # are 151.0, so an A1/A2 divergence cannot be a version difference.
    assert env["stock_version"] == env["engine_version"], (
        "the stock control and the subject are no longer the same Firefox "
        "version, so a divergence between them is no longer attributable to "
        "persona's engine patches alone. Re-take the reading on matched "
        "versions, or state the confound in the cell."
    )


def test_the_one_divergence_is_named_and_is_not_this_cells_rows():
    # ⛔ HONESTY CLAUSE. The full 41-key apiPresence diff is NOT empty: the
    # WebSerial pair diverges between the stock control and both persona arms
    # on this host. It is recorded here rather than left out, because
    # `ENV_SENSITIVE_PROBES` in src/services/verify/baseline.py states in its
    # own words that it "excuses" nothing — *"a future movement in
    # stealth.apiPresence still reds baseline.check exactly as it would have
    # before, and still has to be explained"* — and its scope clause says the
    # PS-314 entry covers the WebSerial pair under a platform-gate difference
    # and "says nothing about any other key of this probe".
    #
    # So this test pins BOTH halves: the divergence is exactly that pair (a
    # third key appearing is a new finding and must go red), and it touches
    # NONE of the rows this cell is about.
    reading = _committed_reading()
    for comparison, diff in reading["apiPresence_full_diff"].items():
        for realm, keys in diff.items():
            assert set(keys) <= {"Serial", "navigator.serial"}, (
                f"{comparison}/{realm} now diverges on "
                f"{sorted(set(keys) - {'Serial', 'navigator.serial'})}, which "
                f"is a NEW finding rather than the known WebSerial pair. "
                f"ENV_SENSITIVE_PROBES excuses no key of this probe — state it."
            )
            for row in EXPECTED_ROWS:
                assert row not in keys


# --- the live half: launches real browsers, skips without them ---------------


def _engine_present() -> bool:
    try:
        from src.services.browser.engine_install import is_invisible_installed

        return bool(is_invisible_installed())
    except Exception:
        return False


def _display_present() -> bool:
    return bool(os.environ.get("DISPLAY", "").strip()) or bool(shutil.which("Xvfb"))


def _launcher_present() -> bool:
    try:
        import invisible_playwright  # noqa: F401

        return True
    except Exception:
        return False


def _stock_control() -> str:
    """The STOCK Firefox binary, from the environment ONLY.

    ⛔ NEVER RESOLVED THROUGH THE PRODUCT. ``ps150_stock_control`` records the
    rule: the product's resolver REFUSES to substitute a browser found on PATH
    precisely so a stock reading can never be mistaken for the product. A stock
    control is opted into explicitly by an operator who provisioned one, or it
    does not run.
    """
    return os.environ.get("PS350_STOCK_FIREFOX", "").strip()


requires_engine = pytest.mark.skipif(
    not _engine_present(),
    reason="persona's firefox engine is not installed on this host",
)
requires_display = pytest.mark.skipif(
    not _display_present(),
    reason="no DISPLAY and no Xvfb",
)
requires_launcher = pytest.mark.skipif(
    not _launcher_present(),
    reason="invisible_playwright is not importable on this host",
)
requires_stock = pytest.mark.skipif(
    not _stock_control() or not os.path.exists(_stock_control()),
    reason=(
        "no STOCK firefox control: set PS350_STOCK_FIREFOX to an upstream "
        "Firefox binary. This is the one instrument no CI job provisions, and "
        "it is never resolved through the product's engine resolver."
    ),
)

#: Marked per-test because these are REAL browser launches and the suite's
#: default timeout is not sized for one. Inert without pytest-timeout, so this
#: can only ever relax a bound that exists, never invent one.
_LIVE_TIMEOUT = pytest.mark.timeout(900)



@pytest.fixture(scope="module")
def _display():
    """ONE display for the whole module, exported for every launch below.

    Module-scoped for the reason PS-312 records and PS-330 repeats: a per-launch
    display torn down in the same ``finally`` leaves the NAME of a dead Xvfb in
    ``DISPLAY``, and ``_ensure_display`` hands a non-empty ``DISPLAY`` straight
    back — so the second launch connects to nothing and the run degrades to
    skips, which look like success.

    Obtained rather than merely gated: the skip gate above is satisfied by Xvfb
    being INSTALLED, so under a plain ``pytest`` with no DISPLAY exported these
    tests would RUN and the headful arm would wait forever on a display nobody
    started. PS-312 paid a debugging cycle for that exact gap.
    """
    from src.services.verify.chromium_tier import _ensure_display

    inherited = os.environ.get("DISPLAY", "").strip()
    display, xvfb = _ensure_display()
    os.environ["DISPLAY"] = display
    try:
        yield display
    finally:
        if xvfb is not None:
            try:
                xvfb.terminate()
                xvfb.wait(timeout=10)
            except Exception:
                pass
        if inherited:
            os.environ["DISPLAY"] = inherited
        else:
            os.environ.pop("DISPLAY", None)


@pytest.fixture(scope="module")
def stock_arm(_display) -> dict:
    """ONE live stock-Firefox reading, shared by the live tests below.

    Module-scoped because it is a real browser launch. The bare-marionette
    route is used rather than the product's launcher for the reason PS-171's
    arm C states: stock Firefox has no juggler channel, so driving the two
    binaries over different channels would confound BINARY with CHANNEL and an
    agreement from such a pair could be attributed to neither.
    """
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from scripts.ps350_stealth_control import (
        Marionette,
        _Origin,
        _answering_install,
        _launch_bare,
        read_arm,
    )

    binary = _stock_control()
    origin = _Origin()
    origin.start()
    proc = sock = None
    try:
        proc, sock = _launch_bare(binary, 2845, "/tmp/ps350-live-stock", "/tmp/ps350-live-stock.err")
        mn = Marionette(sock)
        mn.cmd("WebDriver:NewSession", {})
        mn.cmd("WebDriver:Navigate", {"url": origin.url})
        arm = read_arm(mn.evaluate, label="live_stock")
        arm["answering_install"] = _answering_install(proc)
        return arm
    except Exception as exc:  # a live launch that failed is not evidence
        return {"arm": "live_stock", "unobtained": f"{type(exc).__name__}: {exc}"}
    finally:
        try:
            if sock:
                sock.close()
        except Exception:
            pass
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=30)
            except Exception:
                pass
        origin.stop()


def _obtained(reading: dict) -> dict:
    """The reading, or a SKIP naming why it was never taken.

    An unobtained reading must not become evidence in either direction — the
    distinction between a claim about the product and a claim about the
    instrument.
    """
    if reading.get("unobtained"):
        pytest.skip(f"UNOBTAINED reading: {reading['unobtained']}")
    return reading


@requires_engine
@requires_display
@requires_launcher
@requires_stock
@_LIVE_TIMEOUT
def test_a_real_firefox_exposes_neither_api_this_vector_adds(stock_arm):
    # ⭐ THE CELL'S LOAD-BEARING TEST, and the half no corpus of persona's own
    # recordings could ever answer. The firefox:stealth cell reads
    # NOT_APPLICABLE because a real Firefox is missing these APIs too — so a
    # spoof would manufacture a Chrome-only pair on a Gecko browser. This
    # asserts the OUTCOME the cell depends on, live, against a browser persona
    # does not ship.
    #
    # If this goes RED, Mozilla shipped one of these APIs and the cell is
    # wrong: RE-MEASURE with scripts/ps350_stealth_control.py, then restate.
    arm = _obtained(stock_arm)
    from scripts.ps350_stealth_control import _rows

    rows = _rows(arm)
    for realm in ("window", "worker"):
        assert rows[realm]["apiPresence[navigator.connection]"] == "undefined", (
            "a STOCK Firefox now exposes navigator.connection, so persona's "
            "Firefox lacking it is now a DIVERGENCE from an ordinary browser "
            "rather than agreement with one. Restate the firefox:stealth cell."
        )
        assert rows[realm]["apiPresence[NetworkInformation]"] == "undefined", (
            "a STOCK Firefox now exposes NetworkInformation. Restate the "
            "firefox:stealth cell."
        )
        assert rows[realm]["stealth.contentIndex"] == {"hasIndex": False}, (
            "a STOCK Firefox now carries 'index' on "
            "ServiceWorkerRegistration.prototype. Restate the cell."
        )


@requires_engine
@requires_display
@requires_launcher
@requires_stock
@_LIVE_TIMEOUT
def test_the_live_stock_reading_agrees_with_the_committed_one(stock_arm):
    # The committed artifact is what CI reads; this is the check that it still
    # describes a live browser rather than a browser that once existed. A drift
    # here means the reading has aged out of truth — which is precisely what a
    # recorded shelf life is for.
    arm = _obtained(stock_arm)
    from scripts.ps350_stealth_control import _rows

    live_rows = _rows(arm)
    committed = _committed_reading()["rows"]["A1_stock_control"]
    for realm in ("window", "worker"):
        assert live_rows[realm] == committed[realm], (
            f"a live stock Firefox no longer reports what "
            f"readings/ps350-2026-09-09/ recorded, in the {realm!r} realm. The "
            f"committed reading has aged out: RE-MEASURE with "
            f"scripts/ps350_stealth_control.py and restate the cell."
        )
