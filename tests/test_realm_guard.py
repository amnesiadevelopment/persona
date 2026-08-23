"""PS-93 — the per-realm idempotency guard, after the `__persona*` globals went.

WHAT THIS FILE OWNS. Eleven modules used to record "I already ran in this realm"
as a plain `G.__personaX = true`, which put eleven enumerable names on the global
object where `Object.getOwnPropertyNames(self)` — the sweep a fingerprinter runs,
and the one `realm.bootMarkers` encodes — reads them. They are gone, replaced by
`worker_wrap.realm_guard_js`. This suite pins BOTH halves of that trade: the
names are absent AND the idempotency they provided still holds.

The second half is the one worth the runtime. Deleting the markers without a
replacement does not produce a red assertion — it produces a MASKING REGRESSION,
because a leaf that applies twice in one realm applies its noise twice. Measured
on the real audio leaf: 0.99998999 with the guard, 0.99997997 without, so the top
realm and a same-origin iframe would report DIFFERENT audio fingerprints. That is
a self-inflicted unlinkability tell created by a leak fix, and a count of
applications would not have caught what the fingerprint does. So the idempotency
assertions below read the DIGEST, not a counter.

THE THREE CONSTRAINTS the mechanism has to satisfy at once, each with a test:

  (a) per-realm    — one realm's state must not answer for another's, or a child
                     frame silently never gets the leaf;
  (b) shared across INDEPENDENT `__pnaInstall` invocations into the SAME realm —
                     `all_frames:true` means a same-origin child runs the content
                     script ITSELF *and* is installed into by the parent's
                     `contentWindow` accessor, and Firefox re-evaluates whole
                     leaves into open tabs (invisible_launch.py
                     `_apply_audio_to_open_tabs`). This is the constraint a
                     closure `WeakSet` cannot meet — `var SEEN` is declared
                     inside `__pnaInstall`'s body, so it is per-INVOCATION;
  (c) carried into a worker AS TEXT — `fragment()` serialises only
                     `LEAF.toString()` + `__pnaInstall.toString()`, so anything
                     from an enclosing scope is `undefined` in a worker and the
                     leaf would silently never apply there.

Realm machinery is the shared harness in tests/realm_harness.py (PS-68), not a
second one: two harnesses drift, and a drifting realm harness fails by
UNDER-REPORTING coverage, which is the failure mode this subsystem cannot see.
"""

import json
import pathlib
import re
import shutil
import subprocess

import pytest

from src.services.browser.audio_ext import _CHROMIUM_NATIVE_WRAP, _audio_patch_js
from src.services.browser.worker_wrap import realm_guard_js
from tests.realm_harness import HARNESS

SEED = 0x1234ABCD

# The names this ticket removed. `__personaScreenWH` and `__personaMtFactor` are
# deliberately NOT here: they are cross-realm VALUE channels (a DOM-less worker
# reading a constant the top realm measured), not idempotency guards, and no
# closure mechanism can carry a value across a realm boundary. They are a real,
# separate leak — named here so removing this list does not lose them.
REMOVED_MARKERS = [
    "__personaAudio", "__personaCanvasCtx", "__personaScreen", "__personaHw",
    "__personaGeo", "__personaGpu", "__personaMt", "__personaMobile",
    "__personaStealth", "__personaVoice", "__personaWebgl", "__personaLocale",
]

# Still in the tree, and out of scope for this ticket.
RETAINED_VALUE_CHANNELS = ["__personaScreenWH", "__personaMtFactor"]

# Anchored to THIS file, never to the cwd. Several suites in this repo chdir
# (test_main_cwd, test_verify_snapshot, test_app_update, …), so a relative path
# here resolves differently depending on what ran before: these tests passed
# standalone and failed in the full suite, reporting "the guards are gone" when
# the truth was "the directory was not there". A source sweep that silently
# matches nothing is the worst shape for an ABSENCE assertion — it goes green
# for the wrong reason — which is why the sweep below also asserts it actually
# read some files.
_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "services" / "browser"


def _requires_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    return node


# ---------------------------------------------------------------------------
# Source-level: the names are gone from the leaves that carried them.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("marker", REMOVED_MARKERS)
def test_no_leaf_assigns_the_removed_marker_to_a_realm(marker):
    # The assignment is what put the name on the global. Matching the assignment
    # rather than the bare name keeps this honest about `__personaScreenWH`,
    # whose PREFIX is `__personaScreen` — a bare substring search would report
    # the retained value channel as a regression of the removed guard.
    offenders = []
    scanned = 0
    for path in sorted(_SRC.glob("*.py")):
        scanned += 1
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(rf"\.{marker}\s*=(?!=)", line):
                offenders.append(f"{path.name}:{n}: {line.strip()}")
    # An absence assertion that swept nothing is green for the wrong reason.
    assert scanned > 5, f"source sweep read only {scanned} file(s) from {_SRC}"
    assert not offenders, f"{marker} is assigned to a realm again:\n" + "\n".join(offenders)


@pytest.mark.parametrize("name", RETAINED_VALUE_CHANNELS)
def test_the_out_of_scope_value_channels_are_left_alone(name):
    # The counterpart to the test above, and the reason this ticket's marker
    # assertion is a SUBSET check rather than an emptiness one. These are not
    # idempotency guards, so this change neither removes nor should remove them.
    # If a later ticket closes them, this test is the thing that tells you the
    # subset assertion below needs re-deriving rather than silently loosening.
    paths = sorted(_SRC.glob("*.py"))
    assert len(paths) > 5, f"source sweep read only {len(paths)} file(s) from {_SRC}"
    found = any(
        re.search(rf"\.{name}\b", path.read_text(encoding="utf-8"))
        for path in paths
    )
    assert found, (
        f"{name} is gone — it is a cross-realm VALUE channel that was OUT OF "
        f"SCOPE for PS-93. If that was deliberate, re-derive the marker subset "
        f"assertions in this file; do not just delete this test."
    )


# ---------------------------------------------------------------------------
# Behavioural: the real audio leaf, in real node:vm realms.
# ---------------------------------------------------------------------------

_PROBE = HARNESS + r"""
const SRC = fs.readFileSync(process.argv[2], "utf8");

// The audio surface the real leaf patches. getChannelData returns a FRESH array
// per call, so a leaf applied twice COMPOUNDS its noise — which is precisely
// the observable that makes a missing guard a fingerprint move rather than a
// failed assertion.
function audioRealm() {
  const r = makeRealm();
  vm.runInContext(`
    globalThis.AudioBuffer = function AudioBuffer(){};
    globalThis.AudioBuffer.prototype.getChannelData = function(){ return [1.0]; };
    globalThis.AnalyserNode = function AnalyserNode(){};
    globalThis.AnalyserNode.prototype.getFloatFrequencyData = function(a){ return a; };
    globalThis.AnalyserNode.prototype.getByteFrequencyData = function(a){ return a; };
  `, r.ctx);
  return r;
}
const digest = (r) => vm.runInContext("new AudioBuffer().getChannelData()[0]", r.ctx);
const markers = (r) => vm.runInContext(
  "Object.getOwnPropertyNames(globalThis).filter(k=>/^__pna|^__persona/.test(k))", r.ctx);
// An INDEPENDENT invocation: a separate evaluation of the whole content script,
// sharing no closure and holding a DIFFERENT leaf function object — exactly
// what all_frames:true plus the parent's contentWindow accessor produce.
const invoke = (r) => vm.runInContext(SRC, r.ctx);

const out = {};

// --- the top realm, applied ONCE: the reference fingerprint -----------------
const top = audioRealm();
invoke(top);
out.topDigest = digest(top);
out.unpatchedDigest = 1.0;

// --- (b) a SECOND independent invocation into that SAME realm ---------------
invoke(top);
out.topDigestAfterSecondInvoke = digest(top);

// --- (a) a same-origin child realm, applied once ---------------------------
const child = audioRealm();
invoke(child);
out.childDigest = digest(child);

// --- (c) a worker realm, reached as TEXT through the real bootstrap --------
const workerPayload = spawn(child);
const worker = audioRealm();
vm.runInContext(workerPayload, worker.ctx);
out.workerDigest = digest(worker);
vm.runInContext(workerPayload, worker.ctx);   // and again: idempotent there too
out.workerDigestAfterSecondInvoke = digest(worker);

// --- a nested (grandchild) worker: depth must still work -------------------
const nestedPayload = spawn(worker);
const nested = audioRealm();
vm.runInContext(nestedPayload, nested.ctx);
out.nestedDigest = digest(nested);

out.topMarkers = markers(top);
out.workerMarkers = markers(worker);
out.nestedMarkers = markers(nested);
console.log(JSON.stringify(out));
"""


def _run(tmp_path, content_script):
    node = _requires_node()
    d = pathlib.Path(tmp_path)
    src = d / "audio.js"
    src.write_text(content_script, encoding="utf-8")
    probe = d / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    out = subprocess.run(
        [node, str(probe), str(src)], capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def shipped(tmp_path_factory):
    """The real generated Chromium audio content script, exactly as shipped."""
    d = tmp_path_factory.mktemp("realm_guard")
    return _run(d, _audio_patch_js(SEED, _CHROMIUM_NATIVE_WRAP))


def test_the_leaf_applies_at_all(shipped):
    # The control. Every idempotency assertion below is vacuous if the leaf
    # never ran — "applied once" and "did not apply" are both "not twice".
    assert shipped["topDigest"] != shipped["unpatchedDigest"], (
        "the audio leaf did not perturb the top realm at all"
    )


def test_a_second_independent_invocation_does_not_reperturb_the_realm(shipped):
    # CONSTRAINT (b), and the one the retracted mechanism could not meet. Two
    # separate evaluations of the content script into ONE realm: no shared
    # closure, different leaf function objects. The digest must not move.
    assert shipped["topDigestAfterSecondInvoke"] == shipped["topDigest"], (
        "the leaf applied twice in one realm: the noise COMPOUNDED "
        f"({shipped['topDigest']} -> {shipped['topDigestAfterSecondInvoke']}), so this "
        "realm now reports a different audio fingerprint than a realm patched once"
    )


def test_top_realm_and_same_origin_child_report_the_identical_fingerprint(shipped):
    # The observable this ticket names, and a stronger statement than either
    # realm being individually idempotent: a page and its same-origin iframe
    # must be the SAME machine. This is what a checker actually reads.
    assert shipped["childDigest"] == shipped["topDigest"], (
        "top realm and same-origin child report DIFFERENT audio fingerprints "
        f"({shipped['topDigest']} vs {shipped['childDigest']}) — a self-inflicted "
        "unlinkability tell"
    )


def test_the_leaf_still_arrives_in_a_worker_realm(shipped):
    # CONSTRAINT (c). A guard that lived in an enclosing scope would be
    # `undefined` here and the leaf would silently never apply — worse than the
    # leak this ticket removes, and invisible without this assertion.
    assert shipped["workerDigest"] == shipped["topDigest"], (
        "the worker realm did not receive the leaf, or perturbed differently "
        f"({shipped['workerDigest']} vs top {shipped['topDigest']})"
    )


def test_the_worker_realm_is_idempotent_too(shipped):
    assert shipped["workerDigestAfterSecondInvoke"] == shipped["workerDigest"], (
        "the leaf applied twice in the worker realm"
    )


def test_the_leaf_still_arrives_in_a_nested_worker_realm(shipped):
    # (a) at depth: the grandchild is a genuinely separate realm and must get
    # its own application — a guard that was per-PROCESS rather than per-realm
    # would leave this one unmasked.
    assert shipped["nestedDigest"] == shipped["topDigest"], (
        "the nested (depth-2) worker realm did not receive the leaf "
        f"({shipped['nestedDigest']} vs top {shipped['topDigest']})"
    )


@pytest.mark.parametrize("realm", ["topMarkers", "workerMarkers", "nestedMarkers"])
def test_no_removed_marker_is_reachable_by_enumerating_the_global(shipped, realm):
    # The leak itself, asserted the way the shipped probe asserts it
    # (getOwnPropertyNames, so a non-enumerable property would NOT satisfy this
    # by hiding — probes.py walks names, not keys).
    leaked = [m for m in shipped[realm] if m in REMOVED_MARKERS]
    assert not leaked, f"{realm}: removed marker(s) back on the global: {leaked}"


@pytest.mark.parametrize("realm", ["topMarkers", "workerMarkers", "nestedMarkers"])
def test_the_marker_set_is_a_strict_subset_and_gains_no_new_name(shipped, realm):
    # A SUBSET check, deliberately not `markers == []`. This ticket does not
    # close the charter item — `__pnaName` is per-function and out of scope, and
    # the two value channels above remain — so asserting emptiness would ship a
    # knowingly-red gate. What must hold is that the 12 are absent and nothing
    # NEW appeared in their place.
    allowed = set(RETAINED_VALUE_CHANNELS) | {"__pnaName", "__pnaRealm"}
    unexpected = [m for m in shipped[realm] if m not in allowed]
    assert not unexpected, (
        f"{realm}: unexpected persona-family global(s) {unexpected}; the guard "
        f"must not trade twelve names for a new one"
    )


# ---------------------------------------------------------------------------
# FALSIFICATION (non-waivable). Both of these MUST go red against the
# implementations they forbid, or the suite above is not testing what this
# ticket is about.
# ---------------------------------------------------------------------------

def test_falsification_a_restored_marker_is_caught(tmp_path):
    # Put ONE leaf's `G.__personaX = true` back, with everything else in place,
    # and the global-name assertion must catch it BY NAME.
    shipped_src = _audio_patch_js(SEED, _CHROMIUM_NATIVE_WRAP)
    restored = shipped_src.replace(
        "    if (!G) return;\n",
        "    if (!G || G.__personaAudio) return;\n    G.__personaAudio = true;\n",
        1,
    )
    assert restored != shipped_src, "could not build the falsification control"

    got = _run(tmp_path, restored)
    leaked = [m for m in got["topMarkers"] if m in REMOVED_MARKERS]
    assert leaked == ["__personaAudio"], (
        "the marker assertion does not catch a restored guard — it would not "
        f"have caught the original leak either (saw {got['topMarkers']})"
    )


def test_falsification_a_guard_that_fails_constraint_b_is_caught(tmp_path):
    # Mutate the mechanism so it satisfies (a) and (c) but NOT (b): the registry
    # becomes a plain local object, so it is per-INVOCATION — which is exactly
    # the defect that made the retracted closure-WeakSet fix unsound. The
    # idempotency assertion must go red, on the OBSERVABLE.
    shipped_src = _audio_patch_js(SEED, _CHROMIUM_NATIVE_WRAP)
    guard = realm_guard_js("audio")
    assert guard in shipped_src, (
        "the guard is missing from the GENERATED audio script — it is spliced "
        "from realm_guard_js at build time, so either the placeholder was "
        "dropped from _audio_patch_js's .replace() chain or the leaf lost its "
        "placeholder. Fix the SPLICE; do not re-derive this mutation to match "
        "the drift (that would be adjusting the test to fit the defect it "
        "exists to catch). The structural suite at the bottom of this file "
        "asserts the same thing across all twelve sites."
    )
    # Same test-and-set, same key, same fail-open shape — but the registry is a
    # fresh local object per call instead of being resolved from the realm. That
    # is the ONE property under test: per-invocation instead of per-realm.
    per_invocation = (
        '    var __pnaReg = {};\n'
        '    try {\n'
        '      if (__pnaReg["audio"] === true) return;\n'
        '      __pnaReg["audio"] = true;\n'
        '    } catch (e) {}'
    )
    mutated = shipped_src.replace(guard, per_invocation, 1)
    assert mutated != shipped_src, "could not build the (b) falsification control"

    got = _run(tmp_path, mutated)
    assert got["topDigestAfterSecondInvoke"] != got["topDigest"], (
        "a per-INVOCATION guard was NOT caught: the idempotency assertion stays "
        "green under a mechanism that fails constraint (b), so it is not testing "
        "what this ticket is about"
    )
    assert got["childDigest"] != got["topDigestAfterSecondInvoke"], (
        "the top-vs-child fingerprint assertion stays green under a (b) failure"
    )


# ---------------------------------------------------------------------------
# Structural: the guard the helper emits is in EVERY generated script.
#
# WHY THIS EXISTS, and what it caught. The first round of this ticket pasted the
# 21-line guard into all twelve sites and never called `realm_guard_js` from any
# of them — the helper was dead code in production, and the twelve copies were
# coupled to it by nothing. Two failures compounded there, and this test closes
# the second:
#
#   * DRIFT — editing the helper (the single source of truth) changed nothing
#     that ships; editing one copy diverged it from eleven others, silently.
#   * COVERAGE — the behavioural suite above drives `_audio_patch_js` only, so
#     eleven of the twelve sites had their idempotency asserted by NOTHING.
#     Deleting `geo_ext`'s guard outright — a real masking regression, since
#     `applyGeoPatch` would then re-wrap `navigator.geolocation` on every
#     independent invocation into a realm — left the whole suite GREEN (159
#     passed). The source-level tests at the top of this file could not catch it
#     either: they assert the old marker names are ABSENT, and a deleted guard
#     keeps them absent, so they cannot tell "replaced correctly" from "removed
#     entirely".
#
# So this asserts the shipped artifact CONTAINS the helper's own output, keyed
# per module. It is deliberately cheap — twelve builder calls, no node — because
# the alternative that scales to the twelfth site is twelve node runs. The
# behavioural suite above proves the MECHANISM works on the real audio leaf;
# this proves every leaf actually carries it.
# ---------------------------------------------------------------------------

def _generated_scripts(tmp_path):
    """Every Chromium content script this subsystem ships, keyed by module.

    Driven through the REAL builders rather than by reading the templates: the
    guard is spliced at build time, so a template read would test the
    placeholder and not the thing that ships.
    """
    from src.services.browser import (
        audio_ext, canvas_ctx_ext, device_ext, geo_ext, gpu_ext, locale_ext,
        measuretext_ext, mobile_ext, stealth_ext, voice_ext, webgl_ext,
    )
    from src.services.browser.engine_version import ChromiumVersion

    d = pathlib.Path(tmp_path)

    def js(build_dir):
        found = sorted(pathlib.Path(build_dir).glob("*.js"))
        assert len(found) == 1, f"expected one .js in {build_dir}, got {found}"
        return found[0].read_text(encoding="utf-8")

    return {
        "audio": js(audio_ext.build_audio_extension(SEED, str(d / "audio"))),
        # iOS is the arm whose leaf does the work; the guard must be in the
        # script regardless, since the same file ships on every platform.
        "canvas_ctx": js(canvas_ctx_ext.build_canvas_ctx_extension("ios", str(d / "canvas"))),
        "device": js(device_ext.build_device_extension(SEED, str(d / "device"), 3, None, "windows")),
        "geo": js(geo_ext.build_geo_extension(52.52, 13.40, str(d / "geo"))),
        "gpu": js(gpu_ext.build_gpu_extension(SEED, "windows", str(d / "gpu"), 3)),
        "locale": js(locale_ext.build_locale_extension("de-DE", str(d / "locale"))),
        "measuretext": js(measuretext_ext.build_measuretext_extension(str(d / "mt"))),
        "mobile": js(mobile_ext.build_mobile_extension(
            str(d / "mobile"), is_ios=False, platform="Linux armv81", model="Pixel 7",
            chromium_version=ChromiumVersion("140.0.7339.207"), css_width=412,
            css_height=915, dpr=2.625, device_memory=8, hardware_concurrency=8,
        )),
        "stealth": js(stealth_ext.build_stealth_extension(str(d / "stealth"))),
        "voice": js(voice_ext.build_voice_extension("en-US", str(d / "voice"), "windows")),
        "webgl": js(webgl_ext.build_webgl_extension(SEED, str(d / "webgl"))),
    }


# (module file key, guard key, leaf indentation). Twelve guards across eleven
# scripts: device.js carries TWO leaves (screen geometry and hardware
# concurrency) and each needs its own slot, or one leaf's arrival would mark the
# realm covered for the other. `voice_ext` indents its leaf body by 2 and every
# other leaf by 4 — the helper takes that as a parameter, so a wrong indent here
# is a real mismatch and not cosmetic.
GUARD_SITES = [
    ("audio", "audio", 4),
    ("canvas_ctx", "canvas_ctx", 4),
    ("device", "screen", 4),
    ("device", "hw", 4),
    ("geo", "geo", 4),
    ("gpu", "gpu", 4),
    ("locale", "locale", 4),
    ("measuretext", "measuretext", 4),
    ("mobile", "mobile", 4),
    ("stealth", "stealth", 4),
    ("voice", "voice", 2),
    ("webgl", "webgl", 4),
]


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    return _generated_scripts(tmp_path_factory.mktemp("generated"))


@pytest.mark.parametrize("script_key,guard_key,indent", GUARD_SITES)
def test_every_generated_script_carries_the_spliced_guard(
    generated, script_key, guard_key, indent
):
    script = generated[script_key]
    expected = realm_guard_js(guard_key, indent)
    assert expected in script, (
        f"{script_key}.js does not contain the guard for key '{guard_key}'.\n"
        f"The guard must be SPLICED from realm_guard_js at build time — if this "
        f"leaf was hand-edited, or its placeholder was dropped from the "
        f"builder's .replace() chain, it now has no per-realm idempotency and "
        f"will re-apply its spoof on every independent invocation into a realm."
    )


@pytest.mark.parametrize("script_key,guard_key,indent", GUARD_SITES)
def test_each_guard_appears_exactly_once_per_leaf(
    generated, script_key, guard_key, indent
):
    # A guard spliced twice into one leaf is not harmless: the second copy sits
    # AFTER the first has already recorded the realm, so it returns early every
    # time and everything below it becomes unreachable.
    assert generated[script_key].count(realm_guard_js(guard_key, indent)) == 1


def test_no_generated_script_carries_an_unfilled_guard_placeholder(generated):
    # The failure mode splicing introduces that hand-copying could not: a
    # placeholder left in the template with no matching `.replace()` ships the
    # literal token as JS. `__REALM_GUARD__` on its own line is a ReferenceError
    # at document_start, which takes the WHOLE leaf down — an unmasked realm, in
    # exchange for a typo.
    offenders = {
        name: [n for n, line in enumerate(script.splitlines(), 1)
               if "REALM_GUARD__" in line]
        for name, script in generated.items()
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, (
        f"unfilled guard placeholder(s) shipped as JS: {offenders}"
    )


def test_the_helper_is_the_single_source_of_truth_for_every_site(generated):
    # The drift check, stated as one assertion rather than twelve: every
    # generated script must carry the helper's CURRENT output. That catches any
    # site that is not spliced — one whose placeholder was dropped, one whose
    # builder was never wired to `realm_guard_js`, or one hand-copied and since
    # DIVERGED because the helper moved and the copy did not.
    #
    # What it does NOT do, stated because the obvious guess is wrong and was
    # believed here for a round: editing `realm_guard_js` alone does NOT turn
    # these cases red. Both sides of the comparison are recomputed from that
    # same helper at test time — the expected text here, and the script built
    # by the real builders in `generated` — so a helper edit moves them
    # together and is invariant BY CONSTRUCTION. Measured: rename the registry
    # property and the file is still 54 passed.
    #
    # That invariance is the PROPERTY, not a gap. A pure splice SHOULD be
    # insensitive to helper edits; insensitivity is what "spliced" means. The
    # redness appears the moment a site stops tracking the helper:
    #
    #     all 12 spliced, helper mutated          -> green (54 passed)
    #     one site hand-copied, helper unchanged   -> green (the copy still
    #                                                 matches; see below)
    #     one site hand-copied, THEN helper edited -> RED (3 failed)
    #
    # So the guarantee is DIRECTIONAL, and the middle row is its honest bound:
    # a hand-copy is byte-identical to the splice on the day it is written, so
    # this suite cannot tell a fresh paste from a real splice. It catches the
    # paste on the next helper edit, and it catches a MISSING or unwired guard
    # immediately. If you want the stronger claim, assert on the call graph —
    # not on the emitted text.
    total = sum(
        generated[script_key].count(realm_guard_js(guard_key, indent))
        for script_key, guard_key, indent in GUARD_SITES
    )
    assert total == len(GUARD_SITES), (
        f"expected {len(GUARD_SITES)} spliced guards across the generated "
        f"scripts, found {total}"
    )
