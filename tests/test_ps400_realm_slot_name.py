"""PS-400 — the `Object.__pnaRealm` own-property name, RULED ON rather than re-litigated.

WHAT THIS FILE OWNS, AND WHY IT IS A TEST AND NOT A COMMENT
------------------------------------------------------------
`Object.__pnaRealm` is readable in one line from any page realm
(`Object.getOwnPropertyNames(Object)`), and the name identifies persona. That
bound has been stated twice in `worker_wrap.py` — above `realm_guard_js` and
above `realm_slot_js` — and declined twice, both times with the honest phrase
"this slice does not claim to close it".

PS-400 asked for the third thing: **close it, or rule on it**. It is ruled on.
Every alternative was executed, with controls, and every one is worse than the
shipped design. This suite is where that ruling lives, because a decision
recorded only in prose is a decision the next slice re-opens from scratch —
which is precisely what happened here twice before.

⚠️ SO THESE TESTS DO NOT ASSERT "THE NAME IS GONE". The name is still there,
deliberately. They assert the FOUR PROPERTIES THAT MAKE EACH ALTERNATIVE WORSE,
so that a future slice which reaches for one of them goes red with the reason
attached rather than shipping it and discovering the cost in production.

THE FOUR RULINGS, each with its own test below
------------------------------------------------
1. **Closure / WeakMap keyed on realm — RULED OUT by constraint (b).**
   Two INDEPENDENT `__pnaInstall` invocations reach the same realm
   (`all_frames:true`: the child runs the content script itself AND is installed
   into by the parent's `contentWindow` accessor). They share no closure, so a
   closure map is a DIFFERENT map and dedup never fires — the leaf double-applies
   and the realm's noise is applied twice. `worker_wrap.py` states this; this
   file executes it, with the shipped design as the positive control.

   ⛔ PS-368 is NOT the precedent the ticket took it for. Its `__pnaName` cloak
   is per-LEAF state — producer and consumer are the SAME invocation, which is
   why a closure sufficed there. The registry's reader shares no closure with
   its writer. Same `__pna` family, structurally different problem.

2. **Symbol keying — RULED OUT.** Moves the name from `getOwnPropertyNames` to
   `getOwnPropertySymbols`: one more line for a detector, not zero. It also
   breaches this file's own in-tree standard, which requires the registry not be
   "enumerated OR SWEPT FOR SYMBOLS".

3. ⭐ **Per-profile seed-derived name — RULED OUT, and it is a REGRESSION.**
   This is the one that looks like a free win and is not, so it gets the longest
   test. The proposal is to replace the shared literal `__pnaRealm` with a
   per-profile string derived from the profile seed. It fails on BOTH axes:

   * **It does not reduce detection.** The argument for it was that a random
     name converts a zero-baseline identification into one needing a
     per-engine-version baseline of `gOPN(Object)`. That premise is false:
     `Object`'s own property names are **ECMA-262 specified**, so a detector
     needs the SPEC VOCABULARY — public, hardcodable, no host control required —
     and `own name ∉ spec vocabulary` catches a random name exactly as it
     catches `__pnaRealm`. The rename defeats only a detector grepping `__pna`.

   * **It creates a supercookie.** A name that is identical across every realm
     and leaf of one profile, stable across restarts, and unique per profile is
     by construction a cross-site identifier readable in one line — one that
     survives cookie clearing, storage clearing and private browsing. An 8-char
     base36 name carries ~41 bits against the ~33 needed to index every human
     alive. The SHARED constant carries ZERO: every persona profile on earth
     reads the same string, which is what makes them mutually indistinguishable
     on this vector. Uniqueness here is Level 2 of the project bar inverted.

   ⛔ The shared-constant property is therefore LOAD-BEARING, not an accident of
   implementation, and `test_the_slot_name_is_a_shared_constant_across_profiles`
   pins it against exactly this "improvement".

4. **Hiding the name from enumeration — RULED OUT on cost.** Eight independent
   entry points expose an own property name (`getOwnPropertyNames`,
   `Reflect.ownKeys`, `getOwnPropertyDescriptors`, `getOwnPropertyDescriptor`,
   `in`, `Object.hasOwn`, `hasOwnProperty`, and a direct read). Suppressing the
   name means wrapping core intrinsics that ordinary page code calls on a hot
   path — a strictly larger tell than the single name being hidden.

THE RESIDUAL, STATED NOT HIDDEN
---------------------------------
One unexpected own name remains on `Object`. That is not closed and this file
does not claim otherwise; per finding 2 above it is structurally forced, because
a cross-realm channel must hang off something reachable from another realm's
global, and "reachable from another realm's global" is what an own property IS.
What changed is its status: from "declined, unexamined" to "examined, ruled on,
with each alternative's cost measured".

WHY THE ASSERTIONS ARE EXECUTED AND NOT GREPPED. This subsystem's standard (see
`test_realm_value_channels.py`'s header, and knowledge PS-11) is that a
source-text sweep passes on a build that merely renamed a thing and fails on one
that reworded a comment — sensitive to exactly the wrong thing. The behavioural
tests below run real JS under node and read what a fingerprinter reads. The one
test that DOES read generated source is the shared-constant test, which is
legitimate precisely because the thing under test IS a literal in emitted text.

⚠️ AND THAT ONE TEST IS WHERE THIS FILE ALREADY GOT CAUGHT ONCE. Reading
generated source is legitimate here, but it is not free: the built `device.js`
contains `defineProperty` calls that have nothing to do with the realm slot (the
toString cloak copies `length` and `name` onto wrapped functions), so the
shape-matched extraction returned `{'__pnaRealm', 'length', 'name'}` and the
sentinel asking only that the set be non-empty PASSED with the slot's emitter
deleted outright — green for the exact case its own failure message described.
Both pins are now shown to bite by injection rather than observed passing on a
clean tree: removing the emitter takes the sentinel red, and a faithful
per-profile rename takes the distinctness pin red. See `_installed_slot_names`.
"""

import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

from src.services.browser.device_ext import build_device_extension
from src.services.browser.worker_wrap import realm_guard_js, realm_slot_js

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

# Three seeds spanning the space, including the degenerate low one. They are
# arbitrary but FIXED, so a failure is reproducible.
SEEDS = (0x1234ABCD, 0xDEADBEEF, 0x00000001)

# `Object`'s own property names as ECMA-262 specifies them, plus the historical
# V8 extras (`Object.observe` and friends) a detector would tolerate. This list
# is the POINT of ruling 3: it is public, static, and needs no live control — so
# a detector diffing against it is not paying the "per-engine-version baseline"
# cost the rename proposal assumed it would.
SPEC_OBJECT_OWN_NAMES = frozenset({
    "assign", "create", "defineProperties", "defineProperty", "entries",
    "freeze", "fromEntries", "getOwnPropertyDescriptor",
    "getOwnPropertyDescriptors", "getOwnPropertyNames",
    "getOwnPropertySymbols", "getPrototypeOf", "groupBy", "hasOwn", "is",
    "isExtensible", "isFrozen", "isSealed", "keys", "length", "name",
    "preventExtensions", "prototype", "seal", "setPrototypeOf", "values",
    # historical / engine-specific, tolerated by a detector's allow-list
    "observe", "unobserve", "getNotifier", "deliverChangeRecords",
})


def _defined_slot_names(js: str) -> set:
    """The property name(s) a fragment installs on the realm's `Object`.

    ⚠️ DELIBERATELY NOT ANCHORED ON THE `__pna` PREFIX. A prefix-anchored regex
    answers "no literal found" for a fragment that was RENAMED — which is the
    exact change this test exists to catch, reported as if the emitter had
    vanished. Matching the `defineProperty(O, '<name>', …)` SHAPE keeps the
    failure legible whatever the name becomes.
    """
    return set(re.findall(r"defineProperty\(\s*\w+\s*,\s*'([^']+)'", js))


def _installed_slot_names(js: str) -> set:
    """`_defined_slot_names` narrowed to names that could BE the realm slot.

    ⚠️ THE `device.js` CALL SITE NEEDS THIS AND THE TWO EMITTERS DO NOT, which
    is why it is a second function rather than a change to the first. Each
    emitter contains exactly one `defineProperty`, so reading them raw is a real
    comparison. The BUILT artifact is different: the toString cloak also calls
    `Object.defineProperty(g, 'name', …)` and `(s, 'length', …)` to copy a
    wrapped function's arity and name, so the shape-matched set against a real
    build is `{'__pnaRealm', 'length', 'name'}` — two of which have nothing to
    do with the realm slot.

    That contamination made the sentinel in the shared-constant test INERT: it
    asked only that the set be non-empty, and `{'length', 'name'}` satisfies
    that with the slot's `defineProperty` deleted outright. Measured, not
    reasoned — the emitter was removed from a built artifact and the test
    stayed green.

    The filter is `SPEC_OBJECT_OWN_NAMES`, and it is chosen over listing
    `{'length', 'name'}` as known noise for one reason: it is NAME-AGNOSTIC
    about the slot. A per-profile rename — the exact change ruling 3 exists to
    catch — still lands outside the spec vocabulary and is still extracted, so
    scoping the sentinel this way does NOT re-anchor it on the `__pnaRealm`
    literal. It is also the same list ruling 3's detection half already turns
    on: an own name outside the spec vocabulary is precisely what a detector
    reads, so the sentinel and the ruling now measure the same property.
    """
    return _defined_slot_names(js) - SPEC_OBJECT_OWN_NAMES


def _run_node(source: str) -> dict:
    """Run a JS fragment that prints one JSON object on its last line."""
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp, "probe.js")
        path.write_text(source, encoding="utf-8")
        proc = subprocess.run(
            # `encoding="utf-8"` is not decoration: `text=True` alone decodes
            # the probe's stdout under the platform locale, which is cp1252 on
            # Windows against a probe written as utf-8. `tests/` is held at zero
            # such sites by `test_encoding_discipline.py`, and this file was its
            # only violator.
            [NODE, str(path)], capture_output=True, text=True, timeout=60,
            encoding="utf-8",
        )
    assert proc.returncode == 0, (
        f"probe failed ({proc.returncode})\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# RULING 1 — a closure cannot carry the registry, because of constraint (b).
# ---------------------------------------------------------------------------
# This is the test that decides whether PS-400 is closable at all: the ticket
# named "copy PS-368's closure" as the shape to copy, and it cannot work.
#
# The positive control is the whole test. A candidate that fails dedup and a
# shipped design that passes it, on IDENTICAL input, is a located failure; the
# candidate alone would only be an opinion.
_CONSTRAINT_B_PROBE = r"""
// Two INDEPENDENT evaluations of one source text into ONE realm. This is
// `all_frames:true`: the same-origin child runs the content script itself AND
// is installed into by the parent's contentWindow accessor. Neither invocation
// can see the other's closure.
function twoInvocations(src) {
  const G = globalThis;
  const inv1 = eval(src);   // content script
  const inv2 = eval(src);   // parent's contentWindow accessor
  return [inv1(G, "audio"), inv2(G, "audio")];
}

const CANDIDATE = `(function(){
  const SEEN = new WeakMap();                 // fresh per EVALUATION
  return function install(G, key){
    let m = SEEN.get(G);
    if (!m) { m = {}; SEEN.set(G, m); }
    if (m[key] === true) return "SKIPPED";
    m[key] = true;
    return "APPLIED";
  };
})()`;

const SHIPPED = `(function(){
  return function install(G, key){
    var reg = null;
    try {
      var O = G.Object;
      if (O) {
        reg = O.__pnaRealm;
        if (!reg) {
          reg = {};
          O.defineProperty(O, "__pnaRealm", { value: reg, configurable: true });
        }
      }
    } catch (e) { reg = null; }
    try {
      if (reg) { if (reg[key] === true) return "SKIPPED"; reg[key] = true; }
    } catch (e) {}
    return "APPLIED";
  };
})()`;

console.log(JSON.stringify({
  candidate: twoInvocations(CANDIDATE),
  shipped:   twoInvocations(SHIPPED),
}));
"""


@requires_node
def test_a_closure_map_cannot_dedup_two_independent_invocations():
    """Constraint (b), executed. The closure fix the ticket proposed is refuted.

    ⛔ A red here does NOT mean "ship the closure". It means the premise this
    whole ruling rests on has changed, and the ruling needs re-deriving.
    """
    r = _run_node(_CONSTRAINT_B_PROBE)

    assert r["candidate"] == ["APPLIED", "APPLIED"], (
        "the closure-WeakMap candidate deduped two independent invocations — "
        f"got {r['candidate']}. Constraint (b) is the reason a closure is "
        "refused for the realm registry; if it no longer holds, PS-400's "
        "ruling must be re-derived rather than assumed."
    )
    # The positive control: the SAME input, the shipped design, dedups.
    assert r["shipped"] == ["APPLIED", "SKIPPED"], (
        "the shipped Object.__pnaRealm design failed to dedup two independent "
        f"invocations — got {r['shipped']}. This is a real masking regression: "
        "a leaf applying twice in one realm applies its noise twice, so a page "
        "and its same-origin iframe report different fingerprints."
    )


# ---------------------------------------------------------------------------
# RULING 2 — a Symbol moves the name, it does not remove it.
# ---------------------------------------------------------------------------
_SYMBOL_PROBE = r"""
const O = Object;
const SYM = Symbol("pna");
O.defineProperty(O, SYM, { value: {}, configurable: true });
O.defineProperty(O, "__pnaRealm", { value: {}, configurable: true });
console.log(JSON.stringify({
  namesFindString:   O.getOwnPropertyNames(O).includes("__pnaRealm"),
  symbolsFindSymbol: O.getOwnPropertySymbols(O).map(String).some(s => /pna/.test(s)),
  symbolProbeLines:  1,
}));
"""


@requires_node
def test_symbol_keying_relocates_the_tell_rather_than_removing_it():
    """A symbol satisfies a `getOwnPropertyNames` probe while staying readable.

    That is the trap the ticket named and this pins it: the detector's cost goes
    from one line to one line.
    """
    r = _run_node(_SYMBOL_PROBE)
    assert r["namesFindString"] is True
    assert r["symbolsFindSymbol"] is True, (
        "a Symbol-keyed registry was not visible to getOwnPropertySymbols — if "
        "that is genuinely true on some engine, ruling 2 is worth re-deriving; "
        "it was not true when PS-400 ruled on it."
    )


# ---------------------------------------------------------------------------
# RULING 3 — the per-profile rename. Both halves, and both fail.
# ---------------------------------------------------------------------------
# HALF ONE: detection is not reduced, because Object's own names are SPECIFIED.
_SPEC_DIFF_PROBE = r"""
const SPEC = new Set(__SPEC__);
// The generic detector: one line, no baseline, no knowledge of persona.
const caught = (name) => !SPEC.has(name);
console.log(JSON.stringify({
  shipped:  caught("__pnaRealm"),
  renamedA: caught("_8gai3oie"),
  renamedB: caught("_8594j03i"),
  // and the stock realm itself must be clean against the same list, or the
  // list is wrong and this whole ruling is measuring nothing.
  stockExtras: Object.getOwnPropertyNames(Object).filter(caught),
}));
"""


@requires_node
def test_a_randomised_slot_name_is_caught_by_the_same_one_line_check():
    """HALF ONE of ruling 3: the rename does not raise the detector's cost.

    The rename was proposed on the premise that a detector would need a
    per-engine-version baseline of `gOPN(Object)`. `Object`'s own names are
    ECMA-262 specified, so the baseline is the SPEC — static and public. A
    random name is caught by the identical check.
    """
    r = _run_node(_SPEC_DIFF_PROBE.replace(
        "__SPEC__", json.dumps(sorted(SPEC_OBJECT_OWN_NAMES))
    ))

    # The control: a stock realm has NO name outside the spec vocabulary. If
    # this ever goes red the allow-list has drifted from the engine, and the two
    # assertions below would be measuring the list rather than the design.
    assert r["stockExtras"] == [], (
        "a stock node realm carries own Object names outside this file's spec "
        f"allow-list: {r['stockExtras']}. Add them to SPEC_OBJECT_OWN_NAMES — "
        "until then the ruling-3 assertions below prove nothing."
    )
    assert r["shipped"] is True, "the shipped name is not caught — control broken"
    assert r["renamedA"] is True and r["renamedB"] is True, (
        "a randomised per-profile slot name escaped the generic spec-diff "
        "check. That would make HALF ONE of PS-400's ruling 3 false and the "
        "rename worth re-costing against its supercookie half."
    )


def test_the_slot_name_is_a_shared_constant_across_profiles():
    """HALF TWO of ruling 3, and the guard against the 'improvement'.

    ⭐ THIS IS THE LOAD-BEARING TEST OF THIS FILE. The shared literal is what
    makes every persona profile read IDENTICALLY on this vector — zero bits, and
    therefore mutually unlinkable. A per-profile name would make it a stable,
    unique, one-line-readable cross-site identifier: a supercookie surviving
    cookie and storage clearing.

    Reading emitted source is correct HERE (and only here) because the thing
    under test genuinely IS a literal baked into generated text.

    ⛔ If a future slice makes this red by seeding the name per profile, the
    right move is to revert it, not to update this test.
    """
    emitted = {}
    for seed in SEEDS:
        with tempfile.TemporaryDirectory() as tmp:
            build_device_extension(
                seed, str(pathlib.Path(tmp, "dev")), 3, os_type="windows"
            )
            js = pathlib.Path(tmp, "dev", "device.js").read_text(encoding="utf-8")
        # Every property name the built artifact installs on `Object` that a
        # detector would flag — i.e. excluding the spec-vocabulary names the
        # toString cloak also writes (`length`, `name`) onto wrapped functions.
        # ⚠️ Matched by SHAPE and filtered by SPEC VOCABULARY, not by the
        # `__pna` prefix: a prefix-anchored regex would report "nothing found"
        # for precisely the per-profile rename this test exists to catch.
        emitted[seed] = tuple(sorted(_installed_slot_names(js)))

    # ⭐ THE SENTINEL, AND IT HAS BEEN SHOWN TO BITE. Deleting the slot's
    # `defineProperty` from a built device.js takes this red. It did NOT before
    # PS-400's rework: the unfiltered set still held `{'length', 'name'}` from
    # the toString cloak, so "non-empty" was satisfied with no realm slot
    # emitted at all and the guard passed on the one case its own message
    # describes. Assert the COUNT, not mere non-emptiness: the ruling is that
    # ONE name is installed outside the spec vocabulary, and a second one
    # appearing is as much a change to what was ruled on as zero is.
    assert len(emitted[SEEDS[0]]) == 1, (
        "the built device.js installs "
        f"{len(emitted[SEEDS[0]])} non-spec named properties on Object "
        f"({list(emitted[SEEDS[0]])}), expected exactly 1 (the realm slot). "
        "Zero means the emitter changed shape or vanished and this test is no "
        "longer reading the slot name at all; more than one means the surface "
        "this ruling measured has grown and ruling 4's cost needs re-deriving."
    )
    distinct = set(emitted.values())
    assert len(distinct) == 1, (
        "the realm slot name DIFFERS between profile seeds: "
        + "; ".join(f"{s:#x} -> {emitted[s]}" for s in SEEDS)
        + ". A per-profile name is a stable, unique, one-line-readable "
        "cross-site identifier — ~41 bits against the ~33 needed to index "
        "every human alive — and it does NOT reduce detection (see "
        "test_a_randomised_slot_name_is_caught_by_the_same_one_line_check). "
        "The shared constant is load-bearing: revert the seeding."
    )


def test_both_emitters_agree_on_one_slot_name():
    """(a)/(b) both die if the guard and the value slot disagree on the name.

    They are two functions emitting the same literal into different leaves. A
    partial rename — the likeliest way a future slice breaks this — would leave
    one leaf writing `Object.<A>` and another reading `Object.<B>`: dedup stops
    firing across the pair AND the iframe->top geometry crossing silently
    returns null, which reads identical to a clean realm.
    """
    guard_names = _defined_slot_names(realm_guard_js("audio"))
    slot_names = _defined_slot_names(realm_slot_js())

    assert guard_names, "realm_guard_js installs no named property on Object"
    assert slot_names, "realm_slot_js installs no named property on Object"
    assert guard_names == slot_names, (
        f"realm_guard_js emits {sorted(guard_names)} but realm_slot_js emits "
        f"{sorted(slot_names)}. Both must name ONE slot: the guard writes it "
        "and the value channels read it, including across the iframe->top hop. "
        "A mismatch fails SILENTLY — the crossing returns null and each realm "
        "re-derives its own geometry, which is divergence between realms."
    )


# ---------------------------------------------------------------------------
# RULING 4 — hiding the name costs more than the name.
# ---------------------------------------------------------------------------
_ENTRY_POINT_PROBE = r"""
const O = Object;
O.defineProperty(O, "__pnaRealm", { value: { audio: true }, configurable: true });
const probes = {
  getOwnPropertyNames:       () => O.getOwnPropertyNames(O).includes("__pnaRealm"),
  reflectOwnKeys:            () => Reflect.ownKeys(O).includes("__pnaRealm"),
  getOwnPropertyDescriptors: () => "__pnaRealm" in O.getOwnPropertyDescriptors(O),
  getOwnPropertyDescriptor:  () => !!O.getOwnPropertyDescriptor(O, "__pnaRealm"),
  inOperator:                () => "__pnaRealm" in O,
  objectHasOwn:              () => O.hasOwn(O, "__pnaRealm"),
  hasOwnProperty:            () => O.prototype.hasOwnProperty.call(O, "__pnaRealm"),
  directRead:                () => O.__pnaRealm !== undefined,
};
const exposed = Object.keys(probes).filter(k => { try { return probes[k]() === true; } catch (e) { return false; } });
console.log(JSON.stringify({ exposed }));
"""


@requires_node
def test_suppressing_the_name_would_mean_wrapping_many_core_intrinsics():
    """Ruling 4: the count is the argument.

    Every entry point below would have to be wrapped for the name to be hidden,
    and each wrapper is itself a patched core intrinsic on a path ordinary page
    code calls — a larger tell than the one name, bought at a behavioural cost.
    """
    r = _run_node(_ENTRY_POINT_PROBE)
    exposed = r["exposed"]

    assert len(exposed) >= 6, (
        f"only {len(exposed)} entry points expose the own name ({exposed}). "
        "Ruling 4 rests on the breadth of this surface; if it has genuinely "
        "narrowed, the cost of suppression is worth re-costing."
    )
    # The two that no `ownKeys`-style interception can reach at all: a direct
    # read and `hasOwnProperty` via the prototype both bypass enumeration.
    assert "directRead" in exposed, (
        "a direct property read no longer sees the slot — that would be a real "
        "change to the exposure this ruling measured"
    )
