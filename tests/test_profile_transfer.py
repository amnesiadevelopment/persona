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
