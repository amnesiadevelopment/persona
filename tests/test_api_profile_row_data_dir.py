"""`data_dir` is not on the profile row, and the endpoint that DOES answer it
still answers with an absolute path.

Two properties, from PS-125:

1. **The per-row `ProfileResponse` does not carry `data_dir`.** It is an
   absolute host filesystem path carrying the operator's OS account name, and
   the row is the *broadcast* surface -- every list and every read hands it out
   unasked. `GET /profiles/{name}/data-dir` answers it on explicit request
   instead. This also brings the REST lane into line with the MCP lane, which
   withholds endpoint/location data for stated reasons
   (`mcp_server.py:82`, `:332`, `refusal_report.py:56`).

2. **`GET /profiles/{name}/data-dir` returns an ABSOLUTE path under BOTH a
   relative and an absolute `PERSONA_DATA_DIR`.** This is the half that is easy
   to break. The old code built the path as
   `os.path.join(os.getcwd(), DATA_DIR, name)`. Under the *default* config
   `DATA_DIR` is absolute, so `getcwd()` contributed nothing and the call looked
   dead -- but `config._under_home` returns an env override **verbatim**
   (`if val: return val`, no normalisation), and the shipped `.env.example:7` is
   `PERSONA_DATA_DIR=persona_data`, which is **relative**. For an operator who
   copied the example, `getcwd()` was load-bearing, and replacing the join with a
   bare `os.path.join(DATA_DIR, name)` would silently turn an absolute API
   response relative. The replacement is `os.path.abspath(...)`, and the
   relative case below is what holds that line.

The existing suite is structurally blind to property 2: ~15 test files set
`PERSONA_DATA_DIR` and every one of them uses an absolute `tmp_path`, so the
failing configuration never occurs. `relative_data_dir` constructs it
deliberately.
"""
import os

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.mcp_token import get_or_create_token

# Every route that returns a profile row is exercised, not just one -- the field
# was removed from the shared builder, so a regression would come back on all
# four at once and a single-route test would still be honest but thin.
PROFILE_NAME = "rowprobe"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A token-authenticated client over an isolated home (same shape as
    tests/test_api_auth.py -- config computes paths at import time and each
    module binds its own copy by value, so patch every module that captured
    one)."""
    import src.api.helpers as helpers
    import src.api.mcp_token as tok
    import src.api.routes.profiles as routes
    import src.core.config as cfg
    import src.services.profile.manager as pm

    data_dir = str(tmp_path / "data")
    profiles_file = str(tmp_path / "profiles.json")
    monkeypatch.setattr(tok, "_path", lambda: str(tmp_path / "mcp_token"))
    monkeypatch.setattr(cfg, "DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(cfg, "PROFILES_FILE", profiles_file, raising=False)
    monkeypatch.setattr(pm, "DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(pm, "PROFILES_FILE", profiles_file, raising=False)
    # `routes/profiles.py` does `from ...core.config import DATA_DIR`, binding
    # its OWN copy by value at import. Patching `cfg` alone does NOT reach the
    # data-dir endpoint -- it would keep answering out of the real ~/.persona.
    monkeypatch.setattr(routes, "DATA_DIR", data_dir, raising=False)
    # Same by-value trap for the row builder. It does not read DATA_DIR today
    # (that is the point of this file), but patch it anyway with raising=False:
    # if the field is ever reintroduced, the row must be built from the isolated
    # tmp path so the leak assertion below compares against the right string
    # instead of passing vacuously.
    monkeypatch.setattr(helpers, "DATA_DIR", data_dir, raising=False)

    from src.core.container import Container

    c = TestClient(create_app(Container()))
    token = get_or_create_token()
    headers = {"host": "127.0.0.1:8000", "authorization": f"Bearer {token}"}
    return c, headers


def _create(c, headers, name=PROFILE_NAME):
    r = c.post("/api/v1/profiles", headers=headers, json={"name": name})
    assert r.status_code == 201, r.text
    return r


# --------------------------------------------------------------------------
# 1. the row does not carry the path
# --------------------------------------------------------------------------


def test_created_profile_row_does_not_carry_data_dir(client):
    c, headers = client
    r = _create(c, headers)
    assert "data_dir" not in r.json()


def test_read_profile_row_does_not_carry_data_dir(client):
    c, headers = client
    _create(c, headers)
    r = c.get(f"/api/v1/profiles/{PROFILE_NAME}", headers=headers)
    assert r.status_code == 200, r.text
    assert "data_dir" not in r.json()


def test_listed_profile_rows_do_not_carry_data_dir(client):
    c, headers = client
    _create(c, headers, "one")
    _create(c, headers, "two")
    r = c.get("/api/v1/profiles", headers=headers)
    assert r.status_code == 200, r.text
    rows = r.json()["profiles"]
    assert len(rows) == 2, "both profiles must be listed or this proves nothing"
    for row in rows:
        assert "data_dir" not in row


def test_patched_profile_row_does_not_carry_data_dir(client):
    c, headers = client
    _create(c, headers)
    r = c.patch(
        f"/api/v1/profiles/{PROFILE_NAME}", headers=headers, json={"notes": "hi"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["notes"] == "hi", "the patch must have applied"
    assert "data_dir" not in r.json()


def test_no_value_anywhere_in_the_row_looks_like_the_data_dir_path(client):
    """Not merely that the *key* is gone -- that the path is not smuggled into
    some other field. Asserting on the key alone would pass if the value were
    renamed rather than withheld."""
    import src.core.config as cfg

    c, headers = client
    _create(c, headers)
    row = c.get(f"/api/v1/profiles/{PROFILE_NAME}", headers=headers).json()

    expected = os.path.abspath(os.path.join(cfg.DATA_DIR, PROFILE_NAME))
    for key, value in row.items():
        if isinstance(value, str):
            assert expected not in value, f"{key} leaks the data dir path"


# --------------------------------------------------------------------------
# 2. the dedicated endpoint still answers, and still answers ABSOLUTE
# --------------------------------------------------------------------------


def test_the_dedicated_endpoint_still_answers_the_path(client):
    """Dropping the field from the row must not remove the way to ask."""
    import src.core.config as cfg

    c, headers = client
    _create(c, headers)
    r = c.get(f"/api/v1/profiles/{PROFILE_NAME}/data-dir", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == PROFILE_NAME
    assert body["data_dir"] == os.path.abspath(
        os.path.join(cfg.DATA_DIR, PROFILE_NAME)
    )
    assert os.path.isabs(body["data_dir"])


@pytest.fixture
def relative_data_dir(client, monkeypatch, tmp_path):
    """Put the route's DATA_DIR into the shape the shipped `.env.example`
    produces -- a RELATIVE override -- and move the process off the repo root so
    a cwd contribution is unmistakable.

    `src/api/routes/profiles.py` binds `DATA_DIR` by value at import, so the
    module global is what the endpoint actually reads; patching `cfg` alone
    would not reach it.
    """
    import src.api.routes.profiles as routes

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setattr(routes, "DATA_DIR", "persona_data", raising=False)
    return client + (str(workdir),)


def test_data_dir_endpoint_is_absolute_under_a_RELATIVE_persona_data_dir(
    relative_data_dir,
):
    """The case the rest of the suite cannot see, and the reason the
    replacement is `abspath` rather than a bare join.

    A bare `os.path.join("persona_data", name)` returns `persona_data/<name>` --
    relative -- where this endpoint has always returned an absolute path. That
    is a silent behaviour change for exactly the operators who copied
    `.env.example`.
    """
    c, headers, workdir = relative_data_dir
    _create(c, headers)

    r = c.get(f"/api/v1/profiles/{PROFILE_NAME}/data-dir", headers=headers)
    assert r.status_code == 200, r.text
    got = r.json()["data_dir"]

    assert os.path.isabs(got), f"endpoint returned a RELATIVE path: {got!r}"
    # and it is anchored at the cwd, which is what the old getcwd() join did
    assert got == os.path.join(workdir, "persona_data", PROFILE_NAME)


def test_data_dir_endpoint_is_absolute_under_an_ABSOLUTE_persona_data_dir(
    client, tmp_path
):
    """The default configuration -- the arm where `getcwd()` contributed
    nothing. Kept beside the relative arm so the pair states the whole
    invariant: absolute out, under either override shape."""
    import src.core.config as cfg

    c, headers = client
    _create(c, headers)
    r = c.get(f"/api/v1/profiles/{PROFILE_NAME}/data-dir", headers=headers)
    got = r.json()["data_dir"]

    assert os.path.isabs(cfg.DATA_DIR), "fixture precondition: override is absolute"
    assert os.path.isabs(got)
    assert got == os.path.join(cfg.DATA_DIR, PROFILE_NAME)
