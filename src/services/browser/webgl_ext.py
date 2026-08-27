"""MAIN-world extension that makes each profile's WebGL pixel readback distinct.

The engine spoofs the WebGL vendor/renderer/parameter strings per seed, but the
actual rendered pixels come from the shared software renderer (SwiftShader in a
GPU-less VM), so gl.readPixels collides across profiles and links them. WebGL
readback is not patched in C++ (unlike 2D-canvas toDataURL), so a MAIN-world
override of readPixels survives and is where per-profile entropy belongs.

A deterministic per-(seed, position) sub-pixel delta is added to a sparse set of
bytes in the returned buffer: enough to change the readback hash per profile,
small enough to stay a plausible pixel output. It only touches Uint8/clamped
byte readbacks (RGBA UNSIGNED_BYTE), the path fingerprinters use; float/integer
pixel reads are left untouched so WebGL maths is unaffected.
"""

import json
import pathlib

from .worker_wrap import (
    CHROMIUM_WORKER_CLOAK,
    WorkerCloak,
    firefox_native_wrap_js,
    firefox_worker_cloak,
    realm_bootstrap_js,
    realm_guard_js,
)

# How many bytes we aim to nudge in any one readback, by +/-1.
#
# A BUDGET rather than the fixed `_STRIDE = 17` byte-comb it replaces, because a
# byte stride is a function of BUFFER GEOMETRY and the geometry belongs to
# whoever calls readPixels — i.e. to the fingerprinter. PS-97 measured what that
# costs. CreepJS reads a `drawingBufferWidth/15 x drawingBufferHeight/6` corner
# (`src/webgl/index.ts:355`), which off a 256x256 canvas is 17x42, so its row is
# 68 bytes = EXACTLY 4 x 17. A `i += 17` walk therefore visited pixel 0/4/8/12 of
# every row and nothing else, for the whole buffer, while the antialiased edge it
# had to reach sat at x=13..16. Measured on a real engine: of the 172 offsets the
# comb visited, ZERO passed the mid-range guard below. Two profiles with two
# different seeds published a byte-identical `pixels:` hash (`51df3565`) while
# every other rendered vector differed — a vector on which they are linkable.
#
# Two distinct failures were behind that one number, and a stride cannot fix
# either. ALIASING: any stride dividing the row length collapses onto a handful
# of columns, and the row length is not ours to choose. STARVATION: that region
# is 98.9% cleared zeros, and only 16 of its 2856 bytes pass the guard at all, so
# even a stride coprime with the row expects ~1 hit — entropy by luck.
#
# So the budget is spent over the bytes that CARRY CONTENT (see `perturbBytes`),
# never over byte offsets. No row width can alias content ordinals away, and a
# sparse readback gets a guaranteed floor instead of a lottery.
#
# NOT a magnitude increase, deliberately. PS-97 also measured that CreepJS
# publishes `pixels:` as a SHA-256 over the raw array (`utils/crypto.ts:23`) with
# no rounding anywhere in its WebGL path, so ONE changed byte moves the hash.
# The +/-1 nudge and the mid-range guard are untouched; only the CHOICE of which
# bytes to spend it on changes.
#
# WHAT THIS COSTS AS A TELL, stated in both directions rather than from the
# favourable case only. Against the old `length/17` comb this is:
#
#   SPARSER on a large readback — a 1920x1080 frame moves 512 bytes where the
#   comb moved ~488k, three orders of magnitude fewer.
#   DENSER on a small one — up to `_BUDGET` eligible bytes means EVERY eligible
#   byte moves. On this repo's own 512-byte all-128 `GL_OBSERVABLE_PROBE` that
#   is 512 bytes against the comb's 31.
#
# The crossover is `eligible == _BUDGET`. The dense side is accepted knowingly:
# it is the same property that rescues the starved CreepJS readback, where all
# 16 eligible bytes in 2856 must move or the vector delivers nothing. A +/-1
# nudge on every mid-range byte of a sub-kilobyte buffer is sub-pixel dither;
# a readback that publishes the SAME hash for two profiles is a link. Between
# those two, the link is the worse outcome. `_BUDGET` is what stops that
# reasoning from generalising to a full frame, where moving every eligible byte
# WOULD be a visible tell — and it is enforced structurally (see `perturbBytes`)
# rather than by an arithmetic that can overshoot.
_BUDGET = 512

# The ONLY engine-specific part of the patch, kept as a seam rather than a
# second copy of the whole script: everything that computes the perturbation is
# shared, and the two engines differ solely in how a wrapper is made to
# stringify as a native built-in.
#
# Chromium's form is the ORIGINAL text, unchanged and pinned byte-for-byte by
# tests/test_webgl_ext.py. Chromium's readback is the baseline every prior
# reading was taken against (PS-78 boundary: "Chromium is unchanged"), so this
# seam must reproduce it EXACTLY, not merely equivalently.
_CHROMIUM_NATIVE_WRAP = r"""  function nativeWrap(orig, replacement) {
    try {
      Object.defineProperty(replacement, 'name', { value: orig.name });
      // Mark for the native_ext Function.prototype.toString patch so a detector
      // calling Function.prototype.toString.call(replacement) reads native. A
      // plain replacement.toString override is bypassed by that .call form.
      Object.defineProperty(replacement, '__pnaName', { value: orig.name });
    } catch (e) {}
    return replacement;
  }"""

_CONTENT_SCRIPT = r"""
(function () {
  // Patch one realm G (window or a WorkerGlobalScope). Detectors read a WebGL
  // pixel hash from an OffscreenCanvas inside a worker to catch a page-only
  // spoof, so the readback noise must run in every realm — carried into workers
  // below. SEED/BUDGET live INSIDE so applyWebglPatch.toString() carries them
  // into the worker realm (a var in the outer IIFE would be undefined there).
  function applyWebglPatch(G) {
   try {
    if (!G) return;
__REALM_GUARD__
    var SEED = __SEED__;
    var BUDGET = __BUDGET__;

  function bit(i) {
    var h = SEED ^ (i + 0x9e3779b1);
    h = Math.imul(h ^ (h >>> 16), 0x85ebca6b);
    h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
    h = (h ^ (h >>> 16)) >>> 0;
    return (h & 1) ? 1 : -1;
  }

__NATIVE_WRAP__

  function perturbBytes(buf) {
    // Only byte-typed pixel data (the RGBA UNSIGNED_BYTE readback path).
    //
    // BRAND-CHECKED BY Object.prototype.toString, NOT BY `instanceof`, and that
    // is a CROSS-REALM correctness requirement rather than a style choice.
    // `instanceof` walks a prototype chain and compares against the
    // `Uint8Array` binding THIS COPY OF THE LEAF closed over, so its answer
    // depends on which realm the leaf was evaluated in. A buffer allocated in a
    // DIFFERENT realm from the one whose context is being read fails that check
    // and the readback goes out UNPERTURBED.
    //
    // That is not hypothetical: it is exactly how a detector reads us. CreepJS
    // allocates its pixel buffer in the TOP realm and calls `readPixels` on a
    // context belonging to its phantom IFRAME realm. Measured on real Firefox
    // (PS-215, loopback, the committed `readings/ps193-2026-08-26/realm_probe.py`),
    // one run, one instant, differing only in which realm allocated the buffer:
    //
    //     new win.Uint8Array(...)  (child realm) -> 855826239   perturbed
    //     new Uint8Array(...)      (top realm)   -> 660023932   UNPERTURBED
    //
    // The wrapper was installed in the child realm and RAN -- it was admitted
    // once out of two calls -- so this reads as "the spoof is not there" while
    // the spoof is very much there and declining. The published PS-193 table
    // (`phantom_*` seed-invariant while `top_*` moves) is produced by this, and
    // by a missing-delivery defect, IDENTICALLY -- which is why that reading
    // could not tell the two apart and PS-215 was written against the other one.
    //
    // `Object.prototype.toString` reads the object's internal type tag, which
    // is realm-independent, so the check now answers the question it always
    // meant to ask: "are these bytes", not "are these bytes from MY realm".
    // Strictly permissive -- every buffer admitted before is still admitted, so
    // same-realm digests do not move.
    var __tag = Object.prototype.toString.call(buf);
    if (__tag !== "[object Uint8Array]" && __tag !== "[object Uint8ClampedArray]") {
      return;
    }
    // Spend the budget over the bytes that CARRY CONTENT, never over byte
    // offsets. A fixed byte stride is a function of the buffer's row geometry,
    // and the caller chooses that geometry — CreepJS's 17x42 corner has a
    // 68-byte row, so the old `i += 17` hit four columns of every row forever
    // and never once landed on a byte this guard admits. Selecting by ORDINAL
    // AMONG ELIGIBLE BYTES makes the choice depend on the IMAGE instead, which
    // no row width can alias away, and gives a sparse readback a guaranteed
    // floor rather than a ~1-hit lottery.
    //
    // ONE pass, and the budget is a HARD bound rather than an arithmetic aim.
    // The obvious way to write this — count the eligible bytes, then divide to
    // get a stride — needs a second full pass over the buffer AND overshoots:
    // `floor(eligible / BUDGET)` is 1 for every `eligible` in [BUDGET, 2*BUDGET),
    // so at 1023 eligible bytes it moves all 1023 while claiming a cap of 512.
    // Both faults are removed by never computing a stride at all.
    //
    // Instead the eligible offsets stream into a reservoir capped at BUDGET.
    // When it fills, HALF the entries are dropped and the spacing doubles —
    // which is exactly a stride of 2, then 4, then 8, discovered as the content
    // arrives instead of guessed in advance. The invariant is that `sel` always
    // holds the eligible ordinals congruent to `phase` modulo `step`, in order,
    // so decimating to every second entry IS the next power-of-two stride.
    //
    // WHICH half survives is seed-derived, so two seeds differ by WHICH bytes
    // move as well as by which direction they move. On a buffer with no more
    // than BUDGET eligible bytes `step` never leaves 1 and EVERY eligible byte
    // moves — the property that rescues the starved CreepJS readback, where all
    // 16 usable bytes of 2856 must move or the vector delivers nothing.
    //
    // The guard is the original one: skip fully transparent/black and fully
    // opaque/white edges so we don't make obviously-wrong pixels; nudge
    // mid-range bytes only. It is applied in ONE place now, so the two-pass
    // hazard of the passes disagreeing cannot arise.
    var sel = [];
    var count = 0;
    var step = 1;
    var phase = 0;
    var lvl = 0;
    var ord = 0;
    var i;
    for (i = 0; i < buf.length; i++) {
      var v = buf[i];
      if (v <= 1 || v >= 254) continue;
      if (ord % step === phase) {
        if (count === BUDGET) {
          var takeOdd = (SEED >>> (lvl & 31)) & 1;
          var w = 0;
          for (var j = takeOdd ? 1 : 0; j < count; j += 2) { sel[w++] = sel[j]; }
          count = w;
          if (takeOdd) phase += step;
          step *= 2;
          lvl++;
        }
        // Re-tested because the decimation above may have just moved this
        // ordinal off the lattice.
        if (ord % step === phase) { sel[count++] = i; }
      }
      ord++;
    }

    // Touch only the selected offsets — at most BUDGET of them, so this pass is
    // bounded by the budget and not by the buffer.
    for (var k = 0; k < count; k++) {
      var at = sel[k];
      // Keyed by byte offset, not by ordinal: the offset is stable under a
      // change of content, so the same pixel keeps the same direction.
      buf[at] = buf[at] + bit(at);
    }
  }

  function patch(proto) {
    if (!proto || !proto.readPixels) return;
    var orig = proto.readPixels;
    proto.readPixels = nativeWrap(orig, function (x, y, w, h, fmt, type, pixels) {
      var r = orig.apply(this, arguments);
      try { perturbBytes(pixels); } catch (e) {}
      return r;
    });
  }

  try { if (G.WebGLRenderingContext) patch(G.WebGLRenderingContext.prototype); } catch (e) {}
  try { if (G.WebGL2RenderingContext) patch(G.WebGL2RenderingContext.prototype); } catch (e) {}
   } catch (e) {}
  }

__REALM_BOOTSTRAP__
})();
"""

_MANIFEST = {
    "manifest_version": 3,
    "name": "persona-webgl",
    "version": "1.0",
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["webgl.js"],
            "run_at": "document_start",
            "all_frames": True,
            "world": "MAIN",
        }
    ],
}


def _webgl_patch_js(
    seed: int, native_wrap: str, worker_cloak: WorkerCloak = CHROMIUM_WORKER_CLOAK
) -> str:
    """The shared patch body, with the engine's cloak seams spliced in.

    Everything that computes the perturbation — the seed mixing, the stride, the
    byte nudging, the readPixels overrides, the realm bootstrap — is identical on
    both engines. Only the cloaks differ, and there are TWO of them because there
    are two sets of wrappers:

    * ``native_wrap`` cloaks the LEAF's wrappers (``readPixels``);
    * ``worker_cloak`` cloaks the wrappers the BOOTSTRAP installs (``Worker``,
      ``SharedWorker``, the two ``HTMLIFrameElement`` accessors).

    Round 2 of PS-78 passed the first and forgot the second, which left the
    Chromium ``__pnaName`` marker on Firefox's ``Worker`` — an own property no
    browser has, on an engine with no extension to read it. Both default to the
    Chromium form so its bytes cannot move.

    THE WORKER DELIVERY PATH IS SHARED TOO, deliberately. An earlier revision of
    PS-78 gave Firefox its own ``blob:`` branch here via a
    ``blob_via_import_scripts`` flag; the premise was wrong and the flag broke
    workers whose URL is revoked after construction. See ``realm_bootstrap_js``
    for the measurement. One path, both engines.
    """
    return (
        _CONTENT_SCRIPT.replace("__SEED__", str(int(seed) & 0xFFFFFFFF))
        .replace("__BUDGET__", str(_BUDGET))
        .replace("__NATIVE_WRAP__", native_wrap)
        .replace(
            "__REALM_BOOTSTRAP__", realm_bootstrap_js("applyWebglPatch", worker_cloak)
        )
        .replace("__REALM_GUARD__", realm_guard_js("webgl"))
    )


def firefox_webgl_init_script(seed: int) -> str:
    """The same per-seed readPixels perturbation, as an init script for FIREFOX.

    WHY THIS EXISTS AT ALL. ``build_webgl_extension`` has exactly one call site —
    ``process.py:503`` — and ``spawn_browser`` returns on the Firefox arm about
    150 lines BEFORE it, so the extension list carrying the WebGL delta is
    assembled on a path Firefox never reaches. The vector was not merely unwired
    on that engine, it was unreachable.

    That matters because of what this module's own docstring records: the engine
    spoofs the WebGL vendor/renderer/parameter STRINGS per seed, but on a
    GPU-less host the actual rendering is identical everywhere, so ``readPixels``
    collides across profiles and links them. On Firefox the strings were spoofed
    and the pixels were not — which is the sharper of the two failures, because a
    profile that CLAIMS a distinct GPU while RENDERING the shared one is
    self-contradictory in a way a plain missing spoof is not.

    An init script rather than a copied extension directory: MV3 unpacked
    extensions are not a mechanism this engine has, and this is the route the
    other per-profile Firefox spoofs already take. ``add_init_script`` runs at
    document_start in the page realm — the same moment and realm the Chromium
    content script gets — and ``realm_bootstrap_js`` carries the leaf onward into
    workers and child frames, so the worker realm is covered too.

    ⚠️ "AND CHILD FRAMES" WAS FALSE FOR A FRAME REACHED BY INDEX until PS-215,
    and the correction is worth stating because the false version read as a
    completed inventory. The bootstrap reached a child realm through exactly one
    door — the chained ``HTMLIFrameElement`` accessors — and a consumer that
    takes its frame as ``self[N]`` never opens it. CreepJS does exactly that and
    then builds its WebGL context FROM that realm, so it received unperturbed
    pixels while the page realm moved with the seed: measured at two seeds in
    one run (``readings/ps193-2026-08-26/EVIDENCE.md``), a Level 2 mutual-
    unlinkability failure. PS-215 added a second trigger — the same installer,
    fired when a frame becomes CONNECTED to the document — so the claim above
    now holds for both doors. It is a TRIGGER, not a door: an asynchronous one
    (``MutationObserver``) is structurally too late, because the indexed read is
    synchronous with the insertion.

    Deliberately shares ``_CONTENT_SCRIPT`` with the Chromium builder rather than
    copying it: a second copy of the perturbation would let the two engines drift
    apart, and a profile's WebGL identity must not depend on which engine it
    launched. The two differ ONLY in the cloak seam, and Chromium's is reproduced
    verbatim so its readbacks do not move.
    """
    return (
        "(function(){"
        + _webgl_patch_js(seed, firefox_native_wrap_js(), firefox_worker_cloak())
        + "})();"
    )


def build_webgl_extension(seed: int, base_dir: str) -> str:
    """Generate an unpacked extension that adds a deterministic per-seed delta
    to WebGL readPixels byte readbacks, so each profile has a distinct WebGL
    pixel fingerprint. Returns its directory.
    """
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    script = _webgl_patch_js(seed, _CHROMIUM_NATIVE_WRAP)
    (ext_dir / "webgl.js").write_text(script, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(_MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
