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
    import hashlib
    data = b"persona code"
    assert fu.sha256_bytes(data) == hashlib.sha256(data).hexdigest()


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

    monkeypatch.setattr(au, "_curl_get", lambda *a, **k: '{"assets":['
        '{"name":"update-manifest.json","browser_download_url":"m"},'
        '{"name":"app.zip","browser_download_url":"z"}]}')
    import src.services.app_update.fast_update as fu2
    monkeypatch.setattr(fu2, "can_fast_update", lambda: True)
    # the manifest fetch (2nd _curl_get) returns requires_full_install
    calls = {"n": 0}
    def curl(url, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return ('{"assets":['
                    '{"name":"update-manifest.json","browser_download_url":"m"},'
                    '{"name":"app.zip","browser_download_url":"z"}]}')
        return '{"version":"9.9.9","requires_full_install":true,"app_zip_sha256":"x"}'
    monkeypatch.setattr(au, "_curl_get", curl)
    monkeypatch.setattr(au, "releases_api", lambda: "https://api")
    assert au._try_windows_fast_update(lambda *a: None) is False


def test_try_windows_fast_update_takes_fast_path(monkeypatch):
    from src.services.app_update import updater as au
    import src.services.app_update.fast_update as fu2

    monkeypatch.setattr(fu2, "can_fast_update", lambda: True)
    monkeypatch.setattr(au, "releases_api", lambda: "https://api")
    calls = {"n": 0}
    def curl(url, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return ('{"assets":['
                    '{"name":"update-manifest.json","browser_download_url":"m"},'
                    '{"name":"app.zip","browser_download_url":"z"}]}')
        return '{"version":"9.9.9","requires_full_install":false,"app_zip_sha256":"deadbeef"}'
    monkeypatch.setattr(au, "_curl_get", curl)
    applied = {}
    def fake_apply(url, sha, log=None):
        applied["url"] = url
        applied["sha"] = sha
        return True  # pretend it swapped + exited
    monkeypatch.setattr(fu2, "apply_code_only_and_restart", fake_apply)
    assert au._try_windows_fast_update(lambda *a: None) is True
    assert applied["url"] == "z"
    assert applied["sha"] == "deadbeef"
