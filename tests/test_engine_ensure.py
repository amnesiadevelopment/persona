import hashlib

import pytest

from src.services.engine import updater


def test_is_installed_false_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(updater._platform, "IS_MACOS", False)
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(tmp_path / "engine.bin"))
    assert updater.is_installed() is False


def test_is_installed_true_when_present(tmp_path, monkeypatch):
    p = tmp_path / "engine.bin"
    p.write_bytes(b"\x00" * 10)
    monkeypatch.setattr(updater._platform, "IS_MACOS", False)
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(p))
    assert updater.is_installed() is True


def test_is_installed_false_when_empty(tmp_path, monkeypatch):
    # a zero-byte file is a failed/partial download, not a usable engine
    p = tmp_path / "engine.bin"
    p.touch()
    monkeypatch.setattr(updater._platform, "IS_MACOS", False)
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(p))
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


def test_sha256_skipped_when_no_digest():
    # no digest available -> don't block the install
    assert updater.sha256_ok(b"anything", "") is True
    assert updater.sha256_ok(b"anything", None) is True
