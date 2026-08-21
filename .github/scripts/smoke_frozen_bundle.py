#!/usr/bin/env python3
"""Open the frozen bundle this build just produced and make it prove it works,
BEFORE anything is packaged, checksummed or uploaded.

WHY THIS EXISTS
---------------
Freezing a Python application rewrites how modules are found. The failure class
is narrow and predictable: a module importable on the build machine but never
collected into the frozen tree; an asset found by a filesystem path that stops
existing once frozen; an entry point that is fine interpreted and broken frozen.
Each of those assembles, compresses and uploads without complaint, then dies the
instant a person opens it.

release.yml already imports the engine packages before building. That check is
real and stays exactly as it is — but it validates the RUNNER's pip environment,
so it is structurally blind to all three failures above: it cannot see inside the
bundle. This script is the counterpart that looks inside.

It matters more here than it would elsewhere because persona self-updates. A
broken build replaces a working one before anyone gets the chance to notice.

THE NO-SCREEN CONSTRAINT, AND WHAT IS ACTUALLY EXERCISED
--------------------------------------------------------
A desktop app told to open on a machine with no display normally dies for
reasons unrelated to the breakage being hunted. Running the packaged executable
is NOT available to us: updater.verify_appimage_runs documents in place why
(AppRun execs the Flutter host, and that host paints BEFORE any Python — the
PERSONA_SELFTEST gate included — gets to run, #199). So "launch the exe with a
timeout" was never on the table.

What IS available is the frozen PAYLOAD. This script finds the shipped app tree
and the bundle's own site-packages, then runs the real entry point out of that
tree with `-S -E`, so the runner's site-packages and PYTHONPATH are both off and
every import MUST resolve from inside the bundle. It drives main.py's existing
PERSONA_SELFTEST gate, which is pre-GUI and pre-port-bind by construction, and
requires the bundle to print its own version. DISPLAY / WAYLAND_DISPLAY are
actively scrubbed so this cannot pass by accident on a runner that has a screen.

Stated plainly, so no reader mistakes its reach:
  * The interpreter running the payload is the RUNNER's, not the one embedded in
    the Flutter host. A bundle can carry an interpreter; it cannot lend it out.
  * The Flutter host layer itself is NOT exercised. That needs a display and is
    explicitly out of scope.
  * This proves the frozen PYTHON tree is complete, importable and correctly
    versioned. It does not prove the window paints.
That is the honest boundary of what can be asked without a screen. It is a real
check inside that boundary, not a check that passes because it asked nothing.

WHY "IT OPENED" IS NOT ENOUGH (a measured finding, not a precaution)
--------------------------------------------------------------------
Deleting paramiko from a bundle still let it open and still let it print its
correct version — the app imports it lazily on the SSH path, so it would only
die later, in front of a user. Opening is therefore necessary but not
sufficient, and this script also imports the hard dependencies explicitly from
inside the frozen tree. That list is exactly the follow-up release.yml's own
"a true guard would import from inside the built bundle" note asked for.

FAILS CLOSED
------------
Every "I could not find it" path is an ERROR, never a skip. A smoke test that
finds nothing to run and reports success is worse than a missing one, because
the missing one is at least visible.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

# The token main.py prints when PERSONA_SELFTEST=1. This spelling is a CONTRACT
# with the self-updater (it waits for exactly this string to decide a staged
# build is safe to swap in), so it must never be "tidied". If someone changes it,
# every installed copy's next update breaks — this breaks the BUILD instead.
SELFTEST_TOKEN = "SELFTEST_OK"

# Markers this script's own bootstrap prints. Distinct from anything the app
# prints so a log line can never be mistaken for a passing check.
VERSION_MARKER = "PERSONA_BUNDLE_VERSION="
IMPORTS_MARKER = "PERSONA_BUNDLE_IMPORTS_OK"

# Hard dependencies that MUST be present inside the frozen tree. Derived from the
# third-party top-level imports actually reachable from src/. Several are imported
# lazily at runtime, which is precisely why they are listed explicitly here rather
# than left to the entry point to discover in front of a user.
REQUIRED_IMPORTS = [
    "flet",
    "fastapi",
    "uvicorn",
    "pydantic",
    "httpx",
    "cryptography",
    "paramiko",
    "socks",
    "mcp",
    "invisible_core",
    "invisible_playwright",
]

# Assets addressed by filesystem path at runtime. These are the ones that stop
# existing once frozen if the packaging step drops them.
REQUIRED_ASSETS = [
    "assets/icon.png",
]


def fail(msg: str) -> "NoReturn":  # type: ignore[valid-type]
    print(f"::error::{msg}", flush=True)
    print(f"SMOKE FAILED: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def find_app_payload(root: Path, workdir: Path) -> Path:
    """The frozen app tree (the directory holding main.py + src/).

    flet ships it under flutter_assets/app, either unpacked or as app.zip. Both
    shapes are handled; a zip is extracted into `workdir` and used from there.
    """
    candidates = [p for p in root.rglob("flutter_assets/app") if p.is_dir()]
    if not candidates:
        fail(
            f"no flutter_assets/app anywhere under {root} — the app payload was "
            "not found, so this check would prove nothing about the bundle"
        )
    app_dir = candidates[0]

    zip_path = app_dir / "app.zip"
    if (app_dir / "main.py").is_file():
        return app_dir
    if zip_path.is_file():
        dest = workdir / "app_unzipped"
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(dest)
        if not (dest / "main.py").is_file():
            fail(f"{zip_path} contains no main.py — the frozen entry point is missing")
        return dest
    fail(
        f"{app_dir} holds neither main.py nor app.zip — the frozen app tree is "
        "empty or has an unrecognised shape"
    )


def find_site_packages(root: Path) -> Path:
    """The bundle's OWN site-packages — the thing that makes -S meaningful."""
    exact = [p for p in root.rglob("site-packages") if p.is_dir()]
    if exact:
        return exact[0]
    # flet's layouts differ per OS; fall back to the directory that actually
    # carries the engine packages rather than assuming a fixed path.
    for probe in ("invisible_core", "flet"):
        hits = [p for p in root.rglob(probe) if p.is_dir()]
        if hits:
            return hits[0].parent
    fail(
        f"no bundled site-packages under {root} — without it every import would "
        "silently resolve from the runner instead, and this check would prove nothing"
    )


def expected_version(repo_root: Path) -> str:
    """APP_VERSION, read the SAME way preflight reads it.

    Deliberately not a second source of truth: preflight already compares the tag
    against this value before the build starts, so the bundle is held against the
    same number rather than against a freshly minted one.
    """
    src = repo_root / "src" / "services" / "app_update" / "updater.py"
    if not src.is_file():
        fail(f"cannot read APP_VERSION: {src} does not exist")
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', src.read_text(encoding="utf-8"))
    if not m:
        fail(f"cannot parse APP_VERSION out of {src}")
    return m.group(1)


BOOTSTRAP = r'''
import sys, os
sys.path[:0] = [APP_DIR, SITE_PACKAGES]

# Import the hard dependencies FIRST, from inside the frozen tree. Opening the
# app alone does not prove these are present: the lazily-imported ones would let
# the app open, print its version, and only die later in front of a user.
missing = []
for name in REQUIRED:
    try:
        __import__(name)
    except Exception as exc:
        missing.append("%s (%s)" % (name, exc.__class__.__name__))
if missing:
    print("PERSONA_BUNDLE_MISSING=" + ",".join(missing), flush=True)
    sys.exit(3)
print("PERSONA_BUNDLE_IMPORTS_OK", flush=True)

# Make the bundle state its OWN version, read from its OWN frozen copy.
from src.services.app_update.updater import APP_VERSION
print("PERSONA_BUNDLE_VERSION=" + APP_VERSION, flush=True)

# Now open it for real. main.py's PERSONA_SELFTEST gate is pre-GUI and
# pre-port-bind; reaching it means interpreter startup, the frozen module tree
# and the entry point all worked. It prints the token and hard-exits.
import main
'''


def run_bundle(app_dir: Path, site_packages: Path, timeout: int) -> subprocess.CompletedProcess:
    code = (
        BOOTSTRAP.replace("APP_DIR", repr(str(app_dir)))
        .replace("SITE_PACKAGES", repr(str(site_packages)))
        .replace("REQUIRED", repr(REQUIRED_IMPORTS))
    )
    env = {
        k: v
        for k, v in os.environ.items()
        # Scrub the display so this cannot pass by accident on a runner that has
        # one — the check must be genuinely windowless, not incidentally so.
        if k not in ("DISPLAY", "WAYLAND_DISPLAY", "PYTHONPATH", "PYTHONHOME")
    }
    env["PERSONA_SELFTEST"] = "1"
    # -S: no runner site-packages.  -E: ignore PYTHONPATH/PYTHONHOME.
    # Together they force every import to resolve from inside the bundle.
    return subprocess.run(
        [sys.executable, "-S", "-E", "-c", code],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=str(app_dir),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bundle_root", help="the tree flet build produced (or the .app)")
    ap.add_argument("--repo-root", default=".", help="checkout root, for APP_VERSION")
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()

    root = Path(args.bundle_root).resolve()
    if not root.exists():
        fail(f"bundle root {root} does not exist — nothing was built to open")

    want = expected_version(Path(args.repo_root).resolve())
    print(f"opening bundle at {root}; this release claims version {want}", flush=True)

    workdir = Path(tempfile.mkdtemp(prefix="persona-smoke-"))
    try:
        app_dir = find_app_payload(root, workdir)
        site_packages = find_site_packages(root)
        print(f"app payload:   {app_dir}", flush=True)
        print(f"site-packages: {site_packages}", flush=True)

        for rel in REQUIRED_ASSETS:
            if not (app_dir / rel).is_file() and not list(app_dir.rglob(Path(rel).name)):
                fail(
                    f"asset {rel} did not survive freezing — it is addressed by "
                    "filesystem path at runtime, so the app would fail once opened"
                )

        try:
            proc = run_bundle(app_dir, site_packages, args.timeout)
        except subprocess.TimeoutExpired:
            fail(
                f"the bundle did not reach its selftest gate within {args.timeout}s — "
                "it hung on open"
            )

        out = (proc.stdout or "") + (proc.stderr or "")
        print("--- bundle output ---", flush=True)
        print(out.strip(), flush=True)
        print("--- end ---", flush=True)

        if "PERSONA_BUNDLE_MISSING=" in out:
            missing = out.split("PERSONA_BUNDLE_MISSING=", 1)[1].splitlines()[0]
            fail(
                f"hard dependencies are missing from the frozen tree: {missing}. "
                "The app imports some of these lazily, so it would open, look "
                "healthy, and fail later in front of a user"
            )
        if IMPORTS_MARKER not in out:
            fail("the bundle's imports did not complete — the frozen tree is incomplete")
        if VERSION_MARKER not in out:
            fail("the bundle never stated its version — it did not reach the entry point")

        got = out.split(VERSION_MARKER, 1)[1].splitlines()[0].strip()
        if got != want:
            fail(
                f"the bundle says it is {got}, but this release claims {want} — "
                "a stale payload was packaged"
            )
        if SELFTEST_TOKEN not in out:
            fail(
                f"the bundle did not print {SELFTEST_TOKEN} — it does not open "
                f"(exit code {proc.returncode})"
            )
        if proc.returncode != 0:
            fail(f"the bundle opened but exited {proc.returncode}")

        print(
            f"OK: the frozen bundle opened without a display, imported every hard "
            f"dependency from inside itself, and reported version {got}",
            flush=True,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
