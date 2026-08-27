"""PS-182: does the FIREFOX WebGL readback carry per-profile entropy — really?

WHAT THIS FILE IS FOR, AND WHY IT IS NOT test_ff_webgl_seed.py's job
--------------------------------------------------------------------
``tests/test_ff_webgl_seed.py`` (PS-78) proves the Firefox script is BUILT and
DELIVERED. That is a necessary claim and it is not this one. PS-182 was filed
because two Firefox profiles differing only by seed handed CreepJS one
byte-identical ``webgl_pixel_hash`` (``51df3565``) — with the delivery already
in place — so the open question was never "is it wired up" but **"does a
readback actually come out DIFFERENT per profile"**.

Those are different questions and only the second one is a linkability claim.
A test that asserts the perturbation function was CALLED would pass on a
perturbation that moves zero bytes, which is the exact defect under
investigation. PS-11 is the standing article on this and the ticket names it:
*"A unit test showing a perturbation function was called is necessary and not
sufficient here."*

So every seat below **executes the shipped script** — the real
``firefox_webgl_init_script(seed)`` text that ``invisible_launch.py:3345``
installs — in a fresh realm, calls ``readPixels`` through the prototype it
patched, and asserts on **the bytes that came back**. Nothing here greps source.

THE INSTRUMENT DEFECT THESE SEATS ARE SHAPED AROUND — read before editing
-------------------------------------------------------------------------
The first version of the PS-182 harness ran every seed in ONE node process,
sharing one ``Object``. The shipped per-realm idempotency guard
(``worker_wrap.realm_guard_js``) stores its flag at ``Object.__pnaRealm.webgl``,
so seed 1337 patched normally and **every later seed returned early having
patched nothing**, reporting the unperturbed digest. That is a clean, plausible,
entirely FAKE collision produced by the instrument rather than the product.

Hence ``vm.createContext`` per seed below: one fresh realm per profile, which is
what a real page load is. ``test_realm_guard_is_what_makes_isolation_necessary``
pins that hazard directly, so if a future edit collapses the realms the suite
says WHY it broke instead of just going red.

WHY A COLLIDING GEOMETRY IS ALSO PINNED
---------------------------------------
``test_collision_reproduces_only_with_no_guard_eligible_bytes`` asserts a
NEGATIVE — four seeds landing on one digest — and a negative is also what a
broken harness returns. It is only evidence because the POSITIVE seats run in
the same file against the same machinery: a buffer with content varies per seed,
a buffer with none does not. Without that pairing it would be a check that
cannot fail.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

from src.services.browser.webgl_ext import firefox_webgl_init_script

# The seeds the PS-182 reading was taken at. 111/1337/4242 are the three the
# real firefox-20 engine recorded in readings/ps135-2026-08-24/, which is what
# makes the corpus-validation seat below possible.
SEEDS = (111, 1337, 4242, 9001)

# Digests recorded by a REAL packaged firefox-20 engine under xvfb in
# readings/ps135-2026-08-24/reading.firefox.seed*.json, from the `webgl.readback`
# probe. Reproducing these bit-identically is what proves the harness executes
# the product's arithmetic rather than its own.
CORPUS_PS135_FIREFOX = {111: 2372980207, 1337: 1471895271, 4242: 1444116715}

# The layer-off value, from
# readings/ps135-2026-08-24/counterfactual.chromium.no-fingerprint-flag.*.json.
# A seed whose digest equals this moved NOTHING.
UNPERTURBED_PROBE_DIGEST = 2952899525


def _node() -> str:
    node = shutil.which("node")
    if not node:  # pragma: no cover - environment-dependent
        pytest.skip("node is required to execute the shipped WebGL script")
    return node


# The harness. `which` selects the realm shape: 1 = WebGL1 only, 2 = WebGL2 only
# (the Firefox WORKER shape — probes.py measured that in a FF worker only
# 'webgl2' yields a context), 3 = both.
#
# The fake GL context is built INSIDE the sandbox realm deliberately: the shipped
# `perturbBytes` gates on `buf instanceof Uint8Array`, and a typed array from
# another realm fails that check. Constructing it outside would make a working
# patch look inert — yet another way to manufacture a false collision.
_HARNESS = r"""
const vm = require("node:vm");
// Payload arrives on STDIN, not argv: it carries four ~19KB copies of the
// shipped script plus the pixel buffers, which is well past a comfortable
// argument size, and `node -e` shifts argv so argv[2] is not the first `--` arg
// anyway.
const IN = JSON.parse(require("fs").readFileSync(0, "utf8"));

function fnv1a(buf) {                 // the reduction probes.py webgl.readback uses
  let h = 2166136261;
  for (let i = 0; i < buf.length; i++) h = Math.imul(h ^ buf[i], 16777619);
  return h >>> 0;
}

function run(src, base, which, sharedContext) {
  const sb = sharedContext || {};
  if (!sharedContext) vm.createContext(sb);

  vm.runInContext(`
    globalThis.__base = null;
    function C1() {}
    C1.prototype.readPixels = function (x,y,w,h,f,t,px) { px.set(globalThis.__base); };
    function C2() {}
    C2.prototype.readPixels = function (x,y,w,h,f,t,px) { px.set(globalThis.__base); };
    if (${which === 1 || which === 3}) globalThis.WebGLRenderingContext = C1;
    if (${which === 2 || which === 3}) globalThis.WebGL2RenderingContext = C2;
    globalThis.self = globalThis; globalThis.window = globalThis;
  `, sb);

  vm.runInContext("globalThis.__base = new Uint8Array(" + JSON.stringify(base) + ");", sb);
  vm.runInContext(src, sb);

  const out = vm.runInContext(`
    (function () {
      var r = {};
      var G = globalThis;
      if (G.WebGLRenderingContext) {
        var d1 = new Uint8Array(G.__base.length);
        new G.WebGLRenderingContext().readPixels(0,0,0,0,0,0,d1);
        r.gl1 = Array.from(d1);
      }
      if (G.WebGL2RenderingContext) {
        var d2 = new Uint8Array(G.__base.length);
        new G.WebGL2RenderingContext().readPixels(0,0,0,0,0,0,d2);
        r.gl2 = Array.from(d2);
      }
      return r;
    })();
  `, sb);

  const res = {};
  for (const k of ["gl1", "gl2"]) {
    if (out[k]) {
      const got = Uint8Array.from(out[k]);
      let moved = 0;
      for (let i = 0; i < base.length; i++) if (got[i] !== base[i]) moved++;
      res[k] = { digest: fnv1a(got), moved: moved };
    }
  }
  return res;
}

const result = {};
// One SHARED realm across every seed, only when explicitly asked for: this
// models the instrument defect so a test can pin it.
const shared = IN.share_one_realm ? vm.createContext({}) : null;
for (const entry of IN.cases) {
  result[entry.seed] = run(entry.src, IN.base, IN.which, shared);
}
process.stdout.write(JSON.stringify(result));
"""


def _probe_buffer() -> list:
    """The loopback probe's OWN draw — ``probes.py`` ``webgl.readback``.

    32x32, four scissored mid-range bands, RGBA UNSIGNED_BYTE, opaque alpha.
    Mid-range on purpose: the shipped guard only nudges ``v > 1 && v < 254``, so
    a black or white surface reads as a total spoof failure while the spoof is
    working perfectly.
    """
    W = H = 32
    bands = [(0.31, 0.45, 0.60), (0.55, 0.35, 0.69),
             (0.42, 0.62, 0.38), (0.66, 0.51, 0.28)]
    px = [0] * (W * H * 4)
    for y in range(H):
        b = bands[y // (H // 4)]
        for x in range(W):
            o = (y * W + x) * 4
            px[o] = round(b[0] * 255)
            px[o + 1] = round(b[1] * 255)
            px[o + 2] = round(b[2] * 255)
            px[o + 3] = 255          # opaque -> NOT guard-eligible
    return px


def _creep_corner(n_eligible: int) -> list:
    """CreepJS's readback corner, at a chosen content census.

    ``webgl_ext.py:30-45`` records the shape measured on a real engine: CreepJS
    reads a ``drawingBufferWidth/15 x drawingBufferHeight/6`` corner — 17x42 =
    2856 bytes off a 256x256 canvas — which is ~98.9% cleared zeros with only
    ~16 bytes passing the mid-range guard. ``n_eligible`` is the only variable,
    and it is what separates "starved but working" from "nothing to work on".
    """
    px = [0] * (17 * 42 * 4)
    for i in range(3, len(px), 4):
        px[i] = 255                  # cleared, opaque alpha
    for k in range(n_eligible):
        px[k * 4] = 128              # antialiased edge bytes that carry content
    return px


def _measure(base: list, which: int = 1, seeds=SEEDS,
             share_one_realm: bool = False) -> dict:
    """Execute the SHIPPED script once per seed and return the readbacks."""
    node = _node()
    payload = {
        "base": base,
        "which": which,
        "share_one_realm": share_one_realm,
        "cases": [{"seed": s, "src": firefox_webgl_init_script(s)} for s in seeds],
    }
    proc = subprocess.run(
        [node, "-e", _HARNESS],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=120, encoding="utf-8",
    )
    assert proc.returncode == 0, (
        f"the shipped script failed to execute:\n{proc.stderr[:4000]}"
    )
    return {int(k): v for k, v in json.loads(proc.stdout).items()}


# --- the central claim: a Firefox readback DIFFERS per profile ---------------


def test_firefox_readback_differs_across_seeds_on_the_probe_geometry():
    """Four profiles differing only by seed produce four DIFFERENT readbacks.

    This is the ticket's first question — *does the Firefox readback carry any
    per-profile entropy at all* — asked of the bytes rather than of the source.
    The assertion is on distinct DIGESTS, and additionally that no seed returned
    the unperturbed value, because "all four moved" and "all four moved
    DIFFERENTLY" fail apart.
    """
    got = _measure(_probe_buffer(), which=1)

    digests = {s: got[s]["gl1"]["digest"] for s in SEEDS}
    assert len(set(digests.values())) == len(SEEDS), (
        "two Firefox profiles produced the SAME WebGL readback, which is the "
        f"linkability defect PS-182 is about: {digests}"
    )
    for s in SEEDS:
        assert got[s]["gl1"]["moved"] > 0, f"seed {s} moved no bytes at all"
        assert digests[s] != UNPERTURBED_PROBE_DIGEST, (
            f"seed {s} returned the UNPERTURBED digest — the patch did not run"
        )


def test_harness_reproduces_the_real_engine_recorded_digests():
    """The harness is validated against a REAL firefox-20 engine, not trusted.

    ``readings/ps135-2026-08-24/`` holds loopback probe readings taken on the
    packaged engine under xvfb. If this seat is green, the perturbation executed
    here is byte-for-byte the one that executed on that engine — which is what
    licenses every other seat in this file to stand in for a browser we cannot
    launch from CI (no GPU, no display, and Firefox WebGL is display-dependent
    here: with no X display ``getContext('webgl')`` returns null, which is
    indistinguishable from a spoof that never loaded).

    It also proves something about the PRODUCT, not just the test: the engine's
    recorded readback equals (ideal band buffer) + (our perturbation at that
    seed), so the per-profile entropy in that reading is OURS and the GPU-less
    renderer contributed none of it.
    """
    got = _measure(_probe_buffer(), which=1, seeds=tuple(CORPUS_PS135_FIREFOX))
    for seed, expected in CORPUS_PS135_FIREFOX.items():
        assert got[seed]["gl1"]["digest"] == expected, (
            f"seed {seed}: harness produced {got[seed]['gl1']['digest']} but a "
            f"real firefox-20 engine recorded {expected} in "
            f"readings/ps135-2026-08-24/. Either the perturbation changed or "
            f"this harness no longer models the product."
        )


def test_readback_still_differs_under_creepjs_starved_geometry():
    """The STARVED case — 16 usable bytes in 2856 — still yields four values.

    This is the geometry PS-97 fixed the Chromium side for, and it is the one
    that matters most: if the perturbation could not survive CreepJS's sparse
    corner, "too sparse to be observed" would be a live explanation for the
    ``51df3565`` collision. It survives, so that explanation is refuted and the
    cause lies elsewhere.
    """
    got = _measure(_creep_corner(16), which=1)
    digests = {s: got[s]["gl1"]["digest"] for s in SEEDS}
    assert len(set(digests.values())) == len(SEEDS), (
        "the perturbation collapsed under CreepJS's starved corner geometry: "
        f"{digests}"
    )
    for s in SEEDS:
        assert got[s]["gl1"]["moved"] == 16, (
            f"seed {s} moved {got[s]['gl1']['moved']} of the 16 eligible bytes; "
            "every eligible byte must move when the budget is not binding"
        )


def test_collision_reproduces_only_with_no_guard_eligible_bytes():
    """The one geometry that DOES collide — and why it is recorded, not fixed.

    With zero bytes passing ``v > 1 && v < 254`` the patch is a no-op by
    construction, and all four seeds return the identical unperturbed digest.
    That is the shape of the observed ``51df3565``, and it is the LEADING
    HYPOTHESIS for the live collision: the region CreepJS samples on the Firefox
    render appears to hold no guard-eligible content.

    Pinned as a NEGATIVE that is only meaningful next to the positives above:
    same file, same machinery, content -> varies, no content -> does not. The
    remedy is deliberately NOT applied here — the guard lives in the shared
    ``_CONTENT_SCRIPT``, is byte-pinned by tests/test_webgl_ext.py, and is out of
    scope for PS-182. Changing it without the live read that would confirm the
    region is empty would be the PS-97 mistake again: a perturbation nothing
    observes.
    """
    got = _measure(_creep_corner(0), which=1)
    digests = {s: got[s]["gl1"]["digest"] for s in SEEDS}
    assert len(set(digests.values())) == 1, (
        "a buffer with no guard-eligible bytes should be untouched by every "
        f"seed; instead the seeds disagreed: {digests}"
    )
    for s in SEEDS:
        assert got[s]["gl1"]["moved"] == 0


# --- the realm axis: the Firefox worker shape --------------------------------


def test_patch_covers_webgl2_only_realms():
    """WebGL2-ONLY is the Firefox WORKER shape, and it must be perturbed too.

    ``probes.py`` declares ``webgl.readback`` WINDOW_ONLY on the strength of a
    measurement on this engine: in a Firefox worker ``getContext('webgl')``
    returns null and only ``'webgl2'`` yields a context. So a checker reading its
    pixel hash from a worker exercises a realm the loopback probe deliberately
    does not read — and had the patch covered only ``WebGLRenderingContext``,
    that would have been a genuine, in-scope Firefox delivery gap.

    It is covered. This seat keeps it that way.
    """
    got = _measure(_probe_buffer(), which=2)
    digests = {s: got[s]["gl2"]["digest"] for s in SEEDS}
    assert len(set(digests.values())) == len(SEEDS), (
        f"WebGL2-only realm did not vary per profile: {digests}"
    )
    for s in SEEDS:
        assert digests[s] != UNPERTURBED_PROBE_DIGEST


def test_both_contexts_are_patched_consistently():
    """A profile's readback must not depend on WHICH context it asked for.

    If WebGL1 and WebGL2 in one realm produced different readbacks off the same
    pixels, a fingerprinter could read both and get two identities from one
    profile — self-contradictory in the same way a spoofed string over a shared
    render is.
    """
    got = _measure(_probe_buffer(), which=3)
    for s in SEEDS:
        assert got[s]["gl1"]["digest"] == got[s]["gl2"]["digest"], (
            f"seed {s}: WebGL1 and WebGL2 disagree in the same realm — "
            f"{got[s]['gl1']['digest']} vs {got[s]['gl2']['digest']}"
        )


# --- the instrument hazard, pinned so it cannot silently return --------------


def test_realm_guard_is_what_makes_isolation_necessary():
    """Sharing ONE realm across seeds manufactures a FAKE collision.

    Not a test of the product — a test of the INSTRUMENT, kept because this
    exact mistake produced a confident false "Firefox has no per-profile
    entropy" reading during PS-182, and the next person to write a WebGL harness
    will reach for a single context by default.

    The shipped guard stores its flag at ``Object.__pnaRealm.webgl``
    (``worker_wrap.realm_guard_js``). In a shared realm the first seed marks it
    and every later seed returns EARLY, patching nothing — so the readbacks
    collapse onto whatever the first seed produced, and the product looks broken
    while being fine.

    If this seat ever goes red, the guard's storage changed; re-read
    ``realm_guard_js`` before trusting any single-realm WebGL measurement.
    """
    shared = _measure(_probe_buffer(), which=1, share_one_realm=True)
    shared_digests = {s: shared[s]["gl1"]["digest"] for s in SEEDS}

    # The precise shape, which is sharper than "they all collide": the FIRST
    # seed installs the patch and perturbs normally; every LATER seed hits the
    # guard, patches nothing, and reads the buffer back untouched. So the tell
    # is not one repeated value — it is one real value followed by the
    # UNPERTURBED digest forever.
    first, rest = SEEDS[0], SEEDS[1:]
    assert shared_digests[first] != UNPERTURBED_PROBE_DIGEST, (
        "the first seed into a shared realm should still patch normally; got "
        f"{shared_digests}"
    )
    for s in rest:
        assert shared_digests[s] == UNPERTURBED_PROBE_DIGEST, (
            f"seed {s} should have been frozen by the per-realm guard and left "
            f"the buffer UNPERTURBED; got {shared_digests}"
        )
        assert shared[s]["gl1"]["moved"] == 0, (
            f"seed {s} moved bytes despite the guard having already fired"
        )
    # The consequence that makes this a hazard: the seeds no longer carry
    # distinct identities, because all but one collapsed onto one value.
    assert len(set(shared_digests.values())) < len(SEEDS), (
        f"a shared realm must destroy per-seed distinctness; got {shared_digests}"
    )
    # And the positive control: the SAME seeds, isolated properly, do vary.
    isolated = _measure(_probe_buffer(), which=1, share_one_realm=False)
    isolated_digests = {s: isolated[s]["gl1"]["digest"] for s in SEEDS}
    assert len(set(isolated_digests.values())) == len(SEEDS), (
        "isolated realms must vary per seed, or this file's other seats are "
        f"measuring nothing: {isolated_digests}"
    )
