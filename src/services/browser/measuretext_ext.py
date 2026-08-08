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

from .worker_wrap import realm_bootstrap_js

# The same noise repair must hold in a fresh child frame and in a Web Worker's
# OffscreenCanvas measureText, else a scanner measuring text in a pristine realm
# sees the raw noised geometry (functional inconsistency across realms). The
# shared recursive registry carries applyMtPatch everywhere. The multiplicative
# noise factor is a session constant, so it is learned once in a DOM-bearing
# realm and shared via top.__personaMtFactor to realms without a DOM.
CONTENT_SCRIPT = r"""
(function () {
  function applyMtPatch(G) {
   try {
    if (!G || G.__personaMt) return;
    var proto = (G.CanvasRenderingContext2D || {}).prototype;
    var off = (G.OffscreenCanvasRenderingContext2D || {}).prototype;
    if ((!proto || !proto.measureText) && (!off || !off.measureText)) return;
    G.__personaMt = true;

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

    // The noise scale is a session constant; share the learned factor across
    // realms via the top window so a DOM-less realm (worker) can still repair.
    function getFactor() {
      try { if (G.top && typeof G.top.__personaMtFactor === 'number') return G.top.__personaMtFactor; } catch (e) {}
      return (typeof G.__personaMtFactor === 'number') ? G.__personaMtFactor : null;
    }
    function setFactor(f) {
      G.__personaMtFactor = f;
      try { if (G.top) G.top.__personaMtFactor = f; } catch (e) {}
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
    )
    (ext_dir / "measuretext.js").write_text(js, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
