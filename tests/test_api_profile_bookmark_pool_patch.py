"""PS-157 / AC6: the REST lane's PATCH must distinguish an OMITTED
``bookmark_pool`` key from a SUPPLIED empty one — and the model must be safe
either way.

The sibling of ``test_api_profile_proxy_patch.py``, and it exists for the same
reason. Before the fix this route defended itself: it read the stored pool and
passed it back in (``supplied.get("bookmark_pool", profile.bookmark_pool)``),
with a comment saying why it had to. That was a correct thing for it to do, but
it was the DOOR protecting the model rather than the model protecting itself —
and the proof that this is not hypothetical is that a door which FORGETS already
existed on main, in the product's own behaviour-verification lane.

That compensation is now DELETED. These tests assert the PATCH behaviour is
unchanged — an omitted ``bookmark_pool`` still leaves the pool alone — but it is
now the *model* refusing to clear it, not the route remembering to compensate.
Asserted THROUGH THE ROUTE, not through the model.
"""
import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.mcp_token import get_or_create_token


@pytest.fixture
def client(tmp_path, monkeypatch):
    import src.api.mcp_token as tok
    import src.core.config as cfg
    import src.services.profile.manager as pm

    monkeypatch.setattr(tok, "_path", lambda: str(tmp_path / "mcp_token"))
    for m in (cfg, pm):
        monkeypatch.setattr(m, "DATA_DIR", str(tmp_path / "data"), raising=False)
        monkeypatch.setattr(
            m, "PROFILES_FILE", str(tmp_path / "profiles.json"), raising=False
        )

    from src.core.container import Container

    container = Container()
    app = create_app(container)
    c = TestClient(app, base_url="http://127.0.0.1")
    return c, {"authorization": f"Bearer {get_or_create_token()}"}, container


def _make_pooled(container, name="acct", pool="corp-pool"):
    """A profile with a pool assigned, created straight through the model so the
    test does not depend on the create route's own pool handling."""
    container.profile_manager.add_profile(name, None, "windows", bookmark_pool=pool)
    return container.profile_manager


# --------------------------------------------------------------------------
# An OMITTED bookmark_pool key changes nothing.
# --------------------------------------------------------------------------


def test_patch_without_pool_keeps_the_assignment(client):
    """AC6. The route no longer compensates — the model preserves."""
    c, headers, container = client
    pm = _make_pooled(container)

    r = c.patch("/api/v1/profiles/acct", json={"notes": "unrelated"}, headers=headers)

    assert r.status_code == 200
    assert pm.profiles["acct"].bookmark_pool == "corp-pool"
    assert r.json()["bookmark_pool"] == "corp-pool"


def test_patch_rename_without_pool_keeps_the_assignment(client):
    c, headers, container = client
    pm = _make_pooled(container)

    r = c.patch("/api/v1/profiles/acct", json={"name": "acct-eu"}, headers=headers)

    assert r.status_code == 200
    assert pm.profiles["acct-eu"].bookmark_pool == "corp-pool"


def test_patch_os_only_keeps_the_assignment(client):
    c, headers, container = client
    pm = _make_pooled(container)

    r = c.patch("/api/v1/profiles/acct", json={"os_type": "linux"}, headers=headers)

    assert r.status_code == 200
    assert pm.profiles["acct"].bookmark_pool == "corp-pool"
    assert pm.profiles["acct"].os_type == "linux"


# --------------------------------------------------------------------------
# A SUPPLIED empty bookmark_pool still clears — the caller said so explicitly.
# That distinction is the one job only a route can do, and it stays here.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, ""], ids=["null", "empty-string"])
def test_patch_with_explicit_empty_pool_clears(client, value):
    c, headers, container = client
    pm = _make_pooled(container)

    r = c.patch(
        "/api/v1/profiles/acct", json={"bookmark_pool": value}, headers=headers
    )

    assert r.status_code == 200
    assert pm.profiles["acct"].bookmark_pool is None
    assert r.json()["bookmark_pool"] is None


def test_patch_can_still_set_a_pool(client):
    c, headers, container = client
    pm = _make_pooled(container)

    r = c.patch(
        "/api/v1/profiles/acct", json={"bookmark_pool": "other"}, headers=headers
    )

    assert r.status_code == 200
    assert pm.profiles["acct"].bookmark_pool == "other"


def test_patch_never_stores_a_directive_as_a_pool_name(client):
    """A directive must never reach the wire as if it were a name."""
    c, headers, container = client
    pm = _make_pooled(container)

    r = c.patch("/api/v1/profiles/acct", json={"notes": "unrelated"}, headers=headers)

    assert r.status_code == 200
    stored = pm.profiles["acct"].bookmark_pool
    assert isinstance(stored, str) and stored == "corp-pool"
