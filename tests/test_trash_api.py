"""Lane parity: the REST door and the UI door must agree about deletion.

Lane parity is a named concern of the owning direction — a second door that
still destroyed records would reintroduce exactly the problem the trash closes.
So these tests assert both halves of it: an API delete FILES INTO the same trash
a UI delete does, and the trash is fully OPERABLE through the API (list, restore,
permanent delete, empty), including the refusal-with-a-reason on a name clash.
"""
import os
import pathlib

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.mcp_token import get_or_create_token
from src.services.trash.store import RETENTION_DAYS


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Point every runtime path at tmp so the test is isolated from the real
    # ~/.persona. config computes paths at import time and each store binds its
    # own copy by value, so patch config AND every module that captured one.
    import src.api.mcp_token as tok
    import src.core.config as cfg
    import src.services.profile.manager as pm

    data_dir = str(tmp_path / "data")
    profiles_file = str(tmp_path / "profiles.json")
    monkeypatch.setattr(tok, "_path", lambda: str(tmp_path / "mcp_token"))
    for m in (cfg, pm):
        monkeypatch.setattr(m, "DATA_DIR", data_dir, raising=False)
        monkeypatch.setattr(m, "PROFILES_FILE", profiles_file, raising=False)
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))
    monkeypatch.setenv("PERSONA_PROXIES_FILE", str(tmp_path / "proxies.json"))
    monkeypatch.setenv("PERSONA_SSH_HOSTS_FILE", str(tmp_path / "ssh.json"))
    monkeypatch.setenv("PERSONA_CERTS_FILE", str(tmp_path / "certs.json"))
    monkeypatch.setenv("PERSONA_CERTS_DIR", str(tmp_path / "certificates"))

    from src.core.container import Container

    container = Container()
    # base_url is loopback: the app blocks DNS-rebinding by checking Host.
    client = TestClient(create_app(container), base_url="http://127.0.0.1")
    headers = {"Authorization": f"Bearer {get_or_create_token()}"}
    return type(
        "Env",
        (),
        {
            "client": client, "headers": headers, "container": container,
            "tmp_path": tmp_path,
        },
    )


def _create_profile(env, name="alpha"):
    r = env.client.post(
        "/api/v1/profiles",
        json={"name": name, "os_type": "windows"},
        headers=env.headers,
    )
    assert r.status_code == 201, r.text
    return r


def _trash(env, **params):
    r = env.client.get("/api/v1/trash", headers=env.headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


# --- an API delete files into the trash, exactly like a UI delete ---


def test_an_api_deleted_profile_lands_in_the_trash(env):
    _create_profile(env)
    assert env.client.delete(
        "/api/v1/profiles/alpha", headers=env.headers
    ).status_code == 200
    body = _trash(env)
    assert [e["name"] for e in body["entries"]] == ["alpha"]
    assert body["entries"][0]["kind"] == "profile"


def test_an_api_delete_uses_the_same_trash_the_ui_lane_uses(env):
    # The single shared TrashStore is what makes the two lanes agree; an API
    # delete must be visible to the UI's own service object, not to a second
    # store of its own.
    _create_profile(env)
    env.client.delete("/api/v1/profiles/alpha", headers=env.headers)
    assert [e.name for e in env.container.trash_service.list()] == ["alpha"]


def test_a_ui_lane_delete_is_visible_through_the_api(env):
    # And the reverse direction: the trash must be operable through the API over
    # records the window deleted.
    pm = env.container.profile_manager
    pm.add_profile("beta", "", "windows")
    pm.delete_profile("beta")
    assert [e["name"] for e in _trash(env)["entries"]] == ["beta"]


def test_an_api_deleted_profile_leaves_the_live_profile_list(env):
    _create_profile(env)
    env.client.delete("/api/v1/profiles/alpha", headers=env.headers)
    listed = env.client.get("/api/v1/profiles", headers=env.headers).json()
    assert listed["profiles"] == [] and listed["total"] == 0


def test_a_trashed_profile_is_not_addressable_through_the_api(env):
    _create_profile(env)
    env.client.delete("/api/v1/profiles/alpha", headers=env.headers)
    assert env.client.get(
        "/api/v1/profiles/alpha", headers=env.headers
    ).status_code == 404


def test_a_delete_that_could_not_park_the_data_is_not_reported_as_success(
    env, monkeypatch
):
    # delete_profile returns False when the data dir cannot be moved, leaving
    # the profile fully intact. Replying 200 "deleted" regardless told the
    # caller an identity was gone while it was still on disk — the exact
    # claim-outlives-the-code defect this ticket closes, on the safety-critical
    # side. Lane parity: the UI lane reports the failure too.
    _create_profile(env)
    monkeypatch.setattr(
        env.container.profile_manager, "delete_profile", lambda name: False
    )

    r = env.client.delete("/api/v1/profiles/alpha", headers=env.headers)

    assert r.status_code == 500, r.text
    assert "alpha" in r.json()["detail"]


def test_a_profile_that_could_not_be_parked_is_still_there_afterwards(
    env, monkeypatch
):
    _create_profile(env)
    monkeypatch.setattr(
        env.container.profile_manager, "delete_profile", lambda name: False
    )
    env.client.delete("/api/v1/profiles/alpha", headers=env.headers)

    assert env.client.get(
        "/api/v1/profiles/alpha", headers=env.headers
    ).status_code == 200


# --- reading the trash ---


def test_the_listing_reports_the_retention_window(env):
    assert _trash(env)["retention_days"] == RETENTION_DAYS


def test_each_entry_reports_when_it_was_deleted_and_when_it_expires(env):
    _create_profile(env)
    env.client.delete("/api/v1/profiles/alpha", headers=env.headers)
    entry = _trash(env)["entries"][0]
    assert entry["deleted_at"] > 0
    assert entry["expires_at"] == pytest.approx(
        entry["deleted_at"] + RETENTION_DAYS * 86400
    )


def test_a_secret_bearing_record_is_flagged_as_still_holding_it(env):
    # The honest consequence, stated in the API too: trashing a proxy does NOT
    # remove its credentials from disk — permanent deletion does.
    env.container.proxy_store.add("exit-us", "socks5://user:pw@1.2.3.4:1080")
    env.container.proxy_store.delete("exit-us")
    entry = _trash(env)["entries"][0]
    assert entry["kind"] == "proxy"
    assert entry["holds_secret_material"] is True


def test_a_profile_entry_is_not_flagged_as_secret_bearing(env):
    _create_profile(env)
    env.client.delete("/api/v1/profiles/alpha", headers=env.headers)
    assert _trash(env)["entries"][0]["holds_secret_material"] is False


def test_the_listing_can_be_narrowed_by_kind(env):
    _create_profile(env)
    env.client.delete("/api/v1/profiles/alpha", headers=env.headers)
    env.container.proxy_store.add("exit-us", "socks5://1.2.3.4:1080")
    env.container.proxy_store.delete("exit-us")
    assert [e["name"] for e in _trash(env, kind="proxy")["entries"]] == ["exit-us"]
    assert _trash(env, kind="profile")["total"] == 1


def test_an_unknown_kind_is_rejected(env):
    r = env.client.get(
        "/api/v1/trash", headers=env.headers, params={"kind": "wallet"}
    )
    assert r.status_code == 400


# --- restoring through the API ---


def test_restoring_through_the_api_brings_the_profile_back(env):
    _create_profile(env)
    env.client.delete("/api/v1/profiles/alpha", headers=env.headers)
    entry_id = _trash(env)["entries"][0]["id"]
    r = env.client.post(
        f"/api/v1/trash/{entry_id}/restore", headers=env.headers
    )
    assert r.status_code == 200, r.text
    assert "alpha" in r.json()["message"]
    listed = env.client.get("/api/v1/profiles", headers=env.headers).json()
    assert [p["name"] for p in listed["profiles"]] == ["alpha"]


def test_a_restored_profile_keeps_its_data(env):
    _create_profile(env)
    pm = env.container.profile_manager
    data_dir = pm._data_path("alpha")
    os.makedirs(data_dir, exist_ok=True)
    pathlib.Path(data_dir, "Cookies").write_text("logged-in", encoding="utf-8")
    env.client.delete("/api/v1/profiles/alpha", headers=env.headers)
    entry_id = _trash(env)["entries"][0]["id"]
    env.client.post(f"/api/v1/trash/{entry_id}/restore", headers=env.headers)
    assert pathlib.Path(data_dir, "Cookies").read_text(encoding="utf-8") == "logged-in"


def test_a_restored_entry_leaves_the_trash(env):
    _create_profile(env)
    env.client.delete("/api/v1/profiles/alpha", headers=env.headers)
    entry_id = _trash(env)["entries"][0]["id"]
    env.client.post(f"/api/v1/trash/{entry_id}/restore", headers=env.headers)
    assert _trash(env)["total"] == 0


def test_restoring_over_a_taken_name_is_refused_with_the_reason(env):
    # Refused, and explained — never silently renamed, because a profile's
    # fingerprint derives from its name.
    _create_profile(env)
    env.client.delete("/api/v1/profiles/alpha", headers=env.headers)
    entry_id = _trash(env)["entries"][0]["id"]
    _create_profile(env)  # the name is taken again
    r = env.client.post(
        f"/api/v1/trash/{entry_id}/restore", headers=env.headers
    )
    assert r.status_code == 409
    assert "fingerprint" in r.json()["detail"]


def test_a_refused_restore_leaves_the_entry_recoverable(env):
    _create_profile(env)
    env.client.delete("/api/v1/profiles/alpha", headers=env.headers)
    entry_id = _trash(env)["entries"][0]["id"]
    _create_profile(env)
    env.client.post(f"/api/v1/trash/{entry_id}/restore", headers=env.headers)
    assert [e["id"] for e in _trash(env)["entries"]] == [entry_id]


def test_restoring_an_unknown_entry_is_a_404(env):
    r = env.client.post("/api/v1/trash/nope/restore", headers=env.headers)
    assert r.status_code == 404


# --- permanent deletion through the API ---


def test_permanently_deleting_one_entry_removes_it_for_good(env):
    _create_profile(env)
    pm = env.container.profile_manager
    os.makedirs(pm._data_path("alpha"), exist_ok=True)
    env.client.delete("/api/v1/profiles/alpha", headers=env.headers)
    entry = _trash(env)["entries"][0]
    parked = env.container.trash_store.get(entry["id"]).material_path
    r = env.client.delete(f"/api/v1/trash/{entry['id']}", headers=env.headers)
    assert r.status_code == 200, r.text
    assert _trash(env)["total"] == 0
    assert not os.path.exists(parked)


def test_a_permanently_deleted_entry_cannot_be_restored(env):
    _create_profile(env)
    env.client.delete("/api/v1/profiles/alpha", headers=env.headers)
    entry_id = _trash(env)["entries"][0]["id"]
    env.client.delete(f"/api/v1/trash/{entry_id}", headers=env.headers)
    r = env.client.post(
        f"/api/v1/trash/{entry_id}/restore", headers=env.headers
    )
    assert r.status_code == 404


def test_permanently_deleting_an_unknown_entry_is_a_404(env):
    assert env.client.delete(
        "/api/v1/trash/nope", headers=env.headers
    ).status_code == 404


def test_emptying_the_trash_destroys_everything_and_reports_the_count(env):
    _create_profile(env, "alpha")
    _create_profile(env, "beta")
    env.client.delete("/api/v1/profiles/alpha", headers=env.headers)
    env.client.delete("/api/v1/profiles/beta", headers=env.headers)
    r = env.client.delete("/api/v1/trash", headers=env.headers)
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 2
    assert _trash(env)["total"] == 0


def test_emptying_an_empty_trash_reports_zero(env):
    r = env.client.delete("/api/v1/trash", headers=env.headers)
    assert r.json()["deleted"] == 0


# --- the trash endpoints are behind the same guards as the rest of /api/v1 ---


def test_the_trash_requires_the_bearer_token(env):
    # It exposes deleted records including credential-bearing ones, so it must
    # never be reachable by an unauthenticated local process.
    assert env.client.get("/api/v1/trash").status_code == 401
    assert env.client.delete("/api/v1/trash").status_code == 401
    assert env.client.post("/api/v1/trash/x/restore").status_code == 401


def test_the_trash_rejects_a_rebound_host(env):
    headers = dict(env.headers)
    headers["host"] = "evil.example.com"
    assert env.client.get("/api/v1/trash", headers=headers).status_code == 403
