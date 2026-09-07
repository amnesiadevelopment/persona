#!/usr/bin/env python3
"""PS-341 follow-up control: what actually drives ``prefers-color-scheme``?

WHY THIS EXISTS
---------------
The main reading (``readings/ps341-2026-09-07/reading.json``) captured
``page.dark`` — ``matchMedia('(prefers-color-scheme: dark)').matches`` — as
**False on BOTH legs**, while the profile is seeded ``color_scheme2: 2``
(``profile_seed._default_prefs``) and launched with ``--force-dark-mode``
(``process.py``). That reading sat in the evidence unremarked, and an
unexplained number in a committed reading is a trap for the next person who
greps it.

The obvious explanation — *"``--force-dark-mode`` is a UI-level switch and does
not drive ``prefers-color-scheme``"* — is the one this control was written to
check, and **it is FALSE.** It is written down here rather than in prose
precisely because a plausible sentence is the cheapest thing in the world to
write and the most expensive thing to be wrong about (PS-11: *the sentence
claiming you checked is the least likely to be checked*).

WHAT IT MEASURES
----------------
Four arms on stock Chromium, each a fresh ``--user-data-dir``, each reading the
same one-line page:

  A  fresh profile, no flag            -> the baseline. Must be False, or the
                                          probe cannot detect anything.
  B  fresh profile, --force-dark-mode  -> does the FLAG alone drive it?
  C  persona-seeded prefs, no flag     -> does the SEEDED PREF alone drive it?
  D  persona-seeded prefs, with flag   -> both, i.e. persona's own combination.

Arm A is the negative control and it is not optional: without it, "True
everywhere" is indistinguishable from a probe that cannot read False.

⚠️ THE BOUND, STATED UP FRONT. This runs the **stock** ``chromium`` on the host
in ``--headless=new``. The PS-341 legs were the **packaged fingerprint-chromium
AppImage, headful under Xvfb**. So this control establishes what the naive
expectation *should* produce on a stock engine in this venue; it does NOT
identify which of {the fingerprint patches, the headful/Xvfb venue, the absence
of a desktop colour-scheme portal} produces the ``False`` the packaged engine
reported. Two variables move between this control and that reading, and one
control cannot separate them.

⛔ AND NOTHING IN PS-341 TURNS ON IT. ``page.dark`` reads ``False`` on BOTH
legs — it does not move across the build change — so it is not a continuity
finding and no migration question depends on it. This control exists to stop
the number reading as an unexplained anomaly, not because the ticket needs it
resolved.

Usage::

    python3 scripts/ps341_dark_control.py [--out readings/.../control-dark-mode.json]
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent

PROBE_HTML = """<html><body><div id="r">pending</div><script>
document.getElementById('r').textContent =
  'dark=' + matchMedia('(prefers-color-scheme: dark)').matches +
  ' light=' + matchMedia('(prefers-color-scheme: light)').matches;
</script></body></html>
"""

_RE = re.compile(r"dark=(true|false) light=(true|false)")


def _read(binary: str, user_data_dir: str, page: str, extra: list[str]) -> dict:
    cmd = [
        binary,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        f"--user-data-dir={user_data_dir}",
        *extra,
        "--dump-dom",
        f"file://{page}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    m = _RE.search(proc.stdout or "")
    if not m:
        # An unobtained reading is recorded as unobtained, never scored.
        return {"dark": None, "light": None, "unreadable": True,
                "stderr_tail": (proc.stderr or "")[-500:]}
    return {"dark": m.group(1) == "true", "light": m.group(2) == "true",
            "unreadable": False}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", default=shutil.which("chromium") or "")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if not args.binary or not os.path.exists(args.binary):
        print("no chromium binary — SKIPPING rather than reporting a result",
              file=sys.stderr)
        return 2

    sys.path.insert(0, str(REPO))
    from src.services.browser.profile_seed import _default_prefs

    ver = subprocess.run([args.binary, "--version"], capture_output=True,
                         text=True).stdout.strip()

    tmp = tempfile.mkdtemp(prefix="ps341-dark-")
    page = os.path.join(tmp, "probe.html")
    with open(page, "w", encoding="utf-8") as f:
        f.write(PROBE_HTML)

    def profile(name: str, seeded: bool) -> str:
        d = os.path.join(tmp, name)
        if seeded:
            default = os.path.join(d, "Default")
            os.makedirs(default, exist_ok=True)
            with open(os.path.join(default, "Preferences"), "w",
                      encoding="utf-8") as f:
                json.dump(_default_prefs("brave"), f)
        else:
            os.makedirs(d, exist_ok=True)
        return d

    arms = {
        "A_fresh_no_flag": (profile("A", False), []),
        "B_fresh_force_dark": (profile("B", False), ["--force-dark-mode"]),
        "C_seeded_no_flag": (profile("C", True), []),
        "D_seeded_force_dark": (profile("D", True), ["--force-dark-mode"]),
    }

    out = {
        "meta": {
            "what": "which input drives prefers-color-scheme on a STOCK engine",
            "binary": args.binary,
            "binary_version": ver,
            "venue": "--headless=new (NOT the headful/Xvfb venue the PS-341 "
                     "legs used — see this script's docstring)",
            "seeded_prefs_from": "profile_seed._default_prefs('brave')",
        },
        "arms": {},
    }
    for label, (udd, extra) in arms.items():
        out["arms"][label] = {"flags": extra, **_read(args.binary, udd, page, extra)}

    a = out["arms"]["A_fresh_no_flag"]
    out["negative_control_holds"] = (a["dark"] is False)
    out["flag_alone_drives_it"] = out["arms"]["B_fresh_force_dark"]["dark"]
    out["seeded_pref_alone_drives_it"] = out["arms"]["C_seeded_no_flag"]["dark"]
    out["persona_combination_drives_it"] = out["arms"]["D_seeded_force_dark"]["dark"]

    text = json.dumps(out, indent=2, sort_keys=True)
    if args.out:
        pathlib.Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
