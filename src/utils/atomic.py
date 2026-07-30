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


def atomic_write_json(path: str, data, *, private: bool = False) -> None:
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".new"
    # Serialize FIRST: if the data can't be encoded, raise before touching the
    # real file so a bad write can't destroy the existing good copy.
    text = json.dumps(data, indent=2)
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, text.encode("utf-8"))
        finally:
            os.close(fd)
        if not private:
            # match the process umask for non-secret files
            os.chmod(tmp, 0o644 & ~_umask())
        os.replace(tmp, path)
    except Exception:
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
