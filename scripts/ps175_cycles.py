"""PS-175 — does restore survive REPEATED restarts?

The single-restart case passes. `browser.sessionstore.resume_session_once` is a
ONCE pref: Firefox consumes it at startup and writes it back false. persona's
comment claims it is "re-armed through user.js on every launch", but the engine
applies prefs over the protocol AFTER startup and persists them into prefs.js —
there is no user.js. So the re-arm depends on prefs.js being flushed at
shutdown, and shutdown is fastShutdownStage=3 (_exit()).

This runs N launch/stop cycles over ONE profile and reports, for each cycle,
what the live page showed and what the pref on disk said. Everything is read
from the running browser and from disk — nothing asserts on a value we wrote.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.ps175_session_probe import (  # noqa: E402
    drain,
    launch,
    session_tab_urls,
    stop_like_production,
)

PREFS = (
    "browser.sessionstore.resume_session_once",
    "browser.startup.page",
    "toolkit.shutdown.fastShutdownStage",
)


def prefs_on_disk(profile_dir: str) -> dict:
    """Read the prefs Firefox actually has on disk (prefs.js, and user.js if
    one exists at all)."""
    out = {}
    for fname in ("prefs.js", "user.js"):
        path = os.path.join(profile_dir, fname)
        if not os.path.exists(path):
            out[fname] = "<absent>"
            continue
        text = open(path, encoding="utf-8", errors="replace").read()
        vals = {}
        for pref in PREFS:
            m = re.search(
                r'user_pref\("' + re.escape(pref) + r'",\s*([^)]+)\);', text
            )
            vals[pref] = m.group(1).strip() if m else "<unset>"
        out[fname] = vals
    return out


def main():
    cycles = int(os.environ.get("PS175_CYCLES", "4"))
    stop_timeout = float(os.environ.get("PS175_STOP_TIMEOUT", "2"))
    settle = float(os.environ.get("PS175_SETTLE", "8"))
    name = os.environ.get("PS175_PROFILE", "ps175-cycles")

    from src.core.config import DATA_DIR
    from src.services.browser.invisible_launch import get_ff_eval
    from src.services.profile.manager import ProfileManager

    page_dir = os.path.join(os.environ["PERSONA_HOME"], "pages")
    os.makedirs(page_dir, exist_ok=True)
    tab_url = "file://" + os.path.join(page_dir, "tab1.html")
    with open(os.path.join(page_dir, "tab1.html"), "w") as fh:
        fh.write("<html><head><title>PS175-TAB-ONE</title></head>"
                 "<body><h1>ps175 tab one</h1></body></html>")

    pm = ProfileManager()
    if name in pm.profiles:
        pm.delete_profile(name, permanent=True)
    assert pm.add_profile(name, proxy=None, os_type="windows", engine="firefox")

    data_dir = os.path.join(DATA_DIR, name)
    inner = os.path.join(data_dir, ".invisible-profile")

    print(f"\n=== PS-175 repeated-restart probe: {cycles} cycles, "
          f"stop_timeout={stop_timeout}s ===")

    results = []
    for cycle in range(1, cycles + 1):
        print(f"\n{'=' * 62}\n--- CYCLE {cycle} ---")
        print(f"  prefs BEFORE launch: "
              f"{json.dumps(prefs_on_disk(inner), indent=2)}")

        profile = ProfileManager().profiles[name]
        proc, sink, _, started = launch(profile, label=f"C{cycle}")
        if not started:
            print(f"  CYCLE {cycle}: no BROWSER_STARTED — unobtained reading")
            results.append((cycle, None, "no-start"))
            return 3

        time.sleep(4.0)
        hook = get_ff_eval(name)
        href = hook["eval"]("location.href") if hook else None
        title = hook["eval"]("document.title") if hook else None
        ntabs = hook["eval"]("1") if hook else None  # liveness ping
        print(f"  live page ON OPEN: href={href!r} title={title!r} "
              f"(hook alive={ntabs == 1})")

        restored = (title == "PS175-TAB-ONE")
        if cycle == 1:
            # First launch has nothing to restore — open the tab.
            hook["goto"](tab_url)
            time.sleep(2.0)
            print(f"  opened tab: {hook['eval']('document.title')!r}")
        else:
            results.append((cycle, restored, href))

        print(f"  settling {settle}s for the periodic sessionstore write...")
        time.sleep(settle)
        print(f"  session file before stop: "
              f"{json.dumps(session_tab_urls(inner))}")

        stop_like_production(proc, name, stop_timeout)
        drain(sink, 6.0, label=f"C{cycle}")
        time.sleep(2.0)
        print(f"  session file AFTER stop : "
              f"{json.dumps(session_tab_urls(inner))}")
        print(f"  prefs AFTER stop: {json.dumps(prefs_on_disk(inner), indent=2)}")

    print(f"\n{'=' * 62}\n=== VERDICT (restart cycles) ===")
    for cycle, restored, href in results:
        print(f"  restart {cycle - 1} (cycle {cycle}): "
              f"TABS RESTORED = {restored}   href={href}")
    all_ok = all(r for _, r, _ in results) if results else False
    print(f"\n  ALL RESTARTS RESTORED: {all_ok}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
