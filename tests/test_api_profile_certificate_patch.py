"""PS-263: the REST lane's PATCH must SAY whether an omitted ``certificate`` key
means "leave it alone" — instead of being right by coincidence.

The third sibling of ``test_api_profile_proxy_patch.py`` (PS-44) and
``test_api_profile_bookmark_pool_patch.py`` (PS-157), and the one whose
behaviour is deliberately UNCHANGED by its ticket.

Those two routes had to change: the model stopped reading an empty value as a
clear, so a route that forwarded ``supplied.get(...)`` verbatim would have made
"clear it" inexpressible. On THIS field the model's semantics are untouched —
``None`` preserves, ``""`` clears — so ``supplied.get("certificate")`` was
already correct. It was correct BY ACCIDENT: nothing in the route said which it
meant, and ``dict.get`` returning ``None`` for an absent key is a property of
the dict, not a statement of intent. The route now states it in two branches.

These tests assert the PATCH behaviour through the ROUTE, so they hold whether
the distinction is drawn deliberately or by luck — which is the point: they are
what would catch a later edit that removes it.
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


def _make_certified(container, name="acct", cert="corp-ca"):
    """A profile with a certificate assigned, created straight through the model
    so the test does not depend on the create route's own handling."""
    container.profile_manager.add_profile(name, None, "windows", certificate=cert)
    return container.profile_manager


# --------------------------------------------------------------------------
# An OMITTED certificate key changes nothing.
# --------------------------------------------------------------------------


def test_patch_without_certificate_keeps_the_assignment(client):
    c, headers, container = client
    pm = _make_certified(container)

    r = c.patch("/api/v1/profiles/acct", json={"notes": "unrelated"}, headers=headers)

    assert r.status_code == 200
    assert pm.profiles["acct"].certificate == "corp-ca"


def test_patch_without_certificate_keeps_the_recorded_trust_verdict(client):
    """The collateral this ticket's dialog half is really about: the verdict is
    cleared on a real reassignment, so an accidental one destroys it."""
    c, headers, container = client
    pm = _make_certified(container)
    pm.set_cert_trust_status("acct", "trusted")

    c.patch("/api/v1/profiles/acct", json={"name": "acct2"}, headers=headers)

    assert pm.profiles["acct2"].certificate == "corp-ca"
    assert pm.profiles["acct2"].cert_trust_status == "trusted"


# --------------------------------------------------------------------------
# A SUPPLIED empty certificate still clears — the distinction only a route can
# draw, and the half that would be lost if the route ever collapsed to one
# branch.
# --------------------------------------------------------------------------


def test_patch_with_an_explicitly_empty_certificate_clears_it(client):
    c, headers, container = client
    pm = _make_certified(container)

    r = c.patch("/api/v1/profiles/acct", json={"certificate": ""}, headers=headers)

    assert r.status_code == 200
    assert pm.profiles["acct"].certificate is None


def test_patch_with_an_explicitly_null_certificate_clears_it(client):
    """``null`` and ``""`` are the same instruction on this field: the schema
    types it ``str | None``, so both arrive as a SUPPLIED falsy value and the
    route maps both to the model's clear."""
    c, headers, container = client
    pm = _make_certified(container)

    r = c.patch("/api/v1/profiles/acct", json={"certificate": None}, headers=headers)

    assert r.status_code == 200
    assert pm.profiles["acct"].certificate is None


def test_patch_with_a_name_reassigns(client):
    c, headers, container = client
    pm = _make_certified(container)

    r = c.patch(
        "/api/v1/profiles/acct", json={"certificate": "other-ca"}, headers=headers
    )

    assert r.status_code == 200
    assert pm.profiles["acct"].certificate == "other-ca"


# --------------------------------------------------------------------------
# No directive leaks onto this lane.
# --------------------------------------------------------------------------


def test_the_route_never_sends_a_directive(client):
    """CERT_UNCHANGED means "I cannot account for the stored assignment", which
    is the DIALOG's state and never a route's — an API caller that omits the key
    is silent about the certificate, not confused about it. A directive reaching
    the model here would still resolve correctly, but it would be a lie about
    where the uncertainty is, so pin that it does not happen."""
    from src.services.profile.cert_assignment import CertDirective

    c, headers, container = client
    pm = _make_certified(container)
    seen = []
    real = pm.update_profile

    def spy(*args, **kwargs):
        seen.append(kwargs.get("new_certificate"))
        return real(*args, **kwargs)

    pm.update_profile = spy
    c.patch("/api/v1/profiles/acct", json={"notes": "unrelated"}, headers=headers)

    assert seen == [None]
    assert not isinstance(seen[0], CertDirective)
