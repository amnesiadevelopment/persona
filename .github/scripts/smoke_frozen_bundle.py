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
    persona's Linux bundle carries `lib/libpython3.12.so.1.0` and a directory of
    stdlib .pyc — a SHARED LIBRARY the Flutter host embeds, with no executable
    to invoke. So borrowing it is not merely awkward, it is unavailable.
  * BECAUSE the runner's interpreter is the one that runs, its VERSION must match
    the one the bundle was frozen for. This is not a nicety; it is the difference
    between a real check and a false alarm. flet freezes site-packages against the
    python-build-standalone runtime it downloads (cpython-3.12.9 today), and a
    compiled extension is stamped with that exact ABI: 3.12 builds
    `_pydantic_core.cpython-312-*.so`, and 3.13 looks ONLY for `-313-`. Import it
    from 3.13 and you get `ModuleNotFoundError` for a file sitting right there on
    disk.
    MEASURED, because this shipped as a release-blocking false alarm (PS-158):
    run against the REAL, PUBLISHED v2.9.17 bundle — one that users are running
    successfully right now — this script under the runner's 3.13 reported
    fastapi, pydantic, paramiko, mcp and invisible_playwright all "missing". The
    same script, the same bundle, under 3.12: every import resolved and the entry
    point printed SELFTEST_OK. The bundle was never the problem.
    The named module was innocent in every case; what actually failed was a
    TRANSITIVE import (`pydantic_core._pydantic_core`, `_cffi_backend`), and the
    bootstrap recorded only the exception's CLASS, discarding the message that
    said so. It now reports the message, because that one omission is what turned
    a one-line diagnosis into a blocked release.
    So the ABI is DETECTED from the bundle's own compiled extensions and an
    interpreter matching it is required. If none can be found this ERRORS — it
    does not fall back to a convenient interpreter, because "check it with the
    wrong Python" is precisely the bug being fixed.
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
#
# ---- MAINTENANCE: THIS LIST IS A SNAPSHOT, AND IT DOES NOT UPDATE ITSELF ----
# ADD A DEPENDENCY TO pyproject => ADD IT HERE. Nothing enforces that, and the
# failure is silent in the direction that matters: a dependency missing from this
# list is simply never checked inside the bundle, so a lazily-imported one can go
# missing from the frozen tree and still sail through this gate green.
# The alternative — deriving the list automatically — was considered and not
# taken: an import scan of src/ would need to resolve conditional and in-function
# imports to be trustworthy, and a derivation that quietly under-reports is worse
# than a hand-list that is visibly a hand-list. An explicit stale list beats no
# lazy-import check at all, which is what stood here before.
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
    if len(candidates) > 1:
        # rglob order is filesystem-dependent, so picking [0] here would silently
        # smoke-test an ARBITRARY one of several payloads and report on the whole
        # bundle. This script fails closed everywhere else; guessing which app is
        # the real one would be the single place it did not.
        fail(
            f"{len(candidates)} flutter_assets/app trees under {root}: "
            f"{[str(p.relative_to(root)) for p in candidates]}. Refusing to guess "
            "which one ships — teach this script the bundle's shape instead"
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


def find_site_packages(root: Path) -> list[Path]:
    """EVERY site-packages the bundle ships — the thing that makes -S meaningful.

    Returns a LIST, and that is the fix for the Windows case rather than a
    relaxation of it. This used to refuse outright when it found more than one,
    reasoning that picking an arbitrary directory would silently decide which
    dependency set the -S run resolves against. The refusal was right; the
    premise was not. On Windows flet ships TWO, and neither is a decoy:

        site-packages       <- the app's dependencies, installed by flet
        Lib/site-packages   <- the embedded interpreter's OWN

    Both are inside the bundle and both are on the real app's import path at
    runtime, so "which one does the app ship with?" had no answer: it ships
    both. Using all of them is what the running app actually does, so this is
    MORE faithful than picking one, not more forgiving.

    It stays honest in the direction that matters. Every path here is inside the
    bundle, the runner's own site-packages is still excluded by -S -E, and a
    module absent from ALL of them still fails the check. Nothing is waved
    through — the search got complete, not lenient.
    """
    found = [p for p in root.rglob("site-packages") if p.is_dir()]
    if not found:
        # flet's layouts differ per OS; fall back to the directories that
        # actually carry the engine packages rather than assuming a fixed path.
        probed: list[Path] = []
        for probe in ("invisible_core", "flet"):
            probed.extend(p.parent for p in root.rglob(probe) if p.is_dir())
        found = sorted(set(probed))
    if not found:
        fail(
            f"no bundled site-packages under {root} — without it every import would "
            "silently resolve from the runner instead, and this check would prove nothing"
        )
    return sorted(set(found))


# A compiled extension carries the EXACT interpreter it was built for in its
# filename: CPython 3.12 produces `_pydantic_core.cpython-312-x86_64-linux-gnu.so`
# on Linux/macOS and `_pydantic_core.cp312-win_amd64.pyd` on Windows. 3.13 does
# not look at those files at all — it searches for `-313-` — so importing a
# 3.12-built bundle from 3.13 raises ModuleNotFoundError for a file that is
# sitting right there. That filename is therefore the bundle's own statement of
# which interpreter it requires, and it is what this script must obey.
ABI_TAG_RE = re.compile(r"\.cp(?:ython-)?(\d)(\d+)[.-]")

# `foo.abi3.so` is deliberately NOT matched above. The stable ABI is exactly the
# case that works across versions (it is why cryptography imported fine from
# 3.13 while pydantic_core did not), so it is evidence of nothing and must not
# be allowed to vote on the version.


def detect_bundle_abi(site_packages: list[Path]) -> tuple[int, int]:
    """Which CPython the bundle was frozen for, read off its own binaries.

    Deliberately measured from the artifact rather than configured. A constant
    here would be a second source of truth that says 3.12 while flet quietly
    moves to 3.13 — and the failure would look exactly like the one this fixes.
    """
    tags: dict[tuple[int, int], list[str]] = {}
    for sp in site_packages:
        for ext in (".so", ".pyd"):
            for lib in sp.rglob(f"*{ext}"):
                m = ABI_TAG_RE.search(lib.name)
                if m:
                    # Tag "312" is 3.12 and "310" is 3.10: first digit major,
                    # the remainder minor.
                    tags.setdefault((int(m.group(1)), int(m.group(2))), []).append(lib.name)
    if not tags:
        # Fail closed, like everywhere else here. persona's bundle carries ~24
        # compiled extensions (pydantic_core, cryptography, bcrypt, ...), so
        # finding NONE means we are looking at the wrong tree or at a bundle
        # that lost them. Continuing would mean picking an interpreter at random
        # — which is the exact defect this function exists to remove.
        fail(
            f"no compiled extension modules under {[str(p) for p in site_packages]}, "
            "so the interpreter version the bundle was frozen for cannot be "
            "determined — refusing to guess which Python to check it with"
        )
    if len(tags) > 1:
        pretty = {f"{a}.{b}": sorted(v)[:3] for (a, b), v in sorted(tags.items())}
        fail(
            f"the bundle carries extensions built for MORE THAN ONE Python: {pretty}. "
            "No single interpreter can import all of them, so the bundle itself is "
            "inconsistent — this is a real packaging defect, not a check problem"
        )
    return next(iter(tags))


def resolve_interpreter(abi: tuple[int, int], explicit: str | None) -> str:
    """An interpreter matching the bundle's ABI — or an ERROR, never a fallback.

    "Could not find the right Python, so I used the one I had" is precisely how
    a green release turned into five phantom ModuleNotFoundErrors. If the right
    interpreter is absent the honest outcome is a red build that says so.
    """
    major, minor = abi

    def version_of(exe: str) -> tuple[int, int] | None:
        try:
            proc = subprocess.run(
                [exe, "-c", "import sys; print('%d %d' % sys.version_info[:2])"],
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        try:
            a, b = proc.stdout.split()
            return int(a), int(b)
        except ValueError:
            return None

    if explicit:
        got = version_of(explicit)
        if got is None:
            fail(f"--python {explicit} could not be run to report its version")
        if got != abi:
            # Refused rather than silently ignored: an explicit --python that
            # does not match means the WORKFLOW is wired wrong, and quietly
            # substituting a different interpreter would hide that.
            fail(
                f"--python {explicit} is {got[0]}.{got[1]}, but the bundle was frozen "
                f"for {major}.{minor} — it cannot import this bundle's extensions"
            )
        return explicit

    if sys.version_info[:2] == abi:
        return sys.executable

    for cand in (f"python{major}.{minor}", f"python{major}{minor}"):
        exe = shutil.which(cand)
        if exe and version_of(exe) == abi:
            return exe
    # The Windows launcher knows about interpreters that are not on PATH.
    if os.name == "nt":
        launcher = shutil.which("py")
        if launcher:
            probe = f"{launcher}"
            try:
                proc = subprocess.run(
                    [probe, f"-{major}.{minor}", "-c",
                     "import sys; print(sys.executable)"],
                    capture_output=True, text=True, timeout=60,
                )
                if proc.returncode == 0:
                    exe = proc.stdout.strip()
                    if exe and version_of(exe) == abi:
                        return exe
            except (OSError, subprocess.SubprocessError):
                pass

    fail(
        f"the bundle was frozen for Python {major}.{minor} (read from its own compiled "
        f"extensions) but no {major}.{minor} interpreter is available here — this check "
        f"is running under {sys.version_info[0]}.{sys.version_info[1]}, which cannot "
        f"import {major}.{minor} extension modules and would report every one of them "
        "as missing. Provision the matching Python (release.yml does this with a "
        "second setup-python step) rather than letting the check run under the wrong one"
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
sys.path[:0] = [APP_DIR] + list(SITE_PACKAGES)

# Import the hard dependencies FIRST, from inside the frozen tree. Opening the
# app alone does not prove these are present: the lazily-imported ones would let
# the app open, print its version, and only die later in front of a user.
#
# Report the exception's MESSAGE, not just its class. The class alone says
# "ModuleNotFoundError" against the name being checked, which reads as "fastapi
# is not in the bundle" — and that sentence was wrong and cost a release
# (PS-158). What had actually failed was a TRANSITIVE import: fastapi was
# present and importing pydantic_core._pydantic_core, a 3.12-built extension,
# under 3.13. The message names the module that really could not be found and
# turns a phantom into a one-line diagnosis.
missing = []
for name in REQUIRED:
    try:
        __import__(name)
    except Exception as exc:
        missing.append("%s (%s: %s)" % (name, exc.__class__.__name__, exc))
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
#
# runpy with run_name="__main__", NOT `import main`. The gate lives inside
# main()'s body, which is reached only through main.py's `if __name__ ==
# "__main__"` guard — so a plain import binds the module, runs no entry point,
# prints no token, and the bundle looks broken when it is fine. This launches
# the entry point the way the app is really launched.
import runpy
runpy.run_path(os.path.join(APP_DIR, "main.py"), run_name="__main__")

# Reaching here means main() RETURNED without the gate firing. On the selftest
# path it must os._exit(0) after printing the token, so a normal return means
# the gate did not run — fail loudly rather than falling off the end quietly.
print("PERSONA_BUNDLE_ENTRYPOINT_RETURNED", flush=True)
sys.exit(4)
'''


def run_bundle(
    app_dir: Path,
    site_packages: list[Path],
    interpreter: str,
    timeout: int,
) -> subprocess.CompletedProcess:
    code = (
        BOOTSTRAP.replace("APP_DIR", repr(str(app_dir)))
        .replace("SITE_PACKAGES", repr([str(p) for p in site_packages]))
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
    #
    # `interpreter` is the ABI-matched Python resolved from the bundle's own
    # compiled extensions, NOT necessarily sys.executable. Running the wrong
    # version here is what made this check condemn a healthy shipped release.
    return subprocess.run(
        [interpreter, "-S", "-E", "-c", code],
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
    ap.add_argument(
        "--python",
        default=None,
        help=(
            "interpreter to run the payload with. Must match the CPython version "
            "the bundle was frozen for (detected from its own compiled "
            "extensions); a mismatch is refused rather than silently accepted. "
            "Omit to let this script find one."
        ),
    )
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
        abi = detect_bundle_abi(site_packages)
        interpreter = resolve_interpreter(abi, args.python)
        print(f"app payload:   {app_dir}", flush=True)
        for sp in site_packages:
            print(f"site-packages: {sp}", flush=True)
        # State the interpreter and WHY it was chosen. When this check next goes
        # red, the first question is "was it checked with the right Python?" —
        # and that must be answerable from the log alone.
        print(
            f"bundle ABI:    CPython {abi[0]}.{abi[1]} (read from the bundle's own "
            f"compiled extensions)",
            flush=True,
        )
        print(f"interpreter:   {interpreter}", flush=True)

        for rel in REQUIRED_ASSETS:
            # The EXACT path, with no "is it anywhere?" fallback. The failure
            # being hunted is an asset addressed by filesystem path that stops
            # resolving once frozen — and an icon.png that survived at some
            # OTHER path is that failure, not an escape from it. A search of the
            # whole tree by basename would wave the relocated case through while
            # only catching outright deletion, so the check is the literal path
            # the running app would open.
            if not (app_dir / rel).is_file():
                stray = [p for p in app_dir.rglob(Path(rel).name) if p.is_file()]
                where = (
                    f" (a file of that name exists at {[str(p.relative_to(app_dir)) for p in stray]}"
                    ", but the app opens it by path, not by name)"
                    if stray
                    else ""
                )
                fail(
                    f"asset {rel} did not survive freezing at the path the app "
                    f"reads it from{where} — the app would fail once opened"
                )

        try:
            proc = run_bundle(app_dir, site_packages, interpreter, args.timeout)
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
