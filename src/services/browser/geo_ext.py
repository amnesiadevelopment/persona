import json
import pathlib

from .worker_wrap import realm_bootstrap_js

# The script is wrapped in an IIFE so none of its names land in the page's global
# scope. It runs in the MAIN world on every frame; a bare top-level const/function
# becomes a real page global, and when the page's own bundle later declares (or
# re-evaluates a module declaring) the same identifier the page throws "Identifier
# '…' has already been declared" and that script dies. Google Sheets hit exactly
# that — its calc worker / date-picker module aborted, so the sheet stuck on
# "Working" and the calendar never opened, but ONLY with a proxy, because this
# geolocation override is loaded only for a proxied profile (#233).
#
# geolocation is Window-only (no WorkerNavigator.geolocation), so the leak vector
# is a fresh about:blank/srcdoc child frame whose pristine navigator.geolocation
# returns the real coords. The shared recursive registry carries applyGeoPatch
# into every nested child frame. LAT/LON live INSIDE the leaf so its .toString()
# re-derives them in each realm.
CONTENT_SCRIPT = r"""
(function () {
  function applyGeoPatch(G) {
   try {
    if (!G || !G.navigator || G.__personaGeo) return;
    G.__personaGeo = true;
    var LAT = __LAT__;
    var LON = __LON__;
    var ACC = 100;
    function pos() {
      return {
        coords: {
          latitude: LAT, longitude: LON, accuracy: ACC,
          altitude: null, altitudeAccuracy: null, heading: null, speed: null,
        },
        timestamp: Date.now(),
      };
    }
    // Mark each override for the native_ext Function.prototype.toString patch so
    // a detector calling Function.prototype.toString.call(fn) reads native code.
    function mark(fn, name) {
      try { Object.defineProperty(fn, "__pnaName", { value: name }); } catch (e) {}
      try { Object.defineProperty(fn, "name", { value: name }); } catch (e) {}
      return fn;
    }
    var geo = G.navigator.geolocation;
    if (geo) {
      geo.getCurrentPosition = mark(function (success) {
        if (typeof success === "function") success(pos());
      }, "getCurrentPosition");
      geo.watchPosition = mark(function (success) {
        if (typeof success === "function") success(pos());
        return 0;
      }, "watchPosition");
      geo.clearWatch = mark(function () {}, "clearWatch");
    }
   } catch (e) {}
  }
__GEO_REALM_BOOTSTRAP__
})();
"""

MANIFEST = {
    "manifest_version": 3,
    "name": "persona-geo",
    "version": "1.0",
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["geo.js"],
            "run_at": "document_start",
            "all_frames": True,
            "world": "MAIN",
        }
    ],
}


def build_geo_extension(lat: float, lon: float, base_dir: str) -> str:
    """Generate an unpacked extension that pins navigator.geolocation to the
    given coordinates, so a site's location matches the proxy's exit IP.

    A JS-layer override (not native): a site inspecting getCurrentPosition's
    source could tell it was replaced. Acceptable for manual use; the
    alternative (CDP) needs a live connection per tab.
    """
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    js = (
        CONTENT_SCRIPT
        .replace("__LAT__", json.dumps(lat))
        .replace("__LON__", json.dumps(lon))
        .replace("__GEO_REALM_BOOTSTRAP__", realm_bootstrap_js("applyGeoPatch"))
    )
    (ext_dir / "geo.js").write_text(js, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
