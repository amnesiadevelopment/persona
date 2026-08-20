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
    # Point every runtime path at tmp so the test is isolated from the real
    # ~/.persona (else the ProfileManager loads the user's live profiles and a
    # "profile already exists" 409 masks the real assertion). config computes the
    # paths at import time and each store binds its own copy by value, so patch
    # both config and every module that captured a path constant.
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


def test_token_compared_constant_time(client):
    # #3: the bearer check must not short-circuit on the first differing byte (a
    # timing oracle a co-resident process could use to recover the token). A
    # token sharing a long prefix must still be rejected.
    c, token = client
    almost = token[:-1] + ("0" if token[-1] != "0" else "1")
    r = c.get(
        "/api/v1/profiles",
        headers={"host": "127.0.0.1:8000", "authorization": f"Bearer {almost}"},
    )
    assert r.status_code == 401


def test_auth_uses_hmac_compare_digest():
    # Guard against a regression back to `!=`.
    import inspect

    import src.api.app as app_mod

    src = inspect.getsource(app_mod.create_app)
    assert "compare_digest" in src
    assert 'header != f"Bearer' not in src


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


def test_docs_endpoints_disabled(client):
    # #9 (audit4): /docs, /redoc, /openapi.json bypass the bearer + Host guards
    # (they match neither /mcp nor /api/v1). They must not exist at all.
    c, _ = client
    for path in ("/docs", "/redoc", "/openapi.json"):
        r = c.get(path, headers={"host": "127.0.0.1:8000"})
        assert r.status_code == 404, f"{path} should be disabled"


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


def test_create_profile_honours_all_fields(client):
    # #402: ProfileCreate only declared name/proxy/os_type/notes, so a profile
    # created via REST was always chromium/auto/desktop/no-bookmarks regardless of
    # the body. engine/resolution/device_type/bookmarks/search_engine must be
    # stored, and the response must reflect them.
    #
    # Every asserted value stays NON-DEFAULT, or the test would pass against the
    # very bug it was written for. Since PS-28 the Firefox engine pins the OS to
    # windows, so the non-default engine and the non-default mobile OS/device
    # cannot ride on one profile: they are split across two, and both halves keep
    # asserting a value the #402 bug would have dropped.
    c, token = client
    h = _auth(token)
    body = {
        "name": "fullprofile",
        "os_type": "windows",
        "engine": "firefox",
        "resolution": "1280x720",
        "device_type": "desktop",
        "search_engine": "google",
        "bookmarks": ["https://a.example", "https://b.example"],
        "notes": "hi",
    }
    r = c.post("/api/v1/profiles", json=body, headers=h)
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["engine"] == "firefox"
    assert j["resolution"] == "1280x720"
    assert j["search_engine"] == "google"
    assert j["bookmarks"] == ["https://a.example", "https://b.example"]
    # and the manager actually persisted them
    prof = c.app.state.container.profile_manager.profiles["fullprofile"]
    assert prof.engine == "firefox" and prof.resolution == "1280x720"
    assert prof.bookmarks == body["bookmarks"]

    # the mobile half: device_type/os_type are honoured too (chromium is the
    # only coherent engine for a mobile OS, and is asserted as such).
    r = c.post(
        "/api/v1/profiles",
        json={
            "name": "mobileprofile",
            "os_type": "android",
            "device_type": "mobile",
            "engine": "chromium",
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["device_type"] == "mobile"
    assert r.json()["os_type"] == "android"
    mprof = c.app.state.container.profile_manager.profiles["mobileprofile"]
    assert mprof.device_type == "mobile" and mprof.os_type == "android"


def test_update_profile_changes_engine_and_resolution(client):
    c, token = client
    h = _auth(token)
    c.post("/api/v1/profiles", json={"name": "p2", "os_type": "windows"}, headers=h)
    r = c.patch(
        "/api/v1/profiles/p2",
        json={"engine": "firefox", "resolution": "1920x1080"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["engine"] == "firefox"
    assert r.json()["resolution"] == "1920x1080"
    # untouched fields stay put
    assert r.json()["os_type"] == "windows"


def test_update_profile_omitted_fields_are_untouched(client):
    # PATCH is partial: a body that omits bookmarks must NOT wipe an existing
    # bookmark selection. exclude_unset guards this.
    c, token = client
    h = _auth(token)
    c.post(
        "/api/v1/profiles",
        json={"name": "p3", "os_type": "windows", "bookmarks": ["https://x.example"]},
        headers=h,
    )
    r = c.patch("/api/v1/profiles/p3", json={"notes": "changed"}, headers=h)
    assert r.status_code == 200, r.text
    prof = c.app.state.container.profile_manager.profiles["p3"]
    assert prof.bookmarks == ["https://x.example"]
    assert prof.notes == "changed"


def test_update_profile_can_clear_bookmarks(client):
    # Distinguish "omitted" (keep) from "explicitly []" (clear). Sending
    # bookmarks: [] must empty them.
    c, token = client
    h = _auth(token)
    c.post(
        "/api/v1/profiles",
        json={"name": "p4", "os_type": "windows", "bookmarks": ["https://y.example"]},
        headers=h,
    )
    r = c.patch("/api/v1/profiles/p4", json={"bookmarks": []}, headers=h)
    assert r.status_code == 200, r.text
    prof = c.app.state.container.profile_manager.profiles["p4"]
    assert prof.bookmarks == []
