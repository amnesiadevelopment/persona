import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.browser import router as browser_router
from src.api import cdp_endpoint
from src.api.dependencies import (
    get_browser_launcher,
    get_event_bus,
    get_profile_manager,
)
from src.models.profile import Profile

GUID_WS = "ws://127.0.0.1:9333/devtools/browser/abc-123-guid"


class FakeLauncher:
    def __init__(self):
        self.launched = []
        self._running = set()

    def running_profile_names(self):
        return set(self._running)

    def is_running(self, name):
        return name in self._running

    def started_at(self, name):
        return 1000.0 if name in self._running else None

    def start_thread(self, profile, log, on_ready=None, on_stop=None):
        self.launched.append(profile)
        self._running.add(profile.name)
        if on_ready:
            on_ready()

    def stop_profile(self, name, timeout=2):
        self._running.discard(name)
        return True


class FakePM:
    def __init__(self, names, ai_names=()):
        self.profiles = {n: Profile(name=n) for n in names}
        # Profiles whose operator turned AI control ON. Kept separate from the
        # plain ones so both kinds stay available: the gate tests need a
        # genuinely OFF profile, and the CDP/engine tests need an ON one.
        self.profiles.update(
            {n: Profile(name=n, ai_control=True) for n in ai_names}
        )


class FakeBus:
    def emit(self):
        pass


@pytest.fixture
def client(monkeypatch):
    launcher = FakeLauncher()
    pm = FakePM(["shopper"], ai_names=["autobot"])
    app = FastAPI()
    app.include_router(browser_router, prefix="/api")
    app.dependency_overrides[get_browser_launcher] = lambda: launcher
    app.dependency_overrides[get_profile_manager] = lambda: pm
    app.dependency_overrides[get_event_bus] = lambda: FakeBus()

    async def fake_cdp(name, *, not_before=None):
        from src.api.schemas.browser import BrowserCdpInfo, CdpWebSockets
        return BrowserCdpInfo(
            name=name, debug_port=9333,
            ws=CdpWebSockets(puppeteer=GUID_WS, playwright=GUID_WS,
                             selenium="127.0.0.1:9333"),
        )

    monkeypatch.setattr("src.api.routes.browser.cdp_info_for", fake_cdp)
    c = TestClient(app)
    c._launcher = launcher
    c._pm = pm
    return c


def test_launch_returns_cdp_endpoint(client):
    # Subject is the CDP payload, so the profile must be one the operator
    # actually enabled automation for — a bare profile is refused by the gate.
    r = client.post("/api/browser/autobot/launch")
    assert r.status_code == 202
    body = r.json()
    assert body["success"] is True
    assert body["cdp"]["debug_port"] == 9333
    assert body["cdp"]["ws"]["playwright"] == GUID_WS
    assert body["cdp"]["ws"]["selenium"] == "127.0.0.1:9333"


def test_launch_refused_when_profile_not_ai_enabled(client):
    # The operator's stored ai_control=False governs the unauthenticated CDP
    # channel an automation launch would open, so this lane must REFUSE rather
    # than compose ai_control=True over it (as it did before PS-33).
    assert client._pm.profiles["shopper"].ai_control is False
    r = client.post("/api/browser/shopper/launch")
    assert r.status_code == 409
    # The refusal must name the remedy, mirroring the MCP lane's wording.
    assert r.json()["detail"] == "profile is not AI-enabled (enable AI control first)"
    # The point is that no browser starts — asserting only the 409 would pass
    # even if the launch had gone ahead.
    assert client._launcher.launched == []
    # And the refusal must not write the profile record either way.
    assert client._pm.profiles["shopper"].ai_control is False


def test_launch_ai_enabled_profile_keeps_ai_control_on(client):
    r = client.post("/api/browser/autobot/launch")
    assert r.status_code == 202
    launched = client._launcher.launched[-1]
    assert launched.name == "autobot"
    assert launched.ai_control is True
    # A successful automation launch still writes nothing to the record.
    assert client._pm.profiles["autobot"].ai_control is True


def test_manual_launch_has_no_cdp(client):
    r = client.post("/api/browser/shopper/launch", params={"automation": "false"})
    body = r.json()
    assert body["cdp"] is None
    assert client._launcher.launched[-1].ai_control is False


def test_manual_launch_of_ai_enabled_profile_has_no_cdp(client):
    # automation=false is unaffected by the gate on both kinds of profile. The
    # raw profile is passed straight through, so an AI-enabled one keeps its
    # stored True (unchanged from before PS-33) — what matters is that this
    # lane neither rewrites the flag nor resolves a CDP endpoint.
    r = client.post("/api/browser/autobot/launch", params={"automation": "false"})
    assert r.status_code == 202
    assert r.json()["cdp"] is None
    launched = client._launcher.launched[-1]
    assert launched.name == "autobot"
    assert launched.ai_control is True
    assert client._pm.profiles["autobot"].ai_control is True


def test_launch_409_when_running(client):
    # Must use an AI-enabled profile: on a gated profile the FIRST post would
    # be refused, the browser would never start, and the second 409 would come
    # from the gate instead of the already-running conflict this test is for.
    first = client.post("/api/browser/autobot/launch")
    assert first.status_code == 202
    assert client._launcher.is_running("autobot")
    r = client.post("/api/browser/autobot/launch")
    assert r.status_code == 409
    assert r.json()["detail"] == "Browser already running"


def test_launch_404_unknown(client):
    r = client.post("/api/browser/ghost/launch")
    assert r.status_code == 404


def test_cdp_route_409_when_not_running(client):
    r = client.get("/api/browser/shopper/cdp")
    assert r.status_code == 409


def test_cdp_route_ok_when_running(client):
    # Subject is the CDP route, so the profile has to actually launch.
    client.post("/api/browser/autobot/launch")
    r = client.get("/api/browser/autobot/cdp")
    assert r.status_code == 200
    assert r.json()["ws"]["playwright"] == GUID_WS


def test_firefox_launch_409_when_engine_missing(client, monkeypatch):
    # #119 carry-over: the API launch path must only CHECK for the Firefox
    # engine — never fall through into the engine's own blocking download.
    from src.services.browser import invisible_launch as il

    def _boom(*a, **k):
        raise AssertionError("API launch must not trigger the engine download")

    monkeypatch.setattr(il, "is_invisible_installed", lambda: False)
    monkeypatch.setattr(il, "ensure_invisible_installed", _boom)
    client._pm.profiles["fox"] = Profile(name="fox", engine="firefox")
    r = client.post("/api/browser/fox/launch")
    assert r.status_code == 409
    assert "engine" in r.json()["detail"].lower()
    assert client._launcher.launched == []


def test_firefox_launch_proceeds_when_engine_installed(client, monkeypatch):
    from src.services.browser import invisible_launch as il

    monkeypatch.setattr(il, "is_invisible_installed", lambda: True)
    client._pm.profiles["fox"] = Profile(name="fox", engine="firefox")
    r = client.post("/api/browser/fox/launch")
    assert r.status_code == 202
    assert [p.name for p in client._launcher.launched] == ["fox"]
    # Firefox speaks Juggler, not CDP — no endpoint to wait for.
    assert r.json()["cdp"] is None


def test_mobile_firefox_launch_ignores_missing_firefox_engine(client, monkeypatch):
    # A mobile profile launches chromium even if it stored engine=firefox, so a
    # missing Firefox engine must not block it. Subject is engine resolution,
    # so the profile is AI-enabled to get past the ai_control gate.
    from src.services.browser import invisible_launch as il

    monkeypatch.setattr(il, "is_invisible_installed", lambda: False)
    client._pm.profiles["mob"] = Profile(
        name="mob", engine="firefox", os_type="android", ai_control=True
    )
    r = client.post("/api/browser/mob/launch")
    assert r.status_code == 202
    assert [p.name for p in client._launcher.launched] == ["mob"]


def test_mobile_firefox_profile_is_gated_because_it_resolves_to_chromium(
    client, monkeypatch
):
    # A mobile profile stores engine="firefox" but effective_engine resolves it
    # to chromium, so it DOES open a CDP port and must be gated. This fails if
    # the guard reads profile.engine instead of the resolved engine.
    from src.services.browser import invisible_launch as il
    from src.services.browser.process import effective_engine

    monkeypatch.setattr(il, "is_invisible_installed", lambda: False)
    prof = Profile(name="mob", engine="firefox", os_type="android")
    assert prof.engine == "firefox" and effective_engine(prof) == "chromium"
    client._pm.profiles["mob"] = prof
    r = client.post("/api/browser/mob/launch")
    assert r.status_code == 409
    assert r.json()["detail"] == "profile is not AI-enabled (enable AI control first)"
    assert client._launcher.launched == []


def test_fetch_browser_ws_url_parses_real_json_version():
    # honest test: a real HTTP server serving /json/version shape, parsed by
    # the real fetch function (no mock of the parse logic).
    import http.server
    import socketserver
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                b'{"webSocketDebuggerUrl": "ws://127.0.0.1:%d/devtools/browser/xyz"}'
                % self.server.server_address[1]
            )

        def log_message(self, *_):
            pass

    with socketserver.TCPServer(("127.0.0.1", 0), H) as srv:
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            ws = asyncio.run(cdp_endpoint.fetch_browser_ws_url(port, timeout_s=5))
        finally:
            srv.shutdown()
    assert ws == f"ws://127.0.0.1:{port}/devtools/browser/xyz"


def test_build_cdp_info_shape():
    info = cdp_endpoint.build_cdp_info("p", 9333, GUID_WS)
    assert info.debug_port == 9333
    assert info.ws.selenium == "127.0.0.1:9333"
    assert info.ws.puppeteer == GUID_WS
