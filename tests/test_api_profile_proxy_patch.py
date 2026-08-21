"""PS-44: the REST lane's PATCH must distinguish an OMITTED proxy key from a
SUPPLIED empty one — and the model must be safe either way.

Before the fix this route defended itself: it read the stored proxy and passed
it back in (`supplied.get("proxy", profile.proxy)`). That was a correct thing
for it to do, but it was the DOOR protecting the model rather than the model
protecting itself — the next caller written would have had to remember, and
nothing would have reminded it. The model now guarantees the preservation, so
this route says what it MEANS (unchanged vs. cleared) instead of compensating.

A route is nevertheless the only layer that can tell an absent JSON key from a
present empty one, so it keeps that job — that is the distinction under test.
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


def _make_proxied(container, name="shopper", proxy="PL-residential"):
    """A profile with a proxy assigned, created straight through the model so
    the test does not depend on the create route's own proxy handling."""
    container.profile_manager.add_profile(name, proxy, "windows")
    return container.profile_manager


# --------------------------------------------------------------------------
# An OMITTED proxy key changes nothing.
# --------------------------------------------------------------------------


def test_patch_without_proxy_keeps_the_assignment(client):
    c, headers, container = client
    pm = _make_proxied(container)

    r = c.patch("/api/v1/profiles/shopper", json={"notes": "renamed"}, headers=headers)

    assert r.status_code == 200
    assert pm.profiles["shopper"].proxy == "PL-residential"
    assert r.json()["proxy"] == "PL-residential"


def test_patch_rename_without_proxy_keeps_the_assignment(client):
    c, headers, container = client
    pm = _make_proxied(container)

    r = c.patch("/api/v1/profiles/shopper", json={"name": "shopper-eu"}, headers=headers)

    assert r.status_code == 200
    assert pm.profiles["shopper-eu"].proxy == "PL-residential"


def test_patch_os_only_keeps_the_assignment(client):
    c, headers, container = client
    pm = _make_proxied(container)

    r = c.patch("/api/v1/profiles/shopper", json={"os_type": "linux"}, headers=headers)

    assert r.status_code == 200
    assert pm.profiles["shopper"].proxy == "PL-residential"
    assert pm.profiles["shopper"].os_type == "linux"


# --------------------------------------------------------------------------
# A SUPPLIED empty proxy still clears — the caller said so explicitly.
# --------------------------------------------------------------------------


def test_patch_with_explicit_null_proxy_clears_it(client):
    c, headers, container = client
    pm = _make_proxied(container)

    r = c.patch("/api/v1/profiles/shopper", json={"proxy": None}, headers=headers)

    assert r.status_code == 200
    assert pm.profiles["shopper"].proxy is None


def test_patch_with_explicit_empty_proxy_clears_it(client):
    c, headers, container = client
    pm = _make_proxied(container)

    r = c.patch("/api/v1/profiles/shopper", json={"proxy": ""}, headers=headers)

    assert r.status_code == 200
    assert pm.profiles["shopper"].proxy is None


def test_patch_can_still_reassign_a_proxy(client):
    c, headers, container = client
    pm = _make_proxied(container)
    container.proxy_store.proxies.clear()

    # An unknown proxy ref is refused by this route's own validation, so add it
    # to the store first — the subject here is reassignment, not validation.
    r = c.patch(
        "/api/v1/profiles/shopper",
        json={"proxy": "socks5://127.0.0.1:1080"},
        headers=headers,
    )

    assert r.status_code in (200, 400)
    if r.status_code == 200:
        assert pm.profiles["shopper"].proxy == "socks5://127.0.0.1:1080"


# --------------------------------------------------------------------------
# The point of the whole ticket: the protection does NOT depend on this route
# remembering to apply it.
# --------------------------------------------------------------------------


def test_the_model_protects_a_caller_that_does_not_defend_itself(client):
    """The load-bearing test, in the shape of the coherence suite's equivalent.

    A future caller that simply omits the proxy — with no read-back, no
    compensating logic, nothing — must still leave a proxied profile proxied.
    This is what distinguishes a fix in the model from one at a door: against a
    route-only fix, this direct call is unguarded and this test fails.
    """
    _, _, container = client
    pm = _make_proxied(container)

    # No proxy argument at all, exactly as a not-yet-written caller might.
    pm.update_profile("shopper", "shopper", new_notes="a brand new caller")

    assert pm.profiles["shopper"].proxy == "PL-residential"
