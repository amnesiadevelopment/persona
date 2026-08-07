"""The whole /api/v1 REST surface — the functional twin of the token-gated /mcp
endpoint — must require the bearer token and a loopback Host, so no local process
(or DNS-rebinding web page) can drive the browser or read proxy credentials.
Only /health is open."""
import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.mcp_token import get_or_create_token


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point the token + data dirs at tmp so the test is isolated and the token
    # is deterministic for this run.
    import src.api.mcp_token as tok
    import src.core.config as cfg

    monkeypatch.setattr(tok, "_path", lambda: str(tmp_path / "mcp_token"))
    monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path / "data"), raising=False)

    from src.core.container import Container

    app = create_app(Container())
    return TestClient(app), get_or_create_token()


def test_api_v1_rejects_missing_token(client):
    c, _ = client
    r = c.get("/api/v1/profiles", headers={"host": "127.0.0.1:8000"})
    assert r.status_code == 401


def test_api_v1_rejects_wrong_token(client):
    c, _ = client
    r = c.get(
        "/api/v1/profiles",
        headers={"host": "127.0.0.1:8000", "authorization": "Bearer nope"},
    )
    assert r.status_code == 401


def test_api_v1_accepts_valid_token(client):
    c, token = client
    r = c.get(
        "/api/v1/profiles",
        headers={"host": "127.0.0.1:8000", "authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200


def test_api_v1_rejects_foreign_host(client):
    # DNS-rebinding: a rebound attacker domain resolves to 127.0.0.1 but its Host
    # header is the attacker domain — reject before even checking the token.
    c, token = client
    r = c.get(
        "/api/v1/profiles",
        headers={"host": "evil.example.com", "authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


def test_health_is_open(client):
    c, _ = client
    r = c.get("/api/v1/health", headers={"host": "127.0.0.1:8000"})
    assert r.status_code == 200


def test_browser_launch_requires_token(client):
    c, _ = client
    r = c.post("/api/v1/browser/anything/launch", headers={"host": "127.0.0.1:8000"})
    assert r.status_code == 401


# --- authorized CRUD (also covers routes/profiles.py: create/get/update/delete) ---

def _auth(token):
    return {"host": "127.0.0.1:8000", "authorization": f"Bearer {token}"}


def test_profile_crud_roundtrip(client):
    c, token = client
    h = _auth(token)
    # create
    r = c.post("/api/v1/profiles", json={"name": "alpha", "os_type": "windows"}, headers=h)
    assert r.status_code == 201, r.text
    # duplicate -> 409
    r = c.post("/api/v1/profiles", json={"name": "alpha", "os_type": "windows"}, headers=h)
    assert r.status_code == 409
    # invalid name -> 400
    r = c.post("/api/v1/profiles", json={"name": "../evil", "os_type": "windows"}, headers=h)
    assert r.status_code == 400
    # get
    r = c.get("/api/v1/profiles/alpha", headers=h)
    assert r.status_code == 200
    assert r.json()["name"] == "alpha"
    # update os
    r = c.patch("/api/v1/profiles/alpha", json={"os_type": "linux"}, headers=h)
    assert r.status_code == 200
    assert r.json()["os_type"] == "linux"
    # list shows it
    r = c.get("/api/v1/profiles", headers=h)
    assert r.status_code == 200
    assert any(p["name"] == "alpha" for p in r.json()["profiles"])
    # delete
    r = c.delete("/api/v1/profiles/alpha", headers=h)
    assert r.status_code == 200
    # gone
    r = c.get("/api/v1/profiles/alpha", headers=h)
    assert r.status_code == 404


def test_get_unknown_profile_404(client):
    c, token = client
    r = c.get("/api/v1/profiles/ghost", headers=_auth(token))
    assert r.status_code == 404


def test_create_profile_accepts_proxy_by_name(client, tmp_path, monkeypatch):
    # #308: the profile.proxy field is a NAME reference into the proxy store, not
    # a URL. Creating a profile with an existing proxy name must succeed (it 400'd
    # before, because the route ran the URL regex on the name).
    c, token = client
    h = _auth(token)
    # seed a named proxy into the same container's store (the app was built from
    # this container in the fixture)
    container = c.app.state.container
    container.proxy_store.add("MyProxy", "socks5://1.2.3.4:1080")
    r = c.post(
        "/api/v1/profiles",
        json={"name": "withproxy", "proxy": "MyProxy", "os_type": "windows"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["proxy"] == "MyProxy"


def test_create_profile_accepts_proxy_url(client):
    c, token = client
    r = c.post(
        "/api/v1/profiles",
        json={"name": "urlproxy", "proxy": "socks5://1.2.3.4:1080", "os_type": "windows"},
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text


def test_create_profile_rejects_bad_proxy(client):
    c, token = client
    r = c.post(
        "/api/v1/profiles",
        json={"name": "badproxy", "proxy": "not-a-proxy-or-name", "os_type": "windows"},
        headers=_auth(token),
    )
    assert r.status_code == 400
