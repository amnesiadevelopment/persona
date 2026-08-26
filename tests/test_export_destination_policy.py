"""Export writes where the caller says, and that is a DECISION (PS-180).

`export_to_zip` joins the caller's `export_path` and writes there, with no
confinement to PERSONA_HOME — unlike `import_from_zip` next to it, and unlike
`_confine_sftp_path` on the MCP lane. That difference was raised as a defect,
reviewed, and settled: the capability stays. The reasoning is recorded at the
enforcement point (`services/profile/transfer.py`, above `export_to_zip`) and
pointed at from the door (`api/routes/profiles.py::export_profile`).

**What this file is for.** A test that asserted "the comment is present" would
be brittle and would prove nothing about the product. So these tests lock the
BEHAVIOUR the decision preserves: an export to an arbitrary absolute directory
outside PERSONA_HOME succeeds and the archive lands there, on BOTH lanes. A
future confinement — the thing PS-180 decided against — turns these red, and the
red points the implementer at the recorded reasoning instead of letting the
capability be narrowed silently.

So read a failure here as "someone is confining the export path; go read the
DESTINATION POLICY note and PS-180 before proceeding", NOT as a stale test to
be updated. If the decision is ever genuinely reversed, this file is part of
what gets rewritten, deliberately.

The last test is the honest complement: it asserts the record still EXISTS, so
the reasoning cannot be deleted while the behaviour it explains stays. It is
bound to the citation of the ticket, not to any prose, so the note can be
reworded freely.
"""
import json
import os
import zipfile

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.mcp_token import get_or_create_token
from src.models.profile import Profile
from src.services.profile.transfer import export_to_zip

PROFILE_NAME = "exportdest"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Token-authenticated client over an isolated home (same shape as
    tests/test_api_auth.py — config computes paths at import time and each
    module binds its own copy by value, so patch every module that captured
    one)."""
    import src.api.mcp_token as tok
    import src.core.config as cfg
    import src.services.profile.manager as pm

    data_dir = str(tmp_path / "data")
    profiles_file = str(tmp_path / "profiles.json")
    monkeypatch.setattr(tok, "_path", lambda: str(tmp_path / "mcp_token"))
    monkeypatch.setattr(cfg, "DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(cfg, "PROFILES_FILE", profiles_file, raising=False)
    monkeypatch.setattr(pm, "DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(pm, "PROFILES_FILE", profiles_file, raising=False)

    from src.core.container import Container

    app = create_app(Container())
    return TestClient(app), get_or_create_token()


def _auth(token):
    return {"host": "127.0.0.1:8000", "authorization": f"Bearer {token}"}


def test_rest_export_to_an_arbitrary_outside_directory_still_succeeds(
    client, tmp_path
):
    """The REST lane's preserved capability, driven through the REAL route.

    `elsewhere` is deliberately an absolute path with no relationship to
    PERSONA_HOME or the data dir — it stands in for the USB stick. The
    assertion is on WHERE THE BYTES LANDED, not on the status code, because a
    confinement that returned 200 and quietly wrote somewhere else would be the
    same loss of the capability.
    """
    c, token = client
    r = c.post(
        "/api/v1/profiles",
        headers=_auth(token),
        json={"name": PROFILE_NAME, "os_type": "windows"},
    )
    assert r.status_code in (200, 201), r.text

    elsewhere = tmp_path / "usb-stick" / "backups"
    elsewhere.mkdir(parents=True)

    r = c.post(
        f"/api/v1/profiles/{PROFILE_NAME}/export",
        headers=_auth(token),
        json={"export_dir": str(elsewhere), "include_data": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True, body

    zip_path = body["zip_path"]
    # The archive is where the caller asked for it -- not redirected under
    # PERSONA_HOME, not refused.
    assert os.path.isfile(zip_path), zip_path
    assert os.path.realpath(os.path.dirname(zip_path)) == os.path.realpath(
        str(elsewhere)
    )
    landed = sorted(p.name for p in elsewhere.iterdir())
    assert len(landed) == 1 and landed[0].endswith(".zip"), landed

    # And it is a real profile archive, so "succeeds" means the feature worked,
    # not merely that a file appeared.
    with zipfile.ZipFile(zip_path) as z:
        assert json.loads(z.read("profile.json"))["name"] == PROFILE_NAME


def test_operator_lane_export_to_an_arbitrary_outside_directory_still_succeeds(
    tmp_path,
):
    """The same capability one lane over, at the function the file picker feeds.

    Kept beside the REST case on purpose: the two lanes converge at
    `pm.export_profile`, so a confinement placed in shared code would take BOTH.
    This is the direct-call half of that guard.
    """
    pdir = tmp_path / "pdata"
    pdir.mkdir()
    (pdir / "cookies.sqlite").write_text("ok")

    elsewhere = tmp_path / "external-drive"
    elsewhere.mkdir()

    profile = Profile(name=PROFILE_NAME, os_type="windows")
    ok, zip_path = export_to_zip(profile, str(pdir), str(elsewhere))

    assert ok is True, zip_path
    assert os.path.isfile(zip_path), zip_path
    assert os.path.realpath(os.path.dirname(zip_path)) == os.path.realpath(
        str(elsewhere)
    )


def test_the_destination_decision_is_recorded_at_the_enforcement_point():
    """The reasoning must not be deletable while the behaviour it explains stays.

    Bound to the TICKET CITATION and the two call sites' files, not to any
    sentence of the prose, so the note can be reworded or expanded freely. This
    is the one assertion here that is about the record rather than the
    behaviour, and it exists because an unargued difference in write authority
    between two doors is exactly what the charter forbids -- the argument is the
    artifact.
    """
    import inspect

    import src.api.routes.profiles as routes_mod
    import src.services.profile.transfer as transfer_mod

    enforcement = inspect.getsource(transfer_mod)
    assert "PS-180" in enforcement, (
        "the DESTINATION POLICY record above export_to_zip is gone; if the "
        "unconfined write is still shipping, the reasoning must ship with it"
    )

    door = inspect.getsource(routes_mod.export_profile)
    assert "PS-180" in door, (
        "the export route no longer points at the recorded decision; the next "
        "reader will re-derive it as a defect"
    )
