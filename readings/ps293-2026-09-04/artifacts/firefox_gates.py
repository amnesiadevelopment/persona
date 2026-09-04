"""PS-293 — the Firefox gate probe: seal identity, seal override, and the real drive.

Reproduces §2.3 and §2.4 of readings/ps293-2026-09-04/REPORT.md, INCLUDING the
two-directional control (shipped driver x firefox-20/21, current driver x
firefox-26). Committed transcript: `artifacts/firefox_gates.txt`.

WHAT THIS ANSWERS. The ticket asks whether a persona release is genuinely required
for a Firefox build above the driver pin, or only for one whose ASSET NAMES changed.
Neither: the gate is the BuildID seal, and the seal is standing in front of a real
juggler protocol change. This script measures both halves.

⚠️ PREREQUISITE 0 — WHICH DRIVER YOU RUN UNDER DECIDES WHAT THIS MEASURES.
The seal is bound at IMPORT time, so the `invisible_core` version in the running
interpreter IS the active seal. §2.3's transcript opens `active seal: firefox-20`
because it was taken under persona's SHIPPED pin, `invisible_core==20.14.0`
(pyproject.toml). Run the same probe under a stock dev container that happens to
carry core 26.17.0 and `active_seal()` reports firefox-26, `ensure_binary
("firefox-21")` refuses with a firefox-26 message instead, and nothing tells you
whether you mis-set up or the report was wrong. So this script REFUSES TO GUESS:
`_need_core()` below asserts the version each leg requires and fails loudly.

Two interpreters are therefore required, and building both is the setup:

    # A — persona's SHIPPED driver (§2.3, §2.4 rows 1-2)
    python3 -m venv /tmp/ps293/venv-shipped
    /tmp/ps293/venv-shipped/bin/pip install \
      "invisible_playwright @ git+https://github.com/feder-cr/invisible_playwright.git@353df4faac4fb202cc4d836c46d981855ecf1bd9" \
      "invisible_core==20.14.0"

    # B — the CURRENT upstream driver (§2.4 row 3, the closing control)
    python3 -m venv /tmp/ps293/venv-current
    /tmp/ps293/venv-current/bin/pip install \
      "invisible_playwright @ git+https://github.com/feder-cr/invisible_playwright.git@03f695d8"

PREREQUISITE 1 — the three engine trees, extracted side by side:

    mkdir -p /tmp/ps293/ff
    for t in firefox-20 firefox-21 firefox-26; do
      curl -sL -o /tmp/ps293/$t.tar.gz \
        "https://github.com/feder-cr/firefox_antidetect_patch/releases/download/$t/firefox-151.0-stealth-linux-x86_64.tar.gz"
      mkdir -p /tmp/ps293/ff/$t && tar xzf /tmp/ps293/$t.tar.gz -C /tmp/ps293/ff/$t
    done

PREREQUISITE 2 — the published seals, for the override legs:

    for t in firefox-21 firefox-26; do
      curl -sL -o /tmp/ps293/seal-$t.json \
        "https://github.com/feder-cr/firefox_antidetect_patch/releases/download/$t/seal.json"
    done

PREREQUISITE 3 — the launch legs need a display: `sudo apt install xvfb`.

RUN IT:  /tmp/ps293/venv-shipped/bin/python firefox_gates.py
(the script drives venv-current itself for the closing control; it does not need
to be invoked twice).

THE OVERRIDE LEG MUST RUN IN ITS OWN PROCESS. `INVISIBLE_SEAL_FILE` is read when
invisible_core is imported, so a seal swap inside a live interpreter is a no-op
against already-bound constants. Hence the subprocess below rather than an env
mutation in place — reading the seal check as passing when it never re-ran is
exactly the false green this file exists to avoid. The same reasoning is why the
core-26.17.0 control is a subprocess into venv-current rather than a claim in prose.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

BASE = Path(os.environ.get("PS293_DIR", "/tmp/ps293"))
TREES = BASE / "ff"
SEAL21 = BASE / "seal-firefox-21.json"
SEAL26 = BASE / "seal-firefox-26.json"

# The two interpreters, by the core version each is built to carry.
SHIPPED_PY = Path(os.environ.get("PS293_SHIPPED_PY", BASE / "venv-shipped/bin/python"))
CURRENT_PY = Path(os.environ.get("PS293_CURRENT_PY", BASE / "venv-current/bin/python"))
SHIPPED_CORE = "20.14.0"   # persona's pin, pyproject.toml
CURRENT_CORE = "26.17.0"   # what invisible_playwright@03f695d8 pins


def _need(path: Path, what: str) -> bool:
    if path.exists():
        return True
    print(f"MISSING {what}: {path} — see this file's docstring for the fetch commands")
    return False


def _need_core(expected: str) -> None:
    """Fail LOUDLY when the running interpreter is not the driver this leg measures.

    Not a warning: an unnoticed core version silently changes what every line
    below reports (a firefox-26 seal refuses firefox-21 for a *different* reason
    than a firefox-20 seal does), and the output looks perfectly plausible either
    way. Same treatment as _need() gives the tree files, because the same class of
    mistake — a missing prerequisite — is what makes the transcript unreadable.
    """
    import invisible_core

    got = invisible_core.__version__
    if got != expected:
        raise SystemExit(
            f"WRONG DRIVER: this leg measures invisible_core=={expected} "
            f"(persona's shipped pin), but the running interpreter carries "
            f"{got}.\n"
            f"  The seal is bound at import, so {got} would report "
            f"'active seal: firefox-{got.split('.')[0]}' and refuse the probe "
            f"builds for a different reason than the report describes.\n"
            f"  Run:  {SHIPPED_PY} {__file__}\n"
            f"  See this file's PREREQUISITE 0."
        )


def _need_interpreter(py: Path, label: str) -> bool:
    if py.exists():
        return True
    print(f"MISSING {label} interpreter: {py} — see PREREQUISITE 0 in this file")
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
    import invisible_core
    print("CORE " + invisible_core.__version__)
    from invisible_core.seal import active_seal
    print("SEAL " + active_seal().tag)
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


def drive(tag: str, seal: str = "", py: Path = SHIPPED_PY, expect_core: str = SHIPPED_CORE) -> None:
    """Launch one real engine tree, in a fresh process so the seal is re-read.

    `py` selects WHICH DRIVER drives it — that is the whole point of the §2.4
    table, so it is a parameter rather than the ambient interpreter.
    """
    entry = TREES / tag / "firefox"
    if not _need(entry, f"{tag} tree"):
        return
    if not _need_interpreter(py, f"core-{expect_core}"):
        return
    out = subprocess.run(
        [str(py), "-c", _DRIVE, str(entry), seal],
        capture_output=True,
        text=True,
        timeout=600,
    )
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    core = next((ln[5:] for ln in lines if ln.startswith("CORE ")), "?")
    active = next((ln[5:] for ln in lines if ln.startswith("SEAL ")), "?")
    verdict = next(
        (ln for ln in lines if not ln.startswith(("CORE ", "SEAL "))),
        out.stderr.strip()[:200] or "(no output)",
    )
    if core != expect_core:
        verdict = f"WRONG DRIVER (core {core}, expected {expect_core}) — {verdict}"
    label = f"{tag} (seal={Path(seal).name})" if seal else tag
    print(f"  driver core {core:9s} seal {active:11s} x {label:34s} -> {verdict}")


def main() -> None:
    # §2.3 runs in THIS interpreter, so this interpreter must be the shipped one.
    _need_core(SHIPPED_CORE)

    probe_identity()

    print("\n=== §2.4 — the pin is a REAL protocol contract ===")
    # Row 1 — control: the build the shipped driver is actually paired with.
    drive("firefox-20")
    # Row 2 — the measurement: clear the seal check, and fail one layer deeper anyway.
    if _need(SEAL21, "firefox-21 seal"):
        drive("firefox-21", str(SEAL21))
    # Row 3 — the CLOSING control, in the other direction: the same engine family
    # under the driver it is actually paired with. Executed here rather than
    # asserted in the report, so the loop is proven rather than described.
    if _need(SEAL26, "firefox-26 seal"):
        drive("firefox-26", str(SEAL26), py=CURRENT_PY, expect_core=CURRENT_CORE)
    print(
        "\n  Expected: firefox-20 drives under the shipped driver; firefox-21 with\n"
        "  its own seal in force clears the seal check and then dies at\n"
        "  `Browser.enable`; firefox-26 drives under the CURRENT driver. So the\n"
        "  seal guards a real juggler change, not bookkeeping — and a newer engine\n"
        "  is drivable, but only by a newer driver, which is a persona release."
    )

    print("\n=== §2.1 — the asset names did NOT change ===")
    import urllib.error
    import urllib.request

    url = (
        "https://api.github.com/repos/feder-cr/firefox_antidetect_patch"
        "/releases?per_page=30"
    )
    # Anonymous GitHub allows 60 req/h per IP and this is the only networked leg,
    # so it is the one that breaks on a shared runner. Reported as UNMEASURED
    # rather than raised: a traceback here would read as the seal measurement
    # above having failed, when in fact all of §2.3/§2.4 already completed.
    req = urllib.request.Request(url)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            releases = json.load(resp)
    except urllib.error.HTTPError as exc:
        print(
            f"  UNMEASURED — GitHub API returned {exc.code} ({exc.reason}). "
            "Set GITHUB_TOKEN and re-run;\n"
            "  equivalently: gh api repos/feder-cr/firefox_antidetect_patch/releases "
            "--jq '.[]|\"\\(.tag_name) \\([.assets[].name]|map(select(test(\"macos\")))|length)\"'"
        )
        return
    for rel in releases:
        tag = rel.get("tag_name", "")
        if not tag.startswith("firefox-"):
            continue
        macs = [a["name"] for a in rel.get("assets", []) if "macos" in a["name"]]
        print(f"  {tag:12s} macOS legs: {len(macs)}")


if __name__ == "__main__":
    main()
