import asyncio
import time

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
from src.services.browser.refusal import classify_refusal
from src.services.proxy.errors import (
    GeographyDisprovenError,
    GeographyUnknownError,
    ProxyUnresolvedError,
)

GUID_WS = "ws://127.0.0.1:9333/devtools/browser/abc-123-guid"


class FakeLauncher:
    def __init__(self):
        self.launched = []
        self._running = set()
        # The verdict the next start_thread attempt will record, as (exception,
        # ) — set by a test to make that attempt a REFUSED one. The real
        # launcher swallows the guard's exception, classifies it, stores it, and
        # returns None; this double reproduces exactly that contract, through
        # the SHIPPED classify_refusal rather than a hand-built Refusal, so a
        # test cannot assert a kind the real classifier would never produce.
        self.refuse_next_with = None
        # name -> Refusal, mirroring the launcher's _last_refusal dict.
        self._last_refusal = {}
        # Clock the test controls. None means "use the real clock", which is
        # what the launcher does (it stamps time.time() in its handler) — a
        # frozen default would make every refusal look ancient to the route's
        # staleness check and mask the very behaviour under test. A test that
        # wants an explicitly OLD verdict sets this to a past value.
        self.now = None

    def running_profile_names(self):
        return set(self._running)

    def is_running(self, name):
        return name in self._running

    def started_at(self, name):
        return 1000.0 if name in self._running else None

    def start_thread(self, profile, log, on_ready=None, on_stop=None):
        if profile.name in self._running:
            # The real launcher returns HERE, before the pop below — a duplicate
            # is not an attempt and must not erase the verdict from the attempt
            # that did run (launcher.py). Reproduced because AC4(a) turns on it.
            if on_stop:
                on_stop()
            return
        # A NEW attempt supersedes the previous verdict, dropped at the attempt
        # rather than at its outcome (launcher.py).
        self._last_refusal.pop(profile.name, None)
        exc = self.refuse_next_with
        if exc is not None:
            self.refuse_next_with = None
            # Swallowed, classified, recorded — and None returned, the same None
            # a successful launch returns. That is the whole defect's shape.
            # Stamped with the real clock unless a test pinned one, exactly as
            # the launcher stamps time.time() at the instant it handles the
            # failure.
            at = time.time() if self.now is None else self.now
            refusal = classify_refusal(exc, at)
            if refusal is not None:
                self._last_refusal[profile.name] = refusal
            if on_stop:
                on_stop()
            return
        self.launched.append(profile)
        self._running.add(profile.name)
        if on_ready:
            on_ready()

    def last_refusal(self, name):
        return self._last_refusal.get(name)

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


# ---------------------------------------------------------------------------
# PS-82: a launch the fail-closed guard REFUSED must not be reported as success.
#
# The launcher already computes this verdict: start_thread catches the guard's
# exception, classifies it, records it under the profile name, and returns the
# same None a successful launch returns. Before this slice both API lanes
# composed success over that None — REST answered 202 {"success": true} for a
# profile that never opened, and no follow-up call could recover the reason.
#
# These assertions bind to the REPORTED VERDICT (status code + kind + detail),
# never to "the route is able to call the accessor" — an assertion that a
# mechanism EXISTS passes against an implementation that does not work (PS-11).
# Delete the last_refusal read from routes/browser.py and every test below goes
# red on the response itself.
# ---------------------------------------------------------------------------


def _refusal_body(r):
    """The refusal payload from a 409, whatever FastAPI wrapped it in."""
    return r.json()["detail"]


def test_launch_refused_by_unresolved_proxy_is_not_reported_as_success(client):
    # AC1/AC3. On origin/main this returns 202 {"success": true} — the profile
    # never opened, the guard fired, and the caller was told it worked.
    client._launcher.refuse_next_with = ProxyUnresolvedError(
        "Profile 'autobot' has proxy 'home' assigned but it could not be "
        "resolved (deleted/renamed?). Refusing to launch DIRECT."
    )
    r = client.post("/api/browser/autobot/launch")

    assert r.status_code == 409, "a refused launch must not answer the 202 success shape"
    body = _refusal_body(r)
    assert body["kind"] == "proxy_unresolved"
    # The settled operator sentence, passed through untouched — asserted by its
    # load-bearing content, not by restating the whole string here.
    assert "Refusing to launch DIRECT" in body["detail"]
    # The point is that nothing launched. Asserting only the 409 would pass even
    # if the browser had come up.
    assert client._launcher.launched == []
    assert client._launcher.is_running("autobot") is False


def test_geography_unknown_and_disproven_are_distinguishable_to_a_caller(client):
    # AC2. GeographyDisprovenError is a SUBCLASS of GeographyUnknownError, so a
    # lane that collapses them tells an operator the proxy was "never checked"
    # when it WAS checked and the check FAILED — sending them to re-run a check
    # that already ran. Assert on `kind`, never on prose.
    client._launcher.refuse_next_with = GeographyUnknownError(
        "Profile 'autobot' has proxy 'home' assigned but its geography could "
        "not be established (the proxy has never been checked successfully)."
    )
    unknown = client.post("/api/browser/autobot/launch")

    client._launcher.refuse_next_with = GeographyDisprovenError(
        "Profile 'autobot' has proxy 'home' assigned, but that proxy's LAST "
        "CHECK FAILED — the geography still on file is disproven."
    )
    disproven = client.post("/api/browser/autobot/launch")

    assert unknown.status_code == 409
    assert disproven.status_code == 409
    assert _refusal_body(unknown)["kind"] == "geography_unknown"
    assert _refusal_body(disproven)["kind"] == "geography_disproven"
    assert _refusal_body(unknown)["kind"] != _refusal_body(disproven)["kind"], (
        "the two causes have different remedies and must not collapse"
    )


def test_duplicate_launch_does_not_re_report_an_earlier_refusal(client):
    # AC4(a) — THE TRAP. _last_refusal is keyed by profile name and is dropped
    # at the START of an attempt, but a duplicate-launch call returns BEFORE
    # that drop, deliberately ("a click that gets refused as a duplicate is not
    # an attempt and must not erase the verdict from the attempt that did run").
    # A lane that read the dict unconditionally would hand that older refusal to
    # this caller as its own verdict.
    client._launcher.refuse_next_with = ProxyUnresolvedError("first attempt refused")
    first = client.post("/api/browser/autobot/launch")
    assert first.status_code == 409, "precondition: the first attempt was refused"

    # Put the profile in a running state so the NEXT call is a duplicate that
    # returns early — while the earlier verdict is still on record.
    client._launcher._running.add("autobot")
    assert client._launcher.last_refusal("autobot") is not None, (
        "precondition: the earlier verdict is still on record"
    )

    second = client.post("/api/browser/autobot/launch")

    assert second.status_code == 409
    # It must be refused as a DUPLICATE, not as the earlier proxy failure.
    assert second.json()["detail"] == "Browser already running", (
        "the second call re-reported a refusal that belonged to the first attempt"
    )


def test_a_successful_launch_after_a_refused_one_reports_success(client):
    # AC4(b). The other direction of the same trap: once a real attempt
    # succeeds, the stale verdict must not leak into its response.
    client._launcher.refuse_next_with = ProxyUnresolvedError("refused once")
    refused = client.post("/api/browser/autobot/launch")
    assert refused.status_code == 409, "precondition: the first attempt was refused"

    ok = client.post("/api/browser/autobot/launch")

    assert ok.status_code == 202
    assert ok.json()["success"] is True
    assert client._launcher.last_refusal("autobot") is None
    assert [p.name for p in client._launcher.launched] == ["autobot"]


def test_profile_with_no_proxy_is_unchanged(client):
    # AC5. That population never reaches the guard, so its response must be
    # byte-identical to today's.
    assert client._pm.profiles["autobot"].proxy in (None, "")
    r = client.post("/api/browser/autobot/launch")
    assert r.status_code == 202
    assert r.json()["success"] is True
    assert r.json()["cdp"]["debug_port"] == 9333


def test_ordinary_spawn_failure_is_not_reported_as_a_refusal(client):
    # AC6. classify_refusal returns None for anything that is not one of the
    # three guard classes, so the response shape must not change. Routine noise
    # has to stay quiet enough that a refusal reads as loud.
    client._launcher.refuse_next_with = RuntimeError("engine binary exploded")
    r = client.post("/api/browser/autobot/launch")

    assert r.status_code == 202, "an ordinary failure must not become a 409 refusal"
    assert r.json()["success"] is True
    assert client._launcher.last_refusal("autobot") is None
