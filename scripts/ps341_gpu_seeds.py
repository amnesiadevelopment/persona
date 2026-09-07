"""PS-341 Q4b — is the WebGL pair's move across a build change GENERAL, or one seed?

The main run measured ONE profile whose engine-authored WebGL identity changed
across the build change (``RTX 3060`` on 152 → ``Iris Xe`` on 148, same seed,
same profile dir, everything else identical). Two questions that single reading
cannot answer, and both change what the finding MEANS:

  1. **Is it the seed's bad luck?** One seed moving could be a coincidence of
     two pools that happen to differ at that index. Several seeds moving is a
     property of the engine.
  2. **Does it move for EVERY seed, or only some?** "Every seed's card changes"
     and "one seed in five changes" are different-sized findings, and the
     honest report names which.

⭐ THE VECTOR IS ENGINE-AUTHORED ON THIS ARM, WHICH IS WHY IT IS THE ONE THAT
MOVED. ``gpu_ext.engine_authors_identity_for_engine_platform("windows")``
returns True: persona's own GPU layer deliberately STANDS DOWN on the windows
arm and leaves the engine as the single author of every realm. So the pair a
page reads there is the ENGINE's seed-derived value, and it is authored by a
table that lives inside the engine binary — a different binary per build. The
observed ``0x00009A49`` is not in persona's ``WIN_GPUS`` pool at all (which
carries Iris Xe as ``0x0000A7A1``), which independently confirms the author.

This does NOT need a profile or a launch through the product: the question is
what the ENGINE reports for a given ``--fingerprint`` seed, so it is asked of
the engine directly, headless, one page per seed. That keeps it cheap enough to
run across many seeds and across both builds.

⚠️ THE VENUE IS HEADFUL-UNDER-CDP, NOT ``--dump-dom``, AND THAT IS A CORRECTION
RATHER THAN A PREFERENCE. An earlier draft of this probe read both builds with
``--headless=new --dump-dom`` and reported **8 seeds of 8 moved** — a clean,
confident, and COMPLETELY FALSE result. The old build's headless arm returned no
reading at all for every seed (empty stdout; its WebGL context never came up
under this container's software GL), and "no reading" compared against a real
string is unequal, so every row scored MOVED. The instrument, not the engine,
produced the 8/8.

That is this project's own recorded failure mode — a check that could not have
failed is not coverage — and it is why the arms are now read through the SAME
venue the main measurement used: a real headful launch under Xvfb, read over
CDP. A leg that fails to produce a reading is recorded as ``None`` and EXCLUDED
from the moved/same tally rather than silently counted as a difference.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

SEEDS = [3805799318, 12345, 1, 999983, 42424242, 777, 20260907, 88888888]



def read_pair(binary: str, seed: int, platform: str = "windows"):
    """The pair build ``binary`` reports for ``seed``, or None if unreadable.

    HEADFUL under the inherited DISPLAY, read over CDP — the same venue the
    main measurement uses. Returns None (never a string, never a sentinel) when
    the launch produced no reading, so an unreadable leg can be EXCLUDED from
    the tally instead of scoring as a difference.
    """
    from playwright.sync_api import sync_playwright

    udd = tempfile.mkdtemp(prefix="ps341-gpu-")
    args = [binary]
    if binary.endswith(".AppImage"):
        args.append("--appimage-extract-and-run")
    args += [
        "--no-sandbox",
        f"--user-data-dir={udd}",
        f"--fingerprint={seed}",
        f"--fingerprint-platform={platform}",
        "--fingerprint-brand=Chrome",
        "--use-gl=angle",
        "--use-angle=swiftshader",
        "--enable-unsafe-swiftshader",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-debugging-port=0",
        "--remote-allow-origins=*",
        "about:blank",
    ]
    proc = subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        port = None
        portfile = os.path.join(udd, "DevToolsActivePort")
        for _ in range(90):
            if os.path.exists(portfile):
                try:
                    with open(portfile) as f:
                        port = int(f.readline().strip())
                    break
                except Exception:
                    pass
            time.sleep(1)
        if port is None:
            return None
        time.sleep(2)
        with sync_playwright() as pw:
            b = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            ctx = b.contexts[0]
            pg = ctx.new_page()
            pair = pg.evaluate(
                """() => {
                     try {
                       const c = document.createElement('canvas');
                       const g = c.getContext('webgl');
                       if (!g) return null;
                       const d = g.getExtension('WEBGL_debug_renderer_info');
                       if (!d) return null;
                       return [g.getParameter(d.UNMASKED_VENDOR_WEBGL),
                               g.getParameter(d.UNMASKED_RENDERER_WEBGL)];
                     } catch (e) { return null; }
                   }"""
            )
            b.close()
        return pair
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
        time.sleep(1)
        shutil.rmtree(udd, ignore_errors=True)


def main() -> int:
    new = os.environ["PS341_NEW_BINARY"]
    old = os.environ["PS341_OLD_BINARY"]
    out = os.environ.get("PS341_GPU_OUT", "/tmp/ps341/gpu_seeds.json")

    rows = []
    for seed in SEEDS:
        a = read_pair(new, seed)
        b = read_pair(old, seed)
        readable = a is not None and b is not None
        rows.append({
            "seed": seed, "new": a, "old": b,
            "readable": readable,
            "moved": (a != b) if readable else None,
        })
        print(seed, ("MOVED" if a != b else "same") if readable else "UNREADABLE",
              a, "|", b, flush=True)

    scored = [r for r in rows if r["readable"]]
    res = {
        "new_binary": new,
        "old_binary": old,
        "seeds_attempted": len(SEEDS),
        "seeds_scored": len(scored),
        "seeds_unreadable": len(SEEDS) - len(scored),
        "moved": sum(1 for r in scored if r["moved"]),
        "same": sum(1 for r in scored if not r["moved"]),
        "rows": rows,
    }
    with open(out, "w") as f:
        json.dump(res, f, indent=2)
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
