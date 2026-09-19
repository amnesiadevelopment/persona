"""PS-456 — the Firefox ``outer-size`` override must track a window RESIZE.

THE DEFECT, and why every existing test was green over it
---------------------------------------------------------
``_outer_size_override_script`` pins ``outerWidth``/``outerHeight`` to
``inner + chrome``. Its ``def`` helper took the value as an ARGUMENT::

    const def=(o,k,v)=>{...{get:__cloak(()=>v,'get '+k,k)...}};
    def(window,'outerWidth', window.innerWidth + 14);

``window.innerWidth + 14`` is evaluated EAGERLY, once, at init-script time. The
getter ``()=>v`` closes over a frozen number and never recomputes, so after any
window resize the page reads the size the window had AT PAGE LOAD.

No test caught it because every existing harness reads the values ONCE, with no
resize — and in that case the frozen read and a live read are identical by
construction. ``test_reported_values_are_unchanged`` in
``tests/test_ff_language_override.py`` asserts 1200+14 / 800+91 on a harness
whose ``innerWidth`` never moves; it was green before this fix and is green
after it, which is the point of AC3 rather than a gap.

THE RELATIONS (``tests/test_ps327_outer_size.py``'s vocabulary, used verbatim)
------------------------------------------------------------------------------
    R1  outer >= inner    a window contains its own content
    R2  inner <= screen   the content fits the monitor
    R3  outer <= screen   the window fits the monitor

The frozen value breaks R1 the moment the window GROWS: measured here, a
1280 -> 1920 grow reported ``outer 1294x811`` against ``inner 1920x1080``, i.e.
chrome -626x-269 — a window smaller than its own content, which is the exact
negative-chrome signature #327 deleted a fix for producing.

⭐ WHY THIS FILE ASSERTS R1, R2 AND R3 TOGETHER AND NOT R1 ALONE
#327's FIRST attempt fixed R3 by breaking R1 (it clamped the reported outer
down to the spoofed screen and produced oW-iW = -639). A test that asserts only
the relation its own fix targets cannot see that trade, and the whole suite
stays green while the window becomes incoherent a different way. So every
geometry below is read through :func:`_relations`, which reports all three.

⭐ AND WHY THE ``inner == screen`` EDGE IS ASSERTED AS A VIOLATION, ON PURPOSE
At a MAXIMIZED window the live inner EQUALS the spoofed screen (the operator's
pick is what ``kwargs["pin"]`` writes to ``screen.width``), so ``inner + chrome``
necessarily EXCEEDS it and the page reads ``outer > screen`` — R3 violated.
That is a real, known bound of the reporting layer and it is asserted here
rather than avoided by a fixture that never reaches the edge, because a fixture
that never reaches the edge tests nothing about it.

It is NOT introduced by this fix. :func:`test_the_maximized_r3_bound_predates_this_fix`
drives the PRE-FIX (eager) form at the same geometry and shows it reported the
identical ``outer 1934x1171`` against ``screen 1920x1080``. The fix makes that
reading CONSISTENT (it is now what a maximized window reports whenever it is
maximized, rather than whatever size the window happened to have at load); it
does not create it. The root is R2 — ``inner`` is the real content box and is
not spoofable at the reporting layer without breaking layout — and #327 records
that it is fixed at SOURCE by capping the window, not here.

A clamp at the screen was measured and REJECTED, not omitted: see
:func:`test_a_screen_clamp_would_move_the_no_resize_values`, which shows it
changes the reported values at every no-resize geometry — a regression in the
case the spoof was actually written for, traded for the edge-case gain.

THE VENUE, stated rather than implied
-------------------------------------
⛔ NO BROWSER IS LAUNCHED HERE. There is no engine binary, no display and no
root in the container these assertions were written in, so this drives the REAL
shipped artifact in a ``node:vm`` realm. What that venue CAN establish is the
whole of what this file claims: the emitted JS, executed, reports a value that
does or does not follow ``innerWidth``.

⭐ EVERY RESIZE ASSERTION CARRIES A POSITIVE CONTROL, and that is what makes
this a measurement rather than a dead probe. :func:`_pre_fix_form` rewrites the
shipped artifact back to the eager form; the same harness, same realm, reports
``tracked=False`` for it and ``tracked=True`` for the shipped one. A harness
that could not observe movement at all would fail the control.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

import src.services.browser.invisible_launch as il


# --- the pre-fix form, derived from the shipped one --------------------------

# The eager `def` the fix replaced. Written as a TEXTUAL rewrite of the shipped
# artifact rather than as a frozen copy, deliberately: a frozen copy drifts the
# moment anything else in the script changes and then the "control" is a
# comparison against a fossil. This way the control differs from the shipped
# script in EXACTLY the derivation, and nothing else.
_THUNK_CALL = "{get:__cloak(()=>v(),'get '+k,k),configurable:true}"
_VALUE_CALL = "{get:__cloak(()=>v,'get '+k,k),configurable:true}"
_LAZY_W = "def(window,'outerWidth', ()=>window.innerWidth + 14);"
_EAGER_W = "def(window,'outerWidth', window.innerWidth + 14);"
_LAZY_H = "def(window,'outerHeight', ()=>window.innerHeight + 91);"
_EAGER_H = "def(window,'outerHeight', window.innerHeight + 91);"


def _pre_fix_form(js):
    """The shipped script with the derivation rewound to the eager form.

    Asserts each substitution landed. A silently-failed rewrite would make the
    "control" a second copy of the shipped script, and the control would then
    agree with it for the most misleading possible reason.
    """
    out = js
    for old, new in ((_THUNK_CALL, _VALUE_CALL), (_LAZY_W, _EAGER_W), (_LAZY_H, _EAGER_H)):
        assert old in out, (
            f"the shipped script no longer contains {old!r}, so this file's "
            "pre-fix control cannot be derived from it. Update the control "
            "rather than deleting it — a resize test with no control is a "
            "probe that cannot report its own deadness."
        )
        out = out.replace(old, new)
    assert out != js
    return out


# --- harness -----------------------------------------------------------------

_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const js = fs.readFileSync(cfg.script, "utf8");

const ctx = vm.createContext({});
vm.runInContext(
  "globalThis.window = globalThis;" +
  "globalThis.screen = {width:" + cfg.screen[0] + ", height:" + cfg.screen[1] + "};" +
  "globalThis.innerWidth = " + cfg.init[0] + ";" +
  "globalThis.innerHeight = " + cfg.init[1] + ";", ctx);

vm.runInContext(js, ctx);

const read = () => vm.runInContext(
  "({inner:[window.innerWidth, window.innerHeight]," +
  " outer:[window.outerWidth, window.outerHeight]," +
  " screen:[window.screen.width, window.screen.height]})", ctx);

const before = read();
vm.runInContext(
  "globalThis.innerWidth = " + cfg.live[0] + ";" +
  "globalThis.innerHeight = " + cfg.live[1] + ";", ctx);
const after = read();

// The cloak, read IN-REALM — inside the patched Function.prototype.toString.
// Reading it from the HOST realm returns the raw arrow source and looks like a
// regression; it is not, it is the wrong realm. A page only ever sees this one.
const cloak = vm.runInContext(`
  (() => {
    const out = {};
    for (const k of ["outerWidth", "outerHeight"]) {
      const d = Object.getOwnPropertyDescriptor(window, k);
      out[k] = {
        name: d.get.name,
        length: d.get.length,
        ownProps: Object.getOwnPropertyNames(d.get).sort(),
        configurable: d.configurable,
        enumerable: d.enumerable,
        hasSetter: d.set !== undefined,
        src: Function.prototype.toString.call(d.get),
      };
    }
    return out;
  })()
`, ctx);

console.log(JSON.stringify({ before: before, after: after, cloak: cloak }));
"""


def _seen(tmp_path, screen, init, live, script=None):
    """Run the override in a fresh realm and report what a PAGE reads.

    `init` is the inner size at init-script time; `live` is the inner size
    after the (simulated) resize. `init == live` is the no-resize case.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    work = pathlib.Path(tmp_path)
    work.mkdir(parents=True, exist_ok=True)
    js = il._outer_size_override_script() if script is None else script
    (work / "script.js").write_text(js, encoding="utf-8")
    (work / "harness.js").write_text(_HARNESS, encoding="utf-8")
    (work / "cfg.json").write_text(
        json.dumps(
            {
                "script": str(work / "script.js"),
                "screen": list(screen),
                "init": list(init),
                "live": list(live),
            }
        ),
        encoding="utf-8",
    )
    out = subprocess.run(
        [node, str(work / "harness.js"), str(work / "cfg.json")],
        capture_output=True, text=True, timeout=60, encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _relations(g):
    """All three relations for one reading, as a dict — never one of them."""
    return {
        "R1": g["outer"][0] >= g["inner"][0] and g["outer"][1] >= g["inner"][1],
        "R2": g["inner"][0] <= g["screen"][0] and g["inner"][1] <= g["screen"][1],
        "R3": g["outer"][0] <= g["screen"][0] and g["outer"][1] <= g["screen"][1],
    }


def _chrome(g):
    return [g["outer"][0] - g["inner"][0], g["outer"][1] - g["inner"][1]]


# --- AC1: the premise, inverted, on the VALUE the page received --------------


def test_a_grown_window_reports_its_new_outer_size(tmp_path):
    """THE AC1 TEST. Asserted on the received VALUE, never on source text — so
    reverting the fix goes red here on what a page READS."""
    r = _seen(tmp_path, screen=(1920, 1080), init=(1280, 720), live=(1920, 1080))

    assert r["before"]["outer"] == [1294, 811], (
        f"at init the override must report inner+chrome; got {r['before']['outer']}"
    )
    assert r["after"]["outer"] == [1934, 1171], (
        "after the window grew to 1920x1080 the page must read the NEW outer "
        f"size (1920+14, 1080+91). Got {r['after']['outer']} — which is the "
        "size the window had at PAGE LOAD. The derivation is frozen again."
    )
    assert _chrome(r["after"]) == [14, 91], (
        f"chrome must stay inner+14/+91 after a resize; got {_chrome(r['after'])}"
    )


def test_a_shrunk_window_reports_its_new_outer_size(tmp_path):
    """The other direction. It does NOT violate R1, which is why it is easy to
    miss — it reports an absurd chrome instead: a frozen 1920-era outer against
    a 1280 inner reads as a window frame 654px wider than its content."""
    r = _seen(tmp_path, screen=(1920, 1080), init=(1920, 1080), live=(1280, 720))

    assert r["after"]["outer"] == [1294, 811], (
        f"after shrinking to 1280x720 the page must read 1294x811; got "
        f"{r['after']['outer']}"
    )
    assert _chrome(r["after"]) == [14, 91], (
        f"got chrome {_chrome(r['after'])} — a window frame that size is its "
        "own implausible reading even though R1 survives it."
    )


def test_the_pre_fix_form_is_frozen_and_this_harness_can_see_it(tmp_path):
    """THE POSITIVE CONTROL — the assertion that makes the two above a
    MEASUREMENT rather than a probe that cannot report its own deadness.

    Same harness, same realm, same geometry; only the derivation differs. The
    pre-fix form must report the stale pair AND break R1, and the shipped form
    must report the live pair. If a future change made this harness blind to a
    resize, THIS test goes red rather than the ones above going quietly green.
    """
    shipped = il._outer_size_override_script()
    pre = _pre_fix_form(shipped)

    control = _seen(tmp_path / "pre", screen=(1920, 1080),
                    init=(1280, 720), live=(1920, 1080), script=pre)
    live = _seen(tmp_path / "now", screen=(1920, 1080),
                 init=(1280, 720), live=(1920, 1080))

    # the control is frozen...
    assert control["after"]["outer"] == control["before"]["outer"] == [1294, 811], (
        "the eager form must NOT track the resize — if it does, this control "
        "is no longer a control and the tests above prove nothing."
    )
    # ...and specifically it breaks R1, the negative-chrome signature #327
    # deleted a fix for producing.
    assert _relations(control["after"])["R1"] is False
    assert _chrome(control["after"]) == [-626, -269], (
        f"the pre-fix defect is negative chrome; got {_chrome(control['after'])}"
    )
    # ...while the shipped form moves.
    assert live["after"]["outer"] != live["before"]["outer"]
    assert _relations(live["after"])["R1"] is True


# --- AC2: the relation SET, mid-range and at the inner == screen edge --------


def test_all_three_relations_hold_after_a_mid_range_resize(tmp_path):
    """The ordinary case: a window resized to something below the pick. All
    three relations hold on the received values — asserted together, because
    #327 records that fixing one by breaking another is the failure mode a
    single-relation assertion cannot see."""
    r = _seen(tmp_path, screen=(1920, 1080), init=(1280, 720), live=(1600, 900))
    g = r["after"]
    rel = _relations(g)

    assert g["outer"] == [1614, 991], f"got {g['outer']}"
    assert rel == {"R1": True, "R2": True, "R3": True}, (
        f"mid-range resize must be coherent on every relation; got {rel} for "
        f"inner {g['inner']} outer {g['outer']} screen {g['screen']}"
    )


def test_the_maximized_edge_breaks_r3_and_that_is_the_recorded_bound(tmp_path):
    """THE EDGE, ASSERTED RATHER THAN AVOIDED.

    At a maximized window inner == screen, so inner+chrome necessarily exceeds
    the screen and the page reads outer > screen: R3 violated. R1 and R2 hold.

    This test exists so the bound is IN THE TREE. If someone later makes R3
    hold at this edge (by capping the window at source, which is where #327
    says the fix belongs), this test goes red and they must come here and say
    so deliberately — which is the correct outcome, not a nuisance.
    """
    r = _seen(tmp_path, screen=(1920, 1080), init=(1920, 1080), live=(1920, 1080))
    g = r["after"]
    rel = _relations(g)

    assert g["inner"] == g["screen"] == [1920, 1080]
    assert rel["R1"] is True, (
        f"R1 must hold at the edge — outer {g['outer']} vs inner {g['inner']}. "
        "A fix that bought R3 here by reporting outer < inner would be #327's "
        "first attempt arriving again, and it is not acceptable."
    )
    assert rel["R2"] is True
    assert rel["R3"] is False, (
        f"the recorded bound says R3 is violated at inner==screen; got outer "
        f"{g['outer']} vs screen {g['screen']}, i.e. R3 now HOLDS. If that is "
        "deliberate, update this test and the docstring in "
        "_outer_size_override_script together — do not delete the record."
    )
    assert g["outer"] == [1934, 1171]


def test_the_maximized_r3_bound_predates_this_fix(tmp_path):
    """The bound above is NOT introduced by the recompute, and this is the
    assertion that keeps that claim honest instead of asserted in prose.

    The PRE-FIX form, driven at the same maximized geometry, reports the
    IDENTICAL outer against the identical screen — R3 already violated. What
    the recompute changes is that the reading is now consistent (it is what a
    maximized window reports whenever it is maximized) rather than an accident
    of the size the window happened to have at page load.
    """
    pre = _pre_fix_form(il._outer_size_override_script())
    r = _seen(tmp_path, screen=(1920, 1080), init=(1920, 1080), live=(1920, 1080),
              script=pre)

    assert r["after"]["outer"] == [1934, 1171]
    assert _relations(r["after"])["R3"] is False, (
        "the pre-fix form must already violate R3 at this geometry — if it "
        "does not, the recompute INTRODUCED the violation and the recorded "
        "bound in _outer_size_override_script's docstring is wrong."
    )


def test_r2_broken_at_source_is_not_this_layers_to_fix(tmp_path):
    """#327's conclusion, re-asserted at this layer so it is not re-litigated.

    A window whose CONTENT already exceeds the pick (inner 1920 on a 1280 pick)
    breaks R2, and #327 proves the R1/R3 interval is then EMPTY — no value the
    reporting layer emits can satisfy both. The recompute picks R1, which is
    the relation whose violation is the measurable leak signature. R3 stays
    broken because R2 is broken, and that is fixed by capping the WINDOW.
    """
    r = _seen(tmp_path, screen=(1280, 720), init=(1280, 720), live=(1920, 1080))
    g = r["after"]
    rel = _relations(g)

    assert rel["R2"] is False, "this fixture must have R2 broken at source"
    assert rel["R1"] is True, (
        f"with R2 broken the reporting layer must still keep R1 — outer "
        f"{g['outer']} vs inner {g['inner']}. Reporting a window smaller than "
        "its own content is the negative-chrome signature."
    )
    assert rel["R3"] is False, (
        "R3 cannot hold while R2 is broken (the interval is empty); if it "
        "does, something clamped the reported outer and #327's first attempt "
        "is back."
    )


# --- AC3: static-case equivalence -------------------------------------------


@pytest.mark.parametrize(
    "geom", [(1280, 720), (1920, 1080), (800, 600), (2560, 1440), (1366, 768), (1200, 800)]
)
def test_with_no_resize_the_reported_values_are_unchanged_from_base(tmp_path, geom):
    """AC3. The spoof was written for the no-resize case and this change must
    be behaviour-preserving there — asserted against the PRE-FIX form itself
    rather than against numbers copied into this file, so the comparison
    cannot drift from what base actually reported."""
    shipped = il._outer_size_override_script()
    pre = _pre_fix_form(shipped)

    now = _seen(tmp_path / "now", screen=geom, init=geom, live=geom)
    base = _seen(tmp_path / "base", screen=geom, init=geom, live=geom, script=pre)

    assert now["after"]["outer"] == base["after"]["outer"], (
        f"at {geom} with no resize the fix reports {now['after']['outer']} "
        f"where base reported {base['after']['outer']}. A diff that moves the "
        "no-resize numbers is wrong — that is the case the spoof exists for."
    )
    assert now["after"]["outer"] == [geom[0] + 14, geom[1] + 91]


def test_a_screen_clamp_would_move_the_no_resize_values(tmp_path):
    """WHY OPTION 2 WAS REJECTED, recorded as a measurement rather than as an
    opinion in a commit message.

    Clamping the reported outer at the spoofed screen buys R3 at the maximized
    edge. It also changes what the page reads in the NO-RESIZE case at every
    geometry — a 1280x720 pick would report 1280x720 instead of 1294x811 —
    which is a regression in the case the spoof was written for, and it would
    red ``test_reported_values_are_unchanged``. The trade was measured before
    being declined; this test is that measurement, kept so the next person to
    propose the clamp finds the answer instead of re-deriving it.
    """
    shipped = il._outer_size_override_script()
    clamped = shipped.replace(
        _LAZY_W,
        "def(window,'outerWidth', ()=>{const i=window.innerWidth,"
        "s=(window.screen||{}).width;return (typeof s==='number'&&s>0)?"
        "Math.max(i,Math.min(i+14,s)):i+14;});",
    )
    assert clamped != shipped

    g = _seen(tmp_path, screen=(1280, 720), init=(1280, 720), live=(1280, 720),
              script=clamped)["after"]

    assert g["outer"][0] == 1280, (
        f"the clamp is expected to report the screen width here; got {g['outer']}"
    )
    assert g["outer"][0] != 1294, "…and that is NOT what base reported (1294)."


# --- AC4: the cloak, read in-realm ------------------------------------------


@pytest.mark.parametrize("prop", ["outerWidth", "outerHeight"])
def test_the_cloak_is_unchanged_read_in_realm(tmp_path, prop):
    """AC4, on the DESCRIPTOR rather than on a grep.

    ⚠️ Read IN-REALM. ``Function.prototype.toString.call(getter)`` evaluated in
    the HOST realm returns the raw arrow source and looks like a cloak
    regression; it is the wrong realm, and a page never sees it.
    """
    c = _seen(tmp_path, screen=(1920, 1080), init=(1280, 720),
              live=(1920, 1080))["cloak"][prop]

    assert c["name"] == f"get {prop}"
    assert c["length"] == 0
    assert c["ownProps"] == ["length", "name"], (
        f"the accessor grew an own property: {c['ownProps']}. A marker a page "
        "can enumerate is a masking tell (PS-22)."
    )
    assert c["configurable"] is True
    assert c["enumerable"] is False
    assert c["hasSetter"] is False
    # SpiderMonkey's native form, and it drops the `get ` prefix in the source
    # text while keeping it on .name — both halves asserted.
    assert c["src"] == "function %s() {\n    [native code]\n}" % prop


def test_the_thunk_does_not_leak_through_arity_or_source(tmp_path):
    """The specific risk this fix introduces: the getter now CALLS something.

    A wrapper that took the thunk as a parameter would report ``.length == 1``
    where a native accessor reports 0 — the exact arity divergence PS-119
    isolated as a live masking tell. The thunk is closed over, not passed, so
    the arity is unchanged; asserted behaviourally rather than argued.
    """
    c = _seen(tmp_path, screen=(1920, 1080), init=(1280, 720),
              live=(1920, 1080))["cloak"]
    for prop in ("outerWidth", "outerHeight"):
        assert c[prop]["length"] == 0, (
            f"{prop}'s getter reports arity {c[prop]['length']}; every native "
            "accessor reports 0 (PS-119)."
        )
        assert "=>" not in c[prop]["src"], (
            f"{prop} stringifies as {c[prop]['src']!r} — injected source is "
            "visible to a page."
        )
        assert "innerWidth" not in c[prop]["src"]
