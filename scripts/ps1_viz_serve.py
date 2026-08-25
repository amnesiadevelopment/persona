"""PS-1 visual-draft harness: seed a realistic home, then serve the REAL app.

Not test code and not shipped code — a throwaway on an unmerged viz/ branch.
It exists so the captured render shows the application under a plausible
working load (several profiles, a live-looking Activity Log) instead of an
empty first-run screen, which is what the owner is being asked to judge.

Everything it does is data setup: it writes profiles.json and a persona_*.log
into an isolated PERSONA_HOME and then hands off to the repo's own
tests/ui_driver.serve_app. No production code path is special-cased.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

PROFILES = [
    ("shop-us-01", "windows", "chromium", "us-res-14", ["retail", "prod"]),
    ("shop-us-02", "windows", "chromium", "us-res-14", ["retail", "prod"]),
    ("shop-de-03", "windows", "firefox", "de-res-02", ["retail", "eu"]),
    ("mail-warm-11", "macos", "chromium", "us-mob-07", ["warmup"]),
    ("mail-warm-12", "macos", "chromium", "us-mob-07", ["warmup"]),
    ("scrape-uk-21", "linux", "firefox", "uk-dc-31", ["scrape"]),
    ("scrape-uk-22", "linux", "firefox", "uk-dc-31", ["scrape"]),
    ("qa-sandbox", "windows", "chromium", None, ["qa"]),
]

# A realistic session: a bulk launch of several profiles, an engine update, a
# failure, a session end. These are the message shapes src/ui/log_format.py
# already colours, so the render exercises the real colouring rules.
EVENTS = [
    "Engine chromium 128.0.6613.86 available",
    "Downloading engine chromium 128.0.6613.86",
    "Engine updated to chromium 128.0.6613.86",
    "Bookmarks pool 'retail-seed' imported (42 entries)",
    "Launching shop-us-01",
    "shop-us-01: proxy us-res-14 reached, 214ms",
    "Loaded 6 bookmarks, 0 pools for shop-us-01",
    "Browser started!",
    "Launching shop-us-02",
    "shop-us-02: proxy us-res-14 reached, 231ms",
    "Browser started!",
    "Launching shop-de-03",
    "shop-de-03: LAUNCH_FAILED: proxy de-res-02 refused the connection",
    "Launching mail-warm-11",
    "mail-warm-11: proxy us-mob-07 reached, 402ms",
    "Loaded 12 bookmarks, 1 pool for mail-warm-11",
    "Browser started!",
    "Launching mail-warm-12",
    "mail-warm-12: proxy us-mob-07 reached, 389ms",
    "Browser started!",
    "Launching scrape-uk-21",
    "scrape-uk-21: proxy uk-dc-31 reached, 88ms",
    "Browser started!",
    "Certificate 'internal-ca' installed for scrape-uk-21",
    "Launching scrape-uk-22",
    "scrape-uk-22: proxy uk-dc-31 reached, 91ms",
    "Browser started!",
    "shop-us-01: fingerprint seed frozen, hardware generation apple-m2",
    "Session ended: shop-de-03",
    "Exported 3 profiles to /tmp/persona-export.json",
    "qa-sandbox: cookie jar cleared",
    "Launching qa-sandbox",
    "qa-sandbox: proxy none, direct connection",
    "Browser started!",
]


def seed(home: str) -> None:
    os.makedirs(home, exist_ok=True)
    os.makedirs(os.path.join(home, "logs"), exist_ok=True)

    profiles = {}
    for name, os_type, engine, proxy, tags in PROFILES:
        profiles[name] = {
            "name": name,
            "os_type": os_type,
            "engine": engine,
            "proxy": proxy,
            "tags": tags,
            "device_type": "desktop",
            "search_engine": "duckduckgo",
            "notes": "",
            "bookmarks": [],
            "resolution": "auto",
            "fingerprint_seed_value": abs(hash(name)) % (2**32),
        }
    with open(os.path.join(home, "profiles.json"), "w") as fh:
        json.dump(profiles, fh, indent=2)

    # The Activity Log seeds itself from the newest persona_*.log, starting at
    # the last SESSION_MARKER (src/ui/state.py::_load_recent_log_lines), so the
    # file has to carry that marker and then the events in its own format.
    from src.core.logging import SESSION_MARKER

    log_path = os.path.join(
        home, "logs", datetime.now().strftime("persona_%Y%m%d.log")
    )
    t = datetime.now() - timedelta(seconds=len(EVENTS) * 7)
    lines = [
        f"{t.strftime('%Y-%m-%d %H:%M:%S')} - INFO - persona.app - "
        f"{SESSION_MARKER} 3.0.0 =========="
    ]
    for msg in EVENTS:
        t += timedelta(seconds=7)
        lines.append(
            f"{t.strftime('%Y-%m-%d %H:%M:%S')} - INFO - persona.api - {msg}"
        )
    with open(log_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


# Harness-only patch, executed in the served child BEFORE the App is built.
# Three jobs, all of them staging the CAPTURE — none of them design:
#
#   1. Silence the startup notice. The 3.0.0 changelog dialog owns the screen
#      on the first frame and would be the only thing in every capture.
#   2. Silence the engine bootstrap. This container has no engine binaries and
#      no way to fetch them, so the real bootstrap floods the Activity Log with
#      download-retry and install-failure lines. Those are a property of the
#      CAPTURE ENVIRONMENT, not of the design being reviewed, and left in they
#      would be most of what the owner sees in the log he is judging.
#   3. Drive a slow live event feed, so "events are arriving" is a real
#      property of the running app rather than a still frame. The collapsed
#      strip has to prove it still reports arrivals, and it cannot prove that
#      against a frozen log.
PATCH = """
import threading as _th, time as _t
from src.ui.app import App

App._show_startup_notice = lambda self: None
for _name in [n for n in dir(App) if n.startswith("_ensure_engine")]:
    setattr(App, _name, lambda self, *a, **k: None)

_FEED = [
    "Launching shop-us-01",
    "shop-us-01: proxy us-res-14 reached, 207ms",
    "Loaded 6 bookmarks, 0 pools for shop-us-01",
    "Browser started!",
    "mail-warm-12: cookie jar synced, 318 entries",
    "scrape-uk-21: LAUNCH_FAILED: engine firefox-142 missing",
    "Engine chromium 128.0.6613.86 available",
    "qa-sandbox: fingerprint seed frozen",
    "Session ended: shop-us-02",
    "Exported 8 profiles to /tmp/persona-export.json",
]

# The App instance is built by the harness AFTER this patch runs, so the feed
# thread cannot be handed one: it waits for __init__ to publish it instead.
_holder = {}
_orig_init = App.__init__

def _init(self, *a, **k):
    _orig_init(self, *a, **k)
    _holder["gui"] = self

App.__init__ = _init

def _drive():
    _t.sleep(25)
    i = 0
    while True:
        _t.sleep(5)
        gui = _holder.get("gui")
        if gui is None:
            continue
        try:
            if gui.state.add_log(_FEED[i % len(_FEED)]):
                gui.state.schedule_refresh()
            i += 1
        except Exception:
            pass

_th.Thread(target=_drive, daemon=True).start()
"""


def main() -> None:
    home = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ps1-viz-home"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8551
    os.environ["PERSONA_HOME"] = home
    seed(home)

    from tests.ui_driver.server import _CHILD
    import subprocess

    script = _CHILD.format(home=home, repo=REPO, port=port, patch=PATCH)
    proc = subprocess.Popen([sys.executable, "-c", script], cwd=REPO)
    print(f"serving http://127.0.0.1:{port}/  home={home}  pid={proc.pid}")
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()


if __name__ == "__main__":
    main()
