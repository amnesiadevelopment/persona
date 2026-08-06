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
    (pdir / ".persona-mtls" / "client.pem").write_text("SECRET KEY")
    (pdir / "cookies.sqlite").write_text("ok")

    profile = Profile(name="p", os_type="windows")
    ok, path = export_to_zip(profile, str(pdir), str(tmp_path))
    assert ok is True
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    assert any("cookies.sqlite" in n for n in names)
    assert not any(".persona-mtls" in n for n in names)
    assert not any("client.pem" in n for n in names)
