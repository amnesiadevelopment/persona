"""MAIN-world extension that gives each profile a distinct, deterministic
AudioContext fingerprint.

The engine's own audio spoof only nudges the OfflineAudioContext sample rate by
about +/-0.01 Hz, which quantises to roughly two representable float values --
so many profiles collide on the same audio hash. Audio readback is not patched
in C++ (unlike canvas), so a MAIN-world override of the float-buffer readers
survives and is the place to add per-profile entropy.

A tiny per-(seed, index) delta is added to the float samples on the readback
paths fingerprinters actually use. The magnitude is relative and small enough
to read as hardware variance, large enough to survive a coarse sum-and-hash.
"""

import json
import pathlib

from .worker_wrap import realm_bootstrap_js, realm_guard_js

# Magnitude of the per-sample relative perturbation. Larger than the engine's
# ~2e-7 sample-rate effect so it dominates the hash, small enough to stay a
# plausible hardware-level audio fingerprint rather than audible distortion.
_NOISE_REL = 1e-5

# The ONLY engine-specific part of the patch, kept as a seam rather than a
# second copy of the whole script: everything that computes the perturbation is
# shared, and the two engines differ solely in how a wrapper is made to
# stringify as a native built-in.
#
# Chromium's form is the ORIGINAL text, unchanged and pinned byte-for-byte by
# tests/test_audio_ext.py. Chromium's audio path works and its recorded digests
# are the baseline every prior reading was taken against (PS-73 boundary: "a
# 'fix' that moves Chromium's readings is a regression"), so this seam must
# reproduce it exactly, not merely equivalently.
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

# Firefox loads NO persona extension (invisible_launch.py is the whole launch
# path), so native_ext.py's `__pnaName` marker has nobody to read it: on this
# engine that property is not a cloak, it is a bare own property on every
# wrapper — a tell rather than a hiding place. Carry the cloak here instead.
#
# Three deliberate differences from the Chromium form, each mirroring the
# Firefox cloak already in invisible_launch._native_cloak_js:
#
#   * SPIDERMONKEY's native shape (three lines, four-space indent), NOT V8's
#     one-liner. Emitting V8's form on Firefox is itself a masking tell —
#     one `Array.prototype.map.toString()` comparison away.
#   * a closure WeakMap, so NO own property is added to any wrapper and the
#     registry cannot be enumerated or swept for symbols.
#   * `Function.prototype.toString` is CHAINED, not flag-guarded, so this
#     composes with the locale/outer-size cloaks already installed in the realm
#     instead of racing them for the single slot.
#
# It must live INSIDE applyAudioPatch: the leaf crosses into a worker as SOURCE
# TEXT (worker_wrap.realm_bootstrap_js serialises it with `LEAF.toString()`), so
# anything defined in an enclosing scope is undefined in the worker realm — the
# exact failure worker_wrap.py's docstring records, where a depth-2 worker
# silently reported the REAL audio while the page reported spoofed values.
_FIREFOX_NATIVE_WRAP = r"""  var __nm = (typeof WeakMap === 'function') ? new WeakMap() : null;
  var __nl = String.fromCharCode(10);
  // THE REALM MUST COME FROM G, NEVER FROM THE LEXICAL SCOPE. `applyAudioPatch`
  // reaches a child frame as a PARENT-REALM FUNCTION OBJECT — worker_wrap.py's
  // chained `contentWindow` accessor calls `__pnaInstall(childWindow, LEAF)`
  // with the leaf itself, not with source text. So a bare `Function.prototype`
  // here resolves to the PARENT's: it re-patches an already-patched toString (a
  // no-op) and leaves the child realm's own pristine, while the audio wrappers
  // ARE installed into the child. Read from inside that child — where a
  // detector runs — the wrapper then stringifies as raw patch source,
  // `perturbFloat` and all. Measured in two isolated realms, not reasoned.
  //
  // `G.Function.prototype` is Chromium's shape — see `applyNativePatch` in
  // native_ext.py, which reads that same realm-qualified form twice: once to
  // capture `origToString`, once to assign the `patched` wrapper back onto it.
  // That is also the reason its comment names "a fresh about:blank iframe …
  // has its own Function.prototype". The WORKER realm never had this problem —
  // the leaf crosses there as SOURCE TEXT and is re-evaluated in the worker's
  // own realm, so both forms resolve correctly there; this is the frame case
  // only.
  //
  // FAIL SOFT, NEVER `return`: this block is spliced INSIDE applyAudioPatch,
  // so bailing out here would skip the readback overrides too — trading the
  // Level 2 unlinkability fix for the masking one. A realm without a usable
  // Function.prototype loses the cloak and keeps the perturbation.
  var __F = G.Function;
  var __pts = (__F && __F.prototype && __F.prototype.toString)
              || Function.prototype.toString;
  var __ts = function () {
    'use strict';
    // `this` is the function being stringified. Strict mode so a primitive
    // `this` stays primitive and still reaches the original for its TypeError.
    try {
      var n = __nm && __nm.get(this);
      if (typeof n === 'string') {
        return 'function ' + n + '() {' + __nl + '    [native code]' + __nl + '}';
      }
    } catch (e) {}
    return __pts.apply(this, arguments);
  };
  try {
    // Cloak the patch as "toString": a detector stringifies
    // Function.prototype.toString to catch exactly this trick.
    if (__nm) { __nm.set(__ts, 'toString'); }
    Object.defineProperty(__ts, 'name', { value: 'toString', configurable: true });
    // G's prototype, not the lexical one — see the note above.
    if (__F && __F.prototype) { __F.prototype.toString = __ts; }
  } catch (e) {}

  function nativeWrap(orig, replacement) {
    try {
      // configurable: true is the descriptor a native function's own `name`
      // carries; the Chromium form omits it because its marker property, not
      // `name`, is what its extension-side cloak reads.
      Object.defineProperty(replacement, 'name',
                            { value: orig.name, configurable: true });
      if (__nm) { __nm.set(replacement, orig.name); }
    } catch (e) {}
    return replacement;
  }"""

_CONTENT_SCRIPT = r"""
(function () {
  // Patch one realm G (window or a WorkerGlobalScope). Audio fingerprinting is
  // commonly run in a worker via OfflineAudioContext, so the noise must apply in
  // every realm — carried into workers below. SEED/REL live INSIDE so
  // applyAudioPatch.toString() carries them into the worker realm (a var in the
  // outer IIFE would be undefined there).
  function applyAudioPatch(G) {
   try {
    if (!G) return;
__REALM_GUARD__
    var SEED = __SEED__;
    var REL = __REL__;
    var AudioBuffer = G.AudioBuffer, AnalyserNode = G.AnalyserNode;

  // Deterministic per-(seed, index) sign in {-1, +1}; stable across page loads
  // and sessions, distinct per profile.
  function bit(i) {
    var h = SEED ^ (i + 0x9e3779b1);
    h = Math.imul(h ^ (h >>> 16), 0x85ebca6b);
    h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
    h = (h ^ (h >>> 16)) >>> 0;
    return (h & 1) ? 1 : -1;
  }

__NATIVE_WRAP__

  function perturbFloat(data) {
    for (var i = 0; i < data.length; i++) {
      var v = data[i];
      if (v !== 0 && isFinite(v)) {
        data[i] = v + bit(i) * REL * Math.abs(v);
      }
    }
    return data;
  }

  try {
    var gcd = AudioBuffer.prototype.getChannelData;
    AudioBuffer.prototype.getChannelData = nativeWrap(gcd, function () {
      return perturbFloat(gcd.apply(this, arguments));
    });
  } catch (e) {}

  try {
    var gffd = AnalyserNode.prototype.getFloatFrequencyData;
    AnalyserNode.prototype.getFloatFrequencyData = nativeWrap(gffd, function (arr) {
      var r = gffd.apply(this, arguments);
      perturbFloat(arr);
      return r;
    });
  } catch (e) {}

  try {
    var gbfd = AnalyserNode.prototype.getByteFrequencyData;
    AnalyserNode.prototype.getByteFrequencyData = nativeWrap(gbfd, function (arr) {
      var r = gbfd.apply(this, arguments);
      // byte data is 0..255; nudge a single deterministic bin by +/-1 so the
      // byte-domain hash also varies per profile without going out of range.
      for (var i = 0; i < arr.length; i++) {
        var d = bit(i);
        var nv = arr[i] + d;
        if (nv >= 0 && nv <= 255) { arr[i] = nv; }
      }
      return r;
    });
  } catch (e) {}
   } catch (e) {}
  }

__REALM_BOOTSTRAP__
})();
"""

_MANIFEST = {
    "manifest_version": 3,
    "name": "persona-audio",
    "version": "1.0",
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["audio.js"],
            "run_at": "document_start",
            "all_frames": True,
            "world": "MAIN",
        }
    ],
}


def _audio_patch_js(seed: int, native_wrap: str) -> str:
    """The shared patch body, with the engine's own native-cloak spliced in.

    One template, two engines: the perturbation, the per-(seed, index) sign, the
    readback overrides and the realm bootstrap are identical, because a Firefox
    profile and a Chromium profile with the same seed should perturb the same
    way. Only `nativeWrap` differs — see the two constants above.
    """
    return (
        _CONTENT_SCRIPT
        .replace("__SEED__", str(int(seed) & 0xFFFFFFFF))
        .replace("__REL__", repr(_NOISE_REL))
        .replace("__NATIVE_WRAP__", native_wrap)
        .replace("__REALM_BOOTSTRAP__", realm_bootstrap_js("applyAudioPatch"))
        .replace("__REALM_GUARD__", realm_guard_js("audio"))
    )


def build_audio_extension(seed: int, base_dir: str) -> str:
    """Generate an unpacked extension that adds a deterministic per-seed delta
    to AudioContext float readbacks, so each profile has a distinct audio
    fingerprint. Returns its directory.

    CHROMIUM ONLY. Firefox loads no persona extension; it reaches the same
    perturbation through ``firefox_audio_init_script``.
    """
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    script = _audio_patch_js(seed, _CHROMIUM_NATIVE_WRAP)
    (ext_dir / "audio.js").write_text(script, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(_MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)


def firefox_audio_init_script(seed: int) -> str:
    """The same per-seed audio perturbation, as an init script for the Firefox
    engine (PS-73).

    WHY THIS EXISTS AT ALL. ``spawn_browser`` returns on the Firefox arm about a
    hundred lines before the extension list is assembled, so
    ``build_audio_extension`` was never called for a Firefox profile and nothing
    else supplied audio variance to that engine. The measured consequence:
    ``audio.digest`` read 35.749972 on FOUR profiles with four DISTINCT seeds —
    identical to six decimal places, which on a continuous vector is not
    coincidence.

    That mattered more than a missing spoof usually would, because
    ``audio.digest`` is the one probe the inventory grades ``INDEPENDENT``
    (probes.py:365). The other seed-derived vectors are POOLED — drawn from a
    finite set, so two profiles colliding on one proves nothing. Firefox had the
    pooled vectors and not the continuous one, which left mutual unlinkability
    resting on vectors that can collide by chance with no floor underneath.

    An init script rather than a copied extension directory: MV3 unpacked
    extensions are not a mechanism this engine has, and this is the route the
    other per-profile Firefox spoofs already take (the locale and outer-size
    overrides, invisible_launch.py). ``add_init_script`` runs at document_start
    in the page realm, which is the same moment and realm the Chromium content
    script gets, and ``realm_bootstrap_js`` carries the leaf onward into workers
    and child frames — so the worker realm is covered too. That last part is not
    incidental: worker_wrap.py records that a spoof reaching the page but not
    the worker leaves a detector reading the REAL audio out of a worker while
    the page reports the spoofed value, which is a sharper tell than no spoof.

    Deliberately NOT shared with the Chromium builder beyond the patch body
    itself: the two differ only in how a wrapper is cloaked as native, and
    Chromium's cloak text is reproduced verbatim so its digests do not move.
    """
    return "(function(){" + _audio_patch_js(seed, _FIREFOX_NATIVE_WRAP) + "})();"
