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

_LOCK_PATH = os.path.join(os.path.expanduser("~/.persona"), "persona.lock")


def _lock_path() -> str:
    return os.environ.get("PERSONA_LOCK_FILE", _LOCK_PATH)


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
