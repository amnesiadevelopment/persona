#!/usr/bin/env python3
"""PS-293 — verify every code citation in REPORT.md lands on the line it claims.

WHY THIS EXISTS. This report's whole value is that a future reader can re-check
it, and the first submission failed exactly there: 16 of 23 checked `file:line`
references missed against the report's OWN stated tree. Three were worse than an
off-by-two — `firefox.py:171` was offered as the line that CAPS the offer when it
is the uncapped highest release, i.e. the opposite — and §5 is written as a brief
for a follow-on implementer, so a wrong line there misdirects the next ticket.

Hand-correcting the ones a reviewer happened to list is not a fix; the same drift
recurs on the next edit. So the citations are checked MECHANICALLY, by asserting
that the cited line actually contains the code the report invokes it for.

RUN:  python3 readings/ps293-2026-09-04/artifacts/verify_citations.py
Exits non-zero and names every miss. Committed transcript: `verify_citations.txt`.

SCOPE / HONEST LIMITS. It checks citations into persona's own tree at the commit
you run it on — it is a lint, not a proof the surrounding prose is true. Two
classes of reference are deliberately NOT checked and are listed as SKIPPED
rather than silently passing: citations into `invisible_playwright` (a pinned
third-party package, not in this repo) and pure prose line ranges. A citation this
script does not know about is REPORTED as unknown, never treated as passing —
adding a reference to the report without adding it here is itself a failure.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
REPORT = ROOT / "readings/ps293-2026-09-04/REPORT.md"

FILES = {
    "app.py": "src/ui/app.py",
    "src/ui/app.py": "src/ui/app.py",
    "updater.py": "src/services/engine/updater.py",
    "policy.py": "src/services/engine/policy.py",
    "firefox.py": "src/services/engine/firefox.py",
    "engine_install.py": "src/services/browser/engine_install.py",
    "src/core/config.py": "src/core/config.py",
    "scripts/engine_autobump.py": "scripts/engine_autobump.py",
}

# cite -> substring that MUST appear on that line (or anywhere in that range).
# The needle is the claim: it is what the report invokes the line FOR, so a line
# that no longer holds it is a citation that has stopped meaning what it says.
EXPECT: dict[str, str] = {
    # §1 — the call chain
    "src/ui/app.py:3490": "def _check_engines_periodic",
    "app.py:4510": "def _auto_update_engine(self)",
    "app.py:4827": "def _update_engine_async",
    "updater.py:832": "def download_engine(",
    # §1.1 — the eleven-row gate table
    "app.py:4549": "self._engine_busy or not engine.is_installed()",
    "app.py:2677": "if engine.pinned_build():",
    "app.py:2679": "if not engine.is_newer(self._engine_latest",
    "app.py:2681": "if self._engine_unverifiable_tag == self._engine_latest:",
    "policy.py:257": "def is_installable(tag: str)",
    "app.py:4555": "if self._engine_tree_in_use():",
    "updater.py:895": "if httpdl.digest_missing(digest):",
    "updater.py:932": "if not have_asset and not _download_to",
    "updater.py:938": "with _install_lock:",
    "updater.py:583": "def _install_linux(asset_path: str)",
    "updater.py:592": "def _install_windows(asset_path: str)",
    "updater.py:711": "def _install_macos(asset_path: str)",
    # §1.2 — the deferral precedes the marker/sentinel writes
    "updater.py:950": "if defer_if_in_use and _engine_in_use(log=log):",
    "updater.py:954-960": "os.remove(MARKER_FILE)",
    "updater.py:966-970": "_installing_file()",
    "updater.py:945": "BEFORE the marker/sentinel writes",
    # §1.3 — the operator ceiling message names THEIR file
    "policy.py:246-253": "max_tested_major in {POLICY_FILE}",
    # §2.1 — the operator is told a newer persona is needed
    "app.py:3269": "needs a newer persona",
    "app.py:3396": "needs a newer persona",
    # §2.2 — persona's own two caps
    "firefox.py:173": "if num <= pkg_num and num > build_number(drivable_tag):",
    "engine_install.py:148": "if num > pinned_num:",
    # §3 — the un-versioned tree and what it costs
    "src/core/config.py:201": 'ENGINE_DIR = _under_home("engine"',
    "updater.py:21": "from ...core.config import ENGINE_DIR",
    "updater.py:643": "def _promote_staging(staging: str)",
    "updater.py:105": "def set_in_use_provider(fn)",
    "app.py:3325": "engine.set_in_use_provider(",
    "updater.py:117": "def _engine_in_use(log=None)",
    "updater.py:927-931": "have_asset = (",
    # §6 — what was not established
    "updater.py:971-976": "if _platform.IS_WINDOWS:",
    "app.py:4533-4536": "_download_engine_fresh",
    # §2.5 — the autobump models the lockstep
    "scripts/engine_autobump.py:5-8": "MUST ship together",
}

# Not in this repo; checked by hand against the pinned package, not by this lint.
SKIP = {"invisible_playwright/_engine.py:36-39"}

CITE_RE = re.compile(r"`([A-Za-z0-9_/\.]+\.(?:py|yml|toml)):(\d+)(?:-(\d+))?`")


def main() -> int:
    text = REPORT.read_text()
    cites: list[str] = []
    for f, a, b in CITE_RE.findall(text):
        cite = f"{f}:{a}-{b}" if b else f"{f}:{a}"
        if cite not in cites:
            cites.append(cite)

    bad: list[str] = []
    for cite in cites:
        if cite in SKIP:
            print(f"SKIP {cite:32s} (third-party package, not in this repo)")
            continue
        needle = EXPECT.get(cite)
        if needle is None:
            print(f"UNKNOWN {cite:29s} <- add it to EXPECT in this script")
            bad.append(cite)
            continue
        head, _, span = cite.rpartition(":")
        lo, _, hi = span.partition("-")
        lo_i, hi_i = int(lo), int(hi or lo)
        path = ROOT / FILES[head]
        lines = path.read_text().splitlines()
        window = "\n".join(lines[lo_i - 1 : hi_i])
        if needle in window:
            print(f"OK   {cite:32s} {needle[:56]}")
        else:
            got = lines[lo_i - 1].strip()[:60] if lo_i <= len(lines) else "(past EOF)"
            print(f"MISS {cite:32s} wanted {needle[:40]!r}, line says {got!r}")
            bad.append(cite)

    print(f"\n{len(cites) - len(bad)}/{len(cites)} citations verified")
    if bad:
        print("FAILED: " + ", ".join(bad))
        return 1
    print("All citations land.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
