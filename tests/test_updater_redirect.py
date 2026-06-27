"""Regression: remote_size must read the size from the FINAL response after a
GitHub 302 redirect, not the redirect's 'Content-Length: 0'."""

import subprocess

from src.services.app_update import updater as up


class _R:
    def __init__(self, out, rc=0):
        self.stdout = out.encode()
        self.returncode = rc


def test_remote_size_takes_last_nonzero_content_length(monkeypatch):
    # curl -I output across a 302 -> CDN: first CL is 0, real size in the second.
    headers = (
        "HTTP/2 302\r\n"
        "location: https://cdn.example/asset\r\n"
        "content-length: 0\r\n"
        "\r\n"
        "HTTP/2 200\r\n"
        "content-type: application/octet-stream\r\n"
        "content-length: 102689272\r\n"
        "\r\n"
    )
    monkeypatch.setattr(
        up.subprocess, "run", lambda *a, **k: _R(headers, rc=0)
    )
    assert up.remote_size("http://x") == 102689272


def test_remote_size_zero_when_curl_fails(monkeypatch):
    monkeypatch.setattr(up.subprocess, "run", lambda *a, **k: _R("", rc=7))
    assert up.remote_size("http://x") == 0


def test_remote_size_zero_for_empty_url():
    assert up.remote_size("") == 0


def test_download_completes_on_416_when_file_full(monkeypatch, tmp_path):
    # Simulate: staged file already complete, curl -C - returns 416 (rc 33).
    app = tmp_path / "persona.AppImage"
    app.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(app))
    staged = up.staged_path()
    with open(staged, "wb") as f:
        f.write(b"z" * 100)
    monkeypatch.setattr(up, "remote_size", lambda url, timeout=30: 100)

    class R:
        returncode = 33  # curl HTTP 416 Range Not Satisfiable

    monkeypatch.setattr(up.subprocess, "run", lambda *a, **k: R())
    out = up.download_update("http://x")
    assert out == staged  # recognised as complete, not stuck looping
