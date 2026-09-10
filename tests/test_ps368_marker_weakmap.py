"""PS-368 — no spoofed wrapper carries `__pnaName` as an own property.

WHAT THIS FILE MEASURES, AND WHY IT COULD NOT BE A SOURCE-TEXT TEST
───────────────────────────────────────────────────────────────────
Until this ticket every Chromium wrapper carried a non-enumerable `__pnaName`
OWN PROPERTY and native_ext's single `Function.prototype.toString` patch read it
(`this.__pnaName`). That marker is what made a cloak work at all across twelve
content scripts in one MAIN world with no shared closure and no guaranteed load
order — it is a CROSS-SCRIPT PROTOCOL, because the name travels ON the function.

It also made every spoofed wrapper own a third name. Measured out of a realm
after the REAL generated scripts ran, against this suite's native-shaped control:

    NATIVE   Array.prototype.map          ["length","name"]
    CHROMIUM wrapper (own-property tell)  ["length","name","__pnaName"]  ← DETECTED
    FIREFOX  wrapper (WeakMap marker)     ["length","name"]              ← clean

Two one-line probes read that without touching source text, so the toString
cloak was irrelevant to both:

    Object.getOwnPropertyNames(<any spoofed wrapper>)   3 names vs 2
    "__pnaName" in <any spoofed wrapper>                positive persona ID

⛔ THE MARKER WAS LOAD-BEARING, WHICH IS WHY A NAIVE STRIP WOULD HAVE BEEN WORSE
THAN THE DEFECT. Disabling only native_ext's `this.__pnaName` reader decloaked
every leaf instantly — `getParameter` stringified as `m() { return
replacement.apply(this, arguments); }`. So the fix is not "remove the marker": it
is "give each module its own closure WeakMap and its own chained cloak, THEN the
marker has nothing left to buy". Ten of the twelve modules had no cloak of their
own and now do, from one shared emitter (`worker_wrap.chromium_leaf_cloak_js`).

⚠️ NOT A CLAIM ABOUT `Function.prototype.toString`'s OWN SHAPE. That function is
native in the shipped artifact and always has been since PS-215's `__hts`
overwrote native_ext's older expression-shaped `patched`;
`test_ps215_tostring_chain.py` pins it at the real 14-deep chain. This ticket's
original headline said otherwise and was refuted before any code moved.

THE FOUR AXES ARE READ IN ONE PASS, DELIBERATELY (knowledge article PS-22)
──────────────────────────────────────────────────────────────────────────
`Object.getOwnPropertyNames`, `.length`, `.name` and the stringification are read
from the same probe, off the same installed function. A change that fixes the
own-property set BY BREAKING the stringification is exactly the trap this ticket
names, and reading the axes in separate passes is how such a change ships green.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from tests.ps368_harness import (
    CHROMIUM_MODULES,
    CONSTRUCTOR_READS,
    NATIVE_SHAPE,
    NODE,
    WRAPPER_READS,
    build_scripts,
    native_form,
    probe,
)

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node is needed to evaluate the generated scripts in a realm"
)


@pytest.fixture(scope="module")
def scripts() -> dict[str, str]:
    return build_scripts(tempfile.mkdtemp())


@pytest.fixture(scope="module")
def composed(scripts) -> dict:
    """Every Chromium module in one realm, in declaration order."""
    return probe([scripts[m] for m in CHROMIUM_MODULES])


@pytest.fixture(scope="module")
def constructors(scripts) -> dict:
    """The Intl constructors, which are read separately from WRAPPER_READS.

    `locale_ext`'s `_wrap` deliberately does not re-house them, so their
    own-property SET legitimately differs from a method's — see the test below.
    """
    return probe([scripts[m] for m in CHROMIUM_MODULES], reads=CONSTRUCTOR_READS)


@pytest.fixture(scope="module")
def composed_reversed(scripts) -> dict:
    """The same modules, REVERSE order. AC2's both-load-orders requirement.

    Order is not incidental here. Twelve chained cloaks compose only because each
    delegates to whatever it found; a build that flag-guarded instead would leave
    the loser's wrappers registered in a map nobody consults, and that failure is
    ORDER-DEPENDENT and silent. Running the whole composition both ways is the
    only thing that sees it.
    """
    return probe([scripts[m] for m in reversed(CHROMIUM_MODULES)])


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — the control. Without it, every assertion below could be measuring
# nothing.
# ─────────────────────────────────────────────────────────────────────────────


def test_the_realm_baseline_is_native_shaped_and_the_probe_can_see_a_marker():
    """The stand-in built-ins read the native set BEFORE any script runs, and a
    marker pinned by hand IS visible to this probe.

    Both halves are load-bearing and they fail in opposite directions. Without
    the first, "the wrapper owns two names" could be true because the baseline
    owned two names for reasons of its own. Without the second, "no wrapper owns
    `__pnaName`" could be true because the probe cannot see an own property at
    all — a green that can never go red.
    """
    out = probe(
        [],
        reads={
            "native_method": "WebGLRenderingContext.prototype.getParameter",
            "native_accessor":
                "Object.getOwnPropertyDescriptor(G.screen,'width').get",
            "hand_marked": "G.__probe_marked",
        },
        extra_js="""
G.__probe_marked = ({ m() {} }).m;
Object.defineProperty(G.__probe_marked, "__pnaName", { value: "marked" });
""",
    )
    assert out["native_method"]["own"] == NATIVE_SHAPE, (
        f"the realm's stand-in built-in owns {out['native_method']['own']}, not "
        f"the native set — every other assertion in this file is void"
    )
    assert out["native_method"]["marker"] is False
    assert out["native_accessor"]["own"] == NATIVE_SHAPE
    assert out["hand_marked"]["own"] == ["__pnaName", "length", "name"], (
        "a hand-pinned `__pnaName` is invisible to this probe, so 'no wrapper "
        "owns it' would pass for a reason unrelated to the product"
    )
    assert out["hand_marked"]["marker"] is True


# ─────────────────────────────────────────────────────────────────────────────
# AC2 — no wrapper carries `__pnaName` as an own property. Both load orders.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("label", sorted(WRAPPER_READS))
def test_no_wrapper_owns_the_marker(composed, label):
    got = composed[label]
    assert got is not None, (
        f"{label} was not installed by the real script — a script that does not "
        f"parse installs nothing, and every assertion about it would pass"
    )
    assert got["marker"] is False, (
        f"{label} still answers `\"__pnaName\" in fn` — one line, no call, and "
        f"positive identification of persona specifically"
    )
    assert got["own"] == NATIVE_SHAPE, (
        f"{label} owns {got['own']}, not {NATIVE_SHAPE}. A third own name is "
        f"readable by Object.getOwnPropertyNames without calling anything and "
        f"is independent of the toString cloak"
    )


@pytest.mark.parametrize("label", sorted(WRAPPER_READS))
def test_no_wrapper_owns_the_marker_in_the_reverse_load_order(
    composed_reversed, label
):
    got = composed_reversed[label]
    assert got is not None, f"{label} was not installed in the reverse order"
    assert got["marker"] is False
    assert got["own"] == NATIVE_SHAPE, f"{label} owns {got['own']} (reverse order)"


@pytest.mark.parametrize("label", sorted(CONSTRUCTOR_READS))
def test_intl_constructors_drop_the_marker_without_being_re_housed(
    constructors, label
):
    """The Intl constructor boundary: marker gone, `prototype` legitimately kept.

    `locale_ext`'s `_wrap` deliberately does NOT re-house its `W` in a method
    shorthand — a shorthand is NOT CONSTRUCTIBLE, so `new Intl.DateTimeFormat()`
    would throw — and a native Intl constructor genuinely owns `prototype` and
    `supportedLocalesOf` too. That exemption is about the SHAPE axis and says
    nothing about the marker, so this asserts the marker alone.
    """
    got = constructors[label]
    assert got is not None, f"{label} was not installed"
    assert got["marker"] is False, (
        f"{label} still owns `__pnaName` — the constructor-boundary exemption "
        f"covers the function FORM, never the marker"
    )
    assert "__pnaName" not in got["own"]


def test_no_generated_chromium_script_writes_the_marker_at_all(scripts):
    """Not one live `defineProperty(…"__pnaName"…)` survives in any bundle.

    A belt-and-braces companion to the realm reads above, and it catches what
    they structurally cannot: a write site on a code path this realm never
    exercises (a leaf arm that needs a surface the harness does not supply). The
    realm probe is the primary witness precisely because THIS check would pass
    against a script that does not parse — so neither replaces the other.

    Comments are excluded: this ticket's whole record lives in prose that names
    the marker, and a raw substring check would forbid explaining the change.
    """
    for module, js in scripts.items():
        live = [
            (i, line.strip())
            for i, line in enumerate(js.splitlines(), 1)
            if "__pnaName" in line
            and "//" not in line.split("__pnaName")[0]
        ]
        assert live == [], (
            f"{module} still writes `__pnaName` outside a comment: {live[:3]}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# AC3 — the toString cloak still works, INCLUDING ON ITSELF.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("label", sorted(WRAPPER_READS))
def test_every_wrapper_still_stringifies_as_native(composed, label):
    """The axis a WeakMap move opens if the registration is dropped.

    ⛔ This is the trap the ticket names: fixing the own-property set by breaking
    the stringification ships something strictly worse than the marker. Measured
    on the leaves with only native_ext's reader disabled, they decloaked
    instantly — `getParameter` read `m() { return replacement.apply(this,
    arguments); }`.
    """
    got = composed[label]
    assert got is not None, f"{label} was not installed"
    assert got["stringified"] == native_form(got["name"]), (
        f"{label} stringifies as {got['stringified'][:120]!r}, not the native "
        f"form. The own-property fix must JOIN the toString guarantee, never "
        f"replace it (PS-131 / PS-16 / PS-17)"
    )


def test_the_cloak_stringifies_as_native_on_ITSELF(composed, composed_reversed):
    """`Function.prototype.toString.toString()` reads native, in both orders.

    A detector stringifies `Function.prototype.toString` to catch exactly this
    trick, so the outermost cloak must have registered ITSELF in its own map
    (`__pncMark(__pncTs, "toString")`). Whichever of the twelve links ends up
    outermost answers this read, so it is asserted in both compositions rather
    than once.
    """
    for name, out in (("forward", composed), ("reverse", composed_reversed)):
        got = out["toString"]
        assert got is not None
        assert got["stringified"] == native_form("toString"), (
            f"{name} order: the cloak betrayed itself — {got['stringified'][:120]!r}"
        )
        assert got["own"] == NATIVE_SHAPE, (
            f"{name} order: Function.prototype.toString owns {got['own']}; once "
            f"installed the cloak IS that function, so an expression form (which "
            f"owns `prototype`, non-configurably) would be visible here"
        )
        assert got["name"] == "toString"
        assert got["length"] == 0, (
            f"{name} order: Function.prototype.toString reports arity "
            f"{got['length']}; native is 0 and it must be copied from the "
            f"original rather than hard-coded"
        )


def test_the_cloak_reaches_a_worker_realm(scripts):
    """A worker realm gets its own cloak, and its wrappers read native THERE.

    The leaf crosses into a worker as SOURCE TEXT (`LEAF.toString()`), so a map
    declared in an enclosing IIFE would be `undefined` there — the exact failure
    worker_wrap's docstring records, where a depth-2 worker silently reported
    real values while the page reported spoofed ones. This models the crossing by
    re-evaluating the same generated text against a fresh global, which is what
    the worker payload does.

    ⚠️ THE READ MUST HAPPEN INSIDE THE SECOND REALM, and getting that wrong is
    how this probe reports a leak against a correct fix. `Function.prototype
    .toString.call(childFn)` evaluated in THIS realm resolves against THIS
    realm's cloak, whose WeakMap has never seen the child's wrapper — so it
    correctly delegates and returns the raw source, which reads exactly like a
    decloaked child. Every realm has its own `Function.prototype`; the binding
    under test is the CHILD's, so the stringification is taken there and only the
    resulting strings cross back.
    """
    out = probe(
        [scripts["gpu_ext"], scripts["audio_ext"], scripts["native_ext"]],
        reads={
            "worker:getParameter": "G.__WREAD.getParameter",
            "worker:getChannelData": "G.__WREAD.getChannelData",
            "worker:toString": "G.__WREAD.toString",
        },
        extra_js="""
const vm = require("node:vm");
const w = vm.createContext({ require });
vm.runInContext(REALM_SRC, w);
// Read from INSIDE the child realm — its own Function.prototype.toString is the
// binding under test. Only the resulting scalars cross back.
G.__WREAD = vm.runInContext(`(function () {
  const T = Function.prototype.toString;
  const read = function (f) {
    return { own: Object.getOwnPropertyNames(f).sort(),
             name: f.name, length: f.length,
             marker: ("__pnaName" in f),
             stringified: T.call(f) };
  };
  return {
    getParameter: read(WebGLRenderingContext.prototype.getParameter),
    getChannelData: read(AudioBuffer.prototype.getChannelData),
    toString: read(Function.prototype.toString),
  };
})()`, w);
""",
        raw_reads=True,
    )
    for label, expected in (
        ("worker:getParameter", "getParameter"),
        ("worker:getChannelData", "getChannelData"),
        ("worker:toString", "toString"),
    ):
        got = out[label]
        assert got is not None, f"{label} was not installed in the second realm"
        assert got["marker"] is False, f"{label} owns the marker in a worker realm"
        assert got["own"] == NATIVE_SHAPE, f"{label} owns {got['own']}"
        assert got["stringified"] == native_form(expected), (
            f"{label} stringifies as {got['stringified'][:120]!r} in the second "
            f"realm — a cloak whose map lives outside the leaf body reads exactly "
            f"like this"
        )


# ─────────────────────────────────────────────────────────────────────────────
# AC4 — arity and identity preserved per site (the PS-119 / PS-255 axis).
# ─────────────────────────────────────────────────────────────────────────────

# (label, expected .name, expected .length). The arities are the realm's NATIVE
# stand-ins', so a wrapper that MOVED arity fails here — a shape fix that swaps
# one tell for another is the failure mode PS-119 measured on Intl.
IDENTITY_EXPECTATIONS: dict[str, tuple[str, int]] = {
    "gpu:getParameter": ("getParameter", 1),
    "gpu:getExtension": ("getExtension", 1),
    "gpu:getSupportedExtensions": ("getSupportedExtensions", 0),
    "webgl:readPixels": ("readPixels", 7),
    "audio:getChannelData": ("getChannelData", 1),
    "audio:getFloatFrequencyData": ("getFloatFrequencyData", 1),
    "audio:getByteFrequencyData": ("getByteFrequencyData", 1),
    "device:matchMedia": ("matchMedia", 1),
    "device:screen.width": ("get width", 0),
    "device:screen.availHeight": ("get availHeight", 0),
    "device:devicePixelRatio": ("get devicePixelRatio", 0),
    "device:hardwareConcurrency": ("get hardwareConcurrency", 0),
    "device:enumerateDevices": ("enumerateDevices", 0),
    "geo:getCurrentPosition": ("getCurrentPosition", 2),
    "geo:watchPosition": ("watchPosition", 2),
    "geo:clearWatch": ("clearWatch", 0),
    "voice:getVoices": ("getVoices", 0),
    "locale:Date#toLocaleString": ("toLocaleString", 0),
    "locale:Date#toString": ("toString", 0),
    "locale:Number#toLocaleString": ("toLocaleString", 0),
    "canvas_ctx:getContext": ("getContext", 1),
    "mt:measureText": ("measureText", 1),
    "toString": ("toString", 0),
}


@pytest.mark.parametrize("label", sorted(IDENTITY_EXPECTATIONS))
def test_arity_and_name_are_unchanged_per_site(composed, label):
    name, arity = IDENTITY_EXPECTATIONS[label]
    got = composed[label]
    assert got is not None, f"{label} was not installed"
    assert got["name"] == name, (
        f"{label} reads .name={got['name']!r}, expected {name!r}. `.name` is a "
        f"SECOND axis the toString cloak cannot reach"
    )
    assert got["length"] == arity, (
        f"{label} reports arity {got['length']}, native is {arity} — a shape fix "
        f"that moves arity swaps one tell for another (PS-119 / PS-255)"
    )


def test_the_accessors_stringify_with_V8s_get_prefix(composed):
    """⚠️ The one thing the Firefox idiom gets WRONG when ported verbatim.

    SpiderMonkey renders a native getter's source WITHOUT the `get ` prefix, so
    `invisible_launch._native_cloak_js` takes the stringified name SEPARATELY
    from the pinned `.name` and its accessor call sites pass the bare property
    name. V8 KEEPS the prefix — measured off
    `Object.getOwnPropertyDescriptor(Map.prototype,'size').get`, whose source
    reads `function get size() …`.

    So a port that dropped the prefix here would emit the WRONG ENGINE's form,
    which is a sharper tell than the marker it replaced — one
    `Map.prototype.size` comparison away. This is the assertion that catches it.
    """
    for label in (
        "device:screen.width",
        "device:screen.availHeight",
        "device:devicePixelRatio",
        "device:hardwareConcurrency",
    ):
        got = composed[label]
        assert got is not None, f"{label} was not installed"
        assert got["stringified"].startswith("function get "), (
            f"{label} stringifies as {got['stringified'][:80]!r} — V8 keeps the "
            f"`get ` prefix in a native accessor's source text, and emitting "
            f"SpiderMonkey's prefix-less form is itself a masking tell"
        )
        assert got["stringified"] == native_form(got["name"])


def test_the_falsification_arms_do_not_disturb_any_spoofed_value(scripts):
    """⛔ ZERO FINGERPRINT CONTENT MOVES — the ticket's hardest out-of-scope fence.

    This change touches ~21 write sites across twelve modules, several of them
    inside minified one-liners that also carry the arity and name pins. A slip
    there would not fail any assertion above: the own-property set, the arity,
    the name and the stringification would all still read correctly while the
    profile silently reported a different machine — and unlinkability regressions
    are exactly the class this project has shipped green before.

    So the spoofed VALUES are read back out of the realm and pinned. These
    figures were verified byte-identical against the pristine base (`360c488`)
    by running this same probe under both trees, which is the measurement this
    assertion freezes rather than an expectation invented here.

    ⭐ TWO READS WERE REMOVED WHEN THEIR SPOOF LEFT THE PAGE, and the reason is
    worth stating so nobody restores them as "missing coverage". `cores` and
    `mem` used to read `G.navigator.hardwareConcurrency` / `.deviceMemory`,
    which `device_ext` installed as own properties. The pixelscan port deleted
    both installs — the engine authors those properties natively in every realm
    — so in this harness they now read whatever the stub realm happens to carry,
    which is NOT a persona-spoofed value and cannot detect a marker edit.

    ⛔ KEEPING THEM WOULD HAVE BEEN WORSE THAN DROPPING THEM: an assertion on a
    value persona no longer authors is an assertion that cannot fail for the
    reason the test claims, which is precisely the vacuous-guard shape this
    suite exists to prevent. The remaining six reads still span five extensions
    and every value in them is genuinely persona-authored.
    """
    out = probe(
        [
            scripts[m]
            for m in (
                "native_ext",
                "gpu_ext",
                "webgl_ext",
                "audio_ext",
                "device_ext",
                "locale_ext",
                "voice_ext",
                "geo_ext",
            )
        ],
        reads={
            "screen.width": "G.screen.width",
            "screen.height": "G.screen.height",
            "dpr": "G.devicePixelRatio",
            "gpu_vendor":
                "(function(){var c=new WebGLRenderingContext();"
                " return c.getParameter(0x1F00);})()",
            "audio":
                "JSON.stringify(Array.from(new AudioBuffer().getChannelData(0)))",
        },
        raw_reads=True,
    )
    assert out["__errors"] == []
    assert out["screen.width"] == 1440 and out["screen.height"] == 900
    assert out["dpr"] == 1
    assert out["gpu_vendor"] == "WebKit"
    assert out["audio"] == (
        "[0.49999499320983887,-0.25000250339508057,0.12499874830245972]"
    ), (
        "the perturbed audio samples moved — a marker edit reached the noise "
        "path, and two profiles' unlinkability is not something the shape "
        "assertions above can see"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC5 — FALSIFICATION (non-waivable). Both arms must go RED, or the file above
# is asserting properties that hold for reasons unrelated to this change.
# ─────────────────────────────────────────────────────────────────────────────

# The two anchors, asserted present before each mutation runs, so a source edit
# that moves them fails LOUDLY instead of quietly neutering the falsification
# into a no-op that always passes.
_MARKER_REVERT_ANCHOR = "return __pncMark(shell, orig.name);"
_SELF_REGISTER_ANCHOR = '__pncMark(__pncTs, "toString");'


def _mutate(js: str, anchor: str, replacement: str) -> str:
    found = js.count(anchor)
    assert found >= 1, (
        f"FALSIFICATION BROKEN: the anchor {anchor!r} no longer occurs in the "
        f"generated script, so this mutation is a no-op and the arm always "
        f"passes. Update it to match the source."
    )
    return js.replace(anchor, replacement)


def test_falsification_reverting_only_the_marker_change_goes_red(scripts):
    """Put the own-property marker back on ONE leaf: AC2 must catch it.

    Only the marker moves. The leaf keeps its cloak, keeps its WeakMap
    registration, and still stringifies as native — so the RED can only be the
    own-property axis, which is the axis AC2 is about.
    """
    reverted = _mutate(
        scripts["gpu_ext"],
        _MARKER_REVERT_ANCHOR,
        "try { Object.defineProperty(shell, '__pnaName', { value: orig.name }); }\n"
        "    catch (e) {}\n"
        "    return __pncMark(shell, orig.name);",
    )
    out = probe(
        [scripts["native_ext"], reverted],
        reads={"getParameter": "WebGLRenderingContext.prototype.getParameter"},
    )
    got = out["getParameter"]
    assert got is not None, "the mutated script did not install — the arm is void"
    assert got["marker"] is True, (
        "the marker revert did not reintroduce the own property, so AC2's green "
        "does not witness the marker's removal"
    )
    assert got["own"] == ["__pnaName", "length", "name"]
    # ...and the cloak is untouched, so the RED above is the marker alone.
    assert got["stringified"] == native_form("getParameter")


def test_falsification_reverting_only_the_self_registration_goes_red(scripts):
    """Drop `__pncMark(__pncTs, "toString")`: AC3's self-cloak must catch it.

    ⛔ THIS IS THE TRAP THE TICKET NAMES, executed. Moving a marker into a WeakMap
    without also registering the cloak itself fixes the own-property axis and
    OPENS the stringification axis — the patch leaks its own source to any
    detector that stringifies `Function.prototype.toString`, which is the first
    thing one does. The wrappers still read native (their own registration is
    untouched), so only the cloak's self-read goes red — which is exactly the
    discrimination this arm exists to make.

    ⚠️ THE BOOTSTRAP'S OWN CLOAK IS DISABLED FOR THIS ARM, AND THAT IS THE POINT
    RATHER THAN A CONVENIENCE. `realm_bootstrap_js` splices
    `_CHROMIUM_HOOK_CLOAK_SETUP` — `__hts`, which every one of the twelve riders
    carries — AFTER `LEAF(G)`, so `__hts` is ALWAYS installed over the leaf's own
    cloak and is always the link that answers a self-read. Measured: with the
    leaf's self-registration stripped and `__hts` in place, the read still comes
    back native, because `__hts` registered ITSELF.

    That makes the leaf cloak's self-registration DEFENDED IN DEPTH rather than
    unnecessary, and the distinction matters two ways. It is load-bearing on any
    path where `__hts` is absent or fails soft (no `WeakMap`, no derivable native
    shape, a realm whose `Function.prototype` could not be reached — the emitter
    delegates rather than guessing in each case). And leaving the arm as it was
    written would have been the worse outcome: it would still RUN, still be
    green, and witness NOTHING — the vacuity this whole suite exists to avoid.

    So the mutation disables `__hts`'s install as the CONTROL, asserts the read
    is native with the leaf's own registration doing the work, and only then
    strips that registration. The RED is then attributable to the one line under
    test.
    """
    hts_install = "if (__hF && __hF.prototype) { __hF.prototype.toString = __hts; }"

    def build(*mutations):
        out = []
        for module in CHROMIUM_MODULES:
            js = scripts[module]
            for anchor, replacement in mutations:
                js = _mutate(js, anchor, replacement)
            out.append(js)
        return out

    reads = {
        "toString": "G.Function.prototype.toString",
        "getParameter": "WebGLRenderingContext.prototype.getParameter",
    }

    # CONTROL: `__hts` out of the way, leaf self-registration in place. The leaf
    # cloak alone must answer the self-read natively, or the arm below cannot
    # attribute its RED to anything.
    control = probe(build((hts_install, "")), reads=reads)
    assert control["toString"]["stringified"] == native_form("toString"), (
        "with the bootstrap's own cloak disabled the LEAF cloak must still read "
        f"as native; got {control['toString']['stringified'][:160]!r}. If it does "
        "not, the mutation below has nothing to falsify."
    )

    # THE ARM: same build, minus the one line under test.
    broken = probe(
        build((hts_install, ""), (_SELF_REGISTER_ANCHOR, "")), reads=reads
    )
    ts = broken["toString"]
    assert ts is not None
    assert ts["stringified"] != native_form("toString"), (
        "the cloak still read as native with its OWN registration stripped, so "
        "AC3's self-cloak assertion does not witness that registration — which "
        "is the single line whose absence ships something worse than the marker"
    )
    assert "__pncO.apply" in ts["stringified"] or "'use strict'" in ts["stringified"], (
        f"expected the cloak's own source to leak; got {ts['stringified'][:160]!r}"
    )
    # ...and the wrappers are unaffected, so the RED is the self-registration.
    assert broken["getParameter"]["stringified"] == native_form("getParameter")


def test_falsification_the_probe_would_see_a_decloaked_leaf(scripts):
    """Strip a leaf's whole cloak install: its wrapper must leak source.

    The third mutation, and the one that pins the LOAD-BEARING finding. Before
    this ticket a leaf's wrapper was served by native_ext's cross-script reader,
    so removing the leaf's own machinery changed nothing. Now each leaf serves
    its own, and a leaf whose install line is gone registers into a map nothing
    consults — its source leaks even with every other module present. That is
    what makes "give each module its own cloak" load-bearing rather than tidy.
    """
    anchor = (
        "if (__pncF && __pncF.prototype) { __pncF.prototype.toString = __pncTs; }"
    )
    decloaked = _mutate(scripts["gpu_ext"], anchor, "")
    out = probe(
        [decloaked],
        reads={"getParameter": "WebGLRenderingContext.prototype.getParameter"},
    )
    got = out["getParameter"]
    assert got is not None, "the mutated script did not install — the arm is void"
    assert got["stringified"] != native_form("getParameter"), (
        "the leaf's wrapper still read as native with the leaf's OWN cloak "
        "install stripped and no other module loaded — something is serving it "
        "cross-script, which is the own-property protocol coming back"
    )
    # The own-property axis is untouched by this mutation, which is what makes
    # the two axes independently falsifiable rather than one claim in disguise.
    assert got["marker"] is False
