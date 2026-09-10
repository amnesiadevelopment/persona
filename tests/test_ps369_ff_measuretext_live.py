"""What a STOCK Firefox reports on ``measureText`` — and why persona ships no
Firefox measureText spoof.

PS-369. Read this file's reason for existing before changing it.

THE QUESTION, AND WHY ONLY A CONTROL COULD SETTLE IT
----------------------------------------------------
``measuretext_ext.py`` REPAIRS noise the Chromium fingerprint engine injects
into ``Canvas::measureText`` — a single multiplicative factor (~1e-6, its sign
set by the seed) applied to every returned metric — and Chromium installs the
builder unconditionally. Firefox's launch path reaches none of it:
``spawn_browser`` returns on the Firefox arm before the extension list is
assembled, and ``measureText``/``measuretext``/``TextMetrics``/
``actualBoundingBox`` appear zero times across ``invisible_launch.py``.

``tests/test_engine_masking_matrix.py`` recorded that cell as
``position_not_established``, and its note named the exact gap:

    The Chromium builder repairs noise the FINGERPRINT ENGINE injects into
    Canvas::measureText, so a plausible position is 'not applicable — a
    different engine does not inject that noise'. Not recorded, so not claimed.

⭐ THAT SENTENCE IS A CLAIM ABOUT TWO BROWSERS, AND THE TREE ONLY HELD ONE.
The 20 committed Firefox artifacts under ``readings/`` answer the first half
unanimously — persona's Firefox reports real, font-differentiated widths and
leaves ``measureText`` unwrapped — and every one of them is persona's own
engine. They cannot answer the second half at any sample size, and more of them
would not help. So the corpus is not the instrument here: a STOCK Firefox is.

WHAT THE READING FOUND
----------------------
``readings/ps369-2026-09-10/`` — a stock Firefox 151.0 and persona's
``firefox-20`` engine (also 151.0, so NOT a version comparison), read on the
same host in the same run over the same channel against the same page, plus the
product's own ``spawn_browser`` path. On BOTH rows this cell rests on, all three
reading arms agree in BOTH realms:

* ``masking.measureText`` — ``[native code]`` in the window realm,
  ``absent:undefined`` in the worker realm. NOTHING IS WRAPPED, on either
  browser. ⭐ Both halves of that realm split reproduce on STOCK too, so the
  worker row is a property of FIREFOX rather than a gap in persona's corpus.
* the NOISE SIGNATURE — ``|v| < 1`` on 0 of 14 fonts. The Chromium defect
  collapses all 14 metrics below one; neither Firefox does.

⭐⭐ AND THE SHARPER FINDING, which is what makes this cell ``NOT_APPLICABLE``
rather than merely "not needed": THE CHROMIUM VECTOR'S REPAIR CANNOT FIRE ON
THIS ENGINE AT ALL. ``patch()`` is guarded
``var corrupt = hasText && !(Math.abs(m.width) >= 1); if (!corrupt) return m;``
— real widths are ~200, so the guard returns the native metrics untouched and
the divisor never runs. That is MEASURED rather than argued: the no-op control
installs ``measuretext_ext``'s own repair on a stock Firefox and the widths are
14/14 IDENTICAL to the untouched arm, while ``masking.measureText`` stops
reading ``[native code]``. **Zero benefit, and a new tell** — the "one tell
traded for two" the ticket argued, measured on both halves.

⚠️ WHY THE ABSOLUTE WIDTHS ARE NOT COMPARED HERE, and it is not an omission.
``fonts.measureText`` is in ``ENV_SENSITIVE_PROBES``
(``src/services/verify/baseline.py``) because everything that moves a text width
between two machines — installed fonts, rasteriser, hinting — moves it. The
reading host had THREE font families, so its magnitudes are a statement about
fontconfig. Every assertion below is therefore on a HOST-INVARIANT row: the
noise signature (a browser reporting real text at 16px is two orders of
magnitude clear of the defect's threshold on any font stack) and the wrapper
shape. An absolute-width assertion would go red on the next host and teach its
reader to re-baseline reflexively.

WHY THESE TESTS LAUNCH A REAL BROWSER
-------------------------------------
Because the claim is about WHAT A PAGE RECEIVES on a browser persona does not
ship, and no source-side oracle can reach it. A test asserting that no
measureText spoof exists on persona's launch path asserts the half that was
never in doubt, and would pass identically whether a real Firefox injected
canvas noise or not — which is the half the cell's position actually rests on.
That is this project's own recurring failure mode (PS-11: *"tests that assert on
what was written, not on what happens"*), and the sibling live suites
``test_ps312_ff_geolocation_live``, ``test_ps330_ff_mediadevices_live`` and
``test_ps350_ff_stealth_live`` exist for exactly the same reason on their own
vectors.

⛔ THE STOCK ARM IS A CONTROL AND IS NOT THE PRODUCT. Nothing it reports may be
attributed to persona's behaviour in EITHER direction — the
``readings/ps159-2026-08-25`` rule. It is launched directly by
``scripts/ps369_measuretext_control.py``, deliberately never through the
product's engine resolver.

THE CONTROLS ARE NOT OPTIONAL
-----------------------------
A browser that never started and a browser that reports nothing produce
byte-identical rows. So each arm proves its channel (``1+1``), its origin (a
REAL ``http://127.0.0.1`` document, never ``data:`` or ``about:blank``), its
secure context, its engine family, that the document actually RENDERED, and
that a canvas 2d context EXISTS — before a single row is read. The harness
refuses to emit an arm whose gates did not pass.

⚠️ THE REVEAL ARM IS WINDOW-SCOPED, AND ITS UNMOVED WORKER ROW IS ITS SCOPE
RATHER THAN A DEAD PROBE. R patches ``CanvasRenderingContext2D.prototype`` in
the PAGE realm over marionette; the worker harness builds a fresh ``Worker``
from a Blob with its own globals, which a page-realm prototype patch never
reaches. So the WORKER leg has its own liveness control — INTER-ARM VARIATION,
asserted in ``test_the_worker_realm_is_a_discriminating_instrument``: three
browsers returned three DIFFERENT worker vectors on one host in one run, which
a probe reporting a constant could not do.

SKIPPED, NEVER SILENTLY PASSED, wherever the engine, a display, the launcher or
the stock control binary is missing. An absent instrument must not read as a
clean bill of health — and this suite needs one thing its siblings mostly do
not: an upstream Firefox, which no CI job downloads today. It therefore SKIPS on
CI and runs where a control has been provisioned. The committed reading under
``readings/ps369-2026-09-10/`` is what CI reads instead, and the offline half
below re-reads it on every run with no browser at all.
"""

import json
import os
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
READING = REPO_ROOT / "readings" / "ps369-2026-09-10" / "reading.json"

#: The window-realm shape of an UNWRAPPED ``measureText``, and the worker-realm
#: shape of a realm that carries no such function to inspect. Written as DATA so
#: a test cannot quietly assert a different pair than the record states.
#:
#: ⚠️ THE TWO REALMS SAY DIFFERENT THINGS and are deliberately not collapsed:
#: the window row says NOTHING IS WRAPPED, and the worker row says the probe
#: finds no ``CanvasRenderingContext2D`` there at all. A single expected value
#: covering both would claim more than the reading holds.
UNWRAPPED = {
    "window": "function measureText() {\n    [native code]\n}",
    "worker": "absent:undefined",
}

#: The three arms that are READINGS. The two control arms are deliberately not
#: in this tuple: they carry INJECTED state, and comparing them as readings
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
            f"firefox:measuretext cell cites it; restore it or restate the cell."
        )
    return json.loads(READING.read_text(encoding="utf-8"))


def _arm(reading: dict, label: str) -> dict:
    for arm in reading["arms"]:
        if arm["arm"] == label:
            return arm
    pytest.fail(f"the committed reading carries no arm named {label!r}")


def test_the_committed_reading_still_says_what_the_cell_claims():
    # The anti-rot guard, and the load-bearing offline test. The matrix cell
    # rests on this artifact; an artifact that changed its answer while the cell
    # kept citing it is the exact silent degradation this matrix exists to
    # prevent.
    #
    # BOTH host-invariant rows are asserted, on all three reading arms, in both
    # realms — the wrapper shape and the noise signature. The absolute widths
    # are deliberately NOT asserted; see the module docstring.
    reading = _committed_reading()
    for label in READING_ARMS:
        arm = _arm(reading, label)
        assert not arm.get("unobtained"), (
            f"the committed reading's {label!r} arm was never obtained "
            f"({arm.get('unobtained')!r}), so it is not evidence for anything"
        )
        for realm in ("window", "worker"):
            assert reading["wrapper_shape"][label][realm] == UNWRAPPED[realm], (
                f"{label}/{realm} in the committed reading no longer shows "
                f"measureText unwrapped, so 'not applicable' is no longer what "
                f"it establishes. RE-MEASURE with "
                f"scripts/ps369_measuretext_control.py, then restate the cell."
            )
            signature = reading["noise_signature"][label][realm]
            assert signature["readable"] is True, (
                f"{label}/{realm}'s font widths are not all numeric in the "
                f"committed reading, so its noise signature is not a reading"
            )
            assert signature["total"] == 14, (
                f"{label}/{realm} covers {signature['total']} fonts, not the "
                f"probe's 14 — the reading measured a different vector"
            )
            assert signature["below_one"] == 0, (
                f"{label}/{realm} in the committed reading now shows "
                f"{signature['below_one']}/14 metrics collapsed below one — "
                f"the signature of the Chromium canvas noise this vector "
                f"repairs. If a Firefox now injects it, the cell is WRONG: "
                f"RE-MEASURE with scripts/ps369_measuretext_control.py, then "
                f"restate."
            )


def test_the_committed_reading_was_taken_in_a_secure_non_opaque_context():
    # The opaque-origin trap, asserted rather than trusted. A reading taken on
    # data: or about:blank measures the CONTEXT and reports it as the browser's
    # posture.
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
    #
    # ``canvas2d_available`` rides with them because it is THIS probe's own
    # dead-instrument shape: with no 2d context ``fonts.measureText`` returns
    # null for a reason that has nothing to do with either engine.
    reading = _committed_reading()
    for label in READING_ARMS:
        gates = _arm(reading, label)["gates"]
        assert gates["channel_arith"] == 2, f"{label}'s eval channel never answered"
        assert "Gecko" in str(gates["user_agent"]), f"{label} is not a Gecko browser"
        assert gates["layout_width"] > 0, (
            f"{label}'s document parsed but never RENDERED, so its readings "
            f"describe a page nobody laid out — and this probe measures TEXT"
        )
        assert gates["canvas2d_available"] is True, (
            f"{label} had no canvas 2d context, so its fonts.measureText rows "
            f"say nothing about either engine"
        )
        assert gates["worker_available"] is True


def test_the_reveal_control_proves_the_probes_are_live():
    # ⭐ THE TEST THAT MAKES EVERY ROW ABOVE MEAN SOMETHING. Three browsers
    # agreeing on "not wrapped, not noised" is exactly the shape a probe that
    # reports a constant would produce, so the reading carries a mutation in
    # the OPPOSITE direction — the DEFECT itself — and this asserts it moved
    # BOTH rows the cell rests on.
    reading = _committed_reading()
    reveal = _arm(reading, "R_reveal_control_stock")
    assert not reveal.get("unobtained")
    assert reveal["reveal_installed"] == "reveal-installed"

    assert reading["noise_signature"]["R_reveal_control_stock"]["window"][
        "below_one"
    ] == 14, (
        "installing a multiplicative noise wrapper over measureText did not "
        "collapse the reported widths, so the noise signature reads 'clean' "
        "unconditionally and every 0/14 above is worthless. Fix the instrument."
    )
    assert (
        reading["wrapper_shape"]["R_reveal_control_stock"]["window"]
        != UNWRAPPED["window"]
    ), (
        "wrapping measureText in a JS function did not move "
        "masking.measureText off '[native code]', so that probe cannot see a "
        "wrapper and the cell's 'nothing is wrapped' half rests on nothing. "
        "Fix the instrument."
    )


def test_the_worker_realm_is_a_discriminating_instrument():
    # ⚠️ THE WORKER LEG'S OWN LIVENESS CONTROL, and it exists because the reveal
    # arm CANNOT cover this realm. R patches the page realm's
    # CanvasRenderingContext2D.prototype; the worker harness builds a fresh
    # Worker from a Blob with its own globals, which that patch never reaches.
    # So R's unmoved worker row is its SCOPE, not a dead probe — and reading it
    # the second way would be wrong.
    #
    # What establishes this realm as live is INTER-ARM VARIATION: a probe
    # reporting a constant could not return three DIFFERENT vectors from three
    # different browsers on one host in one run.
    liveness = _committed_reading()["realm_liveness"]
    for realm in ("window", "worker"):
        assert len(liveness[realm]["arms_read"]) >= 3, (
            f"fewer than three reading arms produced a {realm} vector, so "
            f"inter-arm variation cannot establish this realm as live"
        )
        assert liveness[realm]["distinct_vectors"] >= 2, (
            f"every arm returned an IDENTICAL {realm} font-width vector, which "
            f"is what a probe reporting a constant looks like. The reveal "
            f"control cannot cover the worker realm, so this variation is the "
            f"only thing establishing it as a live instrument. Fix the "
            f"instrument before trusting any {realm} row."
        )


def test_the_noop_control_measures_the_regression_the_cell_declines():
    # ⭐⭐ THE CELL'S CENTRAL CLAIM, ASSERTED AGAINST A MEASUREMENT RATHER THAN
    # AN ARGUMENT — and the test that makes this cell NOT_APPLICABLE rather
    # than NOT_COVERED_RECORDED.
    #
    # NOT_APPLICABLE requires that the engine cannot reach the configuration
    # the vector addresses. Here that is precise and checkable: the vector
    # REPAIRS an injected noise factor, its patch is guarded on the metric
    # being corrupt, and on a browser reporting real widths that guard refuses
    # to fire. So the arm installs measuretext_ext's own repair on a stock
    # Firefox and both halves are asserted:
    #
    #   1. the arithmetic is a STRUCTURAL NO-OP — the widths do not move, and
    #   2. the wrapper is OBSERVABLE ANYWAY — masking.measureText moves.
    #
    # Zero benefit, and a new tell. If (1) ever fails, the repair CAN fire on
    # this engine and the cell's constraint is gone.
    reading = _committed_reading()
    noop = _arm(reading, "N_noop_control_stock")
    assert not noop.get("unobtained")
    assert noop["noop_installed"] == "noop-installed"

    untouched = reading["rows"]["A1_stock_control"]
    with_repair = reading["rows"]["N_noop_control_stock"]
    for realm in ("window", "worker"):
        assert (
            with_repair[realm]["fonts.measureText"]
            == untouched[realm]["fonts.measureText"]
        ), (
            f"installing measuretext_ext's repair on a STOCK Firefox CHANGED "
            f"the reported {realm} widths. The firefox:measuretext cell claims "
            f"the repair is structurally inert on this engine — its 'corrupt' "
            f"guard cannot fire where widths are real — and that claim is now "
            f"false. RE-MEASURE with scripts/ps369_measuretext_control.py, "
            f"then restate the cell."
        )
    assert (
        reading["wrapper_shape"]["N_noop_control_stock"]["window"]
        != UNWRAPPED["window"]
    ), (
        "installing the repair no longer moves masking.measureText off "
        "'[native code]'. The cell's 'one tell traded for two' rests on the "
        "wrapper being observable; if it is not, re-read PS-22's four axes and "
        "restate the cell rather than deleting this assertion."
    )


def test_the_reading_names_its_shelf_life():
    # AC7, asserted rather than assumed. A recorded position is only honest
    # while a reader can tell WHAT it was read on — and on THIS probe that must
    # include the HOST FONT STACK, not only the platform and the build:
    # fonts.measureText is in ENV_SENSITIVE_PROBES precisely because installed
    # fonts move it, so a record naming only the build would let a future
    # reader attribute a fontconfig difference to the engine.
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
    assert env.get("installed_font_families"), (
        "the reading does not record which font families were installed on the "
        "host. fonts.measureText is env-sensitive by its own registry entry, "
        "so a reader cannot tell an engine difference from a fontconfig one "
        "without it."
    )
    # The version confound is REMOVED rather than merely disclosed: both arms
    # are 151.0, so an A1/A2 divergence cannot be a version difference.
    assert env["stock_version"] == env["engine_version"], (
        "the stock control and the subject are no longer the same Firefox "
        "version, so a divergence between them is no longer attributable to "
        "persona's engine patches alone. Re-take the reading on matched "
        "versions, or state the confound in the cell."
    )


def test_the_reading_measured_the_probes_own_font_list():
    # The inventory-drift guard. This cell is about a FIXED 14-font vector; a
    # reading taken against a different list would be a reading of a different
    # thing, and the counts above (0/14, 14/14) would silently mean something
    # else. The probe is the source of truth, so the two are compared.
    from src.services.verify.probes import PROBES

    probe = next(p for p in PROBES if p.id == "fonts.measureText")
    reading = _committed_reading()
    recorded = reading["probe_font_list"]
    assert len(recorded) == 14
    for font in recorded:
        assert f"'{font}'" in probe.expr, (
            f"the committed reading measured {font!r}, which the probe's own "
            f"font list no longer carries. The inventory moved: RE-MEASURE "
            f"with scripts/ps369_measuretext_control.py rather than leaving "
            f"the cell resting on a reading of a different vector."
        )


def test_the_cross_host_ladder_is_recorded_with_its_direction():
    # ⚠️ THE HONESTY CLAUSE, and it pins a reading that is easy to invert.
    #
    # The committed corpus was taken on OTHER hosts with full font stacks; this
    # reading was taken on a 3-family host. So corpus agreement is NOT an
    # "arms agree" score: a browser reporting what THIS host's fontconfig
    # resolves must score LOW, and a HIGH score means the arm reproduced widths
    # for fonts this host does not have — i.e. it carried its own font set.
    #
    # That direction is the finding: persona's product path reproduces its
    # committed vector on a host that cannot account for it, and stock
    # reproduces none of it. This asserts the SHAPE of that ladder rather than
    # its numbers, so it survives a re-measure on another host.
    ladder = _committed_reading()["corpus_agreement"]
    for realm in ("window", "worker"):
        stock = ladder["A1_stock_control"][realm]["matching"]
        product = ladder["B_persona_product_path"][realm]["matching"]
        assert product > stock, (
            f"in the {realm} realm the STOCK control now reproduces the "
            f"committed corpus at least as well as persona's product path "
            f"({stock} vs {product} of 14). The reading's conclusion — that "
            f"persona carries its own font set and masks the host stack — "
            f"rests on this ordering. RE-MEASURE and restate."
        )


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
    return os.environ.get("PS369_STOCK_FIREFOX", "").strip()


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
        "no STOCK firefox control: set PS369_STOCK_FIREFOX to an upstream "
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

    Module-scoped for the reason PS-312 records and PS-330/PS-350 repeat: a
    per-launch display torn down in the same ``finally`` leaves the NAME of a
    dead Xvfb in ``DISPLAY``, and ``_ensure_display`` hands a non-empty
    ``DISPLAY`` straight back — so the second launch connects to nothing and the
    run degrades to skips, which look like success.

    Obtained rather than merely gated: the skip gate above is satisfied by Xvfb
    being INSTALLED, so under a plain ``pytest`` with no DISPLAY exported these
    tests would RUN and wait forever on a display nobody started.
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
    from scripts.ps369_measuretext_control import (
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
        proc, sock = _launch_bare(
            binary, 2846, "/tmp/ps369-live-stock", "/tmp/ps369-live-stock.err"
        )
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


@requires_display
@requires_launcher
@requires_stock
@_LIVE_TIMEOUT
def test_a_real_firefox_neither_wraps_nor_noises_measure_text(stock_arm):
    # ⭐ THE CELL'S LOAD-BEARING TEST, and the half no corpus of persona's own
    # recordings could ever answer. The firefox:measuretext cell reads
    # NOT_APPLICABLE because a real Firefox injects no canvas-metric noise for
    # the vector to repair and leaves measureText native — so installing the
    # builder would run an inert repair and add an observable wrapper.
    #
    # This asserts the OUTCOME the cell depends on, live, against a browser
    # persona does not ship — and on the HOST-INVARIANT rows only, so it does
    # not go red merely for running on a different font stack.
    #
    # If this goes RED, Gecko changed and the cell is wrong: RE-MEASURE with
    # scripts/ps369_measuretext_control.py, then restate.
    arm = _obtained(stock_arm)
    from scripts.ps369_measuretext_control import _noise_signature, _rows

    rows = _rows(arm)
    for realm in ("window", "worker"):
        assert rows[realm]["masking.measureText"] == UNWRAPPED[realm], (
            f"a STOCK Firefox's {realm}-realm measureText no longer reads "
            f"{UNWRAPPED[realm]!r}. persona's Firefox leaving it native is "
            f"then a DIVERGENCE from an ordinary browser rather than agreement "
            f"with one. Restate the firefox:measuretext cell."
        )
        signature = _noise_signature(rows[realm]["fonts.measureText"])
        assert signature["readable"] is True and signature["total"] == 14
        assert signature["below_one"] == 0, (
            f"a STOCK Firefox now reports {signature['below_one']}/14 canvas "
            f"text metrics collapsed below one in the {realm} realm — the "
            f"signature of the noise measuretext_ext repairs. If Gecko injects "
            f"it, this cell is WRONG and the vector may be applicable after "
            f"all. RE-MEASURE and restate."
        )


@requires_display
@requires_launcher
@requires_stock
@_LIVE_TIMEOUT
def test_the_live_stock_reading_agrees_with_the_committed_one(stock_arm):
    # The committed artifact is what CI reads; this is the check that it still
    # describes a live browser rather than a browser that once existed. A drift
    # here means the reading has aged out of truth — which is precisely what a
    # recorded shelf life is for.
    #
    # ⚠️ COMPARED ON THE HOST-INVARIANT ROWS, NOT ON THE WIDTHS. The committed
    # reading was taken on a 3-font host, and asserting its magnitudes would
    # make this test a fontconfig comparison that reds on any other machine —
    # training its reader to re-baseline reflexively, which is the habit that
    # lets a genuine change through.
    arm = _obtained(stock_arm)
    from scripts.ps369_measuretext_control import _noise_signature, _rows

    live_rows = _rows(arm)
    committed = _committed_reading()
    for realm in ("window", "worker"):
        assert (
            live_rows[realm]["masking.measureText"]
            == committed["wrapper_shape"]["A1_stock_control"][realm]
        ), (
            f"a live stock Firefox's {realm} wrapper shape no longer matches "
            f"what readings/ps369-2026-09-10/ recorded. The committed reading "
            f"has aged out: RE-MEASURE with "
            f"scripts/ps369_measuretext_control.py and restate the cell."
        )
        live_signature = _noise_signature(live_rows[realm]["fonts.measureText"])
        assert (
            live_signature["below_one"]
            == committed["noise_signature"]["A1_stock_control"][realm]["below_one"]
        ), (
            f"a live stock Firefox's {realm} noise signature no longer matches "
            f"the committed reading's. RE-MEASURE and restate the cell."
        )
