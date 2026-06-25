"""MAIN-world extension that repairs Canvas measureText().

The fingerprint engine adds noise to Canvas::measureText() (a Bromite
fingerprinting feature), which scales the returned width to a near-zero / tiny
value. Layout-heavy web apps that measure text to position it — Google Sheets'
canvas grid is the canonical victim — then lay glyphs out against a width of ~0
and the text overlaps into adjacent columns ("looks right for a frame, then
shifts").

getClientRects()/getBoundingClientRect() are NOT noised in this build, so we
recover the true text width by measuring the same string in a hidden DOM node
and return that from measureText. This keeps every other anti-fingerprint
defense intact (canvas readback noise, audio, webgl, …) while making text
measurement correct — which is also *more* natural, since a negative text width
is itself a blatant anti-detect tell.
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

  function patch(target) {
    var orig = target.measureText;
    if (!orig) return;
    function measureText(text) {
      var m = orig.call(this, text);
      try {
        var s = ensureSpan();
        if (!s) return m;
        s.style.font = this.font;
        s.style.letterSpacing = '0px';
        s.textContent = String(text);
        var w = s.getBoundingClientRect().width;
        // Only override when the native width is clearly corrupt (noise drives
        // it to ~0 or negative); leave legitimate values untouched.
        if (!(m.width > 0) || Math.abs(m.width - w) > 1) {
          return new Proxy(m, {
            get: function (t, p) { return p === 'width' ? w : t[p]; },
          });
        }
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
