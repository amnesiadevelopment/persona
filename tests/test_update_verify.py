"""The Windows self-update must verify the staged installer's sha256 against
the release's published checksums.txt BEFORE running it. Size-only completeness
let a truncated/corrupted download execute as an installer (a broken half
install). A fetched-and-mismatching checksum always refuses; a checksum that
can't be fetched (older release without one, or network down) falls back to the
existing size check so old releases still update."""

import hashlib
import os
import sys

import pytest

import src.services.app_update.updater as au


def _write_staged(tmp_path, data=b"installer-bytes", tag="v9.9.9"):
    staged = tmp_path / f"persona-update-setup-{tag}.exe"
    staged.write_bytes(data)
    return staged, hashlib.sha256(data).hexdigest()


# --- fetch_expected_sha256: parsing + retry ---


def test_fetch_expected_sha256_parses_checksums_txt(monkeypatch):
    body = (
        "aaaa1111  persona-x86_64.AppImage\n"
        "bbbb2222 *persona-windows-setup.exe\n"
    )
    urls = []
    monkeypatch.setattr(au, "_curl_get", lambda url, **k: urls.append(url) or body)
    got = au.fetch_expected_sha256("v9.9.9", name="persona-windows-setup.exe")
    assert got == "bbbb2222"
    assert "releases/download/v9.9.9/checksums.txt" in urls[0]


def test_fetch_expected_sha256_retries_then_gives_up(monkeypatch):
    calls = {"n": 0}

    def curl(url, **k):
        calls["n"] += 1
        return ""

    monkeypatch.setattr(au, "_curl_get", curl)
    assert au.fetch_expected_sha256("v9.9.9", name="x.exe") == ""
    assert calls["n"] > 1  # a transient fetch failure is retried


def test_fetch_expected_sha256_empty_when_asset_not_listed(monkeypatch):
    monkeypatch.setattr(au, "_curl_get", lambda url, **k: "aaaa  other-file.zip\n")
    assert au.fetch_expected_sha256("v9.9.9", name="persona-windows-setup.exe") == ""


# --- verify_staged_installer ---


def test_verify_passes_on_matching_sha256(monkeypatch, tmp_path):
    staged, digest = _write_staged(tmp_path)
    monkeypatch.setattr(au, "fetch_expected_sha256", lambda tag, **k: digest)
    msgs = []
    assert au.verify_staged_installer(str(staged), log=msgs.append) is True


def test_verify_refuses_on_mismatching_sha256(monkeypatch, tmp_path):
    staged, _ = _write_staged(tmp_path)
    monkeypatch.setattr(au, "fetch_expected_sha256", lambda tag, **k: "0" * 64)
    msgs = []
    assert au.verify_staged_installer(str(staged), log=msgs.append) is False
    assert any("checksum" in m.lower() for m in msgs)


def test_verify_falls_back_when_checksum_unavailable(monkeypatch, tmp_path):
    staged, _ = _write_staged(tmp_path)
    monkeypatch.setattr(au, "fetch_expected_sha256", lambda tag, **k: "")
    msgs = []
    assert au.verify_staged_installer(str(staged), log=msgs.append) is True
    assert any("checksum" in m.lower() for m in msgs)  # warns, doesn't block


def test_verify_uses_tag_from_staged_filename(monkeypatch, tmp_path):
    staged, digest = _write_staged(tmp_path, tag="v2.4.0")
    seen = {}

    def fetch(tag, **k):
        seen["tag"] = tag
        return digest

    monkeypatch.setattr(au, "fetch_expected_sha256", fetch)
    assert au.verify_staged_installer(str(staged)) is True
    assert seen["tag"] == "v2.4.0"


# --- apply_and_restart (Windows path) must not launch a refused installer ---


def _force_windows(monkeypatch):
    monkeypatch.setattr(au._platform, "IS_WINDOWS", True)
    # These tests exercise the FULL-installer path; keep the #205 code-only fast
    # path out of the way (it would otherwise probe the real installed app.zip on
    # a Windows dev host and add a manifest-fetch curl before the installer).
    monkeypatch.setattr(au, "_try_windows_fast_update", lambda say: False)


def test_apply_refuses_to_launch_on_checksum_mismatch(monkeypatch, tmp_path):
    _force_windows(monkeypatch)
    staged, _ = _write_staged(tmp_path)
    monkeypatch.setattr(au, "fetch_expected_sha256", lambda tag, **k: "0" * 64)
    monkeypatch.setattr(
        au.subprocess, "Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("installer launched!")),
    )
    monkeypatch.setattr(
        au.os, "_exit", lambda *a: (_ for _ in ()).throw(AssertionError("exited!"))
    )
    msgs = []
    ok = au.apply_and_restart(str(staged), log=msgs.append)
    assert ok is False
    assert not staged.exists()  # corrupt file dropped so it re-downloads fresh
    assert any("checksum" in m.lower() for m in msgs)


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="exercises the real Windows apply_and_restart os._exit path",
)
def test_apply_launches_on_matching_checksum(monkeypatch, tmp_path):
    _force_windows(monkeypatch)
    staged, digest = _write_staged(tmp_path)
    monkeypatch.setattr(au, "fetch_expected_sha256", lambda tag, **k: digest)
    launched = []
    monkeypatch.setattr(au.subprocess, "Popen", lambda *a, **k: launched.append(a))
    monkeypatch.setattr(au, "_installed_windows_exe", lambda: "")

    class Exit(Exception):
        pass

    monkeypatch.setattr(au.os, "_exit", lambda *a: (_ for _ in ()).throw(Exit()))
    with pytest.raises(Exit):
        au.apply_and_restart(str(staged), log=lambda m: None)
    assert launched and str(staged) in launched[0][0]


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="exercises the real Windows apply_and_restart os._exit path",
)
def test_apply_launches_when_checksum_unavailable(monkeypatch, tmp_path):
    _force_windows(monkeypatch)
    staged, _ = _write_staged(tmp_path)
    monkeypatch.setattr(au, "fetch_expected_sha256", lambda tag, **k: "")
    launched = []
    monkeypatch.setattr(au.subprocess, "Popen", lambda *a, **k: launched.append(a))
    monkeypatch.setattr(au, "_installed_windows_exe", lambda: "")

    class Exit(Exception):
        pass

    monkeypatch.setattr(au.os, "_exit", lambda *a: (_ for _ in ()).throw(Exit()))
    with pytest.raises(Exit):
        au.apply_and_restart(str(staged), log=lambda m: None)
    assert launched and str(staged) in launched[0][0]
