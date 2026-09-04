"""PS-293 — the Firefox gate probe: seal identity, seal override, and the real drive.

Reproduces §2.3 and §2.4 of readings/ps293-2026-09-04/REPORT.md.

WHAT THIS ANSWERS. The ticket asks whether a persona release is genuinely required
for a Firefox build above the driver pin, or only for one whose ASSET NAMES changed.
Neither: the gate is the BuildID seal, and the seal is standing in front of a real
juggler protocol change. This script measures both halves.

PREREQUISITES (it prints these and exits if they are missing):

    # the two engine trees, extracted side by side
    for t in firefox-20 firefox-21; do
      curl -sL -o /tmp/ps293/$t.tar.gz \
        "https://github.com/feder-cr/firefox_antidetect_patch/releases/download/$t/firefox-151.0-stealth-linux-x86_64.tar.gz"
      mkdir -p /tmp/ps293/ff/$t && tar xzf /tmp/ps293/$t.tar.gz -C /tmp/ps293/ff/$t
    done

    # firefox-21's published seal, for the override leg
    curl -sL -o /tmp/ps293/seal21.json \
      "https://github.com/feder-cr/firefox_antidetect_patch/releases/download/firefox-21/seal.json"

    # the launch legs need a display
    sudo apt install xvfb

THE OVERRIDE LEG MUST RUN IN ITS OWN PROCESS. `INVISIBLE_SEAL_FILE` is read when
invisible_core is imported, so a seal swap inside a live interpreter is a no-op
against already-bound constants. Hence the subprocess below rather than an env
mutation in place — reading the seal check as passing when it never re-ran is
exactly the false green this file exists to avoid.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

BASE = Path(os.environ.get("PS293_DIR", "/tmp/ps293"))
TREES = BASE / "ff"
SEAL21 = BASE / "seal21.json"


def _need(path: Path, what: str) -> bool:
    if path.exists():
        return True
    print(f"MISSING {what}: {path} — see this file's docstring for the fetch commands")
    return False


def probe_identity() -> None:
    """§2.3 — the seal refuses firefox-21 on BuildID, with everything else equal."""
    from invisible_core.constants import BINARY_VERSION
    from invisible_core.seal import (
        EngineMismatch,
        active_seal,
        read_engine_identity,
        verify_engine,
    )

    seal = active_seal()
    print(f"active seal: {seal.tag}, upstream {seal.upstream_version}")
    print(f"BINARY_VERSION: {BINARY_VERSION}\n")

    for tag in ("firefox-20", "firefox-21"):
        entry = TREES / tag / "firefox"
        if not _need(entry, f"{tag} tree"):
            continue
        ident = read_engine_identity(entry)
        print(f"--- {tag} ---")
        print(
            f"  Version {ident.version}  BuildID {ident.build_id}  "
            f"juggler {ident.juggler_layout} {ident.marked_entries}/4"
        )
        try:
            verify_engine(entry, seal, source=f"ps293 probe {tag}")
            print("  verify_engine -> ACCEPTED")
        except EngineMismatch as exc:
            print("  verify_engine -> REFUSED")
            for problem in exc.problems:
                print(f"     {problem}")

    # The by-name route refuses just as firmly as the by-path one.
    from invisible_core import ensure_binary
    from invisible_core.seal import SealMismatch

    try:
        ensure_binary("firefox-21")
        print("\nensure_binary('firefox-21') -> ACCEPTED (unexpected)")
    except SealMismatch as exc:
        print(f"\nensure_binary('firefox-21') -> SealMismatch: {str(exc).splitlines()[0]}")


_DRIVE = textwrap.dedent(
    """
    import os, sys
    seal = sys.argv[2]
    if seal:
        os.environ["INVISIBLE_SEAL_FILE"] = seal
    from invisible_playwright import InvisiblePlaywright
    try:
        with InvisiblePlaywright(headless=True, binary_path=sys.argv[1]) as ip:
            page = ip.new_page()
            page.goto("about:blank")
            print("DROVE OK, ua=" + page.evaluate("navigator.userAgent"))
    except Exception as exc:
        print(f"{type(exc).__name__}: {str(exc).splitlines()[0]}")
    """
)


def drive(tag: str, seal: str = "") -> None:
    """Launch one real engine tree, in a fresh process so the seal is re-read."""
    entry = TREES / tag / "firefox"
    if not _need(entry, f"{tag} tree"):
        return
    out = subprocess.run(
        [sys.executable, "-c", _DRIVE, str(entry), seal],
        capture_output=True,
        text=True,
        timeout=300,
    )
    label = f"{tag} (seal={Path(seal).name})" if seal else tag
    tail = [ln for ln in out.stdout.splitlines() if ln.strip()]
    print(f"  drive {label}: {tail[-1] if tail else out.stderr.strip()[:200]}")


def main() -> None:
    probe_identity()

    print("\n=== §2.4 — the pin is a REAL protocol contract ===")
    # Control: the build the shipped driver is actually paired with.
    drive("firefox-20")
    # The measurement: clear the seal check, and fail one layer deeper anyway.
    if _need(SEAL21, "firefox-21 seal"):
        drive("firefox-21", str(SEAL21))
        print(
            "\n  Expected: firefox-20 drives; firefox-21 reaches the browser and\n"
            "  dies at `Browser.enable` — so the seal guards a real juggler change,\n"
            "  not bookkeeping. See REPORT.md §2.4 for the control on core 26.17.0."
        )

    print("\n=== §2.1 — the asset names did NOT change ===")
    import urllib.request

    url = (
        "https://api.github.com/repos/feder-cr/firefox_antidetect_patch"
        "/releases?per_page=30"
    )
    with urllib.request.urlopen(url, timeout=20) as resp:
        releases = json.load(resp)
    for rel in releases:
        tag = rel.get("tag_name", "")
        if not tag.startswith("firefox-"):
            continue
        macs = [a["name"] for a in rel.get("assets", []) if "macos" in a["name"]]
        print(f"  {tag:12s} macOS legs: {len(macs)}")


if __name__ == "__main__":
    main()
