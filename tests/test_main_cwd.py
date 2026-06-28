"""The app must survive being launched with a current directory that no longer
exists — which happens after a self-update re-exec unmounts the old AppImage.
We reproduce that exact condition in a subprocess (delete its cwd, then run the
startup guard) rather than mocking, because the failure is in real getcwd()."""

import os
import subprocess
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The reproduction deletes the process's own working directory, which only
# works on POSIX — Windows holds an open handle on the CWD and refuses to remove
# it (WinError 32). The crash being guarded against is the unmounted-AppImage
# case, which is Linux-only anyway, so skipping off-POSIX loses no coverage.
pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="deleted-cwd reproduction is POSIX-only"
)


def _run_in_deleted_cwd(snippet: str) -> subprocess.CompletedProcess:
    """Start a fresh interpreter whose working directory is deleted out from
    under it before the snippet runs, mirroring the post-self-update state."""
    doomed = tempfile.mkdtemp()
    # rmdir-from-inside: chdir in, then a sibling process removes it. We do it
    # by launching python already cd'd into a dir we delete right after spawn is
    # not race-free; instead the child deletes its own cwd, which leaves getcwd
    # failing exactly like the unmounted-AppImage case.
    prelude = (
        "import os\n"
        f"os.chdir({doomed!r})\n"
        f"os.rmdir({doomed!r})\n"
    )
    return subprocess.run(
        [sys.executable, "-c", prelude + snippet],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


GUARD = '''
def _ensure_valid_cwd():
    try:
        if os.getcwd():
            return
    except OSError:
        pass
    for candidate in (os.path.expanduser("~"), os.environ.get("HOME", ""), "/tmp", "/"):
        if not candidate:
            continue
        try:
            os.chdir(candidate)
            return
        except OSError:
            continue
'''


def test_getcwd_fails_in_deleted_cwd_without_guard():
    """Sanity check the reproduction: without the guard, getcwd() raises."""
    r = _run_in_deleted_cwd("import os; os.getcwd()")
    assert r.returncode != 0
    assert "directory" in (r.stderr.lower())


def test_guard_recovers_cwd_after_deletion():
    """With the guard, getcwd() works again and points at a real directory."""
    snippet = GUARD + (
        "_ensure_valid_cwd()\n"
        "import os\n"
        "cwd = os.getcwd()\n"
        "assert cwd and os.path.isdir(cwd), cwd\n"
        "print('OK', cwd)\n"
    )
    r = _run_in_deleted_cwd(snippet)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("OK ")


def test_abspath_works_after_guard():
    """os.path.abspath (called by main.py's sys.path setup) must not raise once
    the guard has run — this is the line that crashed before any window opened."""
    snippet = GUARD + (
        "_ensure_valid_cwd()\n"
        "import os\n"
        "p = os.path.abspath('x')\n"
        "print('OK', p)\n"
    )
    r = _run_in_deleted_cwd(snippet)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("OK ")
