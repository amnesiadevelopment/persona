"""PS-150: prove persona's geo extension REACHES THE PAGE on the verify tier.

The PS-78 rule: an assertion that a builder was called is not evidence the
spoof reached the page. PS-150's arm B installed ``build_geo_extension`` in
DENY mode and pixelscan's verdicts did not move — but NO checker in the matrix
reads geolocation at all (grep: zero hits for getCurrentPosition across
``verify/checkers.py`` and ``verify/local_probe.py``), so an unmoved verdict
there is equally consistent with "the extension landed and pixelscan does not
care" and with "the extension never landed". Those are different findings and
only a direct observation separates them.

So this observes the vector the checkers do not: a LOOPBACK page that calls
``navigator.geolocation.getCurrentPosition`` and renders the outcome as text,
read back through the tier's own ``inner_text``. No exit, no credential, no
checker.

Run it from the repo root:

    xvfb-run -a .venv/bin/python -m scripts.ps150_geo_reached
"""

from __future__ import annotations

import http.server
import json
import threading

PAGE = """<!doctype html>
<html><body><pre id="out">PENDING</pre>
<script>
function done(t) { document.getElementById('out').textContent = t; }
if (!navigator.geolocation) {
  done('NO_GEOLOCATION_OBJECT');
} else {
  // A real host answers with coords or with the PROMPT-denial; persona's
  // DENY-mode extension answers PERMISSION_DENIED (code 1) synthetically.
  navigator.geolocation.getCurrentPosition(
    function (p) { done('POSITION ' + p.coords.latitude + ',' + p.coords.longitude); },
    function (e) { done('ERROR code=' + e.code + ' message=' + e.message); },
    { timeout: 5000 }
  );
  setTimeout(function () {
    if (document.getElementById('out').textContent === 'PENDING') {
      done('TIMED_OUT_NO_CALLBACK');
    }
  }, 6000);
}
</script></body></html>
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib naming
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args, **kwargs):
        pass


def _read(include_geo: bool, url: str) -> tuple[str, tuple]:
    from src.services.verify.chromium_tier import ChromiumSession

    session = ChromiumSession(
        "",
        seed=9001,
        declared_machine="windows",
        allow_no_proxy=True,
        allow_unsandboxed=True,
        include_geo=include_geo,
    )
    with session as live:
        installed = session.layer_report.installed
        page = live.new_page() if hasattr(live, "new_page") else live
        page.goto(url, timeout=30000, wait_until="load")
        import time

        time.sleep(8)
        return page.inner_text("body").strip(), installed


def main() -> int:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

    out = {}
    for flag in (False, True):
        try:
            text, installed = _read(flag, url)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            text, installed = f"FAILED {type(exc).__name__}: {exc}", ()
        out[f"include_geo={flag}"] = {
            "geolocation_result": text,
            "geo_in_installed": "geo" in installed,
            "installed_count": len(installed),
        }
        print(f"include_geo={flag}: {text}  (geo installed: {'geo' in installed})")

    srv.shutdown()
    print()
    print(json.dumps(out, indent=2))
    a = out["include_geo=False"]["geolocation_result"]
    b = out["include_geo=True"]["geolocation_result"]
    print()
    print("MOVED" if a != b else "UNMOVED", "— the geo vector",
          "REACHES the page" if a != b else "did NOT change the page")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
