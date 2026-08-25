"""PS-175 — does an ENGINE BUILD BUMP lose the session?

The plain restart restores fine (4/4 cycles). This probe tests the condition the
plain restart does NOT cover, and which the ticket says is routine ("the engine
autobumps on a schedule"):

  _migrate_profile_for_engine_build -> _reset_prefs_on_engine_build_change
  DELETES prefs.js when the profile's compatibility.ini names a different
  firefox-NN than the engine about to launch. It then re-writes only
  _WARMUP_CHROME_PREFS, which contains NO session prefs.

  Firefox decides whether to restore AT STARTUP, from prefs.js. persona's
  session prefs travel over the juggler protocol and are applied AFTER startup
  (the engine's own launcher.py comment documents this: writePreferences/user.js
  is not on the Juggler path). So the first launch after a bump starts with
  the session prefs ABSENT.

The bump is simulated exactly the way the production check reads it: by
rewriting the profile's own compatibility.ini LastPlatformDir to a different
firefox-NN. Forward direction only (19 -> 20), so this exercises the prefs.js
reset and NOT the downgrade guard.

Verdict is read from the live page after the restart, never from a pref we wrote.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.ps175_cycles import prefs_on_disk  # noqa: E402
from scripts.ps175_session_probe import (  # noqa: E402
    drain,
    launch,
    session_tab_urls,
    stop_like_production,
)


def fake_engine_bump(profile_dir: str) -> str:
    """Make the profile look like it was last opened by an OLDER engine build,
    which is what an autobump leaves behind. Rewrites only LastPlatformDir's
    firefox-NN token — the exact field _profile_last_engine_dir reads."""
    path = os.path.join(profile_dir, "compatibility.ini")
    text = open(path, encoding="utf-8", errors="replace").read()
    before = text
    # firefox-20_151.0_... -> firefox-19_151.0_...  (older build, forward bump)
    text = text.replace("firefox-20", "firefox-19")
    assert text != before, "compatibility.ini did not name firefox-20"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def main():
    stop_timeout = float(os.environ.get("PS175_STOP_TIMEOUT", "2"))
    settle = float(os.environ.get("PS175_SETTLE", "8"))
    name = os.environ.get("PS175_PROFILE", "ps175-bump")
    simulate_bump = os.environ.get("PS175_NO_BUMP") != "1"

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

    print(f"\n=== PS-175 engine-bump probe (simulate_bump={simulate_bump}) ===")

    # ---- launch 1: open a tab -------------------------------------------
    print("\n--- LAUNCH 1 ---")
    profile = ProfileManager().profiles[name]
    proc, sink, _, started = launch(profile, label="L1")
    if not started:
        print("RESULT: no BROWSER_STARTED — unobtained reading")
        return 3
    hook = get_ff_eval(name)
    hook["goto"](tab_url)
    time.sleep(2.0)
    print(f"  opened tab: {hook['eval']('document.title')!r}")
    time.sleep(settle)
    stop_like_production(proc, name, stop_timeout)
    drain(sink, 6.0, label="L1")
    time.sleep(2.0)
    print(f"  session on disk after stop: {json.dumps(session_tab_urls(inner))}")
    print(f"  prefs after stop: {json.dumps(prefs_on_disk(inner), indent=2)}")

    # ---- simulate the autobump -------------------------------------------
    if simulate_bump:
        print("\n--- SIMULATED ENGINE BUMP (compatibility.ini firefox-20 -> 19) ---")
        p = fake_engine_bump(inner)
        print(f"  rewrote {p}")
        print("  (the launch below sees a build change and resets prefs.js)")

    # ---- launch 2: the restart after the bump ----------------------------
    print("\n--- LAUNCH 2 (restart) ---")
    profile = ProfileManager().profiles[name]
    proc2, sink2, lines2, started2 = launch(profile, label="L2")
    if not started2:
        print("RESULT: no BROWSER_STARTED on relaunch — unobtained reading")
        return 3
    migrated = [ln for ln in lines2 if "ENGINE_BUILD" in ln]
    print(f"  migration lines: {migrated or '<none>'}")
    time.sleep(4.0)
    hook2 = get_ff_eval(name)
    href = hook2["eval"]("location.href") if hook2 else None
    title = hook2["eval"]("document.title") if hook2 else None
    print(f"  live page after restart: href={href!r} title={title!r}")
    print(f"  prefs at this point: {json.dumps(prefs_on_disk(inner), indent=2)}")

    stop_like_production(proc2, name, stop_timeout)
    drain(sink2, 5.0, label="L2")

    print("\n=== VERDICT ===")
    restored = (title == "PS175-TAB-ONE")
    print(f"  tab opened before restart : {tab_url}")
    print(f"  live page after restart   : {href}")
    print(f"  TABS RESTORED: {restored}")
    if simulate_bump and not restored:
        print("\n  >>> ENGINE BUMP LOSES THE SESSION <<<")
    return 0 if restored else 1


if __name__ == "__main__":
    sys.exit(main())
