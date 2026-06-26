"""Standalone launcher for a Camoufox (Firefox/Juggler) profile.

Run as a subprocess (so persona's GUI isn't blocked and stop is uniform with the
Chromium path). Reads a JSON config on argv[1] describing the profile, launches
Camoufox headful with the mapped options, and blocks until killed — Camoufox is
a Python context manager that lives in-process, so this script IS the browser
process. persona terminates it like any other engine subprocess.

Camoufox spoofs at the Firefox C++ level and drives via Juggler (not CDP), so a
profile launched this way has no navigator.webdriver / CDP automation tells —
the gap the Chromium+extension engine can't close.
"""

import json
import sys
import time
from urllib.parse import urlparse


def _proxy_dict(proxy_url: str) -> dict | None:
    if not proxy_url:
        return None
    p = urlparse(proxy_url if "://" in proxy_url else "socks5://" + proxy_url)
    scheme = p.scheme or "socks5"
    d = {"server": f"{scheme}://{p.hostname}:{p.port}"}
    if p.username:
        d["username"] = p.username
    if p.password:
        d["password"] = p.password
    return d


def main() -> None:
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    from camoufox.sync_api import Camoufox

    os_map = {"android": "linux", "ios": "macos"}
    os_type = cfg.get("os_type", "windows")
    cam_os = os_map.get(os_type, os_type)
    if cam_os not in ("windows", "macos", "linux"):
        cam_os = "windows"

    opts: dict = {
        "headless": False,
        "os": cam_os,
        "humanize": True,
        "block_webrtc": True,  # no WebRTC IP leak past the proxy
        "i_know_what_im_doing": True,
    }
    proxy = _proxy_dict(cfg.get("proxy_url", ""))
    if proxy:
        opts["proxy"] = proxy
        # derive timezone/locale/geo from the proxy exit IP automatically
        opts["geoip"] = True
    screen = cfg.get("screen")  # {"width":..,"height":..} optional
    if screen:
        opts["screen"] = screen

    with Camoufox(**opts) as browser:
        page = browser.new_page()
        try:
            page.goto(cfg.get("start_url", "about:blank"), timeout=60000)
        except Exception:
            pass
        # signal readiness to persona's launcher (same protocol as the Chromium
        # wrapper) so the UI flips to "running".
        print("BROWSER_STARTED", flush=True)
        # keep the browser alive until the user closes the window or persona
        # kills the process.
        while True:
            try:
                if not browser.contexts or not browser.contexts[0].pages:
                    break
                time.sleep(1)
            except Exception:
                break
    print("BROWSER_CLOSED", flush=True)


if __name__ == "__main__":
    main()
