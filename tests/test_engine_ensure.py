import hashlib

import pytest

from src.services.engine import updater


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
        updater.urllib.request, "urlopen", lambda *a, **k: FakeResp()
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
        updater.urllib.request, "urlopen", lambda *a, **k: FakeResp()
    )
    ok = updater._download_to(
        str(path), "http://x/engine", 5, None, None, allow_missing=True
    )
    assert ok is True
    assert path.read_bytes() == payload
