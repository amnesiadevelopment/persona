#!/usr/bin/env python3
"""PS-293 §2.5 / §4 — is the engine autobump armed, and at what?

Reproduces the `plan()` output quoted in §2.5 and the hazard §4 rests on: the
daily job's plan is `firefox-20 -> firefox-26`, and firefox-26 ships NO macOS
asset. Committed transcript: `artifacts/autobump_plan.txt`.

WHY IT INJECTS `fetch`. `engine_autobump.plan()` takes an injectable fetcher
(`scripts/engine_autobump.py:89`) and its default is ANONYMOUS. Anonymous GitHub
allows 60 req/h per IP, and on a shared runner that limit is routinely spent — at
which case `plan()` returns `needed=False, reason="could not fetch releases: HTTP
Error 403"`. ⚠️ READ THAT CAREFULLY: `needed=False` from a rate limit looks
EXACTLY like `needed=False` from "no newer engine exists", and the two mean
opposite things — one is a measurement failure, the other is the all-clear. So
this script injects an authenticated fetcher and, more importantly, REFUSES to
report a negative it cannot distinguish: a `reason` mentioning a fetch failure
exits non-zero rather than printing a reassuring `needed: False`.

RUN:  GH_TOKEN=$(gh auth token) python3 readings/ps293-2026-09-04/artifacts/autobump_plan.py

It performs NO git actions and writes nothing — `plan()` is pure by design
(`scripts/engine_autobump.py:16`); only the workflow commits.
"""
from __future__ import annotations

import os
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import engine_autobump as ab  # noqa: E402


def _authed_fetch(url: str) -> str:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode()


def main() -> int:
    if not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        print("WARNING: no GH_TOKEN/GITHUB_TOKEN — anonymous GitHub is rate limited,")
        print("         and a 403 renders as needed=False. Set one for a real answer.")

    plan = ab.plan(
        (ROOT / "pyproject.toml").read_text(), "0.0.0", fetch=_authed_fetch
    )
    for key in ("needed", "reason", "current_core", "latest_core", "new_baseline"):
        print(f"  {key}: {getattr(plan, key, None)}")

    reason = (plan.reason or "").lower()
    if "could not fetch" in reason or "error" in reason:
        print(
            "\nUNMEASURED — plan() could not reach GitHub, so `needed` is a fetch\n"
            "failure and NOT an all-clear. Set GH_TOKEN and re-run."
        )
        return 1

    print(
        "\nExpected (§2.5): needed=True, firefox-20 -> firefox-26, core 20.14.0 ->\n"
        "26.17.0. ⛔ That is the §4 hazard: firefox-26 ships NO macOS asset, and the\n"
        "job runs daily at 06:00 UTC (.github/workflows/engine-autoupdate.yml:15).\n"
        "tests/test_engine_driver_platform_support.py is what catches it."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
