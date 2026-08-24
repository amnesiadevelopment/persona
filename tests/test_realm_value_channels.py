"""PS-139 — the last two `__persona*` VALUE channels, after they left the global.

WHAT THIS FILE OWNS. Two cross-realm channels used to be written as plain,
ENUMERABLE properties of the global object, so `Object.keys(window)` found them
in one line, in every realm at every depth:

    device_ext.py       G.__personaScreenWH = { W, H }   the profile's screen
    measuretext_ext.py  G.__personaMtFactor = f          the measureText noise

These were materially worse than the eleven booleans PS-93 removed. A boolean
said "a persona tool is installed". These hand the page LIVE SESSION VALUES —
and `__personaMtFactor` is the DIVISOR THAT INVERTS the text-metrics spoof, so
one property read undid the mask outright. They now ride the non-enumerable
per-realm `Object.__pnaRealm` slot that already shipped for the idempotency
guard, so the fix adds NO new global name (worker_wrap.realm_slot_js).

THIS SUITE PINS BOTH HALVES OF THAT TRADE, and the second half is the one worth
the runtime. Removing the globals without keeping the channel working does not
produce a red assertion — it produces SILENT DIVERGENCE BETWEEN REALMS, where a
page and its iframe report different monitors, or repair text to different
widths. That is a worse tell than the global it replaces, because a checker
reads it as two machines. So the crossing assertions below drive an ACTUAL
child->top read and compare the OBSERVABLE (screen.width, measureText().width),
never the source text.

WHY NOT A SOURCE-TEXT SWEEP, anywhere in this file. A grep for the retired name
passes on a build that merely RENAMED the channel, and fails on a build that
kept the behaviour while rewording a comment — it is sensitive to exactly the
wrong thing. Worse, the leaves narrate their own history, so a comment naming
the retired channel keeps the string in the shipped artifact and a text
assertion goes GREEN on a build that no longer has the behaviour. Every
assertion here reads `Object.getOwnPropertyNames(globalThis)` in a live realm,
which is what the shipped probe reads (`src/services/verify/probes.py`, regex
`/^__pna|^__persona/`) and what a fingerprinter runs. See knowledge PS-11 on
tests that assert what was written rather than what happens.

THE WORKER CROSSING IS NOT TESTED HERE, DELIBERATELY, and this is the one thing
to read before adding a worker case. `WorkerGlobalScope` has no `top`, so a
worker never read either value: `getFactor()` finds no `top`, finds no local
factor, and lands in the leaf's own "no DOM here + not learned yet" branch —
before this change and after it, identically. The channel that FUNCTIONS is
iframe->top, and that is what this file drives. A test that appeared to exercise
a worker reading the top's learned factor would be asserting a crossing that has
never existed. Carrying a runtime-learned value into a worker needs a mechanism
nobody has specified (the leaf crosses as SOURCE TEXT built at new-Worker time,
which cannot carry a value learned later); that gap is real, is unchanged by
this slice, and is tracked separately.

Realm machinery is the shared harness in tests/realm_harness.py, not a second
one — two harnesses drift, and a drifting realm harness fails by UNDER-REPORTING
coverage, which is the failure mode this subsystem cannot see.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

from src.services.browser.device_ext import build_device_extension
from src.services.browser.measuretext_ext import build_measuretext_extension
from tests.realm_harness import HARNESS

SEED = 0x1234ABCD
GEN = 3

# The two names this ticket moved off the global object. Named as data so the
# assertions can report WHICH channel leaked rather than a bare boolean.
VALUE_CHANNELS = ["__personaScreenWH", "__personaMtFactor"]

# The text stub's un-noised width, and the noise the engine applies. The repair
# only engages when the noised width is < 1 (the leaf's own `corrupt` test), so
# these model fingerprint-chromium's collapse-to-tiny noise, not a mild jitter.
TRUE_WIDTH = 50.0
NOISE = 0.01
NOISED_WIDTH = TRUE_WIDTH * NOISE          # 0.5 — what an unrepaired realm reports
REPAIRED_WIDTH = TRUE_WIDTH                # 50.0 — what a repaired realm reports

# Window extents chosen so the two realms would pick DIFFERENT resolutions if the
# child re-measured its own extent instead of reusing the top's. The control
# assertion below proves that divergence is real rather than assumed — if these
# ever coincide, that control goes red rather than the crossing test passing
# vacuously. It has already earned its keep: the first pair tried here
# ((1300,700) / (2000,1300)) resolved to 2560x1080 and 2560x1440 — different
# HEIGHTS but the same WIDTH — so a width-only comparison would have been green
# on a dead channel. These two differ in BOTH dimensions.
#
# The child is SMALLER than the top, which is also the realistic shape: an
# iframe's extent is bounded by its parent's.
TOP_EXTENT = (1400, 800)      # resolves to 1920x1200 unaided
CHILD_EXTENT = (1000, 600)    # resolves to 2560x1080 unaided


def _requires_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    return node


# ---------------------------------------------------------------------------
# The probe. Three realm shapes, built on the shared harness.
# ---------------------------------------------------------------------------
#
# A realm here is a real `vm` context, so it genuinely has its OWN `Object` —
# which is the whole point: `__pnaRealm` hangs off `G.Object`, so a per-realm
# slot and a shared one are distinguishable only in a harness where the
# intrinsics really differ. `top` is wired the way a browser wires it: the top
# realm's `top` is ITSELF (so `top !== G` is false and it does not read its own
# slot as if it were a parent's), and a child frame's `top` is the top realm's
# global object.
#
# The CHILD deliberately has a document with NO documentElement. That is not a
# contrivance to force the crossing — it is the real shape of a child frame at
# `run_at: document_start`, which is when these content scripts run
# (`all_frames: true`, see both manifests). Such a realm cannot calibrate
# locally, so it must obtain the factor from the top or report noise.
_PROBE = HARNESS + r"""
const DEVICE = fs.readFileSync(process.argv[2], "utf8");
const MT = fs.readFileSync(process.argv[3], "utf8");

const TRUE_WIDTH = __TRUE_WIDTH__;
const NOISE = __NOISE__;

// A realm that can be measured: a screen to spoof, a window extent, and a
// noised measureText. `withDom` decides whether this realm can calibrate the
// noise itself (a top window can; a document_start child frame cannot).
function makeMeasurableRealm(extent, withDom) {
  const r = makeRealm();
  vm.runInContext(`
    globalThis.screen = { width: 0, height: 0, availWidth: 0, availHeight: 0,
                          colorDepth: 0, pixelDepth: 0 };
    globalThis.outerWidth = ${extent[0]};
    globalThis.innerWidth = ${extent[0]};
    globalThis.outerHeight = ${extent[1]};
    globalThis.innerHeight = ${extent[1]};
    globalThis.navigator = { userAgent: "stub" };

    // The engine's noised measureText: it collapses the width so far that the
    // leaf's own corruption test (abs(width) >= 1) fires.
    globalThis.CanvasRenderingContext2D = function CanvasRenderingContext2D(){};
    globalThis.CanvasRenderingContext2D.prototype.measureText =
      function measureText(t) {
        return { width: String(t).length > 0 ? ${TRUE_WIDTH} * ${NOISE} : 0 };
      };

    // The un-noised DOM path the leaf calibrates against. A realm WITHOUT a
    // documentElement is a real document_start child frame: trueWidth() returns
    // null there, so it has nothing to calibrate from and must use the top's.
    globalThis.document = ${withDom} ? {
      documentElement: { appendChild: function(){}, removeChild: function(){} },
      createElement: function () {
        return { style: {}, textContent: "",
                 getBoundingClientRect: function () { return { width: ${TRUE_WIDTH} }; } };
      },
    } : { documentElement: null, body: null, createElement: function () { return null; } };
  `, r.ctx);
  return r;
}

const globalOf = (r) => vm.runInContext("globalThis", r.ctx);

// Wire `top` the way the browser does. A top window's `top` is itself.
function asTopWindow(r) { r.ctx.top = globalOf(r); return r; }
function asChildOf(child, top) { child.ctx.top = globalOf(top); return child; }

const run = (r, src) => vm.runInContext(src, r.ctx);

// THE LEAK ASSERTION, read exactly as the shipped probe reads it: the names on
// the GLOBAL, via getOwnPropertyNames. A non-enumerable property would NOT
// satisfy this by hiding — probes.py walks names, not keys.
const markers = (r) => vm.runInContext(
  "Object.getOwnPropertyNames(globalThis).filter(k=>/^__pna|^__persona/.test(k))", r.ctx);

const screenOf = (r) => vm.runInContext(
  "({W: screen.width, H: screen.height, avail: screen.availHeight})", r.ctx);

// The OBSERVABLE for the text channel: what a page in this realm actually
// measures. Not "was a factor stored" — what the width comes out as.
const measured = (r) => vm.runInContext(
  "new CanvasRenderingContext2D().measureText('hello').width", r.ctx);

// What the slot holds, to prove the values MOVED rather than vanished.
const slotOf = (r) => vm.runInContext(`(function(){
  var s = Object.__pnaRealm;
  if (!s) return null;
  return { hasScreenWH: !!s.screenWH, screenWH: s.screenWH || null,
           mtFactor: (typeof s.mtFactor === 'number') ? s.mtFactor : null };
})()`, r.ctx);

// Is the slot itself reachable by sweeping the GLOBAL? It must not be: it hangs
// off Object, which is the honest bound this slice inherits and does not close.
const slotOnGlobal = (r) => vm.runInContext(
  "Object.getOwnPropertyNames(globalThis).indexOf('__pnaRealm') !== -1", r.ctx);

const out = {};

// --- the top realm: measures its own extent, calibrates its own factor -----
const top = asTopWindow(makeMeasurableRealm([__TOP_W__, __TOP_H__], true));
run(top, DEVICE);
run(top, MT);
out.topScreen = screenOf(top);
out.topMeasured = measured(top);
out.topMarkers = markers(top);
out.topSlot = slotOf(top);
out.topSlotOnGlobal = slotOnGlobal(top);

// --- a same-origin CHILD FRAME: different extent, no documentElement -------
// It must reuse the top's geometry and the top's learned factor. This is the
// crossing the channels exist for, driven as an actual child->top READ.
const child = asChildOf(makeMeasurableRealm([__CHILD_W__, __CHILD_H__], false), top);
run(child, DEVICE);
run(child, MT);
out.childScreen = screenOf(child);
out.childMeasured = measured(child);
out.childMarkers = markers(child);

// --- THE CONTROL: the same child shape with NO top ------------------------
// Proves the crossing test above can DISTINGUISH. Without a parent to read,
// this realm re-measures its own extent and cannot repair its text at all — so
// if the channel were dead, the child would look like THIS, not like the top.
const orphan = makeMeasurableRealm([__CHILD_W__, __CHILD_H__], false);
orphan.ctx.top = null;
run(orphan, DEVICE);
run(orphan, MT);
out.orphanScreen = screenOf(orphan);
out.orphanMeasured = measured(orphan);

// --- THE OTHER DIRECTION: a DOM-BEARING child that measures FIRST --------
// Everything above drives top->child, because every child above is built
// `withDom` false. That models a document_start frame correctly but freezes it:
// the leaf reads the DOM at CALL time (`var doc = G.document` sits inside
// measureText), so by the time page script actually measures, a real child
// frame HAS a document. A child that measures before the top does is the normal
// case in a browser, not a corner — and it is the only thing that drives
// setFactor's upward publish.
//
// The realms below are built on a SECOND top that has deliberately not measured
// yet, so the factor cannot have come from above: whatever `kidB` reads, `kidA`
// put there through the top.
const pubTop = asTopWindow(makeMeasurableRealm([__TOP_W__, __TOP_H__], true));
run(pubTop, DEVICE);
run(pubTop, MT);
// NOT measured here on purpose — this top has no learned factor yet.
out.pubTopFactorBeforeKid = (slotOf(pubTop) || {}).mtFactor;

// kidA CAN calibrate (it has a documentElement) and does so first.
const kidA = asChildOf(makeMeasurableRealm([__CHILD_W__, __CHILD_H__], true), pubTop);
run(kidA, DEVICE);
run(kidA, MT);
out.kidAMeasured = measured(kidA);
out.pubTopFactorAfterKid = (slotOf(pubTop) || {}).mtFactor;

// kidB is a DOM-less sibling: it can only repair by reading what kidA
// published to the shared top. This is the payoff of the upward publish.
const kidB = asChildOf(makeMeasurableRealm([__CHILD_W__, __CHILD_H__], false), pubTop);
run(kidB, DEVICE);
run(kidB, MT);
out.kidBMeasured = measured(kidB);

// And the top itself, measuring last, converges on the child's factor.
out.pubTopMeasuredLast = measured(pubTop);

// --- a WORKER realm, reached as TEXT through the real bootstrap -----------
// Asserted for the LEAK only. It has no `top` and never read either value; see
// this file's module docstring before adding a crossing assertion here.
const workerPayload = spawn(top);
const worker = makeMeasurableRealm([800, 600], false);
run(worker, workerPayload);
out.workerMarkers = markers(worker);
out.workerHasTop = vm.runInContext("typeof top", worker.ctx);

console.log(JSON.stringify(out));
"""


def _probe_source(top_extent=TOP_EXTENT, child_extent=CHILD_EXTENT):
    return (
        _PROBE.replace("__TRUE_WIDTH__", repr(TRUE_WIDTH))
        .replace("__NOISE__", repr(NOISE))
        .replace("__TOP_W__", str(top_extent[0]))
        .replace("__TOP_H__", str(top_extent[1]))
        .replace("__CHILD_W__", str(child_extent[0]))
        .replace("__CHILD_H__", str(child_extent[1]))
    )


def _build_scripts(tmp_path, resolution=None):
    """The REAL generated content scripts, exactly as shipped.

    Driven through the builders rather than read from the templates: the slot
    helper is spliced at build time, so a template read would test the
    placeholder and not the thing that ships.
    """
    d = pathlib.Path(tmp_path)
    dev_dir = build_device_extension(SEED, str(d / "dev"), GEN, resolution, "windows")
    mt_dir = build_measuretext_extension(str(d / "mt"))
    return (
        (pathlib.Path(dev_dir) / "device.js").read_text(encoding="utf-8"),
        (pathlib.Path(mt_dir) / "measuretext.js").read_text(encoding="utf-8"),
    )


def _run(tmp_path, device_js, mt_js, *, top_extent=TOP_EXTENT, child_extent=CHILD_EXTENT):
    node = _requires_node()
    d = pathlib.Path(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    dev = d / "device.js"
    dev.write_text(device_js, encoding="utf-8")
    mt = d / "measuretext.js"
    mt.write_text(mt_js, encoding="utf-8")
    probe = d / "probe.js"
    probe.write_text(_probe_source(top_extent, child_extent), encoding="utf-8")
    out = subprocess.run(
        [node, str(probe), str(dev), str(mt)],
        capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def shipped(tmp_path_factory):
    d = tmp_path_factory.mktemp("value_channels")
    return _run(d, *_build_scripts(d / "build"))


# ---------------------------------------------------------------------------
# Controls. Every assertion below is vacuous if the leaves did not run.
# ---------------------------------------------------------------------------

def test_the_leaves_applied_at_all(shipped):
    # "did not spoof" and "spoofed consistently" are both "no divergence", so
    # without this the crossing assertions could pass on two dead realms.
    assert shipped["topScreen"]["W"] > 0, "device_ext did not spoof the top realm's screen"
    assert shipped["topMeasured"] == pytest.approx(REPAIRED_WIDTH), (
        "measuretext_ext did not repair the top realm's noised width "
        f"({shipped['topMeasured']} != {REPAIRED_WIDTH}) — the realm this suite "
        "calibrates from is not actually calibrating"
    )


def test_the_control_realm_really_would_diverge(shipped):
    # The load-bearing control for AC4. The child realm and the orphan realm are
    # IDENTICAL except that one can reach a top. If the orphan happened to agree
    # with the top anyway, the crossing assertions could not tell a working
    # channel from a dead one, and would be green on both.
    assert shipped["orphanScreen"]["W"] != shipped["topScreen"]["W"], (
        "the control realm picked the SAME geometry as the top without reading "
        f"it ({shipped['orphanScreen']['W']}) — the crossing test below cannot "
        "distinguish a working channel from a dead one. Re-pick TOP_EXTENT / "
        "CHILD_EXTENT so the two realms would genuinely resolve differently."
    )
    assert shipped["orphanMeasured"] == pytest.approx(NOISED_WIDTH), (
        "the control realm repaired its text without a top to learn from "
        f"({shipped['orphanMeasured']}) — it must report the RAW noised width, "
        "or the factor crossing below is not what makes the child work"
    )


# ---------------------------------------------------------------------------
# AC1 — no `__pna`/`__persona` name on the global object, in any realm.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("realm", ["topMarkers", "childMarkers", "workerMarkers"])
def test_no_persona_family_name_is_reachable_by_enumerating_the_global(shipped, realm):
    # THE LEAK ITSELF. Read as the shipped probe reads it (getOwnPropertyNames,
    # so a non-enumerable property does not satisfy this by hiding) and as a
    # fingerprinter runs it. This is an EMPTINESS gate, not the subset gate
    # tests/test_realm_guard.py could only make: with these two channels moved,
    # a realm carrying device_ext + measuretext_ext has nothing left to declare.
    assert shipped[realm] == [], (
        f"{realm}: persona-family global(s) {shipped[realm]} — a name here is "
        f"either a value channel back on the global or a NEW coordination "
        f"channel introduced under the __pna/__persona prefix"
    )


def test_the_slot_is_not_itself_on_the_global(shipped):
    # The fix must not trade two names for a third. `__pnaRealm` hangs off
    # `Object`, so the GLOBAL sweep — the one probes.py runs — gains nothing.
    #
    # THE HONEST BOUND, inherited from PS-93 and NOT closed here: a detector
    # walking `getOwnPropertyNames(Object)` still finds `__pnaRealm`. That is
    # out of scope, is stated in worker_wrap.py in place, and this assertion
    # deliberately does not claim otherwise.
    assert shipped["topSlotOnGlobal"] is False, (
        "__pnaRealm appeared on the GLOBAL object — the slot must hang off "
        "Object, or this change swapped two swept names for one"
    )


def test_the_values_moved_into_the_slot_rather_than_vanishing(shipped):
    # The counterpart to the emptiness gate above, and the reason it is not
    # satisfied by simply deleting the channels. Deleting them would also make
    # the sweep read clean — while breaking the crossing the next tests pin.
    slot = shipped["topSlot"]
    assert slot is not None, "the top realm has no __pnaRealm slot at all"
    assert slot["hasScreenWH"], "the resolved geometry is not in the slot"
    assert slot["screenWH"]["W"] == shipped["topScreen"]["W"], (
        "the slot's geometry disagrees with the geometry the realm reports — "
        f"slot {slot['screenWH']} vs screen {shipped['topScreen']}"
    )
    assert slot["mtFactor"] == pytest.approx(NOISE), (
        f"the learned measureText factor is not in the slot (got {slot['mtFactor']})"
    )


# ---------------------------------------------------------------------------
# AC4 — the iframe->top crossing still works. This is the property the channels
# exist for, and the failure it guards is SILENT DIVERGENCE BETWEEN REALMS.
# ---------------------------------------------------------------------------

def test_a_child_frame_reuses_the_tops_screen_geometry(shipped):
    # An ACTUAL child->top read: the child has a different window extent, so
    # re-measuring locally would resolve a different monitor (the control test
    # above proves that concretely). A page and its iframe reporting different
    # screens is a self-inflicted unlinkability tell — a checker reads it as two
    # machines — and is worse than the global this replaces.
    assert shipped["childScreen"] == shipped["topScreen"], (
        "the child frame did not reuse the top's screen geometry "
        f"(child {shipped['childScreen']} vs top {shipped['topScreen']}) — the "
        "realms now disagree about the monitor"
    )


def test_a_child_frame_repairs_text_from_the_tops_learned_factor(shipped):
    # The other half, and the one with teeth: this child CANNOT calibrate (no
    # documentElement, the real shape of a document_start frame), so the only
    # way it reports a repaired width is by reading the top's learned factor
    # across the realm boundary. The control test above pins that the same realm
    # without a top reports the raw noised width instead.
    assert shipped["childMeasured"] == pytest.approx(shipped["topMeasured"]), (
        "the child frame did not repair text to the same width as the top "
        f"(child {shipped['childMeasured']} vs top {shipped['topMeasured']}) — "
        "a scanner measuring text in the two realms sees different fonts"
    )
    assert shipped["childMeasured"] == pytest.approx(REPAIRED_WIDTH), (
        f"the child reported {shipped['childMeasured']}, not the repaired "
        f"{REPAIRED_WIDTH} — it is serving the raw noised geometry"
    )


def test_a_child_that_calibrates_first_publishes_the_factor_up_to_the_top(shipped):
    # THE OTHER DIRECTION, and the one the tests above could not see. Every
    # child realm above is built without a DOM, so the factor only ever flowed
    # top->child and `setFactor`'s upward publish was driven by nothing:
    # replacing it with `if (false) ts.mtFactor = f;` left the whole suite green.
    #
    # This is the control that makes the claim non-vacuous: the top of THIS
    # group has run both leaves but has never measured, so it has no factor of
    # its own. Anything that appears in its slot got there from below.
    assert shipped["pubTopFactorBeforeKid"] is None, (
        "the publishing group's top already had a learned factor before the "
        f"child measured ({shipped['pubTopFactorBeforeKid']}) — this group's "
        "top must not measure, or the assertions below cannot tell an upward "
        "publish from the top's own calibration and pass vacuously"
    )
    assert shipped["kidAMeasured"] == pytest.approx(REPAIRED_WIDTH), (
        f"the DOM-bearing child did not calibrate locally (got "
        f"{shipped['kidAMeasured']}) — it has a documentElement, so it must"
    )
    assert shipped["pubTopFactorAfterKid"] == pytest.approx(NOISE), (
        "the child calibrated but did not publish its factor UP to the top "
        f"(top slot holds {shipped['pubTopFactorAfterKid']}) — `setFactor`'s "
        "second write is what puts it there, and without it the child's "
        "siblings have nothing to read"
    )


def test_a_dom_less_sibling_repairs_from_the_factor_the_other_child_published(shipped):
    # The payoff, and the assertion with teeth: kidB has no documentElement and
    # the top never measured, so the ONLY path to a repaired width is the factor
    # kidA published upward. Suppress that publish and this realm reports the
    # raw noised width while its sibling reports the repaired one — a page and
    # its iframe repairing text to 50 and 0.5, which is precisely the silent
    # cross-realm divergence this file's docstring says it exists to prevent.
    assert shipped["kidBMeasured"] == pytest.approx(shipped["kidAMeasured"]), (
        "two sibling frames repaired text to DIFFERENT widths (DOM-less "
        f"{shipped['kidBMeasured']} vs calibrating {shipped['kidAMeasured']}) — "
        "a scanner measuring text in both realms sees two machines"
    )
    assert shipped["kidBMeasured"] == pytest.approx(REPAIRED_WIDTH), (
        f"the DOM-less sibling reported {shipped['kidBMeasured']}, not the "
        f"repaired {REPAIRED_WIDTH} — it is serving the raw noised geometry "
        "because nothing published a factor it could reach"
    )
    # The top measuring LAST converges on the same value. Stated as agreement
    # rather than as proof of the publish: this top has a DOM, so it could also
    # have calibrated its own identical factor. The discriminating assertion is
    # kidB's above, not this one.
    assert shipped["pubTopMeasuredLast"] == pytest.approx(REPAIRED_WIDTH), (
        f"the top realm reported {shipped['pubTopMeasuredLast']} after its "
        f"children had already agreed on {REPAIRED_WIDTH}"
    )


def test_the_worker_realm_has_no_top_and_is_asserted_for_the_leak_only(shipped):
    # NOT a crossing test, and this assertion exists to stop one being added by
    # mistake. A worker has no `top`, so it never read either value — before
    # this change or after. If this ever reports a `top`, the premise of this
    # file's worker exclusion has changed and the exclusion must be re-derived,
    # not quietly relied on.
    assert shipped["workerHasTop"] == "undefined", (
        "a worker realm now has `top` — this file (and the ticket) excludes the "
        "worker crossing on the grounds that it does not exist. Re-derive it."
    )


# ---------------------------------------------------------------------------
# AC5 — device_ext's FORCED/measured precedence is UNCHANGED.
#
# The ordering is: the top realm's published geometry wins over FORCED, which
# wins over the measured extent. PINNED, not re-derived: the FORCED-over-
# measured half carries a recorded incident (#167 — the render scale leaked
# under --force-device-scale-factor when the forced value was gated on the
# window extent), and device_ext.py narrates it in place.
# ---------------------------------------------------------------------------

def test_forced_resolution_wins_over_the_measured_extent(tmp_path):
    # INCIDENT #167. The top realm has a real extent it could measure, and a
    # FORCED resolution that is deliberately not in the resolution table — so
    # only an honored FORCED can produce it.
    forced = (1234, 1000)
    got = _run(tmp_path / "forced", *_build_scripts(tmp_path / "b", resolution=forced))
    assert (got["topScreen"]["W"], got["topScreen"]["H"]) == forced, (
        "a user-picked resolution was not honored outright "
        f"(got {got['topScreen']}) — gating it on the window extent is exactly "
        "what leaked the render scale in #167"
    )


def test_the_tops_published_geometry_wins_over_forced_in_a_child(tmp_path):
    # The top of the ordering. The child is built WITH a FORCED resolution and
    # still must defer to what the top published — otherwise a forced profile
    # would make every child frame disagree with its parent.
    forced = (1234, 1000)
    got = _run(tmp_path / "prec", *_build_scripts(tmp_path / "b", resolution=forced))
    assert (got["topScreen"]["W"], got["topScreen"]["H"]) == forced
    assert got["childScreen"] == got["topScreen"], (
        "the child frame preferred its own FORCED value over the geometry the "
        f"top published (child {got['childScreen']} vs top {got['topScreen']}) — "
        "the precedence order changed"
    )


def test_the_measured_extent_is_used_when_there_is_neither(shipped):
    # The bottom of the ordering: no published geometry, no FORCED, so the top
    # realm resolves from its own extent — and must pick something that actually
    # covers it, which is the property the resolution table exists to provide.
    assert shipped["topScreen"]["W"] >= TOP_EXTENT[0], (
        f"the measured branch picked {shipped['topScreen']} — smaller than the "
        f"window extent {TOP_EXTENT} it must cover"
    )
    assert shipped["topScreen"]["avail"] < shipped["topScreen"]["H"], (
        "availHeight must subtract a work-area inset; equal values are a VM tell"
    )


# ---------------------------------------------------------------------------
# FALSIFICATION (NON-WAIVABLE, AC3). Restore ONE channel's global assignment
# with the rest of the diff in place; the leak gate must go RED NAMING IT.
#
# A source-text assertion is not acceptable for this and is not used: it passes
# on a build that merely renames the channel, which is the mutation most likely
# to be made by accident. These mutate the SHIPPED script and re-run the realms.
# ---------------------------------------------------------------------------

_RESTORE = {
    # (anchor in the generated script, the global write to put back beside it)
    "__personaScreenWH": (
        "      if (ss && !ss.screenWH) ss.screenWH = { W: W, H: H };",
        "      if (ss && !ss.screenWH) ss.screenWH = { W: W, H: H };\n"
        "      try { if (!G.__personaScreenWH) G.__personaScreenWH = { W: W, H: H }; } catch (e) {}",
    ),
    "__personaMtFactor": (
        "        var s = __pnaSlot(G, true);\n        if (s) s.mtFactor = f;",
        "        var s = __pnaSlot(G, true);\n        if (s) s.mtFactor = f;\n"
        "        try { G.__personaMtFactor = f; } catch (e) {}",
    ),
}


@pytest.mark.parametrize("channel", VALUE_CHANNELS)
def test_falsification_a_restored_value_channel_is_caught_by_name(tmp_path, channel):
    device_js, mt_js = _build_scripts(tmp_path / "b")
    anchor, restored = _RESTORE[channel]
    target_is_device = channel == "__personaScreenWH"
    src = device_js if target_is_device else mt_js

    assert anchor in src, (
        f"could not find the write site for {channel} in the GENERATED script. "
        f"The leaf was edited without updating this control, so the "
        f"falsification below would mutate nothing and pass vacuously. Fix the "
        f"anchor to match the shipped write — do NOT delete this test."
    )
    mutated = src.replace(anchor, restored, 1)
    assert mutated != src, "could not build the falsification control"

    got = _run(
        tmp_path / "falsify",
        mutated if target_is_device else device_js,
        mt_js if target_is_device else mutated,
    )
    assert channel in got["topMarkers"], (
        f"the leak gate did NOT catch {channel} restored to the global "
        f"(saw {got['topMarkers']}) — it would not have caught the original "
        f"leak either, so it is not testing what this ticket is about"
    )
