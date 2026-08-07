"""Atomic JSON writes.

A truncate-and-write in place loses the whole file if the process dies mid-write
(crash, kill, power loss). Serializing to a sibling temp file and renaming it
over the target makes the swap atomic: a reader sees either the old file or the
whole new one, never a half-written one. Credential files are additionally
written 0600 so they aren't world-readable on multi-user systems.
"""

from __future__ import annotations

import json
import os
import tempfile


def atomic_write_json(path: str, data, *, private: bool = False) -> None:
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    # Serialize FIRST: if the data can't be encoded, raise before touching the
    # real file so a bad write can't destroy the existing good copy.
    text = json.dumps(data, indent=2)
    # A UNIQUE temp per call (not a fixed "<path>.new"): two threads writing the
    # same target concurrently would otherwise share one temp and interleave into
    # a corrupt file before the rename. fsync before replace so the bytes are on
    # disk if power is lost right after the (atomic) rename.
    fd, tmp = tempfile.mkstemp(
        dir=parent, prefix=os.path.basename(path) + ".", suffix=".new"
    )
    try:
        os.write(fd, text.encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        fd = -1
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


def _umask() -> int:
    # read-and-restore; there's no getter for the umask
    m = os.umask(0)
    os.umask(m)
    return m
