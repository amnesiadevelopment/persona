#!/usr/bin/env python3
"""Detect a newer patched-Firefox engine and bump the pins so a release rebuilds
persona with the engine + its matching driver in lockstep.

The engine (firefox-NN binary) and its driver (invisible_playwright, which pins
invisible_core==NN.x where the major N == firefox-NN) MUST ship together — a
newer firefox-NN speaks a juggler contract only a newer driver can drive (#405).
So "update the engine" == "bump the driver pin + rebuild persona". This script:

  1. reads the latest invisible_playwright release tag and the invisible_core
     version it pins,
  2. compares that core MAJOR (== firefox-NN) to what pyproject currently pins,
  3. if newer, rewrites the pins in pyproject.toml, engine-baseline.txt,
     updater.py APP_VERSION (patch+1) and adds a CHANGELOG entry.

It performs NO git actions — the workflow commits/tags. `plan()` is pure and
unit-tested; `apply()` does the file edits. Network access is injected so tests
run offline.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from dataclasses import dataclass

PLAYWRIGHT_RELEASES = (
    "https://api.github.com/repos/feder-cr/invisible_playwright/releases?per_page=10"
)
PLAYWRIGHT_TAGS = (
    "https://api.github.com/repos/feder-cr/invisible_playwright/tags?per_page=20"
)


def core_major(core_version: str) -> int:
    """invisible_core '19.14.0' -> 19 (== firefox-19). -1 if unparseable."""
    m = re.match(r"^(\d+)\.", core_version or "")
    return int(m.group(1)) if m else -1


def _pinned_core_version(pyproject_text: str) -> str:
    m = re.search(r'invisible_core==([0-9][0-9.]*)', pyproject_text)
    return m.group(1) if m else ""


def _pinned_playwright_sha(pyproject_text: str) -> str:
    m = re.search(r"invisible_playwright\.git@([0-9a-f]{7,40})", pyproject_text)
    return m.group(1) if m else ""


@dataclass
class BumpPlan:
    needed: bool
    reason: str
    latest_tag: str = ""            # invisible_playwright tag, e.g. "v0.7.0"
    latest_sha: str = ""            # its commit sha
    latest_core: str = ""           # invisible_core version it pins, e.g. "19.14.0"
    current_core: str = ""          # what pyproject pins now
    new_app_version: str = ""       # APP_VERSION after a patch bump
    new_baseline: str = ""          # engine-baseline.txt after bump, e.g. "firefox-19"


def _fetch_json(url: str, fetch) -> object:
    return json.loads(fetch(url))


def _default_fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode()


def _core_pinned_by_playwright(tag: str, fetch) -> str:
    """Read the invisible_core==NN.x that the given playwright tag pins, from its
    pyproject.toml on the tag."""
    url = (
        "https://raw.githubusercontent.com/feder-cr/invisible_playwright/"
        f"{tag}/pyproject.toml"
    )
    try:
        text = fetch(url)
    except Exception:
        return ""
    m = re.search(r'invisible_core==([0-9][0-9.]*)', text)
    return m.group(1) if m else ""


def plan(pyproject_text: str, app_version: str, fetch=_default_fetch) -> BumpPlan:
    """Decide whether a newer engine+driver is available and what to bump to.
    Pure except for `fetch` (injected for tests)."""
    current_core = _pinned_core_version(pyproject_text)
    cur_major = core_major(current_core)

    # newest non-prerelease invisible_playwright release tag
    try:
        releases = _fetch_json(PLAYWRIGHT_RELEASES, fetch)
    except Exception as e:
        return BumpPlan(False, f"could not fetch releases: {e}", current_core=current_core)
    best = None
    for rel in releases if isinstance(releases, list) else []:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        tag = rel.get("tag_name", "")
        if re.match(r"^v?\d+\.\d+", tag):
            best = best or rel  # releases are newest-first
    if not best:
        return BumpPlan(False, "no usable playwright release", current_core=current_core)

    latest_tag = best.get("tag_name", "")
    latest_sha = (best.get("target_commitish") or "")
    latest_core = _core_pinned_by_playwright(latest_tag, fetch)
    new_major = core_major(latest_core)

    if new_major < 0:
        return BumpPlan(False, f"could not read core pin for {latest_tag}",
                        latest_tag=latest_tag, current_core=current_core)
    if new_major <= cur_major:
        return BumpPlan(
            False, f"already on core major {cur_major} (latest {new_major})",
            latest_tag=latest_tag, latest_core=latest_core, current_core=current_core,
        )

    # a newer engine major is available
    parts = app_version.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    new_app_version = ".".join(parts)
    return BumpPlan(
        needed=True,
        reason=f"engine firefox-{new_major} available (was firefox-{cur_major})",
        latest_tag=latest_tag, latest_sha=latest_sha, latest_core=latest_core,
        current_core=current_core, new_app_version=new_app_version,
        new_baseline=f"firefox-{new_major}",
    )


def _resolve_sha(tag: str, fetch) -> str:
    """The releases API target_commitish can be a branch name; resolve the tag to
    its commit sha via the tags API for a reproducible pin."""
    try:
        tags = _fetch_json(PLAYWRIGHT_TAGS, fetch)
    except Exception:
        return ""
    for t in tags if isinstance(tags, list) else []:
        if t.get("name") == tag:
            return (t.get("commit") or {}).get("sha", "")
    return ""


def apply(root: str, bp: BumpPlan, fetch=_default_fetch) -> list[str]:
    """Rewrite the pin files per the plan. Returns the list of changed paths."""
    import pathlib

    changed = []
    rootp = pathlib.Path(root)

    sha = bp.latest_sha
    if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        sha = _resolve_sha(bp.latest_tag, fetch) or sha
    if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        raise RuntimeError(f"could not resolve a 40-char sha for {bp.latest_tag}")

    # pyproject.toml: playwright sha + core version
    pp = rootp / "pyproject.toml"
    text = pp.read_text(encoding="utf-8")
    text = re.sub(
        r"(invisible_playwright\.git@)[0-9a-f]{7,40}", rf"\g<1>{sha}", text
    )
    text = re.sub(
        r"invisible_core==[0-9][0-9.]*", f"invisible_core=={bp.latest_core}", text
    )
    pp.write_text(text, encoding="utf-8")
    changed.append(str(pp))

    # engine-baseline.txt
    eb = rootp / "engine-baseline.txt"
    eb.write_text(bp.new_baseline + "\n", encoding="utf-8")
    changed.append(str(eb))

    # updater.py APP_VERSION
    up = rootp / "src" / "services" / "app_update" / "updater.py"
    utext = up.read_text(encoding="utf-8")
    utext = re.sub(
        r'(APP_VERSION\s*=\s*)"[^"]+"', rf'\g<1>"{bp.new_app_version}"', utext
    )
    up.write_text(utext, encoding="utf-8")
    changed.append(str(up))

    # CHANGELOG entry
    ch = rootp / "src" / "ui" / "changelog.py"
    ctext = ch.read_text(encoding="utf-8")
    entry = (
        f'    "{bp.new_app_version}": [\n'
        f'        "Updated the Firefox engine to the latest patched build for '
        f'stronger anti-detection.",\n'
        f'    ],\n'
    )
    ctext = ctext.replace(
        "CHANGELOG: dict[str, list[str]] = {\n",
        "CHANGELOG: dict[str, list[str]] = {\n" + entry,
        1,
    )
    ch.write_text(ctext, encoding="utf-8")
    changed.append(str(ch))

    return changed


def main(argv: list[str]) -> int:
    import pathlib

    root = argv[1] if len(argv) > 1 else "."
    rootp = pathlib.Path(root)
    pyproject = (rootp / "pyproject.toml").read_text(encoding="utf-8")
    up = (rootp / "src" / "services" / "app_update" / "updater.py").read_text("utf-8")
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', up)
    app_version = m.group(1) if m else "0.0.0"

    bp = plan(pyproject, app_version)
    print(f"plan: needed={bp.needed} reason={bp.reason} "
          f"latest_tag={bp.latest_tag} latest_core={bp.latest_core}")
    if not bp.needed:
        # signal "nothing to do" to the workflow via a marker on stdout
        print("BUMP=none")
        return 0
    changed = apply(root, bp)
    print("changed:", changed)
    print(f"BUMP={bp.new_app_version}")
    print(f"NEW_BASELINE={bp.new_baseline}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
