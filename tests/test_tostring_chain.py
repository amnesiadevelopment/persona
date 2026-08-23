"""The Chromium `Function.prototype.toString` cloak: does it CHAIN? (PS-68)

Two shipped MAIN-world extensions install this cloak — `native_ext` (whose whole
job it is) and `locale_ext` (which re-applies it because a worker realm's Intl
must read native too, and the two leaves' load order is not guaranteed). They are
separate content scripts in one MAIN world: no shared closure, no ordering.

They used to coordinate through an enumerable global, `G.__pnaToStringPatched`,
so at most one of them wrapped a realm. That flag is a POSITIVE TOOL
IDENTIFICATION and `Object.keys(window)` found it in one line — in every realm,
at every worker/iframe depth, since the realm bootstrap carries both leaves
everywhere, and under persona's own `__pna` prefix. It carried no seed (PS-48
removed the seed-bearing half), so what it disclosed is "a persona-family masking
tool is installed here".

CHAINING dissolves the coordination problem instead of solving it: each script
wraps whatever `Function.prototype.toString` it finds and delegates to it, so N
scripts compose with no shared name between them. The idiom is already in tree on
both engines — `worker_wrap.py:28-32` (the Worker/iframe accessors) and
`invisible_launch.py:296-302` (the Firefox cloak, which this ports).

WHAT IS ASSERTED HERE, AND WHY IN THIS SHAPE
--------------------------------------------
Every assertion below is an EXECUTION result or an ABSENCE claim read off
ENUMERATED GLOBAL NAMES. None is a substring check over the generated text, and
that is the whole point: a text assertion (`"__pnaToStringPatched" not in js`)
passes on a build that merely RENAMES the flag, which is the defect wearing a
new hat. `tests/native_mask_probe.py` makes the same argument at length, and the
test this file replaces (`test_native_ext.py::test_idempotent_guard`) was exactly
the failing shape — it pinned the mechanism string, so it would have gone red for
this strictly-better implementation.

The wrapper probed is one a REAL extension installs (`Intl.DateTimeFormat`, which
`locale_ext` replaces and marks), never one a test marks by hand: hand-rolling
the marker here would hardcode the mechanism into the test and make it go red for
a mechanism rename rather than for a masking regression.

THE FALSIFICATION (AC3, non-waivable) is `_reflagged()`: it puts the guard back
into the generated scripts with the rest of the change in place, and asserts the
invisibility claim goes RED. Without it, "no `__pna` global is enumerable" is a
claim this suite could satisfy by never looking.

SCOPE — read this before extending. `__pnaToStringPatched` is ONE of 15 enumerable
`/^__pna|^__persona/` globals across 12 extension files. The other 14 are
`__persona*` per-module self-idempotency guards, each independently replaceable by
the closure `WeakSet` pattern `worker_wrap.py:95-107` already models; that is a
separate 12-file slice. So this file asserts the `__pna` FAMILY is gone (AC1) and
that the `realm.bootMarkers` set strictly SHRANK (AC7) — it deliberately does NOT
assert `markers == []`, which is unreachable today and would ship a knowingly-red
gate. `locale_ext` writes `__personaLocale` in the very function this change
edits, so even the two files in scope still leave a marker behind.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

from src.services.browser.locale_ext import build_locale_extension
from src.services.browser.native_ext import build_native_extension
from src.services.verify.probes import PROBES
from tests.realm_harness import HARNESS

_LOCALE = "pl-PL"

# The observable a masking detector actually reads, for a wrapper a real
# extension installs. `locale_ext` replaces Intl.DateTimeFormat with a wrapper
# and marks it, so this is the product's own wrapper, not the test's.
_MARKED_NATIVE = "function DateTimeFormat() { [native code] }"


# --------------------------------------------------------------------------
# The falsification (AC3). Put the shared-global guard back, leaving the rest
# of the change in place, and every invisibility assertion must go RED.
#
# Applied to the GENERATED JS rather than by patching src/: the flag is what is
# under test, so reintroducing it exactly where it lived is the counterfactual.
# Both anchors are unique in their generated file (asserted below, so a source
# edit that moves them fails loudly instead of silently neutering the
# falsification into a no-op that always passes).
# --------------------------------------------------------------------------
_NATIVE_ANCHOR = "if (!G || !G.Function) return;"
_NATIVE_REFLAGGED = (
    "if (!G || !G.Function || G.__pnaToStringPatched) return;\n"
    "    G.__pnaToStringPatched = true;"
)
_LOCALE_ANCHOR = "if (FP) {"
_LOCALE_REFLAGGED = (
    "if (FP && !G.__pnaToStringPatched) {\n"
    "        G.__pnaToStringPatched = true;"
)


def _reflag(js: str, anchor: str, replacement: str) -> str:
    assert js.count(anchor) == 1, (
        f"FALSIFICATION BROKEN: the anchor {anchor!r} occurs {js.count(anchor)} "
        f"times in the generated script, so re-introducing the guard does not "
        f"reproduce the pre-change build and AC3 would be a no-op that always "
        f"passes. Update the anchor to match the source."
    )
    return js.replace(anchor, replacement)


def _scripts(tmp_path, *, reflagged: bool):
    """Build both content scripts; optionally revert them to the shared flag."""
    root = pathlib.Path(tmp_path) / ("pre" if reflagged else "post")
    native = pathlib.Path(build_native_extension(str(root / "native"))) / "native.js"
    locale = (
        pathlib.Path(build_locale_extension(_LOCALE, str(root / "locale")))
        / "locale.js"
    )
    if reflagged:
        # encoding="utf-8" on the READS as well as the writes. Both generated
        # scripts carry non-ASCII prose in their comments (locale.js has an em
        # dash a few KB in), and `read_text()` with no encoding uses the
        # PLATFORM default -- cp1252 on Windows, which has no mapping for the
        # trailing byte of a UTF-8 em dash and raises UnicodeDecodeError. The
        # writes below were already explicit; the reads were the asymmetry, and
        # it made this file the only Windows-red one in the suite.
        native.write_text(
            _reflag(
                native.read_text(encoding="utf-8"),
                _NATIVE_ANCHOR,
                _NATIVE_REFLAGGED,
            ),
            encoding="utf-8",
        )
        locale.write_text(
            _reflag(
                locale.read_text(encoding="utf-8"),
                _LOCALE_ANCHOR,
                _LOCALE_REFLAGGED,
            ),
            encoding="utf-8",
        )
    return native, locale


# --------------------------------------------------------------------------
# The realm probe. Reuses the ONE shared node:vm harness (tests/realm_harness.py,
# extracted from tests/test_worker_wrap.py) so the page realm, three worker
# generations and a child frame are the same transport the product ships —
# PS-68 AC6 says to reuse it rather than invent a second.
# --------------------------------------------------------------------------
_PROBE = HARNESS + r"""
const SCRIPTS = [fs.readFileSync(process.argv[2], "utf8"),
                 fs.readFileSync(process.argv[3], "utf8")];
// The bootMarkers probe expression, passed in from the SHIPPED inventory
// (src/services/verify/probes.py) rather than re-typed here, so AC7 consumes
// the detector persona actually runs.
const BOOT_MARKERS = fs.readFileSync(process.argv[4], "utf8");

// What a realm looks like from the inside. Every field is read by EXECUTION or
// by enumerating names — never by inspecting source text.
function report(realm) {
  return vm.runInContext("(function(){" +
    // AC1: enumerated global names carrying persona's own prefix. Split, because
    // this slice removes the __pna family and 14 __persona* per-module guards
    // survive it (see this module's docstring).
    "  var names = Object.getOwnPropertyNames(self);" +
    "  var pna = names.filter(function(k){ return /^__pna/.test(k); });" +
    "  var persona = names.filter(function(k){ return /^__persona/.test(k); });" +
    // AC4/AC6: the observable a detector reads off a REAL persona wrapper.
    "  var marked = null;" +
    "  try { marked = Function.prototype.toString.call(self.Intl.DateTimeFormat); }" +
    "  catch (e) { marked = 'threw: ' + e; }" +
    // AC5a: the patch must cloak ITSELF — a detector stringifies
    // Function.prototype.toString to catch exactly this trick.
    "  var patchSelf = null;" +
    "  try { patchSelf = Function.prototype.toString.call(Function.prototype.toString); }" +
    "  catch (e) { patchSelf = 'threw: ' + e; }" +
    // AC5a, SECOND AXIS: `.name` is read independently of stringification, and
    // __pnaName does not cloak it. Chaining is what puts this on the critical
    // path: it makes locale_ext's wrapper outermost in one of the two orders, so
    // without a pinned name `Function.prototype.toString.name` answers with the
    // persona-internal identifier "_pts" — positive tool identification in one
    // property read, the same class of leak this ticket exists to remove.
    // patchSelf CANNOT see this axis: the stringification is cloaked by
    // __pnaName and stays green while the name leaks underneath it.
    "  var patchName = null;" +
    "  try { patchName = Function.prototype.toString.name; }" +
    "  catch (e) { patchName = 'threw: ' + e; }" +
    // AC5b: an UNMARKED function must still stringify to its real source — the
    // delegate has to be reached, not swallowed. A cloak that returned the
    // native form for everything would pass AC4 and break every page on earth.
    "  var plain = null;" +
    "  try { var f = function notAWrapper(){ return 6 * 7; };" +
    "    plain = Function.prototype.toString.call(f); }" +
    "  catch (e) { plain = 'threw: ' + e; }" +
    // AC5c: a primitive `this` must still reach the original for its TypeError,
    // rather than being answered by the cloak.
    "  var primitiveThrew = false;" +
    "  try { Function.prototype.toString.call(42); }" +
    "  catch (e) { primitiveThrew = true; }" +
    "  return {pna: pna, persona: persona, marked: marked, patchSelf: patchSelf," +
    "          patchName: patchName," +
    "          plain: plain, primitiveThrew: primitiveThrew," +
    "          bootMarkers: (" + BOOT_MARKERS + ")};" +
    "})()", realm.ctx);
}

// ORDER is the parameter: native_ext then locale_ext, or the reverse. The flag
// this change removes existed precisely because that order is not guaranteed.
function pageRealm(order) {
  const realm = makeRealm();
  for (const i of order) vm.runInContext(SCRIPTS[i], realm.ctx, { filename: "s" + i + ".js" });
  return realm;
}

const out = { orders: {} };

for (const [label, order] of [["native_first", [0, 1]], ["locale_first", [1, 0]]]) {
  const page = pageRealm(order);
  const o = { page: report(page), depths: {} };

  // Three worker generations, each spawned from the previous one — the realms a
  // detector reads a "pristine" Intl out of.
  let parent = page;
  for (let depth = 1; depth <= 3; depth++) {
    const payload = spawn(parent);
    const child = makeRealm();
    vm.runInContext(payload, child.ctx);
    o.depths["d" + depth] = report(child);
    parent = child;
  }

  // Child-frame path: touch the accessor as a page does.
  const framed = pageRealm([]);
  vm.runInContext(
    "globalThis.__child = { name: 'child' };" +
    "globalThis.HTMLIFrameElement = function HTMLIFrameElement(){};" +
    "Object.defineProperty(HTMLIFrameElement.prototype,'contentWindow'," +
    "  { configurable:true, get: function(){ return globalThis.__child; } });" +
    "Object.defineProperty(HTMLIFrameElement.prototype,'contentDocument'," +
    "  { configurable:true, get: function(){ return { defaultView: globalThis.__child }; } });",
    framed.ctx);
  for (const i of order) vm.runInContext(SCRIPTS[i], framed.ctx, { filename: "f" + i + ".js" });
  o.iframe = vm.runInContext(
    "(function(){ var f = new HTMLIFrameElement();" +
    " var w1 = f.contentWindow; var w2 = f.contentWindow;" +
    " var names = Object.getOwnPropertyNames(__child);" +
    " return { pna: names.filter(function(k){ return /^__pna/.test(k); })," +
    "          persona: names.filter(function(k){ return /^__persona/.test(k); }) }; })()",
    framed.ctx);

  out.orders[label] = o;
}

console.log(JSON.stringify(out));
"""


def _boot_markers_expr() -> str:
    """The SHIPPED `realm.bootMarkers` probe expression.

    Read out of the inventory rather than re-typed, so AC7 consumes the detector
    persona actually runs. AC7 changes nothing in src/services/verify/ — if that
    probe's regex moves, this test moves with it instead of quietly measuring an
    older definition.
    """
    for probe in PROBES:
        if probe.id == "realm.bootMarkers":
            return probe.expr
    raise AssertionError("realm.bootMarkers is gone from the probe inventory")


def _run(tmp_path, *, reflagged: bool):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    work = pathlib.Path(tmp_path) / ("run-pre" if reflagged else "run-post")
    work.mkdir(parents=True, exist_ok=True)
    native, locale = _scripts(work, reflagged=reflagged)
    probe = work / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    markers = work / "bootmarkers.js"
    markers.write_text(_boot_markers_expr(), encoding="utf-8")
    out = subprocess.run(
        [node, str(probe), str(native), str(locale), str(markers)],
        capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def realms(tmp_path_factory):
    """Both extensions, in BOTH load orders, through every realm they reach."""
    return _run(tmp_path_factory.mktemp("chain"), reflagged=False)


@pytest.fixture(scope="module")
def flagged_realms(tmp_path_factory):
    """The same, with the shared-global guard put back: the pre-change build."""
    return _run(tmp_path_factory.mktemp("chain_pre"), reflagged=True)


_ORDERS = ["native_first", "locale_first"]
_REALMS = ["page", "d1", "d2", "d3"]


def _realm(report, order, name):
    o = report["orders"][order]
    return o["page"] if name == "page" else o["depths"][name]


# --- AC1: no __pna-prefixed global, in a real realm ------------------------

@pytest.mark.parametrize("order", _ORDERS)
@pytest.mark.parametrize("name", _REALMS)
def test_no_pna_global_is_enumerable(realms, order, name):
    # THE INVARIANT. Asserted on ENUMERATED GLOBAL NAMES, never on source text: a
    # text assertion passes on a build that merely renames the flag.
    assert _realm(realms, order, name)["pna"] == [], (
        f"{order}/{name}: a __pna-prefixed global is enumerable — "
        "Object.keys(window) identifies persona in one property read"
    )


def test_no_pna_global_in_a_child_frame(realms):
    for order in _ORDERS:
        assert realms["orders"][order]["iframe"]["pna"] == [], order


# --- AC2/AC3: the premise, and the falsification ---------------------------

@pytest.mark.parametrize("order", _ORDERS)
def test_the_flag_was_enumerable_before_this_change(flagged_realms, order):
    # AC2 (premise inversion) and AC3 (falsification) are the same measurement:
    # with the guard put back and the rest of the change in place, the assertion
    # above MUST go red. If this test ever passes with an empty list, AC1 is
    # witnessing nothing and the whole file is decorative.
    assert flagged_realms["orders"][order]["page"]["pna"] == ["__pnaToStringPatched"], (
        "FALSIFICATION FAILED: re-introducing the shared global guard did not "
        "make the flag enumerable, so test_no_pna_global_is_enumerable cannot "
        "distinguish the fix from the defect"
    )


@pytest.mark.parametrize("depth", ["d1", "d2", "d3"])
def test_the_flag_was_enumerable_at_every_worker_depth_before(flagged_realms, depth):
    # The leak was not a page-realm-only affair: the bootstrap carries both
    # leaves into every realm, so the flag was readable at depth too.
    assert flagged_realms["orders"]["native_first"]["depths"][depth]["pna"] == [
        "__pnaToStringPatched"
    ], depth


# --- AC4: the property the flag existed for, in BOTH load orders -----------

@pytest.mark.parametrize("order", _ORDERS)
@pytest.mark.parametrize("name", _REALMS)
def test_a_marked_wrapper_reads_native_exactly_once(realms, order, name):
    # This is the AC that proves chaining REPLACED the guard rather than dropping
    # it. Both scripts now wrap Function.prototype.toString in every realm, so a
    # double-wrap would show up here — as a doubled `function function ...`
    # prefix, or as the wrapper's own source leaking through.
    #
    # The wrapper is Intl.DateTimeFormat, which locale_ext really installs and
    # really marks. Nothing is marked by hand here: that would pin the mechanism
    # instead of the observable a detector reads.
    got = _realm(realms, order, name)["marked"]
    assert got == _MARKED_NATIVE, (
        f"{order}/{name}: a persona wrapper did not stringify as the native form "
        f"exactly once — got {got!r}"
    )


@pytest.mark.parametrize("order", _ORDERS)
def test_chaining_matches_the_guard_on_what_the_guard_protected(realms, flagged_realms, order):
    # Explicitly: the fix did not trade invisibility for a rendering regression.
    # What the pre-change build rendered for a marked wrapper is what the
    # post-change build renders, in both orders and with two patches installed
    # instead of one.
    assert (
        _realm(realms, order, "page")["marked"]
        == _realm(flagged_realms, order, "page")["marked"]
        == _MARKED_NATIVE
    )


# --- AC5: the cloak still cloaks itself, and still delegates ---------------

@pytest.mark.parametrize("order", _ORDERS)
@pytest.mark.parametrize("name", _REALMS)
def test_the_patch_itself_reads_native(realms, order, name):
    # `applyNativePatch` in native_ext.py pins `__pnaName: "toString"` onto its
    # `patched` wrapper — a detector stringifies Function.prototype.toString to
    # catch exactly this trick. Chaining puts a SECOND wrapper on top, so this is
    # the assertion most at risk from the change: the outer patch must carry its
    # own __pnaName.
    assert _realm(realms, order, name)["patchSelf"] == (
        "function toString() { [native code] }"
    ), f"{order}/{name}: the cloak betrayed itself"


@pytest.mark.parametrize("order", _ORDERS)
@pytest.mark.parametrize("name", _REALMS)
def test_the_patch_does_not_leak_its_internal_name(realms, order, name):
    # The axis the test above CANNOT see. `patchSelf` reads the STRINGIFICATION,
    # which __pnaName cloaks; `.name` is a separate property read that __pnaName
    # does not touch. Delete the `name` pin in locale_ext (or native_ext) and the
    # test above stays green while this one goes red — which is exactly how the
    # leak got as far as review unpinned.
    #
    # Chaining is what made this reachable, and it is the LAST script to run that
    # owns the observable: whichever patch ends up outermost is the one whose
    # `.name` a detector reads. So native_first (native, then locale) exposes
    # locale_ext's "_pts", and locale_first exposes native_ext's patch — which is
    # why BOTH orders have to be pinned and BOTH are asserted here.
    #
    # NB this is the opposite order from the pre-change build, and the difference
    # is the whole point of the ticket: under the old shared flag exactly one
    # script wrapped a realm (the FIRST one, the loser returning early), so it was
    # locale_first that leaked "_pts". Chaining means both wrap, every time.
    assert _realm(realms, order, name)["patchName"] == "toString", (
        f"{order}/{name}: the cloak leaks its own internal identifier via .name "
        "— a persona-internal name is readable in one property access"
    )


@pytest.mark.parametrize("order", _ORDERS)
@pytest.mark.parametrize("name", _REALMS)
def test_an_unmarked_function_still_shows_its_real_source(realms, order, name):
    # The delegate must be REACHED, not swallowed. A cloak that answered the
    # native form for everything would satisfy AC4 and break every page that
    # reads its own function source.
    plain = _realm(realms, order, name)["plain"]
    assert "6 * 7" in plain, f"{order}/{name}: real source was swallowed: {plain!r}"
    assert "[native code]" not in plain, (
        f"{order}/{name}: an unmarked function claimed to be native: {plain!r}"
    )


@pytest.mark.parametrize("order", _ORDERS)
def test_a_primitive_receiver_still_raises(realms, order):
    # The chain must not turn `Function.prototype.toString.call(42)` into a
    # TypeError-free lie; the original is what throws, so the delegate has to be
    # reached for it.
    assert _realm(realms, order, "page")["primitiveThrew"] is True


# --- AC7: realm.bootMarkers is regression-only, NOT clean ------------------

@pytest.mark.parametrize("order", _ORDERS)
def test_boot_markers_strictly_shrank_and_gained_nothing(realms, flagged_realms, order):
    # DELIBERATELY NOT `markers == []`. Fifteen enumerable /^__pna|^__persona/
    # globals ship across 12 extension files; this slice removes one of them, and
    # locale_ext writes __personaLocale in the very function it edits. Asserting
    # an empty set would ship a knowingly-red gate. So: strict subset, the flag
    # gone, nothing new.
    before = set(_realm(flagged_realms, order, "page")["bootMarkers"]["markers"])
    after = set(_realm(realms, order, "page")["bootMarkers"]["markers"])

    assert "__pnaToStringPatched" in before, (
        "the pre-change build did not expose the flag to the shipped probe, so "
        "this comparison measures nothing"
    )
    assert "__pnaToStringPatched" not in after
    assert after < before, f"bootMarkers did not strictly shrink: {before} -> {after}"
    assert after - before == set(), f"a NEW marker appeared: {after - before}"
    # and the honest bound, stated as an assertion so it cannot rot silently:
    # this slice does not empty the set.
    assert after, (
        "bootMarkers is unexpectedly empty — if the __persona* per-module guards "
        "have since been removed too, this test's premise has changed and the "
        "strict-subset comparison should become the equality gate it could not "
        "be while they shipped"
    )


@pytest.mark.parametrize("order", _ORDERS)
def test_the_surviving_markers_are_the_known_persona_family(realms, order):
    # Names the residue explicitly rather than leaving it implied, so the
    # follow-up slice (14 __persona* per-module guards -> closure WeakSet, 12
    # files) has a starting inventory and this test goes red when it lands.
    after = set(_realm(realms, order, "page")["bootMarkers"]["markers"])
    assert after == {"__personaLocale"}, (
        f"the surviving markers in this two-extension realm changed: {after}"
    )
    assert all(n.startswith("__persona") for n in after)
