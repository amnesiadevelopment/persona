"""Shared corrupt-file quarantine + save-blocked guard for the JSON stores.

Every class-based JSON store under src/services/ holds data the user cannot
regenerate — profiles, bookmarks, and three credential files (SOCKS5 proxy
creds, SSH passwords/passphrases, .p12 bundle passwords). They all need the
same two-part protection against silently destroying it:

1. If the file fails to parse, rename it to ``<path>.corrupt-<ts>`` so the
   next save writes a fresh file beside the original instead of over it.
2. If that rename fails, set ``_save_blocked`` so no later save can overwrite
   the unreadable-but-real file with the empty in-memory dict.

That guard was hand-rolled five times and was added to four of the five stores
only after an audit caught the omission — cert/store.py, which holds every
.p12 password, went three audits unguarded. Inheriting this mixin gives a new
store the guard by construction instead of by audit.

A store mixes it in and supplies four hooks:

* ``_store_path()`` — the file to guard. Resolved **at call time**, because
  some stores read a module global the tests monkeypatch and others compute
  the path from an env var on every call.
* ``_guard_logger`` — the store's own ``get_logger("<store>.<module>")``
  channel, so log records keep their existing per-module channel name.
* ``_guard_noun_plural`` / ``_guard_noun_singular`` — the store's nouns. Both
  forms are needed: the file is named in the plural ("certificates file") and
  the activity in the singular ("certificate saving disabled").

An injectable clock (``self._now``) is honoured when the store has one, so a
store that takes ``now=`` for testability keeps controlling the backup suffix.
"""

from __future__ import annotations

import logging
import pathlib
import time


class StoreGuardMixin:
    """Quarantine a corrupt store file and refuse saves that would destroy it."""

    # Overridden per store with that store's own logger/nouns.
    _guard_logger: logging.Logger
    _guard_noun_plural: str
    _guard_noun_singular: str

    # Class-level default so a store that mixes this in is guarded even before
    # its __init__ runs (and if a future store forgets to initialize it).
    _save_blocked: bool = False

    def _store_path(self) -> str:
        """Path of the JSON file this store persists to.

        Resolved on every call, never captured at import time: the profile
        store reads a module global its tests monkeypatch, and the ssh/cert
        stores recompute their path from an env-var override each call.
        """
        raise NotImplementedError

    def _quarantine_store_file(self) -> None:
        """Move the unreadable file aside so the next save can't overwrite it.

        If the move fails, block saving instead — leaving the corrupt file in
        place is recoverable, overwriting it with an empty dict is not.
        """
        path = self._store_path()
        # self._now is ProxyStore's injectable clock; every other store uses
        # the wall clock directly.
        now = getattr(self, "_now", time.time)
        backup = f"{path}.corrupt-{int(now())}"
        try:
            pathlib.Path(path).rename(backup)
            self._guard_logger.warning(
                f"Moved unreadable {self._guard_noun_plural} file to %s", backup
            )
        except OSError:
            self._save_blocked = True
            self._guard_logger.exception(
                f"Could not back up %s; {self._guard_noun_singular} saving "
                "disabled to avoid overwriting it",
                path,
            )

    def _save_is_blocked(self) -> bool:
        """True when a save must be refused because quarantine failed."""
        if not self._save_blocked:
            return False
        self._guard_logger.error(
            f"Not saving: {self._guard_noun_plural} file failed to load and "
            "could not be backed up"
        )
        return True
