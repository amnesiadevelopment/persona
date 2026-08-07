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

CONTENT_SCRIPT = r"""
(function () {
  var proto = (window.CanvasRenderingContext2D || {}).prototype;
  var off = (window.OffscreenCanvasRenderingContext2D || {}).prototype;
  if (!proto || !proto.measureText) return;

  // The engine's constant noise scale (noised = true * factor), learned once
  // and then reused for every repair so no measurement ever touches the DOM.
  var factor = null;

  // One-shot, un-noised true width of `text` in `font`, via a throwaway DOM
  // node that is appended, measured and removed immediately — it never stays
  // resident, so it can't inherit the page's transitions or be rewritten in a
  // hot path. Called at most once (until `factor` is known). Returns null when
  // there's no DOM yet or the text renders to zero width.
  function trueWidth(font, text) {
    var root = document.documentElement || document.body;
    if (!root) return null;
    var span = document.createElement('span');
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

  function patch(target) {
    var orig = target.measureText;
    if (!orig) return;
    function measureText(text) {
      var m = orig.call(this, text);
      try {
        // The engine scales every metric by a tiny factor (~1e-6), so a
        // non-empty string comes back with |width| far below one pixel — the
        // sign of that factor varies with the fingerprint seed, so both a
        // near-zero negative AND a near-zero positive are noise. Empty text
        // legitimately measures zero, so only repair a non-empty string whose
        // width collapsed below a single pixel.
        var hasText = String(text).length > 0;
        var corrupt = hasText && !(Math.abs(m.width) >= 1);
        if (!corrupt) return m;
        if (factor === null) {
          var tw = trueWidth(this.font, text);
          if (tw === null) return m;
          var f = m.width / tw;
          if (!isFinite(f) || f === 0) return m;
          factor = f;
        }
        var scale = factor;
        // Undo the uniform noise: every numeric TextMetrics field is the true
        // value times `scale`, so dividing each by `scale` restores the real,
        // self-consistent geometry. Non-numeric members (e.g. toJSON) pass
        // through untouched, and a field the native object lacks is never
        // synthesised — so no new tell is introduced.
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
      // Mark for the native_ext Function.prototype.toString patch so a detector
      // calling Function.prototype.toString.call(measureText) reads native. A
      // plain measureText.toString override is bypassed by that .call form.
      Object.defineProperty(measureText, '__pnaName', { value: 'measureText' });
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
