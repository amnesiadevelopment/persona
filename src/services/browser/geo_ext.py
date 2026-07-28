import json
import pathlib

# The script is wrapped in an IIFE so none of its names land in the page's global
# scope. It runs in the MAIN world on every frame; a bare top-level const/function
# becomes a real page global, and when the page's own bundle later declares (or
# re-evaluates a module declaring) the same identifier the page throws "Identifier
# '…' has already been declared" and that script dies. Google Sheets hit exactly
# that — its calc worker / date-picker module aborted, so the sheet stuck on
# "Working" and the calendar never opened, but ONLY with a proxy, because this
# geolocation override is loaded only for a proxied profile (#233).
CONTENT_SCRIPT = """\
(function () {{
  const LAT = {lat};
  const LON = {lon};
  const ACC = 100;
  function pos() {{
    return {{
      coords: {{
        latitude: LAT, longitude: LON, accuracy: ACC,
        altitude: null, altitudeAccuracy: null, heading: null, speed: null,
      }},
      timestamp: Date.now(),
    }};
  }}
  const geo = navigator.geolocation;
  if (geo) {{
    geo.getCurrentPosition = function (success) {{
      if (typeof success === "function") success(pos());
    }};
    geo.watchPosition = function (success) {{
      if (typeof success === "function") success(pos());
      return 0;
    }};
    geo.clearWatch = function () {{}};
  }}
}})();
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
    (ext_dir / "geo.js").write_text(
        CONTENT_SCRIPT.format(lat=json.dumps(lat), lon=json.dumps(lon)),
        encoding="utf-8",
    )
    (ext_dir / "manifest.json").write_text(
        json.dumps(MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
