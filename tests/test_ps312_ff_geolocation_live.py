"""What a page ACTUALLY receives when it calls ``navigator.geolocation`` on a
proxied Firefox launch — and why persona ships no Firefox geolocation spoof.

PS-312. Read this file's reason for existing before changing it.

THE QUESTION, and why it had to be MEASURED
-------------------------------------------
``spawn_browser``'s Firefox arm forwards ``locale`` and ``timezone`` to the
child and does not forward ``lat``/``lon``, though ``proxy.lat``/``proxy.lon``
are in scope fifty lines above the cfg dict. The Chromium arm, by contrast,
installs ``build_geo_extension`` for EVERY proxied profile, and ``geo_ext.py``
states the premise in the tree's own words: *"Rather than let
getCurrentPosition fall through to the REAL host coords (a 'spoofed location'
tell — country=DE but coords in the operator's real city, audit7 #5), deny
permission."*

``tests/test_engine_masking_matrix.py`` recorded that cell as
``position_not_established`` — no spoof, and NO RECORDED REASON — because
nothing in the tree said whether the premise was true, false, or already
handled on this engine. This file is the measurement that settles it, and the
matrix cell moved to ``not_covered_recorded`` in the same commit.

THE ANSWER: the engine already refuses, LOCALLY, and yields no coordinates. So
there is no host position for a spoof to displace, and shipping one would be a
NET LOSS under Invariant #0 — it would replace a native refusal that reads
clean (``Function.prototype.toString`` on ``getCurrentPosition`` still renders
``[native code]``) with a JS override a detector can see.

WHY THESE TESTS LAUNCH A REAL BROWSER
-------------------------------------
Because the claim is about WHAT A PAGE RECEIVES, and no cheaper oracle can
reach it. A test over the cfg dict asserts that ``lat`` is absent from a dict —
the half that was never in doubt — and would pass identically whether the
engine leaked the host position or refused it. That is this project's own
recurring failure mode (PS-11: *"tests that assert on what was written, not on
what happens"*), and the sibling live suite
``test_verify_chromium_timezone_live`` exists for exactly the same reason on
the other engine.

``getCurrentPosition`` is CALLBACK-based, so every assertion here is on what
the success or error callback actually received — never on a builder having
been called and never on a substring of generated source. The JS resolves on
whichever callback fires and times out explicitly, so a HANG is a recorded
outcome rather than an absent one.

THE POSITIVE CONTROL IS NOT OPTIONAL
------------------------------------
"No position came back" is ambiguous between *the engine refused* and *my probe
is broken*, and the second reads as the first. So
``test_a_reachable_provider_DOES_reach_the_page`` points
``geo.provider.network.url`` at a local endpoint serving sentinel coordinates
and asserts a position ARRIVES through this exact channel, on this exact page,
with this exact JS. That is what makes the null in the other tests a fact about
the engine. It is also the A/B that isolates the cause: the two launches differ
in exactly one pref.

Both pref overlays here are TEST-SIDE (monkeypatched over ``_profile_prefs``),
never product changes. The engine's two settled geo decisions — ``geo.enabled:
True`` and ``permissions.default.geo`` DELIBERATELY UNSET — are measured
anti-tells that this ticket must not reverse, and
``test_the_engines_two_settled_geo_decisions_are_not_reversed`` pins that the
product still ships neither.

SKIPPED, NEVER SILENTLY PASSED, wherever the engine, the display or the
launcher is missing. An absent engine must not read as a clean bill of health.
"""

import json
import os
import queue
import shutil
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

# --- gates: an absent engine is a provisioning problem, not a green ----------


def _engine_present() -> bool:
    try:
        from src.services.browser.engine_install import is_invisible_installed

        return bool(is_invisible_installed())
    except Exception:
        return False


def _display_present() -> bool:
    return bool(os.environ.get("DISPLAY", "").strip()) or bool(
        shutil.which("Xvfb")
    )


def _ensure_display():
    """``(display, owned_xvfb_or_None)`` — an inherited DISPLAY, else our own.

    NOT a preference, and NOT a second copy: this delegates to
    ``chromium_tier._ensure_display``, the tree's existing owner of exactly
    this problem (it starts an Xvfb, waits for its lock file, and hands back
    the process so the caller can tear down what it owns).

    IT IS ALSO A BUG FIX FOUND BY AC8, recorded because the failure mode is
    subtle. The skip gate above is satisfied by Xvfb merely being INSTALLED, so
    under a plain ``pytest`` with no DISPLAY exported these tests did not skip
    — they RAN, and the headful engine waited forever on a display nobody had
    started, tripping the suite's per-test timeout and dumping every thread in
    the file. A gate that says "a display is OBTAINABLE" has to be paired with
    a step that actually obtains one.
    """
    from src.services.verify.chromium_tier import _ensure_display as _ed

    return _ed()


@pytest.fixture(scope="module", autouse=True)
def _display():
    """ONE display for the whole module, exported for every launch below.

    Module-scoped for a reason that cost a debugging cycle: an earlier draft
    obtained a display PER LAUNCH and tore it down in the same ``finally``.
    Because it also exported ``DISPLAY``, the second launch INHERITED the name
    of an Xvfb that had already been killed — ``_ensure_display`` sees a
    non-empty ``DISPLAY`` and hands it straight back, so the engine connected
    to nothing and the run degraded to three "never reported BROWSER_STARTED"
    skips. Skips are the honest outcome for a host with no display, which is
    exactly what made the bug quiet: the suite still reported success.

    So the display outlives every launch in the file, and only what we started
    is torn down — an inherited ``DISPLAY`` is left exactly as found.
    """
    inherited = os.environ.get("DISPLAY", "").strip()
    display, xvfb = _ensure_display()
    os.environ["DISPLAY"] = display
    try:
        yield display
    finally:
        if xvfb is not None:
            try:
                xvfb.terminate()
                xvfb.wait(timeout=10)
            except Exception:
                pass
            # Do not leave the name of a dead display behind for anything that
            # runs after this module — that is the same trap, one scope up.
            if inherited:
                os.environ["DISPLAY"] = inherited
            else:
                os.environ.pop("DISPLAY", None)


def _launcher_present() -> bool:
    try:
        import invisible_playwright  # noqa: F401

        return True
    except Exception:
        return False


requires_engine = pytest.mark.skipif(
    not _engine_present(),
    reason="persona's firefox engine is not installed on this host",
)
requires_display = pytest.mark.skipif(
    not _display_present(),
    reason="no DISPLAY and no Xvfb: persona ships a HEADED browser",
)
requires_launcher = pytest.mark.skipif(
    not _launcher_present(),
    reason="invisible_playwright is not importable on this host",
)
#: This file's OWN per-test bound, and it must stay comfortably ABOVE the two
#: waits below while every test stays under the project's `timeout = 120`.
#: pyproject sets that bound for the whole suite, and a live browser launch is
#: the one thing here that can exceed it: 120s is generous for a unit test and
#: TIGHT for a cold engine start plus a 30s geolocation wait.
#:
#: AC8 CAUGHT EXACTLY THAT. An earlier draft waited 180s for BROWSER_STARTED —
#: longer than the suite's own bound — so a slow launch could never produce the
#: skip it was written to produce: pytest-timeout fired first and dumped every
#: thread in the file. A bound a test cannot reach is not a bound.
#:
#: So the launch wait is sized to LOSE that race deliberately, leaving room for
#: the reading that follows. A launch slower than this SKIPS (an unobtained
#: reading), which is the honest outcome and never a product verdict.
#:
#: 120s rather than 60s, measured: a cold engine start on a LOADED host (this
#: file's own launches overlap nothing, but a full-suite run has plenty else in
#: flight) intermittently crossed 60s and skipped. A skip is honest but it is
#: still a reading not taken, so the bound is set where the launch reliably
#: finishes rather than where it usually does.
_START_TIMEOUT_S = 120.0

#: Launch attempts before the reading is declared UNOBTAINED. Not flake
#: tolerance for its own sake: the engine documents that ~half of fresh proxied
#: launches wedge on a half-destroyed initial-window attach, and retries them
#: internally on a fresh worker. Measured here: the un-granted leg skipped
#: reproducibly at one attempt and passes with retries.
_LAUNCH_ATTEMPTS = 3

#: Marked per-test rather than inherited, because these are REAL browser
#: launches and the 120s default is not sized for one. Inert without
#: pytest-timeout installed — as is the ini bound it raises — so this can only
#: ever relax a bound that exists, never invent one.
_LIVE_TIMEOUT = pytest.mark.timeout(420)

pytestmark = [requires_engine, requires_display, requires_launcher,
              _LIVE_TIMEOUT]


# --- the exit, and the provider endpoint ------------------------------------
#
# The relay next door (``tests/socks5_relay.py``) deliberately never proxies
# anything — its whole subject is refusal — so it cannot serve here, where the
# launch must actually complete. This one relays, and only to loopback.

#: Deliberately NEITHER the exit's coordinates NOR any host value, so a
#: position that arrives is unambiguously THIS endpoint's and cannot be
#: confused with a leak or with the declared exit.
SENTINEL_LAT = 11.111111
SENTINEL_LON = 22.222222

#: The exit geography, written the way ``ProxyStore.mark_checked`` writes a
#: real check. Berlin, so ``lat``/``lon`` are present and usable — the case in
#: which Chromium's builder pins coordinates rather than denying.
EXIT = {"cc": "DE", "name": "Germany", "tz": "Europe/Berlin",
        "ip": "203.0.113.7", "lat": 52.52, "lon": 13.405}


class _Socks5(threading.Thread):
    """A minimal SOCKS5 CONNECT relay on loopback.

    Not a foreign exit — but a REAL proxy the engine genuinely tunnels through,
    which is what ``spawn_browser``'s fail-closed guard requires before it will
    launch a profile that HAS a proxy assigned. The exit GEOGRAPHY is what the
    proxy store declares; this supplies only the tunnel.
    """

    daemon = True

    def __init__(self):
        super().__init__()
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(64)
        self.port = self._srv.getsockname()[1]
        self._stop = False

    @property
    def url(self) -> str:
        return f"socks5://127.0.0.1:{self.port}"

    def run(self):
        while not self._stop:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,),
                             daemon=True).start()

    def _serve(self, c):
        import select
        import struct

        up = None
        try:
            hdr = c.recv(2)
            if len(hdr) < 2 or hdr[0] != 5:
                return
            c.recv(hdr[1])
            c.sendall(b"\x05\x00")
            req = c.recv(4)
            if len(req) < 4 or req[1] != 1:      # CONNECT only
                c.sendall(b"\x05\x07\x00\x01" + b"\x00" * 6)
                return
            atyp = req[3]
            if atyp == 1:
                host = socket.inet_ntoa(c.recv(4))
            elif atyp == 3:
                host = c.recv(c.recv(1)[0]).decode()
            elif atyp == 4:
                host = socket.inet_ntop(socket.AF_INET6, c.recv(16))
            else:
                c.sendall(b"\x05\x08\x00\x01" + b"\x00" * 6)
                return
            port = struct.unpack("!H", c.recv(2))[0]
            try:
                up = socket.create_connection((host, port), timeout=20)
            except OSError:
                c.sendall(b"\x05\x05\x00\x01" + b"\x00" * 6)
                return
            c.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
            socks = [c, up]
            while not self._stop:
                r, _, x = select.select(socks, [], socks, 60)
                if x or not r:
                    return
                for s in r:
                    other = up if s is c else c
                    data = s.recv(65536)
                    if not data:
                        return
                    other.sendall(data)
        except OSError:
            pass
        finally:
            for s in (c, up):
                try:
                    if s:
                        s.close()
                except OSError:
                    pass

    def stop(self):
        self._stop = True
        try:
            self._srv.close()
        except OSError:
            pass


class _GeoProvider(threading.Thread):
    """A network geolocation provider that ANSWERS, for the positive control.

    Records its hit count, so "no position arrived" can be told apart from "the
    provider was never asked" — two different facts that look identical from
    the page.
    """

    daemon = True

    def __init__(self):
        super().__init__()
        self.hits = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def _reply(self):
                outer.hits += 1
                body = json.dumps(
                    {"location": {"lat": SENTINEL_LAT, "lng": SENTINEL_LON},
                     "accuracy": 33.0}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _reply

            def log_message(self, *a):
                pass

        self._srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self._srv.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1/geolocate"

    def run(self):
        self._srv.serve_forever()

    def stop(self):
        try:
            self._srv.shutdown()
        except Exception:
            pass


class _Origin(threading.Thread):
    """A loopback page for the reading to happen on.

    ``http://127.0.0.1`` is a potentially-trustworthy origin, so it is a SECURE
    CONTEXT — which geolocation requires. Asserted rather than assumed by
    ``test_the_reading_happens_in_a_secure_context``, because a reading taken
    in a NON-secure context would be measuring the context and reporting it as
    the engine's geo posture. Loopback also keeps the suite off the network:
    a public origin would make someone else's uptime a dependency of an
    assertion about geolocation.
    """

    daemon = True

    def __init__(self):
        super().__init__()

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<!doctype html><title>ps312</title><p>geo probe"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self._srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self._srv.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def run(self):
        self._srv.serve_forever()

    def stop(self):
        try:
            self._srv.shutdown()
        except Exception:
            pass


# --- the probe: assert on the VALUE THE CALLBACK RECEIVES --------------------

#: Resolves on whichever callback fires, and times out EXPLICITLY so a hang is
#: a recorded outcome ({"outcome": "timeout"}) rather than a missing one. The
#: inner ``timeout`` option is shorter than the outer guard so the engine's own
#: timeout error, if it fires one, is reported as an ERROR rather than being
#: swallowed by ours.
_GEO_PROBE = """(() => new Promise(resolve => {
    const t0 = Date.now(); let done = false;
    const fin = o => { if (done) return; done = true;
                       o.elapsed_ms = Date.now() - t0; resolve(o); };
    const timer = setTimeout(() => fin({outcome: 'timeout'}), %d);
    try {
        navigator.geolocation.getCurrentPosition(
            pos => { clearTimeout(timer); fin({outcome: 'position',
                lat: pos.coords.latitude, lon: pos.coords.longitude,
                accuracy: pos.coords.accuracy}); },
            err => { clearTimeout(timer); fin({outcome: 'error',
                code: err.code, message: err.message}); },
            {enableHighAccuracy: false, timeout: %d, maximumAge: 0});
    } catch (e) { clearTimeout(timer);
                  fin({outcome: 'throw', message: String(e)}); }
}))()"""

#: How long the page is given to answer. Generous: an outcome that takes longer
#: than this is reported as a TIMEOUT, which is a real reading, so the only
#: cost of being generous is wall-clock.
_WAIT_MS = 30000



def _read_geolocation_from_a_running_page(
    tmp_path, *, proxied: bool, granted: bool, provider_url=None
) -> dict:
    """Launch a REAL profile through ``spawn_browser`` and ask the PAGE.

    ``in_process=True`` is required and is not a preference: the eval hook is
    published in a per-process dict (``register_ff_eval``), and Linux launches
    FORK by default, so a forked session registers its hook where this process
    can never see it. This is the same route ``verify/baseline.py`` uses.

    ``granted`` and ``provider_url`` are TEST-SIDE pref overlays, monkeypatched
    over ``_profile_prefs`` for the duration of one launch and restored after.
    The product ships neither, and must not — see
    ``test_the_engines_two_settled_geo_decisions_are_not_reversed``.

    Returns the whole reading (env + permission state + the geo outcome) so one
    launch answers several questions about ONE moment in ONE browser, rather
    than several launches that could each have gone differently.
    """
    # PERSONA_HOME is read at CALL time by ``config.default_registry``, so this
    # genuinely redirects DATA_DIR and the proxy/profile stores at a scratch
    # dir. Never point it at a real ~/.persona: these stores are written to.
    os.environ["PERSONA_HOME"] = str(tmp_path)

    from src.services.browser import invisible_launch as il
    from src.services.browser.process import spawn_browser
    from src.services.profile.manager import ProfileManager
    from src.services.proxy.store import ProxyStore

    relay = _Socks5()
    relay.start()
    origin = _Origin()
    origin.start()

    _orig_prefs = il._profile_prefs

    def _overlaid(cfg):
        prefs = dict(_orig_prefs(cfg))
        if granted:
            prefs["permissions.default.geo"] = 1        # 1 == ALLOW
        if provider_url is not None:
            prefs["geo.provider.network.url"] = provider_url
        return prefs

    il._profile_prefs = _overlaid
    proc = None
    name = f"ps312-{'proxied' if proxied else 'direct'}-{int(granted)}"
    try:
        store = ProxyStore()
        if proxied:
            store.add("ps312-exit", relay.url)
            store.mark_checked(
                "ps312-exit", EXIT["cc"], EXIT["name"], ip=EXIT["ip"],
                timezone=EXIT["tz"], lat=EXIT["lat"], lon=EXIT["lon"],
            )

        pm = ProfileManager()
        want = "ps312-exit" if proxied else ""
        # os_type 'windows': coherence refuses every other os_type for firefox,
        # so this is the only coherent pairing rather than a choice.
        pm.add_profile(name, want, "windows", engine="firefox")
        profile = pm.profiles[name]

        # RETRIED, because a first-attempt failure is NOT a finding here. The
        # engine says so itself: "a proxied launch of the patched Firefox
        # wedges INSIDE launch_persistent_context and never returns (live:
        # ~half of fresh proxied launches hang on a half-destroyed
        # initial-window attach)" — which is why `_enter_on_worker` bounds each
        # attempt and retries on a FRESH worker. A harness that took one wedged
        # launch as final would skip on a coin flip and report a host problem
        # that is really a known, already-handled engine behaviour.
        started = False
        for attempt in range(_LAUNCH_ATTEMPTS):
            if attempt:
                # Never relaunch over a still-dying instance — the same
                # settling the engine's own retry loop does.
                try:
                    proc.terminate()
                    proc.wait(timeout=20)
                except Exception:
                    pass
                try:
                    il.unregister_ff_eval(name)
                except Exception:
                    pass
                time.sleep(5)

            proc = spawn_browser(profile, in_process=True)

            # BROWSER_STARTED on a PUMP THREAD: ``readline()`` blocks
            # unboundedly, so a deadline tested only BETWEEN reads never fires
            # against a session that starts and then goes silent.
            lines: "queue.Queue[str]" = queue.Queue()

            def pump(stream=proc.stdout, sink=lines):
                try:
                    for line in iter(stream.readline, ""):
                        if not line:
                            break
                        sink.put(line.rstrip())
                except Exception:
                    pass

            threading.Thread(target=pump, daemon=True).start()
            deadline = time.monotonic() + _START_TIMEOUT_S
            while time.monotonic() < deadline:
                try:
                    line = lines.get(timeout=2)
                except queue.Empty:
                    continue
                if "BROWSER_STARTED" in line:
                    started = True
                    break
            if started:
                break
        if not started:
            pytest.skip(
                f"the firefox session never reported BROWSER_STARTED in "
                f"{_LAUNCH_ATTEMPTS} attempts on this host — an UNOBTAINED "
                f"reading, which must not become evidence in either direction"
            )

        hook = il.get_ff_eval(name)
        if not hook or not callable(hook.get("eval")):
            pytest.skip(
                "the session started but published no eval hook (a known "
                "transient) — an UNOBTAINED reading, not a product verdict"
            )
        raw, goto = hook["eval"], hook["goto"]

        def ev(expr, tries=4):
            """Retry a null. A realm torn down mid-navigation answers None, and
            a None that silently became evidence would be the worst outcome
            available here — so a null is retried and, if it persists, reported
            as a null rather than as a value."""
            for _ in range(tries):
                try:
                    value = raw(expr)
                except Exception:
                    value = None
                if value is not None:
                    return value
                time.sleep(4)
            return None

        # The POSITIVE CONTROL for the channel itself, before anything is read
        # through it: a dead eval channel answers None to every probe below,
        # which would read as "the engine returned nothing".
        if ev("1+1") != 2:
            pytest.skip(
                "the eval channel never answered on this host — every reading "
                "would be UNOBTAINED rather than a product verdict"
            )

        for _ in range(3):
            try:
                goto(origin.url)
                break
            except Exception:
                time.sleep(4)
        time.sleep(2)

        reading = {
            "href": ev("location.href"),
            "secure": ev("window.isSecureContext"),
            "has_geo": ev("'geolocation' in navigator"),
            "on_prototype": ev(
                "Object.prototype.hasOwnProperty.call("
                "Navigator.prototype, 'geolocation')"
            ),
            "native_source": ev(
                "Function.prototype.toString.call("
                "navigator.geolocation.getCurrentPosition)"
            ),
            "timezone": ev("Intl.DateTimeFormat().resolvedOptions().timeZone"),
            "language": ev("navigator.language"),
            "permission": ev(
                "(async()=>{try{return (await navigator.permissions.query("
                "{name:'geolocation'})).state}catch(e){return 'ERR '+e}})()"
            ),
        }
        # tries=1: the probe resolves its OWN promise in every branch, so a
        # retry would only re-run a 30s wait after a genuine answer.
        reading["geo"] = ev(_GEO_PROBE % (_WAIT_MS, _WAIT_MS - 5000), tries=1)
        reading["provider_hits"] = None
        return reading
    finally:
        il._profile_prefs = _orig_prefs
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=30)
            except Exception:
                pass
        try:
            il.unregister_ff_eval(name)
        except Exception:
            pass
        origin.stop()
        relay.stop()


@pytest.fixture(scope="module")
def proxied_granted(tmp_path_factory) -> dict:
    """THE READING. One proxied launch, permission granted, engine's own prefs.

    Module-scoped because it is a real browser start and every assertion below
    is a different question about the SAME reading — which is also what makes
    "the surfaces agree" meaningful: separate launches could each be
    individually right and still not establish that.

    GRANTED, deliberately. Under the shipped ``prompt`` default nobody clicks
    the doorhanger and NEITHER callback ever fires (that is
    ``test_the_shipped_default_never_answers_a_page`` below) — which says
    nothing about whether the engine would leak a position if it were asked.
    The granted branch is the one that answers the leak question, and reaching
    it needs the pref overlay.
    """
    return _read_geolocation_from_a_running_page(
        tmp_path_factory.mktemp("ps312-proxied-granted"),
        proxied=True, granted=True,
    )


# --- the reading -------------------------------------------------------------


def test_the_reading_happens_in_a_secure_context(proxied_granted):
    """The precondition, asserted rather than assumed.

    Geolocation requires a secure context. A refusal measured in a NON-secure
    one would be a fact about the CONTEXT reported as the engine's geo posture,
    which is the way this whole measurement could be quietly wrong.
    """
    assert proxied_granted["secure"] is True, (
        f"the reading was taken at {proxied_granted['href']!r}, which is not a "
        f"secure context — every geolocation outcome below would be measuring "
        f"the context rather than the engine"
    )
    assert proxied_granted["permission"] == "granted", (
        f"the permission state is {proxied_granted['permission']!r}, so the "
        f"GRANTED branch was never reached and the leak question is unanswered"
    )


def test_a_granted_page_receives_NO_COORDINATES(proxied_granted):
    """⭐ THE ASSERTION THIS FILE EXISTS FOR.

    On a proxied, headful, secure-context page whose locale and timezone
    already name the exit country, with geolocation GRANTED, the page's own
    callback receives NO position — and in particular not the host's.

    Asserted on the callback's payload, which is the only place the claim is
    observable. The Chromium-side premise (``geo_ext.py``: getCurrentPosition
    would otherwise "fall through to the REAL host coords") does not hold on
    this engine, and that is why persona ships no Firefox geo spoof.
    """
    geo = proxied_granted["geo"]
    assert geo is not None, (
        "the geolocation probe returned nothing at all — that is an UNOBTAINED "
        "reading (a dead realm), not a measurement of the engine"
    )
    assert geo["outcome"] != "position", (
        f"A GRANTED page RECEIVED A POSITION: {geo}. persona's Firefox arm "
        f"forwards no coordinates, so any position reaching the page came from "
        f"the ENGINE — and if it is the host's, that is an Invariant #0 leak "
        f"on a profile whose locale and timezone say {EXIT['cc']}. Re-measure "
        f"and, if it is real, this cell needs a spoof (see the matrix)."
    )
    assert geo["outcome"] == "error", (
        f"expected the engine's local refusal, got {geo!r}. A 'timeout' here "
        f"would mean the granted branch stopped answering; a 'throw' would "
        f"mean the API is unreachable. Either way the recorded reason in "
        f"process.py's Firefox arm no longer describes this engine — "
        f"re-measure before restating the cell."
    )
    # 2 == POSITION_UNAVAILABLE. Named, because the code is the whole finding:
    # a 1 (PERMISSION_DENIED) would mean the permission never took, and this
    # test would then be re-measuring the 'prompt' default under another name.
    assert geo["code"] == 2, (
        f"expected POSITION_UNAVAILABLE (2), got code {geo['code']} "
        f"({geo.get('message')!r}). A 1 here means the grant did not take and "
        f"the granted branch was never measured."
    )


def test_the_refusal_is_LOCAL_not_a_network_round_trip(proxied_granted):
    """The refusal comes from ``geo.provider.network.url: ""``, not from a
    provider that happened to be unreachable in this container.

    A slow network failure and a local refusal both end in POSITION_UNAVAILABLE
    and are indistinguishable from the code alone; the elapsed time separates
    them. Measured at ~12ms; the bound below is loose by two orders of
    magnitude so this cannot go red on a loaded runner while still refusing
    anything that could have crossed a network.
    """
    assert proxied_granted["geo"]["elapsed_ms"] < 2000, (
        f"the refusal took {proxied_granted['geo']['elapsed_ms']}ms, which is "
        f"long enough to have been a network round trip. The finding is that "
        f"the engine refuses LOCALLY (nothing is reachable through "
        f"geo.provider.network.url: \"\") — a slow refusal means something "
        f"WAS contacted, and where the request went is then the question."
    )


def test_the_geolocation_api_is_PRESENT_and_reads_native(proxied_granted):
    """The other half of the posture, and the reason a spoof would cost more
    than it buys.

    ``geo.enabled: True`` is deliberate — an absent
    ``Navigator.prototype.geolocation`` is itself a tell — and the engine's
    ``getCurrentPosition`` still renders as native code. A JS spoof installed
    in front of this replaces a clean native surface with an overridden one, to
    close a hole the test above shows is not open.
    """
    assert proxied_granted["has_geo"] is True, (
        "navigator.geolocation is ABSENT, which is itself a fingerprinting "
        "tell and reverses the engine's deliberate geo.enabled: True"
    )
    assert proxied_granted["on_prototype"] is True
    assert "[native code]" in (proxied_granted["native_source"] or ""), (
        f"getCurrentPosition no longer renders as native code "
        f"({proxied_granted['native_source']!r}) — something is patching it, "
        f"which is exactly the visible-override cost the recorded decision "
        f"declines to pay"
    )


def test_the_exit_geography_really_reached_this_launch(proxied_granted):
    """The reading is only interesting on a profile that DOES declare an exit.

    The Chromium premise is about a page seeing host coordinates while locale
    and timezone say the exit country — so a launch where those two never
    arrived would not be measuring that situation at all.
    """
    assert proxied_granted["timezone"] == EXIT["tz"], (
        f"the page reports timezone {proxied_granted['timezone']!r}, not the "
        f"exit's {EXIT['tz']!r} — this launch is not the configuration whose "
        f"geo posture is under test"
    )
    assert (proxied_granted["language"] or "").lower().startswith("de"), (
        f"the page reports language {proxied_granted['language']!r}, which "
        f"does not name the exit country {EXIT['cc']}"
    )


# --- the POSITIVE CONTROL, and the A/B that names the cause ------------------


def test_a_reachable_provider_DOES_reach_the_page(tmp_path):
    """⭐ NOT OPTIONAL. Proves the null above is a real null, not a null
    instrument — and isolates its cause in the same launch.

    Identical to the reading fixture in every respect but ONE pref:
    ``geo.provider.network.url`` points at a local endpoint serving sentinel
    coordinates. A position arrives. So this channel, this page and this JS
    CAN observe a position, and the absence of one in
    ``test_a_granted_page_receives_NO_COORDINATES`` is a fact about the engine
    rather than about the probe.

    The sentinel is deliberately neither the exit's coordinates nor any host
    value, so a position that arrives cannot be confused with a leak.
    """
    provider = _GeoProvider()
    provider.start()
    try:
        reading = _read_geolocation_from_a_running_page(
            tmp_path, proxied=True, granted=True, provider_url=provider.url
        )
    finally:
        hits = provider.hits
        provider.stop()

    geo = reading["geo"]
    assert geo is not None and geo["outcome"] == "position", (
        f"THE POSITIVE CONTROL FAILED: with a reachable provider the page "
        f"still received {geo!r} (provider saw {hits} request(s)). Until this "
        f"passes, the 'no coordinates' reading next door is UNINTERPRETABLE — "
        f"it cannot be told apart from a broken probe. Fix the control before "
        f"trusting any null in this file."
    )
    assert (round(geo["lat"], 4), round(geo["lon"], 4)) == (
        round(SENTINEL_LAT, 4), round(SENTINEL_LON, 4)
    ), (
        f"a position arrived but carries {geo['lat']},{geo['lon']} rather than "
        f"the sentinel {SENTINEL_LAT},{SENTINEL_LON} — it did not come from "
        f"the control endpoint, so it establishes nothing about the channel"
    )
    assert hits > 0, (
        "the sentinel arrived but the provider logged no request, which cannot "
        "both be true — the control is not measuring what it claims"
    )


def test_the_shipped_default_never_answers_a_page(tmp_path):
    """The DEFAULT posture, recorded because it is what a real user gets.

    ``permissions.default.geo`` is deliberately unset, so a page's first call
    raises the doorhanger and — with nobody to click it — NEITHER callback ever
    fires. That is not a refusal the engine issued; it is a question nobody
    answered, which is why the granted branch above had to be measured
    separately. Recorded here so a future reader does not mistake this hang for
    the engine's answer.
    """
    reading = _read_geolocation_from_a_running_page(
        tmp_path, proxied=True, granted=False
    )
    assert reading["permission"] == "prompt", (
        f"the shipped default is no longer 'prompt' but "
        f"{reading['permission']!r} — if the product started setting "
        f"permissions.default.geo, that reverses a measured anti-tell"
    )
    geo = reading["geo"]
    assert geo is not None and geo["outcome"] == "timeout", (
        f"the default-posture page received {geo!r} rather than hanging on the "
        f"doorhanger. A 'position' here is the more serious reading: it would "
        f"mean a page gets coordinates WITHOUT ANY GRANT."
    )


# --- AC5: a DIRECT profile is unchanged --------------------------------------


def test_a_DIRECT_profile_is_not_treated_differently(tmp_path):
    """The required negative assertion.

    Chromium's builder is ``if proxy:``-gated: a proxy-less profile's
    geolocation is left untouched. The Firefox arm must match, and it does —
    for the stronger reason that it does nothing to EITHER. Measured on the
    page rather than argued: a proxy-less launch, granted, receives the same
    local POSITION_UNAVAILABLE as the proxied one, so no geolocation behaviour
    on this arm is conditional on the proxy.

    The coherence check beside it is not decoration: a DIRECT profile must read
    as one coherent US-English identity (``process.py`` forces en-US and pins a
    US zone so the host location stays hidden), and a test that launched a
    direct profile without confirming that would not know which configuration
    it had measured.
    """
    reading = _read_geolocation_from_a_running_page(
        tmp_path, proxied=False, granted=True
    )
    geo = reading["geo"]
    assert geo is not None and geo["outcome"] == "error" and geo["code"] == 2, (
        f"a DIRECT (proxy-less) profile received {geo!r}, which differs from "
        f"the proxied reading. Any proxy-conditional geolocation behaviour on "
        f"the Firefox arm is new and must be stated in the matrix."
    )
    assert geo["elapsed_ms"] < 2000
    assert reading["language"] == "en-US", (
        f"a DIRECT profile reports language {reading['language']!r} — persona "
        f"forces en-US so the host locale never leaks"
    )
    assert (reading["timezone"] or "").startswith("America/"), (
        f"a DIRECT profile reports timezone {reading['timezone']!r}, which "
        f"must be a US zone AGREEING with the forced en-US rather than the "
        f"host's"
    )


# --- AC3: the two settled engine decisions are not reversed ------------------


def test_the_engines_two_settled_geo_decisions_are_not_reversed():
    """Both are measured anti-tells and this ticket must not touch either.

    Needs no browser — it reads the prefs the PRODUCT ships — but it lives here
    beside the measurement that depends on them, rather than in the matrix
    suite, because the two are one argument: the engine refuses locally BECAUSE
    of these, and reversing either changes what every reading above means.

    ``permissions.default.geo`` must be ABSENT rather than set to a denying
    value: a ``denied`` where stock Firefox says ``prompt`` was measured as the
    only divergence across 21 permission names.
    """
    from src.services.browser import invisible_launch as il

    prefs = il._profile_prefs(
        {"profile_dir": "/tmp/ps312-nonexistent",
         "profile_data_dir": "/tmp/ps312-nonexistent",
         "profile_name": "ps312-prefs", "seed": 1,
         "locale": "de-DE", "timezone": EXIT["tz"], "proxy_url": "socks5://x"}
    )
    assert "permissions.default.geo" not in prefs, (
        "persona now sets permissions.default.geo "
        f"({prefs.get('permissions.default.geo')!r}). The engine leaves it "
        "UNSET deliberately — a 'denied' where stock says 'prompt' is a "
        "divergence a detector can read. AC3 forbids reversing this."
    )
    assert prefs.get("geo.enabled") in (None, True), (
        f"persona now sets geo.enabled={prefs.get('geo.enabled')!r}. It must "
        f"stay True (the engine's own default): an absent "
        f"Navigator.prototype.geolocation is itself a tell. AC3 forbids "
        f"reversing this."
    )
