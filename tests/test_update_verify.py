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
    # asset not in checksums.txt AND no sidecar → empty
    def curl(url, **k):
        if url.endswith("checksums.txt"):
            return "aaaa  other-file.zip\n"
        return ""  # no sidecar
    monkeypatch.setattr(au, "_curl_get", curl)
    assert au.fetch_expected_sha256("v9.9.9", name="persona-windows-setup.exe") == ""


def test_fetch_expected_sha256_reads_sidecar_for_mac_linux(monkeypatch):
    # #6 (audit4 HIGH): mac dmg / linux AppImage aren't in checksums.txt; their
    # hash lives in a per-asset {asset}.sha256 sidecar. The updater must read it,
    # else verify falls open on mac/linux (RCE via a swapped installer).
    def curl(url, **k):
        if url.endswith("checksums.txt"):
            return "aaaa  persona-windows-setup.exe\n"  # asset not here
        if url.endswith("persona-x86_64.AppImage.sha256"):
            return "deadbeefcafe  persona-x86_64.AppImage\n"
        return ""
    monkeypatch.setattr(au, "_curl_get", curl)
    got = au.fetch_expected_sha256("v9.9.9", name="persona-x86_64.AppImage")
    assert got == "deadbeefcafe"


def test_fetch_expected_sha256_checksums_txt_still_wins_for_windows(monkeypatch):
    # the windows exe hash still comes from the combined checksums.txt
    def curl(url, **k):
        if url.endswith("checksums.txt"):
            return "1234abcd  persona-windows-setup.exe\n"
        return ""
    monkeypatch.setattr(au, "_curl_get", curl)
    got = au.fetch_expected_sha256("v9.9.9", name="persona-windows-setup.exe")
    assert got == "1234abcd"


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


def test_tag_recovered_from_linux_appimage_part():
    # audit7 #2: the Linux staged name (.persona-update-<tag>.AppImage.part) must
    # yield the real tag — it used to return '' → checksum lookup skipped → an
    # unverified AppImage ran.
    assert au._tag_from_staged(".persona-update-v2.9.14.AppImage.part") == "v2.9.14"
    assert au._tag_from_staged("/tmp/.persona-update-v2.9.14.AppImage.part") == "v2.9.14"
    # the tagless name still yields '' (no tag baked in)
    assert au._tag_from_staged(".persona-update.AppImage.part") == ""


def test_linux_appimage_verify_actually_checks_checksum(monkeypatch, tmp_path):
    # audit7 #2: a staged Linux AppImage with a recoverable tag must be sha256-
    # verified, not fall through to the fail-OPEN size-only branch. A mismatching
    # checksum must REFUSE (an unverified/substituted AppImage must never run).
    staged = tmp_path / ".persona-update-v2.9.14.AppImage.part"
    staged.write_bytes(b"attacker-substituted-appimage")
    seen = {}

    def fetch(tag, **k):
        seen["tag"] = tag
        return "0" * 64  # a real published checksum that WON'T match

    monkeypatch.setattr(au, "fetch_expected_sha256", fetch)
    msgs = []
    assert au.verify_staged_installer(str(staged), log=msgs.append) is False
    assert seen["tag"] == "v2.9.14", "tag must be recovered so the checksum is fetched"
    assert any("mismatch" in m.lower() for m in msgs)


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
