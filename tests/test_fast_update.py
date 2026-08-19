"""Windows code-only fast update (#205): most releases change only the app's
Python code (app.zip, ~1MB) — not the 218MB runtime the Inno installer
reinstalls. flet re-extracts app.zip on launch when its sha256 (app.zip.hash)
differs from the extracted marker, so swapping just those two files + a relaunch
is a seconds-fast update. The full installer stays the fallback for releases
that change the runtime/dependencies (gated by the manifest's
requires_full_install).
"""
import json
import os

import pytest

from src.services.app_update import fast_update as fu


def test_should_fast_update_true_when_manifest_allows():
    manifest = {
        "version": "2.6.0",
        "requires_full_install": False,
        "app_zip_sha256": "abc",
    }
    assert fu.should_fast_update(manifest, current="2.5.2") is True


def test_should_fast_update_false_when_requires_full_install():
    manifest = {"version": "2.6.0", "requires_full_install": True, "app_zip_sha256": "abc"}
    assert fu.should_fast_update(manifest, current="2.5.2") is False


def test_should_fast_update_false_when_not_newer():
    manifest = {"version": "2.5.2", "requires_full_install": False, "app_zip_sha256": "abc"}
    assert fu.should_fast_update(manifest, current="2.5.2") is False


def test_should_fast_update_false_without_sha():
    manifest = {"version": "2.6.0", "requires_full_install": False, "app_zip_sha256": ""}
    assert fu.should_fast_update(manifest, current="2.5.2") is False


def test_should_fast_update_false_on_empty_manifest():
    assert fu.should_fast_update({}, current="2.5.2") is False
    assert fu.should_fast_update(None, current="2.5.2") is False


def test_parse_manifest_reads_fields():
    body = json.dumps({
        "version": "2.6.0",
        "requires_full_install": False,
        "app_zip_sha256": "deadbeef",
    })
    m = fu.parse_manifest(body)
    assert m["version"] == "2.6.0"
    assert m["requires_full_install"] is False
    assert m["app_zip_sha256"] == "deadbeef"


def test_parse_manifest_bad_json_returns_none():
    assert fu.parse_manifest("not json") is None
    assert fu.parse_manifest("") is None


def test_install_app_zip_paths_finds_flutter_assets(tmp_path, monkeypatch):
    # the install dir holds data/flutter_assets/app/app.zip + app.zip.hash
    app_dir = tmp_path / "data" / "flutter_assets" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "app.zip").write_bytes(b"code")
    (app_dir / "app.zip.hash").write_text("h")
    monkeypatch.setattr(fu, "_install_root", lambda: str(tmp_path))
    zip_path, hash_path = fu.install_app_zip_paths()
    assert zip_path.endswith("app.zip")
    assert hash_path.endswith("app.zip.hash")
    assert zip_path != hash_path
    assert os.path.isfile(zip_path) and os.path.isfile(hash_path)


def test_install_app_zip_paths_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(fu, "_install_root", lambda: str(tmp_path))
    assert fu.install_app_zip_paths() == (None, None)


def test_sha256_of_bytes_matches_hashlib():
    # httpdl is where the ONE hashing helper lives (PS-6); assert it there
    # rather than through a re-export on this module, so a dead-import pass over
    # fast_update.py can't quietly break this test.
    import hashlib
    from src.utils import httpdl
    data = b"persona code"
    assert httpdl.sha256_bytes(data) == hashlib.sha256(data).hexdigest()


def test_manifest_and_appzip_urls_from_assets():
    assets = [
        {"name": "persona-windows-setup.exe", "browser_download_url": "u1"},
        {"name": "update-manifest.json", "browser_download_url": "u2"},
        {"name": "app.zip", "browser_download_url": "u3"},
    ]
    murl, zurl = fu.manifest_and_appzip_urls(assets)
    assert murl == "u2"
    assert zurl == "u3"


def test_manifest_and_appzip_urls_empty_when_absent():
    assert fu.manifest_and_appzip_urls([]) == ("", "")


def test_can_fast_update_false_off_windows(monkeypatch):
    monkeypatch.setattr(fu._platform, "IS_WINDOWS", False)
    assert fu.can_fast_update() is False


def test_apply_code_only_noop_off_windows(monkeypatch):
    monkeypatch.setattr(fu._platform, "IS_WINDOWS", False)
    assert fu.apply_code_only_and_restart("u", "sha") is False


def test_try_windows_fast_update_declines_when_requires_full(monkeypatch):
    # updater's decision gate: manifest requires_full_install → fall back to the
    # full installer (return False), never touch the code-only path.
    from src.services.app_update import updater as au
    import src.services.app_update.fast_update as fu2

    monkeypatch.setattr(fu2, "can_fast_update", lambda: True)
    monkeypatch.setattr(au, "APP_REPO", "amnesiadevelopment/persona")
    monkeypatch.setattr(au, "latest_tag", lambda timeout=30: "v9.9.9")
    monkeypatch.setattr(au, "update_available", lambda tag: True)
    # the manifest (fetched via _curl_get) says requires_full_install
    monkeypatch.setattr(
        au, "_curl_get",
        lambda *a, **k: '{"version":"9.9.9","requires_full_install":true,"app_zip_sha256":"x"}',
    )
    assert au._try_windows_fast_update(lambda *a: None) is False


def test_try_windows_fast_update_takes_fast_path(monkeypatch):
    from src.services.app_update import updater as au
    import src.services.app_update.fast_update as fu2

    monkeypatch.setattr(fu2, "can_fast_update", lambda: True)
    monkeypatch.setattr(au, "APP_REPO", "amnesiadevelopment/persona")
    monkeypatch.setattr(au, "latest_tag", lambda timeout=30: "v9.9.9")
    monkeypatch.setattr(au, "update_available", lambda tag: True)
    monkeypatch.setattr(
        au, "_curl_get",
        lambda *a, **k: '{"version":"9.9.9","requires_full_install":false,"app_zip_sha256":"deadbeef"}',
    )
    applied = {}
    def fake_apply(url, sha, log=None):
        applied["url"] = url
        applied["sha"] = sha
        return True  # pretend it swapped + exited
    monkeypatch.setattr(fu2, "apply_code_only_and_restart", fake_apply)
    assert au._try_windows_fast_update(lambda *a: None) is True
    # deterministic app.zip URL for the resolved tag
    assert applied["url"].endswith("/releases/download/v9.9.9/app.zip")
    assert applied["sha"] == "deadbeef"


def test_fastswap_bat_waits_a_real_beat_before_confirming_launch(tmp_path):
    # #229: after `start`, the new persona re-extracts app.zip behind its boot
    # screen before persona.exe registers in tasklist — several seconds. A
    # near-instant recheck saw "not up yet" and re-launched, spawning a second
    # instance that raced the extraction (one won, the other died: "reopened
    # then closed again quickly"). The confirm must sleep a real ~3s beat first,
    # use its OWN bounded counter (not the wait loop's `tries`, already near its
    # cap), and re-launch only a handful of times.
    exe = tmp_path / "persona.exe"
    exe.write_bytes(b"MZ")
    path = fu._write_appzip_swap_bat(
        str(exe),
        str(tmp_path / "new.zip"),
        str(tmp_path / "new.hash"),
        str(tmp_path / "dst.zip"),
        str(tmp_path / "dst.hash"),
        4242,
    )
    try:
        with open(path, encoding="ascii", newline="") as f:
            bat = f.read()
    finally:
        os.remove(path)
    launch = bat.split(":launch")[1]
    # a real ~3s wait before the liveness check, not a near-instant ping
    assert "ping -n 4 " in launch
    # re-launch is bounded by a SEPARATE counter, tightly, so a genuine failure
    # can't spawn a fistful of persona processes
    assert "boots" in bat
    assert "lss 5" in launch
    assert "lss 930" not in bat


def test_swap_bat_purges_flet_extraction(tmp_path):
    # #12 (audit3): the fast-update swap .bat must purge the stale flet\app
    # extraction (like the full installer) so the new persona re-extracts
    # cleanly instead of racing a straggler handle → errno-32 white screen (#195).
    from src.services.app_update.fast_update import _write_appzip_swap_bat

    bat_path = _write_appzip_swap_bat(
        exe=r"C:\Users\x\AppData\Local\persona\persona.exe",
        new_zip=str(tmp_path / "new.zip"),
        new_hash=str(tmp_path / "new.hash"),
        dst_zip=r"C:\dst\app.zip",
        dst_hash=r"C:\dst\app.zip.hash",
        old_pid=1234,
    )
    import pathlib
    content = pathlib.Path(bat_path).read_text()
    assert r"flet\app" in content
    assert "rd /s /q" in content
    # bounded so a never-dying holder can't block the relaunch forever
    assert "purges" in content
    pathlib.Path(bat_path).unlink()
