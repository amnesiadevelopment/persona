"""PS-341: what a Chromium ENGINE BUILD CHANGE does to an ALREADY-EXISTING profile.

WHY THIS SCRIPT EXISTS
----------------------
``services/browser/process.py``'s Chromium arm has ZERO build-awareness: it
computes ``profile_dir`` and hands it to the engine as ``--user-data-dir``
without ever asking which build wrote it. The Firefox arm, by contrast, runs a
four-part migration on EVERY launch (``_migrate_profile_for_engine_build``),
because a profile seeded on ``firefox-18`` makes ``firefox-19`` SIGSEGV.

The asymmetry is a fact. Whether it is a DEFECT is not — and the only way to
find out is to move a real Chromium build backwards underneath a real profile
and read what happens. That is what this script does.

⛔ THE PARITY QUESTION IS NOT "WHY IS CHROMIUM MISSING FIREFOX'S GUARD". It is
"does Chromium EXHIBIT THE BEHAVIOUR the guard defends against?" Only the second
is a defect. This script asks the second.

THE GESTURE IS THE THING UNDER TEST
-----------------------------------
The build is moved by ``updater.revert_to_previous_build()`` — the function the
operator's own rollback button calls (``ui/app.py`` ``_on_engine_rollback`` →
``engine.revert_to_previous_build``). NOT by hand-swapping files. A hand swap
would measure a state the shipping product may never produce; the gesture
re-downloads against the RECORDED digest, rewrites the one-slot ``version.txt``
and sets the update pin, and any of those could matter.

THE POSITIVE CONTROL IS NOT OPTIONAL
------------------------------------
"The profile survived" is ambiguous between *Chromium tolerated the downgrade*
and *my probe never actually changed the build*, and the second reads as the
first. So every leg asserts the build REALLY moved, three ways that cannot all
be satisfied by a no-op:

  * ``updater.current_version()`` differs across the two launches (the record);
  * the AppImage's sha256 differs (the bytes);
  * the running browser's own ``navigator.userAgent`` major differs (the
    process — the only one of the three a page can see).

Without those, the null result below would mean nothing.

WHAT IS READ, AND FROM WHERE
----------------------------
Four questions, from the ticket, in its order:

  Q1  does the profile OPEN at all on the older build (the Firefox analogue is
      a SIGSEGV before the window paints — record the actual failure mode)
  Q2  does Chromium's own downgrade handling fire — ``Last Version`` before and
      after, a reset/"profile is from a newer version" behaviour, a renamed or
      recreated ``Default/``
  Q3  is DERIVED STATE lost — the three things ``profile_seed._default_prefs``
      writes (Classic theme, dark mode, default search engine), plus bookmarks
      and cookies
  Q4  does the FINGERPRINT move — the Level-2 (bit stability across engine
      updates) question, and the one with real stakes

⭐ Q3 IS READ FROM A RUNNING BROWSER, NOT FROM THE JSON ON DISK. A file that
survives but is IGNORED is the same outcome for the operator, and only the
running browser can tell the two apart. So the search engine is read back
through ``Runtime.evaluate`` on a live page (via CDP), the dark mode through
``matchMedia``, and the bookmarks through the browser's own bookmark store.

TWO OBSERVATIONS SEPARATED BY A FRESH READER: every persistence claim is a read
taken in a SECOND browser process after a full shutdown, never an in-process
read of a value this process just wrote.

⚠️ ``--no-sandbox`` IS PASSED, AND IT IS A DISCLOSED BOUND. This container
forbids unprivileged user namespaces (``apparmor_restrict_unprivileged_userns=1``,
not writable without root), so Chromium's zygote aborts before any window paints
on BOTH builds. The waiver is applied IDENTICALLY to both legs, so it cannot
author a DIFFERENCE between them — and a difference is the entire subject. It is
recorded in the output rather than hidden, the same discipline
``verify/chromium_tier.py`` applies to its own waiver.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree_snapshot(profile_dir: str) -> dict:
    """Everything about the profile's shape that a downgrade could disturb."""
    default = os.path.join(profile_dir, "Default")
    out = {
        "top_level": sorted(os.listdir(profile_dir)),
        "default_exists": os.path.isdir(default),
        "default_entries": sorted(os.listdir(default)) if os.path.isdir(default) else [],
        "last_version": None,
        "backup_dirs": [],
    }
    lv = os.path.join(profile_dir, "Last Version")
    if os.path.exists(lv):
        with open(lv, encoding="utf-8", errors="replace") as f:
            out["last_version"] = f.read().strip()
    # A downgrade reset would show up as a renamed/duplicated profile dir.
    out["backup_dirs"] = [
        e
        for e in out["top_level"]
        if e.lower().startswith(("default backup", "old default", "profile "))
        or "backup" in e.lower()
    ]
    prefs = os.path.join(default, "Preferences")
    if os.path.exists(prefs):
        out["prefs_size"] = os.path.getsize(prefs)
        out["prefs_mtime"] = os.path.getmtime(prefs)
        try:
            with open(prefs, encoding="utf-8") as f:
                p = json.load(f)
            out["prefs_on_disk"] = {
                "theme": (p.get("extensions") or {}).get("theme"),
                "color_scheme2": ((p.get("browser") or {}).get("theme") or {}).get(
                    "color_scheme2"
                ),
                "search_short_name": (
                    ((p.get("default_search_provider_data") or {}).get(
                        "template_url_data"
                    ) or {}).get("short_name")
                ),
                "search_url": (
                    ((p.get("default_search_provider_data") or {}).get(
                        "template_url_data"
                    ) or {}).get("url")
                ),
            }
        except Exception as e:  # pragma: no cover - diagnostic path
            out["prefs_on_disk"] = {"unreadable": repr(e)}
    else:
        out["prefs_present"] = False
    bm = os.path.join(default, "Bookmarks")
    out["bookmarks_present"] = os.path.exists(bm)
    if out["bookmarks_present"]:
        out["bookmarks_size"] = os.path.getsize(bm)
    cookies = os.path.join(default, "Cookies")
    out["cookies_present"] = os.path.exists(cookies)
    if out["cookies_present"]:
        out["cookies_size"] = os.path.getsize(cookies)
    return out


# --------------------------------------------------------------------------
# the live reader — a SECOND process, attached over CDP
# --------------------------------------------------------------------------

_PAGE_PROBE = """
() => {
  const r = {};
  r.ua = navigator.userAgent;
  r.uaMajor = (navigator.userAgent.match(/Chrome\\/(\\d+)/) || [])[1] || null;
  r.dark = matchMedia('(prefers-color-scheme: dark)').matches;
  // the fingerprint surface — the Level-2 question
  r.screen = [screen.width, screen.height, screen.availWidth, screen.availHeight];
  r.colorDepth = screen.colorDepth;
  r.dpr = devicePixelRatio;
  r.platform = navigator.platform;
  r.hw = navigator.hardwareConcurrency;
  r.mem = navigator.deviceMemory ?? null;
  r.langs = navigator.languages;
  r.tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  r.touch = navigator.maxTouchPoints;
  try {
    const c = document.createElement('canvas');
    c.width = 300; c.height = 60;
    const g = c.getContext('2d');
    g.textBaseline = 'top';
    g.font = '14px Arial';
    g.fillStyle = '#f60';
    g.fillRect(0, 0, 125, 20);
    g.fillStyle = '#069';
    g.fillText('persona-ps341-\\u2665', 2, 15);
    r.canvas = c.toDataURL();
  } catch (e) { r.canvas = 'ERR:' + e; }
  try {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl');
    const dbg = gl.getExtension('WEBGL_debug_renderer_info');
    r.webglVendor = gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL);
    r.webglRenderer = gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL);
  } catch (e) { r.webglVendor = 'ERR:' + e; r.webglRenderer = null; }
  try {
    const ac = new (window.OfflineAudioContext || window.webkitOfflineAudioContext)(1, 5000, 44100);
    const osc = ac.createOscillator();
    osc.type = 'triangle'; osc.frequency.value = 10000;
    const comp = ac.createDynamicsCompressor();
    osc.connect(comp); comp.connect(ac.destination); osc.start(0);
    r.audioPending = true;
  } catch (e) { r.audioPending = false; }
  return r;
}
"""


def read_live(port: int, engine_label: str, origin: str) -> dict:
    """Attach over CDP and read what a PAGE and the BROWSER actually report.

    A fresh process every time: this attaches to a browser THIS script did not
    launch in-process, and every value below is what that browser answered —
    never a value we wrote and read back.

    ``origin`` is a real loopback http:// page rather than ``set_content`` or a
    ``data:`` URL. Both of those were tried and both are wrong here:
    ``set_content`` is refused outright on the engine's own start page
    (*"This document requires 'TrustedHTML' assignment"* — persona's new-tab
    page ships a Trusted Types policy), and a ``data:`` URL is an OPAQUE origin,
    which changes the answers of exactly the origin-sensitive readings below.
    """
    from playwright.sync_api import sync_playwright

    out = {"engine_label": engine_label}
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = browser.contexts[0]
        page = ctx.new_page()
        page.goto(origin, wait_until="load", timeout=30000)
        out["page"] = page.evaluate(_PAGE_PROBE)

        # THE DERIVED-STATE READ, FROM THE RUNNING BROWSER — not the JSON.
        # chrome://settings/searchEngines is the browser's own opinion about
        # what its default search engine IS. A Preferences file that survives
        # but is ignored answers differently here than on disk, which is the
        # whole point of reading it from a live browser.
        try:
            sp = ctx.new_page()
            sp.goto("chrome://settings/searchEngines", wait_until="load", timeout=20000)
            # The setting lives inside nested shadow roots; walk them.
            out["search_default_live"] = sp.evaluate(
                """
                () => {
                  const seen = new Set();
                  const walk = (root, depth) => {
                    if (!root || depth > 12) return [];
                    let hits = [];
                    for (const el of root.querySelectorAll('*')) {
                      if (seen.has(el)) continue;
                      seen.add(el);
                      const t = (el.textContent || '').trim();
                      if (el.shadowRoot) hits = hits.concat(walk(el.shadowRoot, depth + 1));
                    }
                    return hits;
                  };
                  walk(document, 0);
                  // Fall back to the whole rendered text of the settings page.
                  const grab = (root, depth, acc) => {
                    if (!root || depth > 12) return acc;
                    for (const el of root.querySelectorAll('*')) {
                      if (el.shadowRoot) grab(el.shadowRoot, depth + 1, acc);
                      else if (el.children.length === 0 && el.textContent.trim())
                        acc.push(el.textContent.trim());
                    }
                    return acc;
                  };
                  return grab(document, 0, []).slice(0, 400);
                }
                """
            )
            sp.close()
        except Exception as e:
            out["search_default_live"] = f"ERR:{e!r}"

        # Bookmarks, from the browser's own store rather than the file.
        try:
            bp = ctx.new_page()
            bp.goto("chrome://bookmarks/", wait_until="load", timeout=20000)
            bp.wait_for_timeout(1500)
            out["bookmarks_live"] = bp.evaluate(
                """
                () => {
                  const acc = [];
                  const grab = (root, depth) => {
                    if (!root || depth > 12) return;
                    for (const el of root.querySelectorAll('*')) {
                      if (el.shadowRoot) grab(el.shadowRoot, depth + 1);
                      else if (el.children.length === 0 && el.textContent.trim())
                        acc.push(el.textContent.trim());
                    }
                  };
                  grab(document, 0);
                  return acc.slice(0, 200);
                }
                """
            )
            bp.close()
        except Exception as e:
            out["bookmarks_live"] = f"ERR:{e!r}"

        # Cookies, from the browser's own jar. Written on the FIRST leg and
        # read back on the SECOND — the two-observations-separated-by-a-fresh-
        # reader rule for anything claimed to persist.
        try:
            out["cookies_live"] = ctx.cookies(origin)
        except Exception as e:
            out["cookies_live"] = f"ERR:{e!r}"

        out["browser_version"] = browser.version
        browser.close()
    return out


def write_cookie(port: int, origin: str) -> None:
    """Put a sentinel cookie in the RUNNING browser's jar (leg 1 only).

    Set from a PAGE on the origin rather than through ``add_cookies``, so what
    is measured on the far side is a cookie the BROWSER wrote into its own jar
    on a real navigation — the ordinary path an operator's session takes.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = browser.contexts[0]
        page = ctx.new_page()
        page.goto(origin, wait_until="load", timeout=30000)
        page.evaluate(
            "() => { document.cookie = "
            "'ps341=sentinel-written-on-build-N; path=/; max-age=2592000'; }"
        )
        page.close()
        browser.close()
