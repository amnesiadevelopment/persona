"""#228: the FF-engine resumable download must survive a slow, flaky Tor
circuit — it bounds retries by CONSECUTIVE no-progress attempts (not a flat
cap), so a transfer that keeps advancing through many drops still completes,
and it backs off between failed tries instead of hammering a dead circuit."""

import urllib.request

import src.services.browser.invisible_launch as inv


class _Resp:
    """A fake HTTP response that yields `payload` then (optionally) 'drops' —
    read() returns b'' early so the caller sees a short read and resumes."""

    def __init__(self, payload: bytes, total: int, start: int):
        self._buf = payload
        self._i = 0
        self.status = 206 if start else 200
        self.headers = {
            "Content-Range": f"bytes {start}-{start + len(payload) - 1}/{total}",
        }

    def read(self, n):
        chunk = self._buf[self._i : self._i + n]
        self._i += len(chunk)
        return chunk

    def close(self):
        pass


def _wire(monkeypatch, chunks, total):
    """chunks: list of byte payloads to hand out on successive opener.open()
    calls (each a partial that then drops). Their concatenation == the file."""
    calls = {"open": 0, "sleep": 0}

    class FakeOpener:
        def open(self, req, timeout=0):
            calls["open"] += 1
            rng = req.headers.get("Range") or req.get_header("Range")
            start = int(rng.split("=")[1].split("-")[0]) if rng else 0
            idx = min(calls["open"] - 1, len(chunks) - 1)
            return _Resp(chunks[idx], total, start)

    monkeypatch.setattr(
        urllib.request, "build_opener", lambda *a, **k: FakeOpener()
    )
    monkeypatch.setattr(
        inv.time, "sleep",
        lambda s: calls.__setitem__("sleep", calls["sleep"] + 1),
    )
    return calls


def test_completes_through_many_progressing_drops(monkeypatch, tmp_path):
    # 20 one-byte partials, each a separate dropped circuit — far more drops
    # than any flat attempt cap, but every one makes progress, so it finishes.
    path = tmp_path / "engine.download"
    total = 20
    chunks = [bytes([65 + i]) for i in range(total)]
    _wire(monkeypatch, chunks, total)

    ok = inv._resumable_download("http://x", str(path))
    assert ok is True
    assert path.stat().st_size == total


def test_gives_up_after_consecutive_no_progress(monkeypatch, tmp_path):
    # Every open yields an empty body (a stalled circuit that connects but sends
    # nothing). No progress ever → it must bail (return False), not spin forever.
    path = tmp_path / "engine.download"
    calls = _wire(monkeypatch, [b""], total=100)

    ok = inv._resumable_download("http://x", str(path))
    assert ok is False
    # bounded: it stopped after the no-progress budget, and backed off between
    # tries rather than hammering with zero pause
    assert calls["open"] <= 13
    assert calls["sleep"] >= 1
