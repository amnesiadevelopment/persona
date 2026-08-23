"""Single-instance guard.

Two persona windows sharing one ~/.persona (profiles.json, settings.json, the
engine caches, the MCP port 8000) race each other and corrupt state, so only one
GUI may run at a time. This takes an OS-level EXCLUSIVE lock on a lockfile; the
lock is held by the process and released automatically when it exits — including
a crash or a kill — so a dead instance never leaves a stale lock that blocks the
next start (a plain pid-file check can't offer that).

acquire() returns a handle on success (keep it alive for the process lifetime)
or None when another instance already holds the lock.
"""

import os
import sys

from . import config


def _lock_path() -> str:
    """Resolve the lockfile the same way every other runtime file resolves.

    This hardcoded ~/.persona/persona.lock, the one runtime file that ignored
    PERSONA_HOME. That had two consequences, both on the normal GUI startup
    path: a relocated install CREATED ~/.persona on the host (acquire() makedirs
    the parent) and wrote a live pid into a directory it never used; and two
    deliberately isolated homes resolved to the SAME lockfile, so the second was
    refused — the guard blocking precisely the configuration it exists to allow,
    since two distinct homes share none of the state the docstring names.

    RESOLVED AT CALL TIME, NOT AT IMPORT. `config.PERSONA_HOME` is baked at
    config import (deliberate — config.py:48), but `_under_home` reads its env
    override with getenv at call time (config.py:54). Computing this into a
    module-level constant would freeze PERSONA_LOCK_FILE at THIS module's import
    and break any caller that sets it afterwards, the test fixture included.
    _under_home already implements the override-wins precedence, so the existing
    PERSONA_LOCK_FILE contract composes rather than conflicts.
    """
    return config._under_home("persona.lock", "PERSONA_LOCK_FILE")


class _Handle:
    """Owns the open lock file descriptor; releasing it (or exiting) frees the
    lock. Kept referenced for the whole process so the lock is never dropped
    early by garbage collection."""

    def __init__(self, fh) -> None:
        self._fh = fh

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            fh.close()
        except OSError:
            pass


def acquire():
    """Take the single-instance lock. Returns a handle to hold for the process
    lifetime, or None if another persona already holds it. Any unexpected error
    (a read-only home, a missing lock primitive) fails OPEN — returns a handle —
    because refusing to start at all is worse than a rare second window."""
    path = _lock_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError:
        return _Handle(None)  # can't even make the dir — don't block startup

    try:
        fh = open(path, "a+")
    except OSError:
        return _Handle(None)

    try:
        if sys.platform.startswith("win"):
            import msvcrt

            # Lock one byte non-blocking; a second instance's lock raises.
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Held by another live instance.
        try:
            fh.close()
        except OSError:
            pass
        return None
    except Exception:
        # No lock primitive available — fail open rather than block startup.
        return _Handle(fh)

    # Record our pid for humans debugging a stuck lock; not used for the decision.
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
    except OSError:
        pass
    return _Handle(fh)
