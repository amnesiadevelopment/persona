"""PS-254: the shell resolver must find Git Bash and must REFUSE the WSL stub.

WHY THIS FILE EXISTS
────────────────────
Five `test_ps218_trial_build_workflow` tests failed on `windows-latest` because
`subprocess.run(["bash", ...], env={"PATH": "/usr/bin:/bin"})` resolved `bash`
to `C:\\Windows\\System32\\bash.exe` — the WSL *launcher*, not a shell. With no
distribution installed it exits 1, so `ps218_attribute.sh` NEVER EXECUTED and
every assertion about its behaviour was vacuous on that platform.

`tests/posix_shell.py` fixes that by resolving the real interpreter. But a
resolver whose Windows branch can only be exercised ON Windows is the same
blindness one level up — so these tests drive that branch from any host by
simulating a runner's layout.

⚠️ THIS IS NOT A SUBSTITUTE FOR THE REAL THING. Passing here means the
resolution *logic* is right; it does not prove Git Bash exists on the runner.
The five ps218 tests running green on `windows-latest` is what proves that, and
that is the measurement to trust.

A NOTE ON WHAT THESE CAUGHT
───────────────────────────
The stub-rejection guard originally read `Path(candidate).parent.name`. On a
real Windows host that is correct — `Path` is `WindowsPath` and splits on `\\`.
Off Windows it is `PosixPath`, backslash is an ordinary character, and the guard
silently returned False for `C:\\Windows\\System32\\bash.exe`. The guard was
therefore unverifiable anywhere except the platform it protects. A negative
control caught it; the resolver now normalises separators so the refusal path
is provable on any host.
"""

from __future__ import annotations

import sys

import pytest

from tests import posix_shell

WSL_STUB = r"C:\Windows\System32\bash.exe"


@pytest.fixture
def fake_git_for_windows(tmp_path):
    """A Git-for-Windows install laid out the way a runner really has it."""
    root = tmp_path / "Git"
    for sub in ("bin", "usr/bin", "mingw64/bin", "cmd"):
        (root / sub).mkdir(parents=True)
    (root / "bin" / "bash.exe").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "usr" / "bin" / "bash.exe").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "cmd" / "git.exe").write_text("", encoding="utf-8")
    return root


@pytest.fixture
def on_windows(monkeypatch):
    """Force the win32 branch, so the Windows logic is actually evaluated.

    A skip marker that is never evaluated is an unfired guard; the same is true
    of a platform branch. Forcing it is what makes these tests evidence.
    """
    monkeypatch.setattr(posix_shell, "sys", type("S", (), {"platform": "win32"}))
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    return monkeypatch


def _which_returning(mapping, monkeypatch):
    monkeypatch.setattr(
        posix_shell.shutil, "which", lambda name: mapping.get(name)
    )


def _isolate_from_real_git(monkeypatch):
    """Remove the env-derived discovery path, so a real Git install cannot leak in.

    ⚠️ WITHOUT THIS THE STUB TEST IS UNFALSIFIABLE ON A MACHINE THAT HAS GIT.
    `_git_bash_candidates()` reads `GIT_INSTALL_ROOT` / `ProgramFiles` /
    `ProgramFiles(x86)` from the environment and probes them with `is_file()`.
    Monkeypatching `shutil.which` alone does NOT cover that path, so on a real
    windows-latest runner the resolver found `C:\\Program Files\\Git\\bin\\bash.exe`
    — correctly! — and the assertion "no shell was resolved" could never hold.

    This was caught by CI, not locally: off Windows those variables are unset and
    that directory does not exist, so the test passed on Linux and failed on the
    one platform it exists to protect. That is the same blindness this whole
    ticket is about, so it is recorded here rather than quietly patched.
    """
    for var in ("GIT_INSTALL_ROOT", "ProgramFiles", "ProgramFiles(x86)"):
        monkeypatch.delenv(var, raising=False)


# ─────────────────────────────────────────────────────────────────────────────
# The refusal — the half that was silently broken
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "stub",
    [
        r"C:\Windows\System32\bash.exe",
        r"C:\Windows\SysNative\bash.exe",
        "C:/Windows/System32/bash.exe",       # forward slashes: same stub
        r"c:\windows\system32\BASH.EXE",      # case must not matter
    ],
)
def test_the_wsl_stub_is_never_accepted_as_a_shell(stub, on_windows, monkeypatch):
    """The WSL launcher exits 1 without running anything. Taking it as a shell
    is what made five tests vacuous, so this must hold for every spelling.

    The isolation below is load-bearing, not tidiness: without it a real Git
    install on the host satisfies the resolver through the environment path and
    this assertion can never fail. See `_isolate_from_real_git`.
    """
    _isolate_from_real_git(monkeypatch)                    # no Git Bash on disk
    _which_returning({"bash": stub}, monkeypatch)          # stub is all PATH offers
    assert posix_shell.find_posix_shell() is None, (
        f"{stub!r} was accepted as a POSIX shell. It is the WSL launcher: it "
        "runs no script, so every assertion downstream would be vacuous."
    )


def test_the_stub_check_does_not_depend_on_the_host_path_flavour():
    """Directly pins the defect the negative control found.

    `Path(...).parent.name` is `System32` on Windows and `''` off it, so the
    original guard could only be verified on the platform it protects.
    """
    assert posix_shell._is_wsl_stub(WSL_STUB) is True
    assert posix_shell._is_wsl_stub("/usr/bin/bash") is False
    assert posix_shell._is_wsl_stub(r"C:\Program Files\Git\bin\bash.exe") is False


# ─────────────────────────────────────────────────────────────────────────────
# The resolution — Git Bash must win, including when the stub is also present
# ─────────────────────────────────────────────────────────────────────────────

def test_git_bash_is_preferred_over_the_stub_on_a_realistic_runner(
    fake_git_for_windows, on_windows, monkeypatch
):
    """The runner has BOTH: the stub on PATH and Git Bash off it. This is the
    exact situation that broke, so it is the one that must resolve correctly."""
    root = fake_git_for_windows
    _which_returning(
        {"git": str(root / "cmd" / "git.exe"), "bash": WSL_STUB}, monkeypatch
    )

    shell = posix_shell.find_posix_shell()

    assert shell is not None, "Git Bash is present and was not found"
    assert "System32" not in shell, "resolved the WSL stub over real Git Bash"
    assert str(root) in shell


def test_the_pinned_path_carries_the_tools_the_script_needs(
    fake_git_for_windows, on_windows, monkeypatch
):
    """Hermeticity is preserved, not traded away.

    `ps218_attribute.sh` uses awk/basename/date/grep/mkdir/printf/sed/sort/
    tail/wc. Those ship under the Git root, not `/usr/bin` — pinning the POSIX
    path is what hid them.
    """
    root = fake_git_for_windows
    _which_returning(
        {"git": str(root / "cmd" / "git.exe"), "bash": WSL_STUB}, monkeypatch
    )

    path = posix_shell.posix_tool_path(posix_shell.find_posix_shell())

    assert str(root / "usr" / "bin") in path, "the coreutils directory is missing"
    assert "/usr/bin:/bin" != path, "the POSIX path is what hid Git Bash's tools"
    for entry in path.split(";" if "\\" in path else ":"):
        assert entry, "the pinned PATH must not contain empty entries"


# ─────────────────────────────────────────────────────────────────────────────
# THE LAST-RESORT ACCEPT BRANCH — untested until now, and it shipped a defect
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def non_git_layout_shell(tmp_path):
    """A REAL bash that is not the WSL stub and not in the Git-for-Windows tree.

    This is what `find_posix_shell()`'s last-resort branch accepts. Nothing
    exercised it before, which is exactly how the empty-PATH defect shipped.
    """
    shell_dir = tmp_path / "SomeShell" / "shell"
    shell_dir.mkdir(parents=True)
    shell = shell_dir / "bash.exe"
    shell.write_text("#!/bin/sh\n", encoding="utf-8")
    return shell


def test_a_non_git_shell_never_yields_an_empty_path(
    non_git_layout_shell, on_windows, monkeypatch
):
    """The blocking defect, pinned.

    `find_posix_shell()` accepts any non-stub `bash` on PATH, including one
    outside the Git-for-Windows layout. For such a shell every candidate in
    `posix_tool_path()` was filtered out and it returned `""`, so the
    subprocess launched with `PATH=""`, none of the script's ten utilities
    resolved, and `ps218_attribute.sh` died on its first command.

    That is a SECOND way to not-really-run the attribution logic, and it is
    worse than the first: the shell resolves, the "treat it as a finding"
    assertion passes, and the failure reads as a defect in the script rather
    than in the harness.
    """
    _isolate_from_real_git(monkeypatch)
    _which_returning({"bash": str(non_git_layout_shell)}, monkeypatch)

    shell = posix_shell.find_posix_shell()
    assert shell == str(non_git_layout_shell), (
        "the last-resort branch must accept a real, non-stub shell"
    )

    path = posix_shell.posix_tool_path(shell)
    assert path, (
        "posix_tool_path() returned an empty PATH. The subprocess would launch "
        "with PATH='' and die on its first utility, while every guard reported "
        "success — the attribution logic would not really run."
    )
    assert str(non_git_layout_shell.parent) in path, (
        "the shell's own directory is the honest fallback: a bash shipped "
        "outside the Git layout usually keeps its coreutils beside it"
    )


def test_the_env_never_hands_a_subprocess_an_empty_path(
    non_git_layout_shell, on_windows, monkeypatch
):
    """`shell_env()` guarded only against `None`, so an empty string sailed
    through it. The env is what a subprocess actually receives, so this is the
    assertion that matters at the call site."""
    _isolate_from_real_git(monkeypatch)
    _which_returning({"bash": str(non_git_layout_shell)}, monkeypatch)

    env = posix_shell.shell_env(UCPL_DIR="u", PATCH_DIR="p")

    assert env["PATH"], (
        "shell_env() handed the subprocess PATH=''. Ten utilities "
        "(awk basename date grep mkdir printf sed sort tail wc) would all fail "
        "to resolve, and the script would never run."
    )


def test_no_resolvable_shell_still_yields_a_usable_path(on_windows, monkeypatch):
    """The `None` case must not regress either.

    `shell_env()` already handled it, and it must keep handling it: when no
    shell resolves at all the caller's assertion fires with a clear message,
    but the env it builds on the way must still be well-formed.
    """
    _isolate_from_real_git(monkeypatch)
    _which_returning({}, monkeypatch)                      # nothing at all

    assert posix_shell.find_posix_shell() is None
    assert posix_shell.shell_env()["PATH"], "the fallback PATH must not be empty"


def test_the_env_carries_systemroot_without_widening_tool_visibility(
    fake_git_for_windows, on_windows, monkeypatch
):
    """Some toolchain binaries fail to initialise without SYSTEMROOT. Carrying
    it grants no extra tool visibility, so hermeticity still holds."""
    root = fake_git_for_windows
    _which_returning(
        {"git": str(root / "cmd" / "git.exe"), "bash": WSL_STUB}, monkeypatch
    )

    env = posix_shell.shell_env(UCPL_DIR="u", PATCH_DIR="p")

    assert env["SYSTEMROOT"] == r"C:\Windows"
    assert env["UCPL_DIR"] == "u" and env["PATCH_DIR"] == "p"
    assert "PATH" in env


# ─────────────────────────────────────────────────────────────────────────────
# POSIX must be untouched — this fix must not move Linux or macOS at all
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(sys.platform == "win32", reason="asserts the POSIX branch")
def test_posix_behaviour_is_byte_identical_to_the_hardcoded_value():
    """The non-regression guarantee. Linux and macOS were green before this
    change and must be unchanged by it, so the pinned PATH must still be
    exactly the string the tests hardcoded."""
    assert posix_shell.posix_tool_path("/usr/bin/bash") == "/usr/bin:/bin"
    assert posix_shell.shell_env()["PATH"] == "/usr/bin:/bin"
    assert posix_shell.find_posix_shell() is not None, (
        "no bash on a POSIX host — the attribution tests cannot run at all"
    )
