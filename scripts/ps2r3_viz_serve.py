"""PS-2 round-2 visual-draft harness: seed a realistic home, then serve the REAL app.

Not test code and not shipped code — a throwaway on an unmerged viz/ branch.
It exists so the captured render shows the application under a plausible
working load (several profiles, a live Activity Log, an engines panel with
real rollback rows) instead of an empty first-run screen.

Everything it does is data setup and CAPTURE STAGING. No production code path
is special-cased and no design decision lives here: the bounded-status fix
being judged is in src/ui/app.py, on the branch.

THE ONE THING THIS ROUND ADDS is a control file. The steering asks to see an
engine row whose status is (a) short, (b) long enough to truncate and (c) a
multi-line error. Those are states the engine check produces on the OWNER'S
machine and cannot produce in this container (no engine binaries, no network),
so the harness writes them into the app's own status fields — the same
attributes the real check assigns — and lets the real panel render them.
That is mocking the DATA, not the design.
"""

from __future__ import annotations

import json
import os
import sys
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

EVENTS = [
    "Engine chromium 148.0.7778.215 available",
    "Downloading engine chromium 148.0.7778.215",
    "Engine updated to chromium 148.0.7778.215",
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

    from src.core.logging import SESSION_MARKER

    log_path = os.path.join(
        home, "logs", datetime.now().strftime("persona_%Y%m%d.log")
    )
    t = datetime.now() - timedelta(seconds=len(EVENTS) * 7)
    lines = [
        f"{t.strftime('%Y-%m-%d %H:%M:%S')} - INFO - persona.app - "
        f"{SESSION_MARKER} 3.0.1 =========="
    ]
    for msg in EVENTS:
        t += timedelta(seconds=7)
        lines.append(
            f"{t.strftime('%Y-%m-%d %H:%M:%S')} - INFO - persona.api - {msg}"
        )
    with open(log_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


PATCH = '''
import json as _json, os as _os, threading as _th, time as _t
from src.ui.app import App

App._show_startup_notice = lambda self: None

# This container has no engine binaries and no network. The real bootstrap
# would flood the Activity Log with download-retry and install-failure lines
# that are a property of the CAPTURE ENVIRONMENT, not of the design.
for _n in [n for n in dir(App) if n.startswith("_ensure_engine")
           or n.startswith("_check_engine") or n.startswith("_auto_update_engine")
           or n.startswith("_check_app_update") or n == "_check_both_engines"
           or n == "_check_engines_periodic"]:
    try:
        setattr(App, _n, lambda self, *a, **k: None)
    except Exception:
        pass

# Engine STATE, mocked at the service layer so the real panel renders real
# rows. The rollback rows are the grey labels this round bounds, so they must
# actually be on screen for the capture to prove anything.
from src.services.engine import updater as _eng
_eng.current_version = lambda: "148.0.7778.215"
_eng.is_installed = lambda: True
_eng.current_build_recorded = lambda: True

from src.services.browser import invisible_launch as _inv
_inv.is_invisible_installed = lambda: True

# THE ROLLBACK STATE, driven from the control file.
#
# These are the REAL build identifiers the owner's machine produces, not
# convenient short ones — a Firefox build reads
# `firefox-20_151.0_20260817150018` (30 chars) and that string is the whole
# complaint: it is what the old label interpolated into a 200px rail. Driving
# a short stand-in here would make the fix look like it worked when it had
# never been put under load.
_CHROME_BUILD = "chromium-148.0.7778.203-linux64"
_FF_BUILD = "firefox-20_151.0_20260817150018"

# state: "retained" | "pinned" | "empty" — the three the row can be in, of
# which only one is ever on screen at a time.
_ROLL = {"chromium": "retained", "firefox": "retained"}

_eng.pinned_build = lambda: (
    _CHROME_BUILD if _ROLL["chromium"] == "pinned" else ""
)
_eng.rollback_target = lambda: (
    (_CHROME_BUILD, "/x") if _ROLL["chromium"] == "retained" else ("", "")
)
_inv.pinned_build = lambda: (
    _FF_BUILD if _ROLL["firefox"] == "pinned" else ""
)
_inv.rollback_target = lambda: (
    _FF_BUILD if _ROLL["firefox"] == "retained" else ""
)

# The "nothing retained" case must render the EMPTY row, not the Chromium
# "rollback available after the next update" explainer — those are different
# states and the steering asks for the empty one. is_installed() False is the
# path that renders nothing at all.
_eng.is_installed = lambda: _ROLL["chromium"] != "empty"

_HOME = _os.environ["PERSONA_HOME"]
_CTL = _os.path.join(_HOME, "viz_control.json")

_FEED = [
    "Launching shop-us-01",
    "shop-us-01: proxy us-res-14 reached, 207ms",
    "Loaded 6 bookmarks, 0 pools for shop-us-01",
    "Browser started!",
    "mail-warm-12: cookie jar synced, 318 entries",
    "scrape-uk-21: LAUNCH_FAILED: engine firefox-142 missing",
    "Engine chromium 148.0.7778.215 available",
    "qa-sandbox: fingerprint seed frozen",
    "Session ended: shop-us-02",
    "Exported 8 profiles to /tmp/persona-export.json",
]

_holder = {}
_orig_init = App.__init__

def _init(self, *a, **k):
    _orig_init(self, *a, **k)
    _holder["gui"] = self

App.__init__ = _init

def _apply_control(gui, ctl):
    """Write the requested status strings into the app's OWN fields.

    These are the same attributes _check_engine2_async and _record_engine_check
    assign on a real machine — the harness supplies the VALUE the owner's
    engine check produced, and the shipped panel decides how to render it.
    """
    if "engines_open" in ctl:
        gui._engines_open = bool(ctl["engines_open"])
    # The rollback STATE of each engine — "retained" | "pinned" | "empty".
    # Only one of the three is ever on screen, so the capture has to drive
    # them one at a time; this flips the service-layer answers the shipped
    # row reads, and the row decides what to render.
    if "rollback" in ctl:
        _ROLL.update(ctl["rollback"])
    if "engine_status" in ctl:
        gui._engine_status = ctl["engine_status"]
        gui.engine_text.value = ctl["engine_status"] or "148.0.7778.215"
    if "engine2_status" in ctl:
        gui._engine2_status = ctl["engine2_status"]
    if "engine_expanded" in ctl:
        gui._engine_status_expanded = bool(ctl["engine_expanded"])
    if "engine2_expanded" in ctl:
        gui._engine2_status_expanded = bool(ctl["engine2_expanded"])
    gui._refresh_sidebar()
    gui._safe_update()

def _drive():
    _t.sleep(18)
    i = 0
    seen = None
    while True:
        _t.sleep(2)
        gui = _holder.get("gui")
        if gui is None:
            continue
        # control file -> engine status states
        try:
            st = _os.path.getmtime(_CTL)
            if st != seen:
                seen = st
                with open(_CTL) as fh:
                    ctl = _json.load(fh)
                gui._ui(lambda: _apply_control(gui, ctl))
        except Exception:
            pass
        # live event feed, so "events are arriving" is a real property
        if i % 3 == 0:
            try:
                if gui.state.add_log(_FEED[(i // 3) % len(_FEED)]):
                    gui.state.schedule_refresh()
            except Exception:
                pass
        i += 1

_th.Thread(target=_drive, daemon=True).start()
'''


def main() -> None:
    home = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ps2r3-viz-home"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8561
    os.environ["PERSONA_HOME"] = home
    seed(home)

    import subprocess

    from tests.ui_driver.server import _CHILD

    script = _CHILD.format(home=home, repo=REPO, port=port, patch=PATCH)
    proc = subprocess.Popen([sys.executable, "-c", script], cwd=REPO)
    print(f"serving http://127.0.0.1:{port}/  home={home}  pid={proc.pid}")
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()


if __name__ == "__main__":
    main()
