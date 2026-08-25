#!/usr/bin/env python3
"""Make pywin32 importable from a frozen bundle by a PLAIN PATH IMPORT (PS-168).

THE DEFECT THIS FIXES
---------------------
The Windows 3.0.0 bundle carried pywin32 and still failed:

    SMOKE FAILED: hard dependencies are missing from the frozen tree:
      mcp (ModuleNotFoundError: No module named 'pywintypes')

The package was NOT missing. `pywin32` was declared in both requirement files,
resolution ran ON the Windows runner, the marker matched, and pip reported
`Successfully installed pywin32-312`. The bundle shipped:

    site-packages/win32/lib/pywintypes.py        <- the module the import needs
    site-packages/pywin32.pth
    site-packages/pywin32_system32/pywintypes312.dll

`pywintypes` is not at the top level of site-packages. It lives in `win32/lib`,
and the ONLY thing that puts `win32/lib` on the path is `pywin32.pth`.

A `.pth` file is processed by the `site` module, and only for directories that
`site` treats as site directories (derived from `sys.prefix`). That is not what
happens here, in EITHER consumer:

  * the shipped app puts `<exeDir>/site-packages` on PYTHONPATH — a PYTHONPATH
    entry is not a site directory, so the .pth is never executed;
  * the smoke check bootstraps with `-S -E` and injects via `sys.path[:0]` —
    `-S` disables `site` outright, so the .pth is never executed.

So the .pth is inert in the gate AND in the product. THIS IS A REAL APP DEFECT,
not a checker artifact — which is exactly why the fix belongs in the BUNDLE and
not in the checker. Relaxing the checker's `-S` would turn the gate green over
an app that still fails in front of a user.

WHAT THIS SCRIPT DOES
---------------------
Flattens pywin32's three payload directories into the top level of the bundled
site-packages, which is a directory that IS on the path in both consumers:

    win32/lib/*.py          -> site-packages/*.py      (pywintypes, win32con, ...)
    win32/*.pyd, *.dll      -> site-packages/*         (win32api, _win32sysloader, ...)
    pywin32_system32/*.dll  -> site-packages/*         (pywintypes312.dll, ...)

`pywintypes.py` resolves its companion DLL by searching, in order:
_win32sysloader -> sys.prefix -> `os.path.dirname(__file__)` -> the
`pywin32_system32` package. Putting `pywintypes312.dll` in the SAME directory as
`pywintypes.py` satisfies the third branch, which is the one that does not
depend on `site`, on `sys.prefix`, or on a package import working first.

The original directories are LEFT IN PLACE. Copy, don't move: `pythonwin` and
the `win32/test` tree are untouched, nothing that already resolved stops
resolving, and re-running is harmless.

WHY NOT THE ALTERNATIVES
------------------------
  * `sitecustomize.py` — `site` is disabled by `-S`, so it would never run in
    the gate. It would also only fix the checker, not the app.
  * Editing the checker to process the .pth — a FALSE GREEN. The shipped app
    still cannot import pywintypes; see above.
  * Adding `pywintypes` to REQUIRED_IMPORTS in smoke_frozen_bundle.py — that
    array is flat and imported UNCONDITIONALLY, so it would turn Linux and
    macOS red, where the module is CORRECTLY absent. The script already records
    that reasoning in EXEMPT_FROM_BUNDLE_IMPORT_CHECK; doing it properly needs
    a platform-aware bootstrap and is its own ticket.

FAIL-CLOSED, AND ONLY WHERE IT SHOULD BE
----------------------------------------
Absent pywin32 is NOT an error: this script runs only in the Windows job today,
but a no-op on a tree that never had pywin32 is the correct answer for any
non-Windows caller, so it stays safe if it is ever wired more broadly.

What IS an error is pywin32 being present in a shape this script cannot make
importable — that is the state that shipped 3.0.0's failure, and it must stop
the build rather than be reported and stepped over.

The verification at the end asks about IDENTITY, not presence, and that
distinction is the whole point. An earlier version of this script asked "is
there a file called pywintypes.py at the top level?" — a question the impostor
answers for you. Dropping a foreign `pywintypes.py` and `pywintypes312.dll` at
the root produced: nothing copied (the names were taken), both sentinels
"verified", exit 0 — success reported over a bundle that imports an
`ImportError` as `pywintypes`. That is this ticket's own named failure mode, a
check that passes without the module actually importing from the bundle.

So the copy loop now RECORDS what it placed, and verification is satisfied only
by a file this run copied or one already byte-identical to pywin32's own. Two
consequences worth stating, because they are what make the check able to fail:

  * a collision on a LOAD_BEARING name is fatal, not a printed line. The bundle
    would import something other than pywin32 under a name `mcp` needs, which
    is the shipped failure wearing a different hat;
  * "already flattened" is decided by bytes, not by `st_size`. Size standing in
    for identity let a same-size foreign file pass as a previous run's work.

Measured against the real cp312 wheel before making collisions fatal: 70
flattened names, zero internal collisions, zero same-size-different-bytes. The
honest case does not collide, so failing closed here cannot spuriously block a
legitimate release.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

# The module that must end up importable by a plain path import. It is the one
# named in the 3.0.0 failure, and the one every other pywin32 module needs
# first, so it is the honest single probe for "did this work".
SENTINEL_MODULE = "pywintypes.py"

# The names whose shadowing would break the very import this script exists to
# fix: the four modules `mcp` reaches for, plus the loader `pywintypes.py` uses
# to find its DLL. A foreign file under one of these names is not a cosmetic
# clash — it is the bundle failing in exactly the way 3.0.0 failed, so it stops
# the build rather than being reported and stepped over.
LOAD_BEARING = frozenset(
    {
        "pywintypes.py",
        "_win32sysloader.pyd",
        "win32api.pyd",
        "win32con.py",
        "win32job.pyd",
    }
)

# Payload directories, in the order they are flattened. `pythonwin` is
# deliberately NOT here: it is the GUI/IDE layer, nothing in this app imports
# it, and copying it would add weight to the bundle for no reachability gain.
PAYLOAD_DIRS = (
    Path("win32") / "lib",   # the .py modules: pywintypes, win32con, winerror, ...
    Path("win32"),           # the .pyd extensions: win32api, _win32sysloader, ...
    Path("pywin32_system32"),  # the ABI-tagged DLLs: pywintypes312.dll, ...
)

# Extensions worth flattening. Everything else in those directories (License.txt,
# pythonservice.exe, the test tree) is not importable and is left alone.
PAYLOAD_SUFFIXES = {".py", ".pyd", ".dll"}


def _iter_payload(site_packages: Path) -> list[tuple[Path, Path]]:
    """(source, destination) for every file worth flattening.

    Only the immediate children of each payload directory are considered:
    `win32/lib` and `win32` are flat by construction, and recursing would drag
    in `win32/test`, which nothing imports.
    """
    pairs: list[tuple[Path, Path]] = []
    for rel in PAYLOAD_DIRS:
        src_dir = site_packages / rel
        if not src_dir.is_dir():
            continue
        for src in sorted(src_dir.iterdir()):
            if not src.is_file() or src.suffix.lower() not in PAYLOAD_SUFFIXES:
                continue
            pairs.append((src, site_packages / src.name))
    return pairs


def _same_bytes(a: Path, b: Path) -> bool:
    """True when two files are byte-identical.

    Identity, not size. The previous version of this script used equal
    `st_size` as a stand-in for "this is already the pywin32 file", which a
    same-size foreign file satisfies — so a shadowed bundle read as success.
    Hashing is affordable here: the payload is ~70 files, once per build.
    """
    if not (a.is_file() and b.is_file()):
        return False
    if a.stat().st_size != b.stat().st_size:
        return False  # cheap reject before reading either file
    return _digest(a) == _digest(b)


def _digest(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def flatten(site_packages: Path) -> int:
    """Copy pywin32's payload to the top level of `site_packages`.

    Returns the number of files copied. Raises SystemExit on a pywin32 that is
    present but cannot be made importable.
    """
    marker = site_packages / "pywin32.pth"
    win32_lib = site_packages / "win32" / "lib"

    if not marker.exists() and not win32_lib.is_dir():
        print(f"pywin32 not present under {site_packages} — nothing to flatten (OK).")
        return 0

    pairs = _iter_payload(site_packages)
    if not pairs:
        sys.exit(
            f"ERROR: pywin32 looks present under {site_packages} "
            f"(pywin32.pth={marker.exists()}, win32/lib={win32_lib.is_dir()}) "
            "but no .py/.pyd/.dll payload was found to flatten. The wheel layout "
            "has changed and this script no longer understands it — refusing to "
            "ship a bundle whose pywin32 is unreachable."
        )

    # `placed[name] = source` for every name this run put at the top level or
    # confirmed was ALREADY the pywin32 file (byte-identical). This is what the
    # verification below reads: a name that is merely occupied is not in here,
    # so "something else owns that name" can no longer masquerade as success.
    placed: dict[str, Path] = {}
    collisions: list[str] = []
    copied = 0

    for src, dst in pairs:
        if dst.exists():
            # Identity, not size. Size standing in for identity is what let a
            # same-size foreign file read as "already flattened".
            if dst.is_file() and _same_bytes(src, dst):
                placed[dst.name] = src  # idempotent re-run: this IS the pywin32 file
                continue
            collisions.append(dst.name)
            continue
        shutil.copy2(src, dst)
        placed[dst.name] = src
        copied += 1

    # A collision on a load-bearing name means the module the bundle will
    # import is NOT pywin32's. That is the 3.0.0 failure wearing a different
    # hat — the files are present and the import still gets the wrong thing —
    # so it stops the build, as the docstring promises.
    fatal = sorted(set(collisions) & LOAD_BEARING)
    if fatal:
        sys.exit(
            f"ERROR: pywin32's payload collided with pre-existing, DIFFERENT files "
            f"under {site_packages} for load-bearing name(s): {', '.join(fatal)}. "
            "The bundle would import something other than pywin32 under those "
            "names — refusing to ship a bundle whose pywin32 may be shadowed."
        )
    if collisions:
        # Not load-bearing: report loudly, but this genuinely is the "pywin32 is
        # shadowing something else" case, and nothing mcp imports is affected.
        print(
            "  WARNING: name(s) already taken by different files, left alone: "
            f"{', '.join(sorted(set(collisions)))}"
        )

    # VERIFY FROM THE FILESYSTEM, not from the loop above — but verify IDENTITY,
    # not mere presence. The whole point of this script is that "the files were
    # installed" was already true and the import still failed, so neither a copy
    # count nor an occupied filename proves anything on its own.
    sentinel = site_packages / SENTINEL_MODULE
    if not sentinel.is_file():
        sys.exit(
            f"ERROR: {SENTINEL_MODULE} is still not at the top level of "
            f"{site_packages} after flattening. pywin32 would remain unimportable "
            "in the bundle — refusing to ship."
        )
    sentinel_src = placed.get(SENTINEL_MODULE)
    if sentinel_src is None or not _same_bytes(sentinel_src, sentinel):
        sys.exit(
            f"ERROR: {SENTINEL_MODULE} at the top level of {site_packages} is not "
            "the file this script flattened from pywin32's own win32/lib. The name "
            "is occupied by something else, so the bundle would import a foreign "
            f"module as `pywintypes` — refusing to ship."
        )

    # pywintypes.py resolves `pywintypesXX.dll` relative to its own __file__ as
    # one of its fallbacks; that is the branch this layout is relying on, so the
    # DLL must have landed beside it — and must be pywin32's own, for the same
    # reason the module must be.
    dlls = sorted(
        name
        for name in placed
        if name.lower().startswith("pywintypes") and name.lower().endswith(".dll")
    )
    if not dlls:
        occupied = sorted(p.name for p in site_packages.glob("pywintypes*.dll"))
        sys.exit(
            f"ERROR: {SENTINEL_MODULE} is at the top level of {site_packages} but "
            "no pywintypes*.dll from pywin32's own payload landed beside it"
            + (f" (found, but not ours: {', '.join(occupied)})" if occupied else "")
            + ". The import would resolve the module and then fail loading its "
            "DLL — refusing to ship."
        )

    print(f"pywin32 flattened into {site_packages}: {copied} file(s) copied.")
    print(f"  {SENTINEL_MODULE} -> {sentinel}")
    print(f"  companion DLL(s): {', '.join(dlls)}")
    return copied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "bundle_root",
        type=Path,
        help="the built bundle root (e.g. build/windows)",
    )
    args = parser.parse_args(argv)

    root: Path = args.bundle_root
    if not root.is_dir():
        sys.exit(f"ERROR: bundle root {root} does not exist or is not a directory.")

    # Windows ships more than one site-packages (the app's, plus the embedded
    # interpreter's own Lib/site-packages). Flatten into whichever ones actually
    # carry pywin32, using the same "find them all" approach the smoke check
    # uses rather than assuming a fixed path.
    site_dirs = sorted({p for p in root.rglob("site-packages") if p.is_dir()})
    if not site_dirs:
        sys.exit(f"ERROR: no site-packages found under {root}.")

    total = 0
    for sp in site_dirs:
        print(f"--- {sp} ---")
        total += flatten(sp)
    print(f"done: {total} file(s) copied across {len(site_dirs)} site-packages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
