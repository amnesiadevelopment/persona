"""PS-175 — does the panic wipe actually destroy the session file?

The ticket requires this answered in the record either way, because a session
file records URLs and the wipe exists to remove exactly that kind of trace. It
cites PS-142, where profile NAMES survived a wipe that exists to remove them —
so this is measured on the real path, not argued from the code.

Drives: real launch -> open a tab -> stop -> confirm the session file is on disk
WITH the URL in it -> real wipe_all_profiles() -> look again.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.ps175_session_probe import (  # noqa: E402
    drain,
    launch,
    session_tab_urls,
    stop_like_production,
)


def scan_for_url(root: str, needle: str):
    """Any file under root whose bytes contain the URL, plus every surviving
    session artifact. Catches residue the tidy per-file check would miss."""
    hits = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            try:
                with open(p, "rb") as fh:
                    if needle.encode() in fh.read():
                        hits.append(p)
            except OSError:
                pass
    return hits


def main():
    name = os.environ.get("PS175_PROFILE", "ps175-wipe")
    settle = float(os.environ.get("PS175_SETTLE", "8"))

    from src.core.config import DATA_DIR
    from src.services.browser.invisible_launch import get_ff_eval
    from src.services.profile.manager import ProfileManager

    page_dir = os.path.join(os.environ["PERSONA_HOME"], "pages")
    os.makedirs(page_dir, exist_ok=True)
    # A distinctive URL so a substring scan cannot match by accident.
    tab_name = "ps175-secret-destination.html"
    tab_url = "file://" + os.path.join(page_dir, tab_name)
    with open(os.path.join(page_dir, tab_name), "w") as fh:
        fh.write("<html><head><title>PS175-WIPE-TAB</title></head></html>")

    pm = ProfileManager()
    if name in pm.profiles:
        pm.delete_profile(name, permanent=True)
    assert pm.add_profile(name, proxy=None, os_type="windows", engine="firefox")

    data_dir = os.path.join(DATA_DIR, name)
    inner = os.path.join(data_dir, ".invisible-profile")

    print("\n=== PS-175 panic-wipe vs session file ===")

    profile = ProfileManager().profiles[name]
    proc, sink, _, started = launch(profile, label="W")
    if not started:
        print("RESULT: no BROWSER_STARTED — unobtained reading")
        return 3
    hook = get_ff_eval(name)
    hook["goto"](tab_url)
    time.sleep(2.0)
    print(f"  opened: {hook['eval']('document.title')!r}")
    time.sleep(settle)
    stop_like_production(proc, name, 2)
    drain(sink, 6.0, label="W")
    time.sleep(2.0)

    before = session_tab_urls(inner)
    print(f"\n  session file BEFORE wipe: {json.dumps(before, indent=2)}")
    hits_before = scan_for_url(data_dir, tab_name)
    print(f"  files under the profile containing the URL: {len(hits_before)}")
    for h in hits_before[:6]:
        print(f"    - {os.path.relpath(h, data_dir)}")

    premise = any(isinstance(v, list) and v for v in before.values())
    if not premise:
        print("\n  PREMISE FAILED: no session on disk to begin with — this run "
              "cannot say anything about the wipe.")
        return 3

    # ---- the real panic wipe --------------------------------------------
    print("\n--- wipe_all_profiles() ---")
    removed = ProfileManager().wipe_all_profiles()
    print(f"  wiped {removed} profile(s)")
    time.sleep(1.0)

    print(f"\n  profile data dir still exists: {os.path.exists(data_dir)}")
    after = session_tab_urls(inner) if os.path.exists(inner) else "<dir gone>"
    print(f"  session file AFTER wipe: {json.dumps(after, indent=2)}")
    hits_after = scan_for_url(DATA_DIR, tab_name) if os.path.exists(DATA_DIR) else []
    print(f"  files under DATA_DIR containing the URL after the wipe: "
          f"{len(hits_after)}")
    for h in hits_after[:10]:
        print(f"    - {h}")

    print("\n=== VERDICT ===")
    covered = (not os.path.exists(data_dir)) and not hits_after
    print(f"  session existed before the wipe : True")
    print(f"  profile data dir gone after     : {not os.path.exists(data_dir)}")
    print(f"  URL residue under DATA_DIR after: {len(hits_after)}")
    print(f"  PANIC WIPE COVERS THE SESSION FILE: {covered}")
    return 0 if covered else 1


if __name__ == "__main__":
    sys.exit(main())
