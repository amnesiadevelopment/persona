"""A dead connection must cost seconds, not the whole budget.

The engine's transport is urllib, and urllib's timeout covers a single read — so with
`timeout=600` a Tor exit that goes silent freezes the download for ten minutes at the
same byte count before the resume loop gets a turn. The app updater already solved this
for its curl path with `--speed-limit`/`--speed-time`: abort when nothing is arriving,
then resume. These tests hold the urllib path to the same promise.

The server here is the honest case: it accepts, promises a large body, sends a little,
and then goes silent WITHOUT closing. A closed socket is easy to detect; a silent open
one is what actually happens and what hangs.
"""
import socket
import threading
import time

import pytest

from src.utils import httpdl


class SilentPeer:
    """Sends a few bytes, then never speaks again, holding the socket open."""

    def __init__(self, total=100_000_000, preamble=4096):
        self.total = total
        self.preamble = preamble
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(5)
        self._srv.settimeout(0.5)
        self.port = self._srv.getsockname()[1]
        self._stop = threading.Event()
        self._held = []
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=5)
        for c in self._held:
            try:
                c.close()
            except OSError:
                pass
        self._srv.close()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/asset.bin"

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except (socket.timeout, OSError):
                continue
            try:
                conn.recv(65536)
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Length: %d\r\n"
                    b"Accept-Ranges: bytes\r\n\r\n" % self.total)
                conn.sendall(b"x" * self.preamble)
                self._held.append(conn)       # held open, deliberately silent
            except OSError:
                pass


def test_a_silent_peer_costs_the_stall_window_not_the_budget(tmp_path):
    """The whole point: silence is noticed in seconds even when the budget is huge."""
    dest = str(tmp_path / "asset.bin")
    with SilentPeer() as peer:
        started = time.monotonic()
        ok = httpdl.resumable_download(
            dest, peer.url, timeout=600, digest="0" * 64,
            max_attempts=1, stall_timeout=3)
        elapsed = time.monotonic() - started

    assert ok is False
    # Generous upper bound: the assertion is "seconds, not the 600s budget".
    assert elapsed < 30, (
        f"a silent peer held the transfer for {elapsed:.0f}s; the stall window was 3s"
    )


def test_the_stall_window_is_what_bounds_it(tmp_path):
    """Two windows, two durations — so the number in the knob is the number that acts.

    Without this, a download that merely failed fast for some unrelated reason would
    satisfy the test above and the knob could be doing nothing.
    """
    timings = {}
    for window in (2, 6):
        dest = str(tmp_path / f"asset-{window}.bin")
        with SilentPeer() as peer:
            started = time.monotonic()
            httpdl.resumable_download(
                dest, peer.url, timeout=600, digest="0" * 64,
                max_attempts=1, stall_timeout=window)
            timings[window] = time.monotonic() - started

    assert timings[6] > timings[2] + 2, (
        f"the stall window did not govern the wait: {timings}")


def test_the_total_budget_still_caps_a_peer_that_keeps_reconnecting(tmp_path):
    """A stall window alone would retry forever; the caller's budget must still end it."""
    dest = str(tmp_path / "asset.bin")
    with SilentPeer() as peer:
        started = time.monotonic()
        ok = httpdl.resumable_download(
            dest, peer.url, timeout=6, digest="0" * 64,
            max_attempts=40, stall_timeout=1)
        elapsed = time.monotonic() - started

    assert ok is False
    assert elapsed < 40, (
        f"40 attempts ran for {elapsed:.0f}s despite a 6s budget")


def test_the_default_stall_window_matches_the_app_updater(tmp_path):
    """One project, one answer for 'how long is silence allowed to last'.

    The curl path uses --speed-time 30. A different number here would mean two
    policies, and the drift the shared module exists to prevent.
    """
    from src.services.app_update import updater as app_updater

    assert httpdl.STALL_TIMEOUT == app_updater._SPEED_TIME
