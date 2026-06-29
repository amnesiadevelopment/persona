"""MAIN-world extension that repairs Canvas measureText().

The fingerprint engine adds noise to Canvas::measureText() (a Bromite
fingerprinting feature), which scales EVERY returned metric — width AND the
actualBoundingBox*/fontBoundingBox* fields — to a near-zero / negative value.
Layout-heavy web apps that measure text to position UI then break:
 - Google Sheets' canvas grid lays glyphs out against a width of ~0 and the
   text overlaps into adjacent columns ("looks right for a frame, then shifts").
 - Sheets' date-cell calendar popover sizes/places itself from the bounding-box
   metrics; with negative values the popover collapses to zero size / off-screen
   and "the calendar doesn't appear at all".

getClientRects()/getBoundingClientRect() are NOT noised in this build, so we
recover the true geometry by measuring the same string in a hidden DOM node and
rebuild a full, self-consistent TextMetrics: width from the node's rect, and the
ascent/descent/left/right box fields from the element's font metrics. This keeps
every other anti-fingerprint defense intact (canvas readback noise, audio,
webgl, …) while making text measurement correct — which is also *more* natural,
since negative text metrics are themselves a blatant anti-detect tell.
"""

import json
import pathlib

CONTENT_SCRIPT = r"""
(function () {
  var proto = (window.CanvasRenderingContext2D || {}).prototype;
  var off = (window.OffscreenCanvasRenderingContext2D || {}).prototype;
  if (!proto || !proto.measureText) return;

  var span = null;
  function ensureSpan() {
    if (span && span.isConnected) return span;
    var root = document.documentElement || document.body;
    if (!root) return null;
    span = document.createElement('span');
    span.style.cssText =
      'position:absolute;left:-99999px;top:0;white-space:pre;' +
      'visibility:hidden;pointer-events:none;margin:0;padding:0;border:0';
    root.appendChild(span);
    return span;
  }

  // Real, self-consistent metrics for `text` in the context's current font,
  // derived from a hidden DOM node (not noised). Returns null if we can't
  // measure (no DOM yet) so the caller can fall back to the native object.
  function realMetrics(font, text) {
    var s = ensureSpan();
    if (!s) return null;
    s.style.font = font;
    s.style.letterSpacing = '0px';
    s.textContent = String(text);
    var rect = s.getBoundingClientRect();
    var width = rect.width;
    // Font size in px → split into ascent/descent. Browsers don't expose the
    // exact font ascent here, so use the standard ~0.8/0.2 split of the em,
    // which is what fonts overwhelmingly use and keeps the box plausible and
    // positive (the layout only needs sane, non-negative geometry).
    var fontPx = parseFloat(getComputedStyle(s).fontSize) || rect.height || 0;
    var ascent = fontPx * 0.8;
    var descent = fontPx * 0.2;
    return {
      width: width,
      actualBoundingBoxLeft: 0,
      actualBoundingBoxRight: width,
      actualBoundingBoxAscent: ascent,
      actualBoundingBoxDescent: descent,
      fontBoundingBoxAscent: ascent,
      fontBoundingBoxDescent: descent,
    };
  }

  function patch(target) {
    var orig = target.measureText;
    if (!orig) return;
    function measureText(text) {
      var m = orig.call(this, text);
      try {
        // Repair when ANY metric is corrupt (noise drives them to ~0 or
        // negative); a healthy positive width with sane boxes is left alone.
        var corrupt = !(m.width > 0) ||
          !(m.actualBoundingBoxRight > 0) ||
          !(m.fontBoundingBoxAscent > 0);
        if (!corrupt) return m;
        var fixed = realMetrics(this.font, text);
        if (!fixed) return m;
        return new Proxy(m, {
          get: function (t, p) {
            return (p in fixed) ? fixed[p] : t[p];
          },
        });
      } catch (e) {}
      return m;
    }
    try {
      Object.defineProperty(measureText, 'name', { value: 'measureText' });
      measureText.toString = function () {
        return 'function measureText() { [native code] }';
      };
    } catch (e) {}
    try { target.measureText = measureText; } catch (e) {}
  }

  patch(proto);
  if (off && off.measureText) patch(off);
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
    (ext_dir / "measuretext.js").write_text(CONTENT_SCRIPT, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
