import os
import hashlib

import pytest

from src.services.engine import updater


class _Opener:
    """Stand-in for the range-preserving opener: .open(req) returns the next
    fake response and records the Range header the caller sent."""

    def __init__(self, factory):
        self._factory = factory
        self.ranges = []

    def open(self, req, timeout=0):
        self.ranges.append(req.headers.get("Range"))
        return self._factory(req)


def _wire_engine_dir(monkeypatch, tmp_path):
    """Point ENGINE_BINARY/MARKER_FILE/VERSION_FILE at tmp_path and force
    non-macOS so is_installed() checks the plain binary + completion marker."""
    monkeypatch.setattr(updater._platform, "IS_MACOS", False)
    monkeypatch.setattr(updater, "ENGINE_DIR", str(tmp_path))
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(tmp_path / "engine.bin"))
    monkeypatch.setattr(updater, "MARKER_FILE", str(tmp_path / ".engine-complete"))
    monkeypatch.setattr(updater, "VERSION_FILE", str(tmp_path / "version.txt"))
    return tmp_path


def test_is_installed_false_when_missing(tmp_path, monkeypatch):
    _wire_engine_dir(monkeypatch, tmp_path)
    assert updater.is_installed() is False


def test_is_installed_true_when_present_and_marked(tmp_path, monkeypatch):
    d = _wire_engine_dir(monkeypatch, tmp_path)
    (d / "engine.bin").write_bytes(b"\x00" * 10)
    (d / ".engine-complete").touch()
    assert updater.is_installed() is True


def test_is_installed_false_when_binary_present_but_no_marker(tmp_path, monkeypatch):
    # binary on disk but NO completion marker and NO version.txt = a partial
    # extract mid-install; must not read as installed (a broken engine can't launch)
    d = _wire_engine_dir(monkeypatch, tmp_path)
    (d / "engine.bin").write_bytes(b"\x00" * 10)
    assert updater.is_installed() is False


def test_is_installed_true_for_legacy_version_txt(tmp_path, monkeypatch):
    # engines installed before the marker existed have version.txt (written last
    # by ensure_engine) — treat that as the completion signal so we don't force a
    # needless re-download on upgrade
    d = _wire_engine_dir(monkeypatch, tmp_path)
    (d / "engine.bin").write_bytes(b"\x00" * 10)
    (d / "version.txt").write_text("148.0.7778.215", encoding="utf-8")
    assert updater.is_installed() is True


def test_is_installed_false_when_empty(tmp_path, monkeypatch):
    # a zero-byte file is a failed/partial download, not a usable engine
    d = _wire_engine_dir(monkeypatch, tmp_path)
    (d / "engine.bin").touch()
    (d / ".engine-complete").touch()
    assert updater.is_installed() is False


def test_sha256_matches():
    data = b"hello world"
    digest = hashlib.sha256(data).hexdigest()
    assert updater.sha256_ok(data, digest) is True
    assert updater.sha256_ok(data, "deadbeef") is False


def test_sha256_accepts_github_digest_prefix():
    # the GitHub API "digest" field looks like "sha256:abcd..."
    data = b"x"
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    assert updater.sha256_ok(data, digest) is True


def test_sha256_fails_closed_when_no_digest():
    # no digest available -> refuse (an unverifiable file could be a MITM swap)
    assert updater.sha256_ok(b"anything", "") is False
    assert updater.sha256_ok(b"anything", None) is False


def test_sha256_allow_missing_lets_unverified_pass():
    # the one place with no digest source (Linux predictable-URL fallback)
    # opts in explicitly
    assert updater.sha256_ok(b"anything", "", allow_missing=True) is True
    assert updater.sha256_ok(b"anything", None, allow_missing=True) is True
    # a present-but-wrong digest is still rejected even with allow_missing
    assert updater.sha256_ok(b"anything", "deadbeef", allow_missing=True) is False


def test_download_to_refuses_missing_digest(tmp_path, monkeypatch):
    # a download with no digest must not install: the .part is discarded and
    # _download_to returns False (fail-closed)
    path = tmp_path / "engine.bin"
    payload = b"engine-bytes"

    class FakeResp:
        status = 200
        headers = {"Content-Length": str(len(payload))}

        def __enter__(self): return self
        def __exit__(self, *a): return False

        def __init__(self):
            self._data = [payload, b""]

        def read(self, n):
            return self._data.pop(0) if self._data else b""

    monkeypatch.setattr(
        updater, "range_opener", lambda: _Opener(lambda *a, **k: FakeResp())
    )
    ok = updater._download_to(str(path), "http://x/engine", 5, None, None)
    assert ok is False
    assert not path.exists()
    assert not (tmp_path / "engine.bin.part").exists()


def test_download_to_allows_missing_digest_when_opted_in(tmp_path, monkeypatch):
    # Linux fallback path: allow_missing=True lets an un-digested asset install
    path = tmp_path / "engine.bin"
    payload = b"engine-bytes"

    class FakeResp:
        status = 200
        headers = {"Content-Length": str(len(payload))}

        def __enter__(self): return self
        def __exit__(self, *a): return False

        def __init__(self):
            self._data = [payload, b""]

        def read(self, n):
            return self._data.pop(0) if self._data else b""

    monkeypatch.setattr(
        updater, "range_opener", lambda: _Opener(lambda *a, **k: FakeResp())
    )
    ok = updater._download_to(
        str(path), "http://x/engine", 5, None, None, allow_missing=True
    )
    assert ok is True
    assert path.read_bytes() == payload


def test_download_to_uses_range_preserving_opener():
    # audit7 #8: GitHub 302s to a signed CDN URL and the default redirect handler
    # drops the Range header, so every resume re-downloads the whole file (200)
    # — over Tor that never finishes. _download_to must build its opener from the
    # range-preserving one so the tail request survives the redirect.
    import inspect

    src = inspect.getsource(updater._download_to)
    assert "range_opener()" in src
    assert "urlopen" not in src  # no bare urlopen that would lose Range


def test_download_to_resumes_with_206_after_a_dropped_connection(tmp_path, monkeypatch):
    # First open: partial body then EOF (dropped circuit). Second open: sees the
    # Range header, returns 206 with the tail, appends to the .part. The verified
    # digest of the concatenation matches → install succeeds.
    path = tmp_path / "engine.bin"
    full = b"ABCDEFGHIJ"  # 10 bytes
    digest = hashlib.sha256(full).hexdigest()

    class Resp:
        def __init__(self, body, start, total):
            self._data = [body, b""]
            self.status = 206 if start else 200
            self.headers = {
                "Content-Range": f"bytes {start}-{start + len(body) - 1}/{total}"
            }

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return self._data.pop(0) if self._data else b""

    def factory(req):
        rng = req.headers.get("Range")
        start = int(rng.split("=")[1].split("-")[0]) if rng else 0
        # first call drops after 4 bytes; the resume delivers the rest
        return Resp(full[start:4] if start == 0 else full[start:], start, len(full))

    opener = _Opener(factory)
    monkeypatch.setattr(updater, "range_opener", lambda: opener)

    ok = updater._download_to(str(path), "http://x/engine", 5, digest, None)
    assert ok is True
    assert path.read_bytes() == full
    # the resume actually sent a Range header (proves append-not-restart)
    assert any(r and r.startswith("bytes=4-") for r in opener.ranges)


# --- PS-6: the Linux engine install gained the rollback the app updater had ---


def test_install_linux_restores_the_working_engine_when_the_swap_fails(
    tmp_path, monkeypatch
):
    # _install_linux used to be a bare os.replace + chmod with NO backup: a
    # failed swap left the user with no engine at all, while the app updater's
    # AppImage swap had kept a .bak and restored it "so we never lose a
    # launchable app" since v2.1.3. Both now go through the shared
    # atomic_replace, so the engine gets that same guarantee.
    engine = tmp_path / "engine.AppImage"
    engine.write_bytes(b"working-engine")
    asset = tmp_path / "downloaded.AppImage"
    asset.write_bytes(b"new-engine")
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(engine))

    real_replace = updater.os.replace

    def failing_replace(src, dst):
        if str(src) == str(asset):
            raise OSError("Text file busy")  # the asset->engine swap fails
        return real_replace(src, dst)  # the backup->engine restore succeeds

    monkeypatch.setattr(updater.os, "replace", failing_replace)

    assert updater._install_linux(str(asset)) is False
    # the working engine is still there, and still WORKING — not a leftover
    # .bak the launcher can't find, and not a half-written file
    assert engine.read_bytes() == b"working-engine"
    assert not (tmp_path / "engine.AppImage.bak").exists()


def test_install_linux_swaps_in_the_new_engine_and_leaves_no_backup(
    tmp_path, monkeypatch
):
    # the success path: the new engine is in place, executable, and the backup
    # is cleaned up rather than left behind to grow on every update
    engine = tmp_path / "engine.AppImage"
    engine.write_bytes(b"old-engine")
    asset = tmp_path / "downloaded.AppImage"
    asset.write_bytes(b"new-engine")
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(engine))

    assert updater._install_linux(str(asset)) is True
    assert engine.read_bytes() == b"new-engine"
    assert not (tmp_path / "engine.AppImage.bak").exists()
    assert os.access(str(engine), os.X_OK)
