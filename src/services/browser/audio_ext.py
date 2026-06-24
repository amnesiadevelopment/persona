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

# Magnitude of the per-sample relative perturbation. Larger than the engine's
# ~2e-7 sample-rate effect so it dominates the hash, small enough to stay a
# plausible hardware-level audio fingerprint rather than audible distortion.
_NOISE_REL = 1e-5

_CONTENT_SCRIPT = r"""
(function () {
  var SEED = __SEED__;
  var REL = __REL__;

  // Deterministic per-(seed, index) sign in {-1, +1}; stable across page loads
  // and sessions, distinct per profile.
  function bit(i) {
    var h = SEED ^ (i + 0x9e3779b1);
    h = Math.imul(h ^ (h >>> 16), 0x85ebca6b);
    h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
    h = (h ^ (h >>> 16)) >>> 0;
    return (h & 1) ? 1 : -1;
  }

  function nativeWrap(orig, replacement) {
    try {
      Object.defineProperty(replacement, 'name', { value: orig.name });
      replacement.toString = function () { return orig.toString(); };
    } catch (e) {}
    return replacement;
  }

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


def build_audio_extension(seed: int, base_dir: str) -> str:
    """Generate an unpacked extension that adds a deterministic per-seed delta
    to AudioContext float readbacks, so each profile has a distinct audio
    fingerprint. Returns its directory.
    """
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    script = _CONTENT_SCRIPT.replace("__SEED__", str(int(seed) & 0xFFFFFFFF)).replace(
        "__REL__", repr(_NOISE_REL)
    )
    (ext_dir / "audio.js").write_text(script, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(_MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
