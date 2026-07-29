"""Single-instance guard: only one persona GUI may hold the lock at a time; the
lock frees on release (and on process exit via the OS), so a crash never leaves
a stale lock blocking the next start.
"""
import os

import pytest

from src.core import single_instance as si


@pytest.fixture
def lockfile(tmp_path, monkeypatch):
    p = str(tmp_path / "persona.lock")
    monkeypatch.setenv("PERSONA_LOCK_FILE", p)
    return p


def test_first_acquire_succeeds(lockfile):
    h = si.acquire()
    assert h is not None
    h.release()


def test_second_acquire_is_blocked_while_first_held(lockfile):
    h1 = si.acquire()
    assert h1 is not None
    h2 = si.acquire()
    assert h2 is None, "a second instance must not get the lock while the first holds it"
    h1.release()


def test_lock_frees_on_release(lockfile):
    h1 = si.acquire()
    h1.release()
    h2 = si.acquire()
    assert h2 is not None, "after release the lock must be acquirable again"
    h2.release()


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows msvcrt locks byte 0, so a separate read of the lockfile is "
    "itself blocked; the pid write path is exercised on POSIX.",
)
def test_lockfile_records_pid(lockfile):
    h = si.acquire()
    with open(lockfile) as f:
        assert f.read().strip() == str(os.getpid())
    h.release()
