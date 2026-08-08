"""Exporting then importing a profile must preserve every field — a restore that
silently drops engine/certificate/resolution/etc. changes how the profile
fingerprints and quietly breaks the operator's setup.
"""
from src.models.profile import Profile
from src.services.profile.transfer import export_to_zip, import_from_zip


def _full_profile():
    return Profile(
        name="acct-7",
        proxy="warsaw",
        os_type="macos",
        device_type="desktop",
        engine="firefox",
        resolution="1920x1080",
        search_engine="brave",
        bookmark_pool="work",
        bookmarks=["gmail", "sheets"],
        certificate="admin-cert",
        cookie_import_status="creep.json · 11 cookies",
        tags=["ops", "eu"],
        notes="primary ops account",
        ai_control=True,
    )


def test_export_import_round_trip_preserves_every_field(tmp_path):
    prof = _full_profile()
    ok, zip_path = export_to_zip(
        prof, str(tmp_path / "nodata"), str(tmp_path), include_data=False
    )
    assert ok, zip_path

    ok, restored = import_from_zip(zip_path, str(tmp_path / "import_data"))
    assert ok, restored
    assert restored.to_dict() == prof.to_dict()


def test_import_defaults_missing_optional_fields(tmp_path):
    # a minimal archive (older export) still imports, defaulting cleanly
    import json
    import zipfile

    zp = tmp_path / "minimal.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("profile.json", json.dumps({"name": "min", "os_type": "windows"}))
    ok, restored = import_from_zip(str(zp), str(tmp_path / "d"))
    assert ok
    assert restored.name == "min"
    assert restored.engine == "chromium"
    assert restored.certificate is None
    assert restored.tags == []


def test_import_rejects_zip_bomb_by_declared_size(tmp_path, monkeypatch):
    # audit3 low: a shared profile zip is untrusted; a tiny archive that declares
    # a huge uncompressed size must be rejected before extraction.
    import json
    import zipfile

    import src.services.profile.transfer as tr

    monkeypatch.setattr(tr, "_MAX_UNCOMPRESSED_BYTES", 1000)
    zp = tmp_path / "bomb.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("profile.json", json.dumps({"name": "bomb"}))
        z.writestr("data/big.bin", b"x" * 5000)  # over the 1000-byte cap
    ok, res = import_from_zip(str(zp), str(tmp_path / "out"))
    assert ok is False
    assert "zip bomb" in res.lower() or "too large" in res.lower()
    # nothing left behind
    out = tmp_path / "out"
    assert not (out / "bomb").exists()


def test_import_partial_failure_leaves_no_halfextracted_dir(tmp_path):
    # audit3 low: a member that fails the zip-slip guard mid-import must not leave
    # a half-extracted, unregistered data dir — extraction is staged then swapped.
    import json
    import zipfile

    zp = tmp_path / "evil.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("profile.json", json.dumps({"name": "victim"}))
        z.writestr("data/ok.txt", b"fine")
        # a backslash arcname that escapes the staging dir
        z.writestr("data/../../escape.txt", b"evil")
    ok, res = import_from_zip(str(zp), str(tmp_path / "out"))
    assert ok is False
    out = tmp_path / "out"
    # the real profile dir must NOT exist, and no .import-* staging leftover
    assert not (out / "victim").exists()
    leftovers = list(out.glob(".import-*")) if out.exists() else []
    assert leftovers == []
