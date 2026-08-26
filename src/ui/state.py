import glob
import os
import re
import threading
import time
from collections import deque

from ..core.config import LOG_DIR
from ..core.logging import SESSION_MARKER

ITEMS_PER_PAGE = 8

# Cap the in-memory Activity Log. It was an unbounded list that grew for the
# whole session, and the fullscreen log dialog materialised every line in one
# patch — a slow open and steady memory growth on a long-running session (audit6
# LOW f). A bounded ring keeps the recent history the UI actually shows; older
# lines remain on disk in the persistent file log.
_MAX_LOG_LINES = 2000


def _load_recent_log_lines(limit: int = 200) -> list[str]:
    """Seed the Activity Log from the persistent file log, starting at the
    current session's SESSION_MARKER so old sessions don't blur into this
    launch; earlier sessions stay on disk in the same file. Reads the newest
    persona_*.log in LOG_DIR and reformats its lines to the UI's
    "HH:MM:SS  > message" shape."""
    try:
        candidates = sorted(glob.glob(os.path.join(LOG_DIR, "persona_*.log")))
        if not candidates:
            return []
        with open(candidates[-1], encoding="utf-8", errors="replace") as f:
            raw = f.readlines()
        for i in range(len(raw) - 1, -1, -1):
            if SESSION_MARKER in raw[i]:
                raw = raw[i:]
                break
        raw = raw[-limit:]
        out = []
        # file lines look like: "2026-06-29 17:07:52 - INFO - persona.api - msg"
        for ln in raw:
            m = re.match(
                r"\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2}) - \w+ - [\w.]+ - (.*)",
                ln.rstrip("\n"),
            )
            if m:
                out.append(f"{m.group(1)}  > {m.group(2)}")
        return out
    except Exception:
        return []


class AppState:
    def __init__(self) -> None:
        self.current_page: int = 1
        self.log_collapsed: bool = False

        self._log_lines: "deque[str]" = deque(
            _load_recent_log_lines(), maxlen=_MAX_LOG_LINES
        )
        # How many lines this ring has EVER accepted, seed included. The
        # Activity Log console appends the difference between this and what it
        # last painted instead of re-rendering the tail, which is what lets a
        # scroll position survive an arrival. A COUNTER rather than a text
        # diff because two profiles can fail with byte-identical lines, and a
        # diff would collapse them into one.
        self._log_seq: int = len(self._log_lines)
        self._loading_profiles: set[str] = set()
        self._loading_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._last_log_ui_update: float = 0.0
        self._pending_log_flush: bool = False
        self._refresh_requested = threading.Event()
        self._last_running_snapshot: set[str] = set()
        self._ui_update_lock = threading.Lock()
        self._selected_profiles: set[str] = set()
        self._selection_lock = threading.Lock()

    def is_loading(self, name: str) -> bool:
        with self._loading_lock:
            return name in self._loading_profiles

    def set_loading(self, name: str, value: bool) -> None:
        with self._loading_lock:
            if value:
                self._loading_profiles.add(name)
            else:
                self._loading_profiles.discard(name)

    def schedule_refresh(self) -> None:
        self._refresh_requested.set()

    def consume_refresh(self) -> bool:
        if self._refresh_requested.is_set():
            self._refresh_requested.clear()
            return True
        return False

    def add_log(self, message: str) -> bool:
        force = (
            message == "Browser started!"
            or message.startswith("Session ended:")
            or "LAUNCH_FAILED:" in message
            or "Error" in message
        )
        now = time.monotonic()
        stamp = time.strftime("%H:%M:%S")
        with self._log_lock:
            self._log_lines.append(f"{stamp}  > {message}")
            self._log_seq += 1
            if force or now - self._last_log_ui_update >= 0.15:
                self._last_log_ui_update = now
                self._pending_log_flush = True
                return True
        return False

    #: How deep a tail the Activity Log console is fed. The old panel took 50
    #: and painted 6 of them into a 150px box, which is why there was nothing
    #: to scroll. The console retains its own bounded list (LogDock.MAX_ROWS),
    #: so this only has to be deep enough to REBUILD a useful scrollback on a
    #: cold start or after a wipe; steady-state growth is append-only.
    FLUSH_TAIL = 600

    def flush_log(self) -> str | None:
        with self._log_lock:
            if not self._pending_log_flush:
                return None
            self._pending_log_flush = False
            # deque doesn't support slicing; snapshot then take the tail.
            return "\n".join(list(self._log_lines)[-self.FLUSH_TAIL :])

    def log_seq(self) -> int:
        """How many lines the ring has ever accepted.

        The console diffs against this to know how many rows are NEW, so it can
        append them and leave every existing row — and every scroll position —
        where it is.
        """
        with self._log_lock:
            return self._log_seq

    def get_all_log_lines(self) -> list[str]:
        with self._log_lock:
            return list(self._log_lines)

    def clear_log(self) -> None:
        """Drop the in-memory Activity Log.

        The panic wipe clears the FILE log, but this ring is a SEPARATE copy of
        it: _load_recent_log_lines() only ever seeds the ring at __init__ (i.e.
        at app startup), after which it accumulates independently via add_log().
        So clearing the file alone leaves every wiped profile's name rendered in
        the sidebar panel and in the fullscreen Activity Log dialog for the rest
        of the session — the operator performs the wipe, opens the log, and reads
        the identity straight back. Sets the flush flag so the panel repaints
        empty on the next _flush_log() instead of keeping its last painted lines
        until some later log line happens to arrive."""
        with self._log_lock:
            self._log_lines.clear()
            # Backwards is the signal the console rebuilds on: the ring it was
            # appending to is gone, so its painted rows are wiped names and
            # must not survive the wipe.
            self._log_seq = 0
            self._pending_log_flush = True

    def toggle_selection(self, name: str) -> None:
        with self._selection_lock:
            if name in self._selected_profiles:
                self._selected_profiles.discard(name)
            else:
                self._selected_profiles.add(name)

    def is_selected(self, name: str) -> bool:
        with self._selection_lock:
            return name in self._selected_profiles

    def selected_names(self) -> set[str]:
        with self._selection_lock:
            return set(self._selected_profiles)

    def clear_selection(self) -> None:
        with self._selection_lock:
            self._selected_profiles.clear()

    def select_all(self, names: list[str]) -> None:
        with self._selection_lock:
            self._selected_profiles = set(names)
