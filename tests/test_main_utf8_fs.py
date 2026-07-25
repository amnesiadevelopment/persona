"""A profile with a non-ASCII (Cyrillic) name must be creatable even when the
app is launched with a C/POSIX locale, where the filesystem encoding is ASCII
and os.mkdir of such a name raises UnicodeEncodeError (#222). We reproduce the
exact condition in a subprocess with LC_ALL=C rather than mocking, because the
failure is in the real interpreter-level filesystem encoding."""

import os
import subprocess
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The reproduction needs an ASCII filesystem encoding, which we get from a
# C/POSIX locale — a POSIX-only mechanism. On Windows the filesystem encoding is
# UTF-16-based and always accepts a Cyrillic name, and the crash being guarded
# against is the bare-locale Linux AppImage, so skipping off-POSIX loses nothing.
pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="ASCII-locale reproduction is POSIX-only"
)

# Mirrors src/main.py's _ensure_utf8_fs so the test exercises the real re-exec
# logic in a controlled subprocess.
GUARD = '''
import os, sys
_REEXEC_UTF8_FLAG = "PERSONA_UTF8_REEXEC"
def _ensure_utf8_fs():
    if os.environ.get(_REEXEC_UTF8_FLAG):
        return
    enc = (sys.getfilesystemencoding() or "").lower()
    if enc.startswith("utf"):
        return
    os.environ[_REEXEC_UTF8_FLAG] = "1"
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except OSError:
        os.environ.pop(_REEXEC_UTF8_FLAG, None)
'''


def _run_ascii_locale(snippet: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    # Don't let an inherited UTF-8 mode mask the ASCII-locale reproduction.
    env.pop("PYTHONUTF8", None)
    env.pop("PYTHONIOENCODING", None)
    env.pop("PERSONA_UTF8_REEXEC", None)
    return subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
    )


def _mkdir_cyrillic() -> str:
    return (
        "import os, sys, tempfile\n"
        "d = os.path.join(tempfile.mkdtemp(), 'тест профиль')\n"
        "os.mkdir(d)\n"
        "print('OK', sys.getfilesystemencoding())\n"
    )


def test_ascii_locale_reproduces_the_crash_without_the_guard():
    """Sanity: under a C locale, mkdir of a Cyrillic dir raises without the
    guard — the exact #222 failure."""
    r = _run_ascii_locale(_mkdir_cyrillic())
    if r.returncode == 0:
        pytest.skip(
            "this platform's C locale still yields a UTF-8 filesystem encoding; "
            "the #222 crash can't be reproduced here"
        )
    assert "UnicodeEncodeError" in r.stderr


def test_guard_reexecs_into_utf8_and_cyrillic_mkdir_succeeds():
    """With the guard, the interpreter re-execs into UTF-8 mode and the Cyrillic
    mkdir succeeds — profile creation no longer crashes (#222)."""
    r = _run_ascii_locale(GUARD + "_ensure_utf8_fs()\n" + _mkdir_cyrillic())
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("OK ")
    # After the re-exec the filesystem encoding is UTF-8.
    assert "utf" in r.stdout.lower()
