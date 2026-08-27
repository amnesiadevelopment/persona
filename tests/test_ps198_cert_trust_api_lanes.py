"""PS-198: the mTLS trust verdict must reach the RECORD from the API lanes too.

The verdict pipeline is intact and lane-agnostic — the engine emits, the monitor
parses, ``cert_trust_status_from`` classifies, ``set_cert_trust_status``
persists. Only the fourth hop was lane-specific: ``ui/actions/browser.py`` passed
``on_cert_trust`` and the REST and MCP lanes did not (``grep -r cert_trust
src/api/`` → 0 hits across all 21 files). The verdict was computed, classified
and in hand at the moment both API lanes discarded it.

THE HARM IS A STALE AFFIRMATIVE, NOT SILENCE — and that is what these tests are
mostly about. A dropped verdict alone would leave the field ``None``, which
renders as nothing: an honest absence. But the field was not CLEARED either, so
an older ``trusted`` survived a later untrusted session and outlived a restart:

    after a good UI launch (trust imported) : 'trusted'
    after an UNTRUSTED API-lane launch      : 'trusted'   <-- unchanged
      dialog renders      : "last launch: trusted"  (styled GOOD)
      what really happened: NOT TRUSTED: opening without certificate trust

Neither existing defence covers it, and both are deliberately left untouched by
this slice: ``update_profile`` clears the verdict only when the CERTIFICATE
changes (so a rename does not discard a real verdict), and the dialog gate only
refuses to render a verdict with no certificate. The API lane produces exactly
the uncovered state — the certificate stays the same while the trust OUTCOME
changes.

HOW THESE TESTS BIND. Every assertion below reads the PERSISTED VALUE on the
profile — never "the lane passed a callback", never "the lane can call the
accessor". An assertion that a mechanism EXISTS passes against an implementation
that does not work (PS-11), and this file is the alarm for the opposite. Both
lanes are driven for REAL: the actual FastAPI route through ``TestClient``, and
the actual MCP tool through ``call_tool``, each over a REAL ``ProfileManager``
(tmp-file backed) and a REAL ``BrowserLauncher`` whose only fake is the engine
process itself. That closes the proposal's own stated bound — "the real HTTP
route and real MCP tool were not driven".

FALSIFICATION (AC8): remove ``on_cert_trust=`` from ``routes/browser.py`` and
``mcp_server.py``, leave everything else, and the AC1/AC3 tests here go RED on
the stored value.
"""

import asyncio
import io
import json
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.services.browser.launcher as launcher_mod
from src.api.dependencies import (
    get_browser_launcher,
    get_event_bus,
    get_profile_manager,
)
from src.api.mcp_server import build_mcp
from src.api.routes.browser import router as browser_router
from src.core.container import Container
from src.services.browser.launcher import BrowserLauncher
from src.services.profile.manager import ProfileManager

# The engine's real emit strings, copied verbatim from invisible_launch.py — the
# same literals tests/test_cert_trust_status.py freezes. Matched, never
# restated: cert_trust_status_from is the one authority for the mapping, so
# nothing here asserts the WORDING of a verdict, only that the right one landed.
MSG_TRUSTED = "MTLS_CA_TRUSTED"
MSG_FAILED = "MTLS_CA_IMPORT_FAILED: opening without certificate trust"


# --------------------------------------------------------------------------
# A fake engine PROCESS — the only thing faked. The launcher, the monitor
# thread, the classifier, the manager and its file are all real.
# --------------------------------------------------------------------------

class _Stdout:
    def __init__(self, lines, eof):
        self._io = io.StringIO("".join(line + "\n" for line in lines))
        self._eof = eof

    def readline(self, *a):
        line = self._io.readline()
        if not line:
            self._eof.set()
        return line

    def close(self):
        pass


class _Proc:
    def __init__(self, lines, returncode=0):
        self._exited = threading.Event()
        self.stdout = _Stdout(lines, self._exited)
        self.returncode = returncode

    def wait(self, timeout=None):
        self._exited.wait(timeout)
        return self.returncode

    def poll(self):
        return self.returncode if self._exited.is_set() else None

    def terminate(self):
        self._exited.set()

    def kill(self):
        self._exited.set()


class _FakeBus:
    def emit(self):
        pass


@pytest.fixture
def pm(tmp_path, monkeypatch):
    """A REAL ProfileManager over a real file in tmp_path.

    Real on purpose: the verdict has to survive ``save_profiles`` AND the
    hand-enumerated load allow-list in ``clean_data``. A fake manager would
    assert the lane called something, which is precisely the assertion this
    file exists to avoid making.
    """
    pf = tmp_path / "profiles.json"
    dd = tmp_path / "data"
    monkeypatch.setenv("PERSONA_PROFILES_FILE", str(pf))
    monkeypatch.setenv("PERSONA_DATA_DIR", str(dd))
    import src.core.config as cfg
    import src.services.profile.manager as mod

    monkeypatch.setattr(cfg, "PROFILES_FILE", str(pf))
    monkeypatch.setattr(cfg, "DATA_DIR", str(dd))
    monkeypatch.setattr(mod, "PROFILES_FILE", str(pf))
    monkeypatch.setattr(mod, "DATA_DIR", str(dd))
    return ProfileManager()


def _profile_with_cert(pm, name="fox", *, ai_control=False):
    pm.add_profile(name, "", "windows")
    p = pm.profiles[name]
    p.certificate = "admin-cert"
    p.engine = "firefox"
    p.ai_control = ai_control
    pm.save_profiles()
    return p


def _arm_engine(monkeypatch, lines):
    """Make the next launch run a fake engine that emits ``lines``.

    ``effective_engine`` is pinned to chromium so the REST lane's engine-install
    guard and its CDP wait stay out of the way — this file is about the LANE
    WIRING, and the message pipeline under test is engine-agnostic once the
    process is speaking.
    """
    proc = _Proc(lines)
    monkeypatch.setattr(launcher_mod, "spawn_browser", lambda p: proc)
    monkeypatch.setattr(
        "src.services.browser.process.effective_engine", lambda p: "chromium"
    )
    return proc


def _settle(proc, pm, name, predicate, timeout=5):
    """Wait for the engine output to be consumed, then for the RECORD to settle.

    The monitor runs on its own thread, so the assertion target is the persisted
    value reaching a steady state — not the launch call returning.
    """
    assert proc._exited.wait(timeout), "engine output was never fully consumed"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate(pm.profiles[name].cert_trust_status):
            break
        time.sleep(0.01)
    time.sleep(0.05)
    return pm.profiles[name].cert_trust_status


# --------------------------------------------------------------------------
# The two lanes, driven for real.
# --------------------------------------------------------------------------

def _rest_client(pm, bl, monkeypatch):
    app = FastAPI()
    app.include_router(browser_router, prefix="/api")
    app.dependency_overrides[get_browser_launcher] = lambda: bl
    app.dependency_overrides[get_profile_manager] = lambda: pm
    app.dependency_overrides[get_event_bus] = lambda: _FakeBus()

    async def _fake_cdp(name, *, not_before=None):
        from src.api.schemas.browser import BrowserCdpInfo, CdpWebSockets

        return BrowserCdpInfo(
            name=name,
            debug_port=9333,
            ws=CdpWebSockets(
                puppeteer="ws://x", playwright="ws://x", selenium="127.0.0.1:9333"
            ),
        )

    monkeypatch.setattr("src.api.routes.browser.cdp_info_for", _fake_cdp)
    return TestClient(app)


def _rest_launch(pm, monkeypatch, lines, name="fox", automation=False):
    bl = BrowserLauncher()
    proc = _arm_engine(monkeypatch, lines)
    client = _rest_client(pm, bl, monkeypatch)
    r = client.post(f"/api/browser/{name}/launch?automation={str(automation).lower()}")
    assert r.status_code == 202, r.text
    return proc


def _mcp_launch(pm, monkeypatch, lines, name="fox"):
    bl = BrowserLauncher()
    proc = _arm_engine(monkeypatch, lines)
    c = Container()
    c._instances["pm"] = pm
    c._instances["bl"] = bl
    mcp = build_mcp(c)
    result = asyncio.run(mcp.call_tool("launch_profile", {"name": name}))
    body = json.loads(result[0].text)
    assert body["launched"] is True, body
    return proc


LANES = [("rest", _rest_launch), ("mcp", _mcp_launch)]


# --------------------------------------------------------------------------
# AC1 — a failing CA import through an API lane PERSISTS the untrusted verdict.
# AC2 — both of these fail on origin/main today (the lanes passed no callback).
# --------------------------------------------------------------------------

@pytest.mark.parametrize("lane,launch", LANES)
def test_failed_import_through_an_api_lane_persists_the_untrusted_verdict(
    lane, launch, pm, monkeypatch
):
    _profile_with_cert(pm)

    proc = launch(pm, monkeypatch, [MSG_FAILED, "BROWSER_CLOSED"])
    status = _settle(proc, pm, "fox", lambda s: s is not None)

    assert status is not None, (
        f"the {lane} lane launched with the CA untrusted and left no trace"
    )
    # Read as an OUTCOME, not as prose: the operator must be able to tell this
    # apart from success. cert_trust_status_from owns the wording.
    assert not status.startswith("trusted")


# --------------------------------------------------------------------------
# AC3 — ⭐ THE STALE AFFIRMATIVE. The point of the slice, and the criterion a
# fix that only adds the argument would miss.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("lane,launch", LANES)
def test_a_stale_trusted_verdict_does_not_survive_an_untrusted_api_launch(
    lane, launch, pm, monkeypatch
):
    """A profile reading 'trusted', launched through an API lane where the
    import FAILS, must not still read 'trusted'."""
    _profile_with_cert(pm)
    pm.set_cert_trust_status("fox", "trusted")
    assert pm.profiles["fox"].cert_trust_status == "trusted", "precondition"

    proc = launch(pm, monkeypatch, [MSG_FAILED, "BROWSER_CLOSED"])
    status = _settle(proc, pm, "fox", lambda s: s != "trusted")

    assert status != "trusted", (
        f"the {lane} lane left an affirmative clean bill of health standing "
        "over a session that ran with its CA untrusted"
    )
    assert status is not None and not status.startswith("trusted")


@pytest.mark.parametrize("lane,launch", LANES)
def test_a_stale_trusted_verdict_does_not_survive_a_launch_that_emits_nothing(
    lane, launch, pm, monkeypatch
):
    """The half that the argument alone cannot close.

    Passing ``on_cert_trust`` records a verdict when the engine EMITS one. A
    launch that reaches the cert path and emits nothing — or dies before the
    line — would still leave the previous session's 'trusted' standing. THE
    DISCRIMINATOR IS THE ATTEMPT, NOT THE MESSAGE (api/refusal_report.py): the
    verdict is dropped at the START of the attempt, mirroring
    ``self._last_refusal.pop(profile.name, None)`` one verdict over.

    Clearing at the OUTCOME instead would be unreachable in exactly this case:
    no message, no clear.
    """
    _profile_with_cert(pm)
    pm.set_cert_trust_status("fox", "trusted")

    proc = launch(pm, monkeypatch, ["BROWSER_STARTED", "BROWSER_CLOSED"])
    status = _settle(proc, pm, "fox", lambda s: s is None)

    assert status is None, (
        f"the {lane} lane ran a whole session without reaching the trust "
        "outcome and kept asserting the PREVIOUS session's verdict"
    )


@pytest.mark.parametrize("lane,launch", LANES)
def test_the_dropped_stale_verdict_does_not_come_back_on_reload(
    lane, launch, pm, monkeypatch
):
    """The stale affirmative's defining property was that it SURVIVED A
    RESTART. Dropping it in memory is not enough — the drop must be saved."""
    _profile_with_cert(pm)
    pm.set_cert_trust_status("fox", "trusted")

    proc = launch(pm, monkeypatch, ["BROWSER_STARTED", "BROWSER_CLOSED"])
    _settle(proc, pm, "fox", lambda s: s is None)

    import src.services.profile.manager as mod

    assert mod.ProfileManager().profiles["fox"].cert_trust_status is None, (
        "the stale 'trusted' was dropped in memory but rose again from disk"
    )


# --------------------------------------------------------------------------
# AC4 — round trip. A fresh manager over the same file reads the verdict.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("lane,launch", LANES)
def test_the_verdict_recorded_by_an_api_lane_survives_a_fresh_manager(
    lane, launch, pm, monkeypatch, tmp_path
):
    """``clean_data``'s load path is a HAND-ENUMERATED allow-list: a field
    absent from it is silently dropped on reload even though to_dict() saved
    it. Pinned here for the API lanes specifically."""
    _profile_with_cert(pm)

    proc = launch(pm, monkeypatch, [MSG_FAILED, "BROWSER_CLOSED"])
    status = _settle(proc, pm, "fox", lambda s: s is not None)

    # NON-VACUITY FIRST. Without this line every assertion below is satisfied by
    # None == None — which is exactly the state on origin/main, where the lane
    # records nothing at all. A round-trip test that passes because there is
    # nothing to round-trip asserts the opposite of what it claims.
    assert status is not None, "nothing was recorded, so nothing was round-tripped"

    # It reached the FILE...
    raw = json.loads((tmp_path / "profiles.json").read_text(encoding="utf-8"))
    assert raw["fox"]["cert_trust_status"] == status

    # ...and it comes back OUT of it.
    import src.services.profile.manager as mod

    assert mod.ProfileManager().profiles["fox"].cert_trust_status == status


# --------------------------------------------------------------------------
# AC5 — the fix must not only record FAILURES.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("lane,launch", LANES)
def test_a_successful_import_through_an_api_lane_persists_trusted(
    lane, launch, pm, monkeypatch
):
    _profile_with_cert(pm)

    proc = launch(pm, monkeypatch, [MSG_TRUSTED, "BROWSER_CLOSED"])
    status = _settle(proc, pm, "fox", lambda s: s == "trusted")

    assert status == "trusted"


def test_the_two_outcomes_are_distinguishable_through_the_api_lanes(
    pm, monkeypatch
):
    """Guards against a wiring that writes the same value on every path — the
    clear-at-attempt makes that failure mode newly available (everything could
    end up None), so it is asserted rather than assumed."""
    _profile_with_cert(pm, "good")
    _profile_with_cert(pm, "bad")

    proc = _rest_launch(pm, monkeypatch, [MSG_TRUSTED, "BROWSER_CLOSED"], "good")
    _settle(proc, pm, "good", lambda s: s == "trusted")
    proc = _mcp_launch(pm, monkeypatch, [MSG_FAILED, "BROWSER_CLOSED"], "bad")
    _settle(proc, pm, "bad", lambda s: s is not None)

    good = pm.profiles["good"].cert_trust_status
    bad = pm.profiles["bad"].cert_trust_status
    assert good == "trusted"
    assert bad is not None and not bad.startswith("trusted")
    assert good != bad


# --------------------------------------------------------------------------
# AC7 — a profile with NO certificate is byte-identical to today.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("lane,launch", LANES)
def test_a_profile_with_no_certificate_is_byte_identical(
    lane, launch, pm, monkeypatch
):
    """No certificate assigned means there is no verdict to invalidate: the
    clear-at-attempt is GATED on the certificate, so no field is written and
    no save is provoked. The engine emits no MTLS_* line for such a profile
    either, so both halves of the wiring must stay silent."""
    pm.add_profile("plain", "", "windows")
    before = json.dumps(pm.profiles["plain"].to_dict(), sort_keys=True)

    proc = launch(pm, monkeypatch, ["BROWSER_STARTED", "BROWSER_CLOSED"], "plain")
    _settle(proc, pm, "plain", lambda s: s is not None, timeout=1)

    assert pm.profiles["plain"].cert_trust_status is None
    after = json.dumps(pm.profiles["plain"].to_dict(), sort_keys=True)
    assert after == before, f"the {lane} lane disturbed a certificate-less profile"


# --------------------------------------------------------------------------
# The precedent this borrows from, held to its own rule.
# --------------------------------------------------------------------------

def test_a_duplicate_launch_does_not_erase_the_verdict_of_the_attempt_that_ran(
    pm, monkeypatch
):
    """THE TRAP, inherited from the refusal verdict one field over.

    The drop is placed AFTER ``start_thread``'s duplicate-launch return, on
    purpose and for the reason already written there: "a click that gets
    refused as a duplicate is not an attempt and must not erase the verdict
    from the attempt that did run". A drop placed before that return would
    wipe a live, correct verdict every time a second launch is refused.
    """
    _profile_with_cert(pm)
    bl = BrowserLauncher()

    # A real attempt records a real verdict...
    proc = _Proc([MSG_FAILED, "BROWSER_CLOSED"])
    monkeypatch.setattr(launcher_mod, "spawn_browser", lambda p: proc)
    monkeypatch.setattr(
        "src.services.browser.process.effective_engine", lambda p: "chromium"
    )
    client = _rest_client(pm, bl, monkeypatch)
    assert client.post("/api/browser/fox/launch?automation=false").status_code == 202
    recorded = _settle(proc, pm, "fox", lambda s: s is not None)
    assert recorded is not None, "precondition: the attempt that ran left a verdict"

    # ...and a launch refused as a DUPLICATE must not disturb it.
    #
    # Driven at the LAUNCHER, not through the route, because that is where the
    # ordering property lives: the route's own is_running() check would answer
    # 409 first and start_thread would never run, so a route-level probe would
    # pass against a launcher that clears before its duplicate return. Reserving
    # the name in `_starting` is the real launcher's own duplicate condition
    # (`_active_sessions or _starting`) and is exactly the state a concurrent
    # launch leaves behind while a slow spawn_browser() runs.
    bl._starting.add("fox")
    stopped = threading.Event()
    bl.start_thread(
        pm.profiles["fox"],
        lambda _m: None,
        on_stop=stopped.set,
        on_cert_trust=lambda s: pm.set_cert_trust_status("fox", s),
    )
    assert stopped.wait(5), "the duplicate launch never returned to its caller"

    assert pm.profiles["fox"].cert_trust_status == recorded, (
        "a duplicate-launch refusal erased the verdict from the attempt that "
        "actually ran"
    )


def test_the_automation_launch_path_still_carries_the_certificate_gate(
    pm, monkeypatch
):
    """The REST lane passes ``dataclasses.replace(profile, ai_control=True)``
    to the launcher on an automation launch. The clear-at-attempt reads
    ``profile.certificate`` off THAT copy, so this pins that replace() keeps
    carrying it — if it ever stopped, the stale affirmative would silently
    come back for exactly the callers most likely to hit it.
    """
    _profile_with_cert(pm, ai_control=True)
    pm.set_cert_trust_status("fox", "trusted")

    proc = _rest_launch(
        pm, monkeypatch, [MSG_FAILED, "BROWSER_CLOSED"], automation=True
    )
    status = _settle(proc, pm, "fox", lambda s: s != "trusted")

    assert status is not None and not status.startswith("trusted")
