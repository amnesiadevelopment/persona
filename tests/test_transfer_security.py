"""Imported profile archives are UNTRUSTED (sharing is a feature). A malicious
zip must not escape the profile data dir via a traversal name or a zip-slip
member, and an exported archive must not leak the mTLS client private key."""
import io
import json
import os
import zipfile

import pytest

from src.services.profile.transfer import export_to_zip, import_from_zip
from src.models.profile import Profile


def _make_zip(name: str, members: dict[str, bytes]) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("profile.json", json.dumps({"name": name}))
        for arc, data in members.items():
            z.writestr(arc, data)
    return buf.getvalue()


def _write_zip(tmp_path, name, members):
    p = tmp_path / "in.zip"
    p.write_bytes(_make_zip(name, members))
    return str(p)


def test_import_rejects_traversal_name(tmp_path):
    zp = _write_zip(tmp_path, "../../evil", {})
    ok, res = import_from_zip(zp, str(tmp_path / "data"))
    assert ok is False
    assert "name" in res.lower()


def test_import_rejects_absolute_name(tmp_path):
    zp = _write_zip(tmp_path, "/etc/evil", {})
    ok, res = import_from_zip(zp, str(tmp_path / "data"))
    assert ok is False


def test_import_rejects_zip_slip_member(tmp_path):
    # Valid profile name, but a data member climbs out of the profile dir.
    zp = _write_zip(
        tmp_path, "good", {"data/../../../../pwned.txt": b"malicious"}
    )
    data_dir = tmp_path / "data"
    ok, res = import_from_zip(zp, str(data_dir))
    assert ok is False
    assert "unsafe" in str(res).lower()
    # nothing was written outside the data dir
    assert not (tmp_path / "pwned.txt").exists()


def test_import_accepts_clean_archive(tmp_path):
    zp = _write_zip(tmp_path, "clean", {"data/cookies.sqlite": b"x"})
    data_dir = tmp_path / "data"
    ok, res = import_from_zip(zp, str(data_dir))
    assert ok is True
    assert isinstance(res, Profile)
    assert (data_dir / "clean" / "cookies.sqlite").read_bytes() == b"x"


def test_export_excludes_mtls_key(tmp_path):
    # A profile dir with a leaked-key dir the export must NOT include.
    pdir = tmp_path / "pdata"
    (pdir / ".persona-mtls").mkdir(parents=True)
    (pdir / ".persona-mtls" / "client.pem").write_text("SECRET KEY", encoding="utf-8")
    (pdir / "cookies.sqlite").write_text("ok", encoding="utf-8")

    profile = Profile(name="p", os_type="windows")
    ok, path = export_to_zip(profile, str(pdir), str(tmp_path))
    assert ok is True
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    assert any("cookies.sqlite" in n for n in names)
    assert not any(".persona-mtls" in n for n in names)
    assert not any("client.pem" in n for n in names)


def test_import_does_not_clobber_existing_before_reporting(tmp_path, monkeypatch):
    # A non-overwrite import of a name that already exists must NOT extract (and
    # overwrite) the existing profile's data before returning "already exists".
    import src.core.config as cfg
    import src.services.profile.manager as mod
    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    for m in (cfg, mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
    from src.services.profile.manager import ProfileManager
    import pathlib
    pm = ProfileManager()
    pm.add_profile("dup", "", "windows")
    # existing data on disk
    ddir = pathlib.Path(pm._data_path("dup"))
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / "cookies.sqlite").write_bytes(b"ORIGINAL")
    # archive with the same name carrying different data
    zp = _write_zip(tmp_path, "dup", {"data/cookies.sqlite": b"ATTACKER"})
    ok, msg = pm.import_profile(str(zp), overwrite=False)
    assert ok is False
    assert "already exists" in msg
    # original data untouched
    assert (ddir / "cookies.sqlite").read_bytes() == b"ORIGINAL"


def test_export_excludes_the_browser_child_scratch_dir(tmp_path):
    # PS-129. Pinning the child's TMPDIR inside the profile is what makes
    # crash-stranded engine temp files wipeable — but it also puts the engine's
    # AppImage self-extraction under the profile dir (MEASURED at ~714MB, and
    # it survives a CLEAN exit, not just a crash). Without the exclusion every
    # profile export would grow by that much.
    #
    # Unlike the .persona-mtls exclusion beside it this is a BULK concern, not
    # a secrecy one: scratch is per-launch state the child recreates on demand,
    # so excluding it loses nothing an importer wanted.
    from src.services.browser.env_policy import CHILD_TMPDIR_NAME

    pdir = tmp_path / "pdata"
    scratch = pdir / CHILD_TMPDIR_NAME
    (scratch / "appimage_extracted_deadbeef" / "opt").mkdir(parents=True)
    (scratch / "appimage_extracted_deadbeef" / "opt" / "chrome").write_text(
        "x" * 1024
    , encoding="utf-8")
    (scratch / "org.chromium.Chromium.AbCdEf").mkdir(parents=True)
    (pdir / "cookies.sqlite").write_text("ok", encoding="utf-8")

    profile = Profile(name="p", os_type="windows")
    ok, path = export_to_zip(profile, str(pdir), str(tmp_path))
    assert ok is True
    with zipfile.ZipFile(path) as z:
        names = z.namelist()

    # The real profile data still exports.
    assert any("cookies.sqlite" in n for n in names)
    # None of the scratch does.
    assert not any(CHILD_TMPDIR_NAME in n for n in names), (
        f"the child's scratch dir rode along in the export: "
        f"{[n for n in names if CHILD_TMPDIR_NAME in n]}"
    )
    assert not any("appimage_extracted" in n for n in names)
