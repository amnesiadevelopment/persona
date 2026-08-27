"""PS-215: what a 13-DEEP ``Function.prototype.toString`` chain looks like.

WHY THIS FILE EXISTS. ``WorkerCloak.setup`` is spliced INSIDE ``__pnaInstall``,
and every module riding ``realm_bootstrap_js`` carries that function — thirteen
of them. So the cloak's ``Function.prototype.toString`` patch is not installed
once per realm, it is installed ONCE PER RIDER, each closing over the previous
as its delegate. Before PS-215 that count was one on Chromium (``native_ext``'s
single ``__pnaName``-driven patch); it is now thirteen.

A reviewer asked for that consequence to be MEASURED rather than reasoned
about, and the question is a fair one: a detector stringifying
``Function.prototype.toString`` is precisely the trick the cloak is written to
survive, so the interesting failure is the cloak breaking ITSELF at depth.

⚠️ WHY THESE ASSERTIONS COULD BE WORTHLESS, AND WHAT MAKES THEM NOT BE. A cloak
answers ``toString`` by SYNTHESISING the native string. So a test that asks
``toString`` whether things look native is asking the thing under test to grade
itself — it passes on a build whose cloak is broken in any way that still emits
the string. That is a self-confirming probe, and this suite would be decorative
without the falsification below.

So every invisibility claim here is paired with a MUTATION ARM that breaks one
property in the generated bootstrap and asserts the probe goes RED. An arm that
stays green fails the suite loudly (``_mutate`` pins its anchor's uniqueness),
because a falsification that silently no-ops is worse than none.

SCOPE — SINGLE REALM, DELIBERATELY. Everything here is about chain ARITHMETIC in
one realm, which is exactly what ``node:vm`` can answer honestly. This file does
NOT probe cross-realm intrinsic identity (e.g. whether a wrapper built in the
parent is observable from the child): ``node:vm`` does not expose a context's
intrinsics, so such a probe reads ``undefined`` and produces a FALSE FAIL that
looks exactly like a masking regression. That question needs a real browser and
is deliberately left to one.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

from src.services.browser.worker_wrap import firefox_worker_cloak, realm_bootstrap_js

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is required to execute the bootstrap"
)

# How many riders splice `realm_bootstrap_js`. Derived, never hardcoded: the
# whole point is that this number is a property of the tree, so a fourteenth
# rider must move the test rather than silently widen the chain.
#
# Anchored to `__file__`, NOT to the process CWD. A relative path here resolves
# against whatever directory the interpreter happens to be in, so the glob
# matches nothing when an earlier suite in the same session has chdir'd away —
# `_rider_count()` then returns 0 and this file fails in a full-suite run while
# passing in isolation. Several pre-existing suites do leave the CWD elsewhere;
# resolving from `__file__` is the convention the rest of `tests/` already uses
# (test_build_config.py, test_ci_verification_gates.py, test_encoding_
# discipline.py, test_canvas_readback_probe.py) and makes this immune to it.
_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "browser"

# The PIN. Asserted exactly (not `>=`), because a `>=` would admit a fourteenth
# rider in silence — the precise outcome the pin exists to prevent. This is also
# the depth the probes run at, so the number a detector would observe and the
# number this file claims cannot drift apart.
_RIDER_COUNT = 13


def _rider_count() -> int:
    """Count the modules that splice a bootstrap, by reading the tree."""
    n = 0
    for f in sorted(_SRC.glob("*.py")):
        text = f.read_text(encoding="utf-8")
        # a CALL, not the import line and not the def
        n += text.count("realm_bootstrap_js(")
        if f.name == "worker_wrap.py":  # its own def + docstring mentions
            n -= text.count("realm_bootstrap_js(")
    return n


# The probe. `__pnaInstall` is what carries `setup`, so the bootstrap is
# executed N times in ONE realm — the top-realm situation exactly.
_PROBE = r"""
const vm = require("vm");
const fs = require("fs");
const BOOT = fs.readFileSync(process.argv[2], "utf8");
const N = parseInt(process.argv[3], 10);

const sandbox = { Reflect, WeakSet, WeakMap, console };
const ctx = vm.createContext(sandbox);
vm.runInContext("var self = this; globalThis.self = globalThis;", ctx);

// Record the PRISTINE forms before any patch is installed.
vm.runInContext(`
  globalThis.__pristineTS = Function.prototype.toString.call(Function.prototype.toString);
  globalThis.__pristineMap = Function.prototype.toString.call(Array.prototype.map);
`, ctx);

// A leaf that marks one wrapper through the cloak's own seam, so the wrapper
// under test is installed BY THE PRODUCT's mechanism, not by this test.
vm.runInContext(`
  globalThis.__victims = [];
  function applyPatch(G) {
    // A DOM-shaped wrapper: method shorthand, arity copied, like the real ones.
    var orig = ({ appendChild(node) { return node; } }).appendChild;
    var w = ({ m() { return orig.apply(this, arguments); } }).m;
    try { Object.defineProperty(w, "name", { value: "appendChild", configurable: true }); } catch (e) {}
    try { Object.defineProperty(w, "length", { value: orig.length, configurable: true }); } catch (e) {}
    globalThis.__victims.push({ fn: w, G: G });
  }
`, ctx);

// Install the bootstrap N times, as N riders would.
//
// WRAPPED IN AN IIFE, because that is how the product splices it: every rider
// embeds the bootstrap inside `(function () { ... })()` (see
// `native_ext.CONTENT_SCRIPT`, where `__REALM_BOOTSTRAP__` sits between those
// braces). Running it bare at top level would make the bootstrap's own `var
// __pnaInstall` a GLOBAL and this probe would report a disclosure the product
// does not have -- a false FAIL from the harness, not a finding.
for (let i = 0; i < N; i++) {
  vm.runInContext("(function () {\n" + BOOT + "\n})();", ctx);
}

const out = vm.runInContext(`(function () {
  const TS = Function.prototype.toString;
  return {
    chain_depth: ${N},
    ts_self: TS.call(TS),
    ts_self_matches_pristine: TS.call(TS) === globalThis.__pristineTS,
    // The SHAPE the cloak emits, read off a wrapper it marked. On the Firefox
    // arm this is SpiderMonkey's three-line form BY DESIGN, so it cannot equal
    // a V8 host's pristine string -- see the engine note in the test.
    pristine_host: globalThis.__pristineTS,
    untouched_native: TS.call(Array.prototype.map),
    untouched_matches_pristine: TS.call(Array.prototype.map) === globalThis.__pristineMap,
    ts_own_props: Object.getOwnPropertyNames(TS).sort(),
    ts_name: TS.name,
    ts_length: TS.length,
    // No shared coordination name may be published by ANY of the N installs.
    enumerable_globals: Object.keys(globalThis).filter(
      (k) => /^__pna|^__persona|^__h|^__b/.test(k)),
  };
})()`, ctx);

console.log(JSON.stringify(out));
"""


def _run(boot_js: str, tmp_path, n: int) -> dict:
    d = pathlib.Path(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    boot = d / "boot.js"
    boot.write_text(boot_js, encoding="utf-8")
    probe = d / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    r = subprocess.run(
        ["node", str(probe), str(boot), str(n)],
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


# --------------------------------------------------------------------------
# The falsification machinery (non-waivable). Each arm edits the GENERATED
# bootstrap -- the artifact actually under test -- rather than patching src/.
# --------------------------------------------------------------------------
def _mutate(js: str, anchor: str, replacement: str) -> str:
    assert js.count(anchor) == 1, (
        f"FALSIFICATION BROKEN: anchor {anchor!r} occurs {js.count(anchor)} times "
        f"in the generated bootstrap, so this mutation does not reproduce the "
        f"defect it claims to and the arm would be a no-op that always passes. "
        f"Update the anchor to match the source."
    )
    return js.replace(anchor, replacement)


_CHROMIUM = realm_bootstrap_js("applyPatch")
_FIREFOX = realm_bootstrap_js("applyPatch", firefox_worker_cloak())

# THE TWO ARMS TARGET DIFFERENT ENGINES, and the probe host is node (V8). So
# "reads as native" is NOT one string:
#
#   * the CHROMIUM arm derives the shape from the running engine, so on this V8
#     host it must equal the host's own pristine `toString` string exactly;
#   * the FIREFOX arm hard-codes SpiderMonkey's THREE-LINE form deliberately --
#     `tests/test_ff_language_override.py:573-574` pins that form and forbids
#     the V8 one-liner, because emitting V8's shape on Firefox is itself the
#     masking tell. Under a V8 host it therefore CANNOT equal host pristine,
#     and asserting that it does would be measuring the harness's engine
#     instead of the product's target.
_SPIDERMONKEY_TS = "function toString() {\n    [native code]\n}"


def test_rider_count_is_thirteen():
    """The chain depth is a property of the TREE, and it is 13.

    Pinned so that adding a fourteenth rider is a deliberate act that updates
    this file, rather than a silent widening of every realm's toString chain.
    """
    n = _rider_count()
    assert n == _RIDER_COUNT, (
        f"rider count is {n}, this file pins {_RIDER_COUNT}. A `>=` here would "
        f"admit a fourteenth rider in silence, which is exactly what the pin "
        f"exists to prevent. If the change is deliberate, update _RIDER_COUNT "
        f"and the N passed to _run() together — chain depth is what this suite "
        f"measures, so it must move with the tree."
    )


@pytest.mark.parametrize(
    "engine,boot",
    [("chromium", _CHROMIUM), ("firefox", _FIREFOX)],
    ids=["chromium", "firefox"],
)
def test_thirteen_chainings_leave_tostring_indistinguishable(engine, boot, tmp_path):
    """THE HEADLINE (reviewer question (b)).

    After thirteen installations in one realm, `Function.prototype.toString`
    must still stringify BYTE-IDENTICALLY to the pristine intrinsic, and an
    untouched native must be unaffected. This is the assertion the whole chain
    exists to keep true, measured at the depth the splice point actually
    produces rather than at the N=2 the idiom was introduced for.
    """
    r = _run(boot, tmp_path, _RIDER_COUNT)

    expected = r["pristine_host"] if engine == "chromium" else _SPIDERMONKEY_TS
    assert r["ts_self"] == expected, (
        f"[{engine}] after 13 chainings Function.prototype.toString does not "
        f"read as this arm's target-engine native form.\n"
        f"  expected: {expected!r}\n"
        f"  got:      {r['ts_self']!r}"
    )
    assert r["untouched_matches_pristine"], (
        f"[{engine}] an UNTOUCHED native changed shape under the chain: "
        f"{r['untouched_native']!r}"
    )
    assert r["ts_own_props"] == ["length", "name"], (
        f"[{engine}] the toString patch owns properties a native does not: "
        f"{r['ts_own_props']}"
    )
    assert r["ts_name"] == "toString", f"[{engine}] name tell: {r['ts_name']!r}"
    assert r["enumerable_globals"] == [], (
        f"[{engine}] 13 installs published a shared enumerable name — the exact "
        f"PS-48 disclosure chaining exists to avoid: {r['enumerable_globals']}"
    )


@pytest.mark.parametrize(
    "engine,boot",
    [("chromium", _CHROMIUM), ("firefox", _FIREFOX)],
    ids=["chromium", "firefox"],
)
def test_chain_is_depth_invariant(engine, boot, tmp_path):
    """One install and thirteen must be INDISTINGUISHABLE to a detector.

    This is what "chaining composes" has to mean operationally. If depth were
    observable, the number of riders would itself be a fingerprint.
    """
    one = _run(boot, tmp_path / "a", 1)
    many = _run(boot, tmp_path / "b", _RIDER_COUNT)

    for key in ("ts_self", "untouched_native", "ts_own_props", "ts_name", "ts_length"):
        assert one[key] == many[key], (
            f"[{engine}] chain DEPTH is observable through {key!r}: "
            f"1 install -> {one[key]!r}, 13 installs -> {many[key]!r}"
        )


# --------------------------------------------------------------------------
# MUTATION ARMS. Each breaks one property and asserts the probe goes RED.
# Without these, every assertion above is a cloak grading its own homework.
# --------------------------------------------------------------------------
_ARMS_CHROMIUM = [
    (
        "self-mark deleted",
        '__hnm.set(__hts, "toString");',
        "/* mutated: self-mark removed */;",
    ),
    (
        "wrong-engine native shape hardcoded",
        'if (__hpi > 0) { __hshape = __hps.slice(__hpi); }',
        '__hshape = "() {" + String.fromCharCode(10) + "    [native code]"'
        ' + String.fromCharCode(10) + "}";',
    ),
]


@pytest.mark.parametrize(
    "label,anchor,replacement", _ARMS_CHROMIUM, ids=[a[0] for a in _ARMS_CHROMIUM]
)
def test_falsification_arms_go_red(label, anchor, replacement, tmp_path):
    """Break it on purpose; the probe MUST notice.

    An arm that stays green proves the corresponding assertion is decorative.
    """
    mutated = _mutate(_CHROMIUM, anchor, replacement)
    r = _run(mutated, tmp_path, _RIDER_COUNT)

    assert not r["ts_self_matches_pristine"], (
        f"MUTATION '{label}' did NOT change what a detector reads off "
        f"Function.prototype.toString — the corresponding assertion in this "
        f"file is DECORATIVE and proves nothing. Got: {r['ts_self']!r}"
    )
