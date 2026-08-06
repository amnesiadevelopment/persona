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
