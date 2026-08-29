"""Resolve a REAL POSIX shell, on every OS the merge gate runs on.

WHY THIS EXISTS
───────────────
`subprocess.run(["bash", script], env={"PATH": "/usr/bin:/bin", ...})` looks
portable and is not. On a `windows-latest` runner it fails twice over:

1. **`bash` resolves to the WSL stub.** `C:\\Windows\\System32\\bash.exe` is the
   WSL *launcher*, not a shell. With no distribution installed it prints
   "Windows Subsystem for Linux has no installed distributions ..." (in
   UTF-16LE, which decodes to mojibake through a UTF-8 pipe) and exits 1.
2. **The hardcoded POSIX `PATH` hides the real one.** Git Bash ships with the
   runner and brings its own `awk`/`grep`/`sed`/`sort`/`tail`/`wc`, but they
   live under the Git installation, not `/usr/bin`.

The consequence is the dangerous part: the script under test **never executes**.
Not one line runs, so every assertion about its behaviour is vacuous on that
platform — and a test asserting only `returncode != 0` would have gone GREEN on
a shell that never started. These five tests failed loudly only because they
assert on `returncode == 0` and on the script's stdout.

⛔ The fix is NOT to skip on Windows. Skipping preserves exactly the blindness
that let these sit unnoticed. Git Bash is present; resolve it honestly and the
assertions run on Windows for the first time.

NOTE ON HERMETICITY: pinning `PATH` was the right instinct — it stops the script
picking up an arbitrary toolchain. That is preserved. What changes is that the
pinned value is now *computed for the platform* instead of assumed to be the
POSIX one, so it still names exactly one known-good toolchain directory set.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# `C:\Windows\System32\bash.exe` is the WSL launcher, never a usable shell here.
_WSL_STUB_PARENTS = {"system32", "sysnative"}


def _is_wsl_stub(candidate: str | Path) -> bool:
    """True for the WSL launcher, which is not a POSIX shell.

    Deliberately parsed as a STRING with both separators normalised, rather than
    via `Path(...).parent.name`. On a real Windows host those agree — `Path` is
    `WindowsPath` there and splits on `\\`. But off Windows `Path` is
    `PosixPath`, backslash is an ordinary character, and the check silently
    returns False for `C:\\Windows\\System32\\bash.exe`.

    That difference made the guard **unverifiable anywhere except the platform
    it protects** — which is the same blindness this whole ticket is about, so
    it is not one to leave in a guard. Normalising here lets the stub-rejection
    path be exercised on any host, and a negative control caught exactly this.
    """
    text = str(candidate).replace("\\", "/")
    parent = text.rsplit("/", 2)[-2] if text.count("/") >= 2 else ""
    return parent.lower() in _WSL_STUB_PARENTS


def _git_bash_candidates() -> list[Path]:
    """Where Git Bash lives on a Windows runner, most reliable first.

    `git` itself is always on PATH there, so deriving the install root from it
    beats hardcoding "Program Files" — it survives a non-default install
    location and the x86 variant.
    """
    roots: list[Path] = []

    git_exe = shutil.which("git")
    if git_exe:
        # <root>/cmd/git.exe or <root>/bin/git.exe  ->  <root>
        roots.append(Path(git_exe).resolve().parent.parent)

    for env_var in ("GIT_INSTALL_ROOT", "ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(env_var)
        if value:
            roots.append(Path(value) / "Git")
            roots.append(Path(value))

    candidates: list[Path] = []
    for root in roots:
        # bin/bash.exe is the wrapper users get; usr/bin/bash.exe is the real one.
        candidates.append(root / "bin" / "bash.exe")
        candidates.append(root / "usr" / "bin" / "bash.exe")
    return candidates


def find_posix_shell() -> str | None:
    """Absolute path to a real `bash`, or None if this host genuinely has none.

    Returns None rather than raising so a caller can decide between skipping and
    failing — but on the three OSes the merge gate runs, this does not return
    None, and a None on CI should be treated as a finding, not routed around.
    """
    if sys.platform != "win32":
        return shutil.which("bash")

    for candidate in _git_bash_candidates():
        if candidate.is_file():
            return str(candidate)

    # Last resort: PATH, provided it is not the WSL stub.
    found = shutil.which("bash")
    if found and not _is_wsl_stub(Path(found)):
        return found
    return None


def posix_tool_path(shell: str) -> str:
    """A pinned PATH carrying the coreutils that `shell` was shipped with.

    Hermetic in the same spirit as the original `"/usr/bin:/bin"`: it names a
    specific known-good toolchain rather than inheriting the caller's PATH. It
    is simply the *correct* such value for the platform.
    """
    if sys.platform != "win32":
        return "/usr/bin:/bin"

    # <root>/bin/bash.exe or <root>/usr/bin/bash.exe -> collect both tool dirs.
    shell_path = Path(shell)
    root = shell_path.parent.parent
    if shell_path.parent.name == "bin" and root.name == "usr":
        root = root.parent

    parts = [root / "usr" / "bin", root / "bin", root / "mingw64" / "bin"]
    return os.pathsep.join(str(p) for p in parts if p.is_dir())


def shell_env(**extra: str) -> dict[str, str]:
    """The env a POSIX-script subprocess needs, with `PATH` pinned correctly.

    `SYSTEMROOT` is carried on Windows because some toolchain binaries fail to
    initialise without it; it grants no extra tool visibility.
    """
    shell = find_posix_shell()
    env = {"PATH": posix_tool_path(shell) if shell else "/usr/bin:/bin"}
    if sys.platform == "win32":
        for passthrough in ("SYSTEMROOT", "SystemRoot", "TEMP", "TMP"):
            value = os.environ.get(passthrough)
            if value:
                env[passthrough] = value
    env.update(extra)
    return env
