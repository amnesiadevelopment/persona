"""PS-341 — the COOKIE 2x2, because the first two readings did not agree.

The main run read the sentinel cookie as ABSENT on the older build, which looks
like a downgrade eating an operator's session. Two later controls refused to let
that stand:

  * written on 148, read back on 148 (SAME build, no build change at all) —
    ALSO absent. So "the downgrade lost it" was never the only explanation.
  * written on 148, then read on 152 after going forward — PRESENT, with the
    expiry stamp of the 148 write. So it was not destroyed either.

Those two together point at a completely different claim from the one the main
run suggested: that **build 148 never serves a persisted cookie in this
container, in either direction**, which is a fact about that build (or about
this host) and NOT about a build CHANGE. This file settles it by filling in the
one cell nothing had measured — write on 152, restart on 152 — and by re-reading
the SQLite row each time so "the jar is empty" and "the row is gone" stay
distinguishable.

⛔ WHY THIS MATTERS MORE THAN THE HEADLINE: a difference observed ACROSS a build
change is only attributable to the change if the same difference does NOT appear
WITHIN one build. That is the whole discipline this project's own QA memory
states — run the measurement twice against unchanged code before a "moved"
result means anything.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from scripts.ps341_run import _install_sandbox_waiver, _serve_origin  # noqa: E402

OUT = os.environ.get("PS341_COOKIE_OUT", "/tmp/ps341/cookie_matrix.json")


def _rows(profile_dir: str) -> list:
    db = os.path.join(profile_dir, "Default", "Cookies")
    if not os.path.exists(db):
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return [
            {
                "host": h,
                "name": n,
                "plain_value": v,
                "enc_len": len(e or b""),
                "enc_prefix": (e or b"")[:3].decode("ascii", "replace"),
            }
            for h, n, v, e in con.execute(
                "select host_key,name,value,encrypted_value from cookies"
            )
        ]
    finally:
        con.close()


def main() -> int:
    from playwright.sync_api import sync_playwright

    from src.core.config import DATA_DIR
    from src.models.profile import Profile
    from src.services.browser import process as bp
    from src.services.browser.cdp import read_cdp_port
    from src.services.engine import updater

    _install_sandbox_waiver(bp)
    updater.set_in_use_provider(lambda: False)
    srv, origin = _serve_origin()

    profile = Profile(
        name="ps341-cookie",
        proxy=None,
        os_type="windows",
        engine="chromium",
        search_engine="brave",
        ai_control=True,
    )
    pdir = os.path.join(DATA_DIR, profile.name)
    shutil.rmtree(pdir, ignore_errors=True)

    res = {"build": updater.current_version(), "origin": origin, "legs": []}

    def session(write: bool, label: str) -> dict:
        started = time.time() - 1
        proc = bp.spawn_browser(profile)
        port = None
        for _ in range(90):
            if proc.poll() is not None:
                break
            try:
                port = read_cdp_port(profile.name, not_before=started)
                break
            except Exception:
                time.sleep(1)
        leg = {"label": label, "opened": port is not None}
        if port is None:
            leg["exit"] = proc.poll()
            return leg
        time.sleep(3)
        with sync_playwright() as pw:
            b = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            ctx = b.contexts[0]
            pg = ctx.new_page()
            pg.goto(origin, wait_until="load", timeout=30000)
            if write:
                pg.evaluate(
                    "()=>{document.cookie='ps341=matrix; path=/; max-age=2592000'}"
                )
                time.sleep(1)
            leg["document_cookie"] = pg.evaluate("()=>document.cookie")
            leg["ctx_cookies"] = ctx.cookies(origin)
            leg["browser_version"] = b.version
            b.close()
        try:
            proc.terminate()
            proc.wait(timeout=40)
        except Exception:
            proc.kill()
        # Chromium flushes its cookie store on shutdown; give it room, then read
        # the SQLite row directly so "jar empty" and "row gone" stay apart.
        time.sleep(5)
        leg["db_rows_after"] = _rows(pdir)
        return leg

    res["legs"].append(session(True, "write"))
    res["legs"].append(session(False, "restart-read"))

    served = bool(res["legs"][1].get("ctx_cookies"))
    rows = res["legs"][1].get("db_rows_after") or []
    res["cookie_served_after_restart"] = served
    res["row_present_after_restart"] = bool(rows)
    res["verdict"] = (
        "served" if served else ("row present but NOT served" if rows else "row gone")
    )

    srv.shutdown()
    with open(OUT, "w") as f:
        json.dump(res, f, indent=2, default=str)
    print(json.dumps({k: v for k, v in res.items() if k != "legs"}, indent=1))
    print("WROTE", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
