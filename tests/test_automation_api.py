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

    def start_thread(self, profile, log, on_ready=None, on_stop=None):
        self.launched.append(profile)
        self._running.add(profile.name)
        if on_ready:
            on_ready()

    def stop_profile(self, name, timeout=2):
        self._running.discard(name)
        return True


class FakePM:
    def __init__(self, names):
        self.profiles = {n: Profile(name=n) for n in names}


class FakeBus:
    def emit(self):
        pass


@pytest.fixture
def client(monkeypatch):
    launcher = FakeLauncher()
    pm = FakePM(["shopper"])
    app = FastAPI()
    app.include_router(browser_router, prefix="/api")
    app.dependency_overrides[get_browser_launcher] = lambda: launcher
    app.dependency_overrides[get_profile_manager] = lambda: pm
    app.dependency_overrides[get_event_bus] = lambda: FakeBus()

    async def fake_cdp(name):
        from src.api.schemas.browser import BrowserCdpInfo, CdpWebSockets
        return BrowserCdpInfo(
            name=name, debug_port=9333,
            ws=CdpWebSockets(puppeteer=GUID_WS, playwright=GUID_WS,
                             selenium="127.0.0.1:9333"),
        )

    monkeypatch.setattr("src.api.routes.browser.cdp_info_for", fake_cdp)
    c = TestClient(app)
    c._launcher = launcher
    return c


def test_launch_returns_cdp_endpoint(client):
    r = client.post("/api/browser/shopper/launch")
    assert r.status_code == 202
    body = r.json()
    assert body["success"] is True
    assert body["cdp"]["debug_port"] == 9333
    assert body["cdp"]["ws"]["playwright"] == GUID_WS
    assert body["cdp"]["ws"]["selenium"] == "127.0.0.1:9333"


def test_launch_automation_forces_ai_control(client):
    client.post("/api/browser/shopper/launch")
    launched = client._launcher.launched[-1]
    assert launched.ai_control is True


def test_manual_launch_has_no_cdp(client):
    r = client.post("/api/browser/shopper/launch", params={"automation": "false"})
    body = r.json()
    assert body["cdp"] is None
    assert client._launcher.launched[-1].ai_control is False


def test_launch_409_when_running(client):
    client.post("/api/browser/shopper/launch")
    r = client.post("/api/browser/shopper/launch")
    assert r.status_code == 409


def test_launch_404_unknown(client):
    r = client.post("/api/browser/ghost/launch")
    assert r.status_code == 404


def test_cdp_route_409_when_not_running(client):
    r = client.get("/api/browser/shopper/cdp")
    assert r.status_code == 409


def test_cdp_route_ok_when_running(client):
    client.post("/api/browser/shopper/launch")
    r = client.get("/api/browser/shopper/cdp")
    assert r.status_code == 200
    assert r.json()["ws"]["playwright"] == GUID_WS


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
