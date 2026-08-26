"""Atomic file writes.

A truncate-and-write in place loses the whole file if the process dies mid-write
(crash, kill, power loss). Serializing to a sibling temp file and renaming it
over the target makes the swap atomic: a reader sees either the old file or the
whole new one, never a half-written one. Credential files are additionally
written 0600 so they aren't world-readable on multi-user POSIX systems.

On Windows os.chmod only toggles the read-only bit — it does NOT restrict who
can read the file. The files live under the user's .persona home, which inherits
the per-user ACL of the profile directory (already owner-scoped), so this is not
a regression there; but the 0600 is a POSIX guarantee, not a Windows one.
"""

from __future__ import annotations

import json
import os
import tempfile


def atomic_write_bytes(path: str, data: bytes, *, private: bool = False) -> None:
    """Write `data` to `path` atomically, optionally 0600.

    THE MECHANISM, and why each step is here rather than an obvious
    simplification:

    * A UNIQUE temp per call (not a fixed "<path>.new"): two threads writing the
      same target concurrently would otherwise share one temp and interleave into
      a corrupt file before the rename. `install_secret._create` uses a
      pid-suffixed temp for the same reason.
    * The temp is a SIBLING, in the target's own directory, because `os.replace`
      is only atomic within one filesystem.
    * fsync before replace so the bytes are on disk if power is lost right after
      the (atomic) rename.
    * The chmod happens BEFORE the rename, so the bytes are never briefly
      readable at the FINAL path under a wider mode. A write-then-chmod at the
      final path leaves exactly that window open, which is the defect this
      function exists to make unrepeatable.

    Callers that hold TEXT should encode and call this; `atomic_write_json`
    below is the JSON-shaped caller.
    """
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=parent, prefix=os.path.basename(path) + ".", suffix=".new"
    )
    try:
        os.write(fd, data)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        # POSIX permission bits. On Windows this only clears the read-only bit
        # (no owner-only restriction); see the module docstring.
        os.chmod(tmp, 0o600 if private else (0o644 & ~_umask()))
        os.replace(tmp, path)
    except Exception:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: str, data, *, private: bool = False) -> None:
    # Serialize FIRST: if the data can't be encoded, raise before touching the
    # real file so a bad write can't destroy the existing good copy. This is why
    # the encode stays HERE and is not pushed down into atomic_write_bytes —
    # by the time that function runs, a temp file has already been created.
    text = json.dumps(data, indent=2)
    atomic_write_bytes(path, text.encode("utf-8"), private=private)


def _umask() -> int:
    # read-and-restore; there's no getter for the umask
    m = os.umask(0)
    os.umask(m)
    return m
