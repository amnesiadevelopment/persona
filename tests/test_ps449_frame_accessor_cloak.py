"""PS-449: the two iframe accessors the Chromium bootstrap chains must not
stringify as their own source.

THE DEFECT, measured on the unmodified tree by executing the generated
bootstrap rather than by reading it::

    Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, "contentWindow").get
    Function.prototype.toString.call(that)
      -> 290 characters beginning `function () {` and naming `__pnaInstall`,
         `LEAF` and the `fresh` dedup check.

``WorkerCloak`` carries ``frame_open``/``frame_close`` wrapped AROUND the
accessor's function expression (an argument position, which cannot take a
statement). ``firefox_worker_cloak()`` fills them with ``__bcloak``;
``CHROMIUM_WORKER_CLOAK`` left them EMPTY, so on that engine these two wrappers
were the only ones the bootstrap installs uncloaked — ``Worker``/``SharedWorker``
go through ``apply``, and the eight DOM inserters plus the ``innerHTML`` setter
go through ``hook_mark``.

WHY THIS IS WORSE THAN A TYPICAL WRAPPER LEAK. ``contentWindow`` is how any page
reaches a frame, so a detector reads it without looking for this tool
specifically; the source it gets back NAMES our machinery rather than merely
revealing that something is wrapped; and the chain is how the leaf reaches a
child realm, so it is installed on every realm by design.

⭐ THE SOURCE NAME IS NOT ``prop``, AND THE TWO ENGINE ARMS GENUINELY DIFFER.
V8 KEEPS the ``get `` prefix in an accessor's SOURCE TEXT. Measured on
/usr/bin/chromium (HeadlessChrome/153.0.0.0)::

    iframe.contentWindow   ts "function get contentWindow() { [native code] }"
    Map.size               ts "function get size() { [native code] }"
    Element.innerHTML[set] ts "function set innerHTML() { [native code] }"
    Element.appendChild    ts "function appendChild() { [native code] }"

so a plain method has no prefix and an accessor does. SpiderMonkey DROPS it,
which is why the Firefox arm passes a bare ``prop`` as its source name and why
copying that across would emit the WRONG ENGINE'S FORM — the identical class of
tell this fix exists to close, one ``Map.prototype.size`` comparison away. The
codebase already records both halves: ``device_ext.py`` beside ``def`` ("THE
``get `` PREFIX IS PART OF THE STRINGIFIED NAME ON V8 … SpiderMonkey drops the
prefix") and ``invisible_launch.py`` in ``_outer_size_override_script``.

Hence :func:`test_the_emitted_source_carries_the_engines_get_prefix`, which is
the sharpest seat here: it compares against the form DERIVED FROM A REAL ENGINE
ACCESSOR rather than against the substring ``[native code]``, and it is the one
case that separates the correct fix from a plausible wrong one. Both strings
contain ``[native code]``; only one is what V8 emits.

⚠️ WHY THESE ASSERTIONS COULD BE WORTHLESS, AND WHAT MAKES THEM NOT BE. A cloak
answers ``toString`` by SYNTHESISING a native string, so asking ``toString``
whether things look native asks the thing under test to grade itself. Two
guards, and neither is waivable:

*   THE INSTRUMENT IS FALSIFIED FIRST. ``readsAsNative`` is proven able to see
    native by reading a REAL engine accessor (``Map.prototype.size``) before it
    is trusted to report on ours. An instrument that cannot see native would
    report every arm red; one that reports everything native would report every
    arm green. Both are caught by measuring the baseline.
*   THE DELIVERY IS ASSERTED SEPARATELY. A cloak that broke the chain would
    leave every child frame UNSPOOFED while this file went green, which would
    trade a tell for an unmasked realm. So touching ``contentWindow`` is
    asserted to still run the leaf.

SCOPE — SINGLE REALM, ``node:vm``, exactly as ``test_ps215_tostring_chain.py``
does and for the same reason: this file asks what a detector READS off an
accessor in one realm, which that transport can answer honestly. It does not
probe cross-realm intrinsic identity, which ``node:vm`` cannot express and which
belongs to a real browser.

⛔ WHAT THIS FILE DELIBERATELY DOES NOT ASSERT, stated so a future reader does
not mistake the silence for coverage: the installed accessors own
``["arguments","caller","length","name","prototype"]`` where a native accessor
owns exactly ``["length","name"]`` (measured both on the live engine and in this
harness). That is a one-line tell INDEPENDENT of ``toString`` which this fix does
not close and cannot: the bootstrap installs a function EXPRESSION, whose
``prototype`` is non-configurable, so the shape must be right at creation
(method shorthand, or a getter pulled out of an object literal — the form
``hookInsert`` and ``device_ext``'s ``def`` already use). Repairing it changes
the SHARED template on BOTH engine arms and is wider than this ticket. Asserted
nowhere here, reported on PS-449 for a ticket of its own.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

from src.services.browser.worker_wrap import realm_bootstrap_js

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is required to execute the bootstrap"
)


# The two accessors the bootstrap chains. Both, not one: they are installed by
# the same `forEach` over the same seam, so a fix applied to a single property
# would be a coincidence rather than a repair.
ACCESSORS = ("contentWindow", "contentDocument")

# The machinery names a detector reads off the raw source. `fresh` is the dedup
# check, `LEAF` the closed-over patch and `__pnaInstall` the installer — these
# are the strings that turn "something is wrapped" into "this tool wrapped it",
# which is why they are asserted on a SEPARATE axis from the native form.
MACHINERY = ("__pnaInstall", "LEAF", "fresh")


# The probe. Executed in `node:vm` with a DOM stand-in whose two accessors are
# pulled out of an object literal, so they start NATIVE-SHAPED — an expression
# getter would make the baseline wrong in the direction that flatters us.
#
# WRAPPED IN AN IIFE, because that is how the product splices it: every rider
# embeds the bootstrap inside `(function () { ... })()`. Running it bare would
# make the bootstrap's own `var __pnaInstall` a global and this probe would
# report a disclosure the product does not have — a false FAIL from the harness.
_PROBE = r"""
const vm = require("vm");
const fs = require("fs");
const BOOT = fs.readFileSync(process.argv[2], "utf8");

const sandbox = { Reflect, WeakSet, WeakMap, console };
const ctx = vm.createContext(sandbox);
vm.runInContext("var self = this; globalThis.self = globalThis;", ctx);

vm.runInContext(`
  // A native-SHAPED DOM stand-in. The accessors are pulled back out of an
  // object literal rather than written as function expressions, so the
  // pre-bootstrap baseline is what a real engine reports.
  function HTMLIFrameElement() {}
  globalThis.__frames = [];
  ['contentWindow', 'contentDocument'].forEach(function (p) {
    var g = Object.getOwnPropertyDescriptor({ get m() {
      // Every frame stands in for its own child realm. contentDocument hands
      // back a document whose defaultView is that same realm, which is the
      // shape the bootstrap's own 'r && r.defaultView' branch reads.
      if (!this.__realm) {
        this.__realm = { __installed: [] };
        this.__realm.defaultView = this.__realm;
        this.__doc = { defaultView: this.__realm };
      }
      return p === 'contentWindow' ? this.__realm : this.__doc;
    } }, 'm').get;
    try { Object.defineProperty(g, 'name', { value: 'get ' + p }); } catch (e) {}
    Object.defineProperty(HTMLIFrameElement.prototype, p, {
      configurable: true, enumerable: false, get: g,
    });
  });
  globalThis.HTMLIFrameElement = HTMLIFrameElement;

  // The leaf. Records the realms it was applied to, so DELIVERY is observable
  // rather than assumed.
  globalThis.__applied = [];
  function applyPatch(G) {
    try { globalThis.__applied.push(G); if (G.__installed) { G.__installed.push(1); } } catch (e) {}
  }
`, ctx);

vm.runInContext("(function () {\n" + BOOT + "\n})();", ctx);

const out = vm.runInContext(`(function () {
  const TS = Function.prototype.toString;
  const P = HTMLIFrameElement.prototype;

  // THE INSTRUMENT'S OWN BASELINE: a REAL engine accessor. Read through the
  // same call the accessors below are read through, so "what native looks
  // like" is measured rather than assumed. If this does not read as native the
  // instrument is broken and every verdict it issues is void.
  const engineAccessor = Object.getOwnPropertyDescriptor(Map.prototype, 'size').get;
  const engineNative = TS.call(engineAccessor);

  const r = { engine_native: engineNative, engine_native_name: engineAccessor.name, accessors: {} };

  ['contentWindow', 'contentDocument'].forEach(function (p) {
    const d = Object.getOwnPropertyDescriptor(P, p);
    const rec = {};
    try {
      rec.source = d && d.get ? TS.call(d.get) : 'absent';
      rec.name = d && d.get ? d.get.name : 'absent';
      rec.own = d && d.get ? Object.getOwnPropertyNames(d.get).sort() : [];
    } catch (e) { rec.source = 'throws:' + e; }
    r.accessors[p] = rec;
  });

  // DELIVERY: touching the accessor must still run the leaf in the child
  // realm. Read through the property, exactly as a consumer would.
  const el = new HTMLIFrameElement();
  const before = globalThis.__applied.length;
  const w = el.contentWindow;
  r.delivery = {
    applied_delta: globalThis.__applied.length - before,
    realm_marked: !!(w && w.__installed && w.__installed.length > 0),
    returned_the_realm: w === el.__realm,
  };

  // The same, through contentDocument — it reaches the realm via defaultView,
  // a different branch of the same wrapper.
  const el2 = new HTMLIFrameElement();
  const before2 = globalThis.__applied.length;
  const doc = el2.contentDocument;
  r.delivery_doc = {
    applied_delta: globalThis.__applied.length - before2,
    returned_the_document: doc === el2.__doc,
  };

  return r;
})()`, ctx);

console.log(JSON.stringify(out));
"""


def _run(boot_js: str, tmp_path) -> dict:
    d = pathlib.Path(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    boot = d / "boot.js"
    boot.write_text(boot_js, encoding="utf-8")
    probe = d / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    r = subprocess.run(
        ["node", str(probe), str(boot)],
        capture_output=True,
        text=True,
        # encoding NAMED: `text=True` alone decodes with the PLATFORM default,
        # which is cp1252 on Windows while the bootstrap this echoes back is
        # utf-8 (it carries em dashes in its comments).
        encoding="utf-8",
        timeout=120,
    )
    assert r.returncode == 0, f"probe failed:\n{r.stdout}\n{r.stderr}"
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def report(tmp_path_factory) -> dict:
    """The Chromium bootstrap, executed once and read by every seat."""
    if shutil.which("node") is None:  # pragma: no cover - environment-dependent
        pytest.skip("node is required to execute the generated bootstrap")
    return _run(realm_bootstrap_js("applyPatch"), tmp_path_factory.mktemp("ps449"))


def _native_shape(report: dict) -> str:
    """The engine's own native-source tail, derived from a REAL accessor.

    Never a literal. Two reasons, the second being why this is not merely
    tidier: `test_device_ext.py` / `test_native_ext.py` pin the ABSENCE of the
    native-form text from every generated bundle, and V8's one-line form
    differs from SpiderMonkey's three-line one — so a literal here would both
    be the wrong shape on some engine and drift from what the product derives.
    """
    native = report["engine_native"]
    return native[native.index("("):]


# --------------------------------------------------------------------------
# THE INSTRUMENT, FALSIFIED FIRST. Nothing below may be trusted until this
# passes: it proves the probe can SEE native before it is believed when it
# reports native.
# --------------------------------------------------------------------------
def test_the_instrument_can_see_a_real_engines_native_accessor(report):
    """The baseline is read off `Map.prototype.size`, which nothing here wraps.

    If this fails, every other seat in this file is void — a probe that cannot
    recognise native would mark a correct fix red, and one that called
    everything native would mark a broken fix green. Measuring the baseline off
    a real engine accessor, through the same `Function.prototype.toString.call`
    the seats use, is what separates those cases from a real reading.
    """
    native = report["engine_native"]
    assert "[native code]" in native, (
        f"the probe cannot see a REAL engine accessor as native, so it is not "
        f"an instrument and its verdicts about our wrappers mean nothing. "
        f"Map.prototype.size read as: {native!r}"
    )
    assert report["engine_native_name"] == "get size", (
        f"the engine accessor's `.name` is not what this file's premise rests "
        f"on; the `get ` prefix reasoning below is measured against it. Got "
        f"{report['engine_native_name']!r}"
    )
    # The prefix is IN THE SOURCE TEXT on this engine. This is the whole basis
    # for the corrected source name, so it is asserted rather than assumed.
    assert native.startswith("function get size"), (
        f"this engine does NOT carry the `get ` prefix in an accessor's source "
        f"text, which is the premise the Chromium source name rests on. If a "
        f"host engine really behaves this way the fix must derive the prefix "
        f"rather than assume it. Got {native!r}"
    )


# --------------------------------------------------------------------------
# AXIS 1 — the accessors read as native at all.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("prop", ACCESSORS)
def test_the_accessor_reads_as_native(report, prop):
    """What a page gets when it stringifies the accessor it reaches a frame by.

    `contentWindow` is not an exotic surface — it is the ordinary door into a
    child frame — so this is read by detectors that are not looking for us.
    """
    read = report["accessors"][prop]["source"]
    assert "[native code]" in read, (
        f"a page that stringifies HTMLIFrameElement.prototype.{prop} reads "
        f"{len(read)} characters of our own source instead of the native form. "
        f"Every real engine returns `[native code]`. Read: {read[:160]!r}"
    )


# --------------------------------------------------------------------------
# AXIS 2 — and the source names none of our machinery. A SEPARATE axis from
# axis 1 and the sharper half: a wrapper could in principle read as native
# while some other read still exposed these names, and more importantly this
# is what turns a generic "something is wrapped" into an identification.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("prop", ACCESSORS)
def test_the_accessor_does_not_name_our_machinery(report, prop):
    """The source must not contain `__pnaInstall`, `LEAF` or `fresh`.

    Reading a wrapper's raw source reveals that something is wrapped; reading
    THESE names identifies the tool that wrapped it. That is why this is its
    own seat rather than a clause of the one above.
    """
    read = report["accessors"][prop]["source"]
    leaked = [n for n in MACHINERY if n in read]
    assert not leaked, (
        f"HTMLIFrameElement.prototype.{prop} stringifies to source naming "
        f"{leaked} — this does not merely disclose that the accessor is "
        f"wrapped, it identifies the tool. Read: {read[:160]!r}"
    )


# --------------------------------------------------------------------------
# AXIS 3 — the SHARP seat. The emitted form must be THIS engine's, derived
# from a real accessor rather than compared against a substring.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("prop", ACCESSORS)
def test_the_emitted_source_carries_the_engines_get_prefix(report, prop):
    """⭐ The seat that separates the correct fix from a plausible wrong one.

    V8 keeps the `get ` prefix in an accessor's SOURCE TEXT
    (`function get size() { [native code] }`, measured off `Map.prototype.size`
    in this very report). SpiderMonkey drops it, which is why the Firefox arm
    passes a bare `prop` as its source name — and why copying that convention
    onto Chromium emits the wrong engine's form.

    Both candidate strings contain `[native code]`, so the seat above cannot
    tell them apart:

        emitted with "get " + prop   "function get contentWindow() { [native code] }"
        emitted with bare prop       "function contentWindow() { [native code] }"

    Comparing against the shape DERIVED from a real engine accessor is what
    makes this checkable at all, and it is the same discipline the product
    itself follows: the shape is read off `hasOwnProperty` at runtime and is
    never written as a literal.
    """
    expected = "function get " + prop + _native_shape(report)
    read = report["accessors"][prop]["source"]
    assert read == expected, (
        f"HTMLIFrameElement.prototype.{prop} does not stringify as THIS "
        f"engine's accessor form. Emitting another engine's native shape is "
        f"itself a masking tell — one `Map.prototype.size` comparison away — "
        f"and it is not caught by an `[native code]` substring check, which "
        f"both forms satisfy.\n"
        f"  expected: {expected!r}\n"
        f"  got:      {read!r}"
    )


@pytest.mark.parametrize("prop", ACCESSORS)
def test_the_accessor_reports_the_engines_accessor_name(report, prop):
    """`.name` is a SECOND, independent axis — no stringification required.

    A native accessor reports `get contentWindow`. Before this fix the
    installed getter reported `"get"`: V8 infers a function expression's name
    from the `get:` property key in the object literal it is written in, so the
    wrapper carried a name no engine produces for that property — readable in
    one property access, with no `toString` call at all.
    """
    expected = "get " + prop
    got = report["accessors"][prop]["name"]
    assert got == expected, (
        f"HTMLIFrameElement.prototype.{prop}'s getter reports .name {got!r} "
        f"where the engine reports {expected!r}. This is readable without "
        f"stringifying anything, so it is a tell independent of the source "
        f"text the seats above cover."
    )


# --------------------------------------------------------------------------
# AXIS 4 — THE DELIVERY. A cloak that broke the chain would leave every child
# frame unspoofed while every seat above went green.
# --------------------------------------------------------------------------
def test_touching_contentwindow_still_delivers_the_leaf(report):
    """The cloak must not cost the chain its job.

    This is the seat that keeps the fix from trading a tell for an unmasked
    realm: `contentWindow` is how the leaf reaches a child frame, so a wrapper
    that reads beautifully and no longer installs anything would be a strictly
    worse outcome than the leak it replaced — and every other assertion in this
    file would still pass.

    Asserted on OBSERVED EFFECT rather than on the wrapper's shape: the leaf
    records the realms it was applied to, and the realm itself records that it
    was marked. The accessor must also still return the frame's window, since a
    chain that delivers but breaks the read would break every consumer.
    """
    d = report["delivery"]
    assert d["applied_delta"] >= 1, (
        "reading .contentWindow no longer runs the leaf — the cloak broke the "
        "chain, so every child frame is now UNSPOOFED while the stringification "
        "seats above pass. That is a worse outcome than the leak this fixes."
    )
    assert d["realm_marked"], (
        "the leaf ran but the child realm was not marked, so the delivery is "
        "not reaching the realm the accessor handed back"
    )
    assert d["returned_the_realm"], (
        "the accessor no longer returns the frame's own window — the wrapper "
        "broke the read itself, which breaks every consumer of the property"
    )

    doc = report["delivery_doc"]
    assert doc["applied_delta"] >= 1, (
        "reading .contentDocument no longer runs the leaf; that property "
        "reaches the realm through `defaultView`, a different branch of the "
        "same wrapper, so it needs its own reading"
    )
    assert doc["returned_the_document"], (
        "the accessor no longer returns the frame's own document"
    )
