"""MAIN-world extension that repairs Canvas measureText().

The fingerprint engine adds noise to Canvas::measureText() (a Bromite
fingerprinting feature), which scales EVERY returned metric — width AND the
actualBoundingBox*/fontBoundingBox*/baseline fields — by a single constant
factor (~1e-6, its sign set by the seed). Layout-heavy web apps that measure
text to position UI then break:
 - Google Sheets' canvas grid lays glyphs out against a width of ~0 and the
   text overlaps into adjacent columns ("looks right for a frame, then shifts").
 - Sheets' date-cell calendar popover sizes/places itself from the bounding-box
   metrics; with near-zero values the popover collapses to zero size / off-screen
   and "the calendar doesn't appear at all".

The noise is a fixed multiplicative scale for the whole session, so we learn it
ONCE: measure a string's true width in a hidden DOM node (getBoundingClientRect
is not noised in this build) and divide the noised width by it to get the
factor. Every later repair is then pure arithmetic — each native metric divided
by the factor — with NO DOM access. That recovers the engine's real, fully
self-consistent geometry and, crucially, touches layout only once: an earlier
version measured through a resident DOM node on EVERY call, and each
getBoundingClientRect forced a synchronous document layout. On an app that
constantly dirties the DOM (Sheets) that turned into layout thrashing that
pinned the main thread, so the compositor never idled — a permanent 'Working…'
throbber that also blocked every Sheets popover/overlay from painting.
"""

import json
import pathlib

from .worker_wrap import realm_bootstrap_js, realm_guard_js, realm_slot_js

# The same noise repair must hold in a fresh child frame and in a Web Worker's
# OffscreenCanvas measureText, else a scanner measuring text in a pristine realm
# sees the raw noised geometry (functional inconsistency across realms). The
# shared recursive registry carries applyMtPatch everywhere.
#
# The multiplicative noise factor is a session constant, so a realm that CAN
# calibrate learns it once and publishes it for realms that cannot. It rides the
# top realm's non-enumerable `Object.__pnaRealm` slot (PS-139); it used to ride a
# `top.__personaMtFactor` global, which put the divisor that INVERTS this spoof
# one property read from any page.
#
# WHICH REALMS ACTUALLY CROSS — this sentence used to claim the factor reached
# "realms without a DOM", and that was overstated. The channel that functions is
# iframe -> top: a same-origin child frame has `top` and reuses the learned
# factor, which is what keeps it repairing text to the same width as the top
# realm. A WORKER has no `top` at all, so it never read this value and does not
# now; it falls through to `trueWidth`'s "no DOM here + not learned yet" branch
# below, exactly as it did before. That gap is real and unchanged by PS-139 —
# a runtime-learned value cannot ride the leaf's source-text crossing into a
# worker — and it is tracked separately rather than assumed away here.
CONTENT_SCRIPT = r"""
(function () {
  function applyMtPatch(G) {
   try {
    if (!G) return;
    var proto = (G.CanvasRenderingContext2D || {}).prototype;
    var off = (G.OffscreenCanvasRenderingContext2D || {}).prototype;
    if ((!proto || !proto.measureText) && (!off || !off.measureText)) return;
__MT_REALM_GUARD__
__MT_REALM_SLOT__

    // One-shot, un-noised true width of `text` in `font`, via a throwaway DOM
    // node measured and removed immediately (the bounding-rect read is not
    // noised). Returns null when there's no DOM (a worker) or zero-width text.
    function trueWidth(doc, font, text) {
      var root = doc && (doc.documentElement || doc.body);
      if (!root) return null;
      var span = doc.createElement('span');
      span.style.cssText =
        'position:absolute;left:-99999px;top:0;white-space:pre;' +
        'visibility:hidden;pointer-events:none;margin:0;padding:0;border:0;' +
        'letter-spacing:0';
      span.style.font = font;
      span.textContent = String(text);
      root.appendChild(span);
      var w = span.getBoundingClientRect().width;
      root.removeChild(span);
      return w > 0 ? w : null;
    }

    // The noise scale is a session constant, so it is learned ONCE in a realm
    // that can calibrate and published for realms that cannot. It rides the top
    // realm's non-enumerable per-realm slot — see the Python note above this
    // template for what does and does not cross (iframe yes, worker no).
    // Reads pass `create` false so probing a realm never mints a registry in it.
    function getFactor() {
      try {
        if (G.top && G.top !== G) {
          var ts = __pnaSlot(G.top, false);
          if (ts && typeof ts.mtFactor === 'number') return ts.mtFactor;
        }
      } catch (e) {}
      try {
        var s = __pnaSlot(G, false);
        if (s && typeof s.mtFactor === 'number') return s.mtFactor;
      } catch (e) {}
      return null;
    }
    function setFactor(f) {
      // Own realm first (`create` true) so a top realm that calibrated always
      // has its own copy, then publish to the top for children to read.
      try {
        var s = __pnaSlot(G, true);
        if (s) s.mtFactor = f;
      } catch (e) {}
      try {
        if (G.top && G.top !== G) {
          var ts = __pnaSlot(G.top, true);
          if (ts && typeof ts.mtFactor !== 'number') ts.mtFactor = f;
        }
      } catch (e) {}
    }

    function patch(target) {
      var orig = target.measureText;
      if (!orig) return;
      function measureText(text) {
        var m = orig.call(this, text);
        try {
          var hasText = String(text).length > 0;
          var corrupt = hasText && !(Math.abs(m.width) >= 1);
          if (!corrupt) return m;
          var factor = getFactor();
          if (factor === null) {
            var doc = G.document;
            var tw = trueWidth(doc, this.font, text);
            if (tw === null) return m;  // no DOM here + not learned yet
            var f = m.width / tw;
            if (!isFinite(f) || f === 0) return m;
            factor = f;
            setFactor(f);
          }
          var scale = factor;
          return new Proxy(m, {
            get: function (t, p) {
              var v = t[p];
              return (typeof v === 'number') ? v / scale : v;
            },
          });
        } catch (e) {}
        return m;
      }
      try {
        Object.defineProperty(measureText, 'name', { value: 'measureText' });
        Object.defineProperty(measureText, '__pnaName', { value: 'measureText' });
      } catch (e) {}
      try { target.measureText = measureText; } catch (e) {}
    }

    if (proto && proto.measureText) patch(proto);
    if (off && off.measureText) patch(off);
   } catch (e) {}
  }
__MT_REALM_BOOTSTRAP__
})();
"""

MANIFEST = {
    "manifest_version": 3,
    "name": "persona-measuretext",
    "version": "1.0",
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["measuretext.js"],
            "run_at": "document_start",
            "all_frames": True,
            "world": "MAIN",
        }
    ],
}


def build_measuretext_extension(base_dir: str) -> str:
    """Generate an unpacked extension that repairs noised Canvas measureText so
    text-measuring web apps (Google Sheets) lay out correctly. Returns its dir.
    """
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    js = CONTENT_SCRIPT.replace(
        "__MT_REALM_BOOTSTRAP__", realm_bootstrap_js("applyMtPatch")
    ).replace(
        "__MT_REALM_GUARD__", realm_guard_js("measuretext")
    ).replace(
        "__MT_REALM_SLOT__", realm_slot_js()
    )
    (ext_dir / "measuretext.js").write_text(js, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
