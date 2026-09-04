"""`check_proxy` must survive ONE geo provider refusing to answer.

The defect: the operator-facing checker asked a SINGLE provider on both
transport branches, so a 429 from `ipwho.is` — a rate limit that attaches to
the EXIT's address, which on a shared mobile exit a co-tenant burns, so it is
not the operator's to clear — returned ok=False. `src/ui/app.py` then calls
`ProxyStore.mark_check_failed`, `freshness.proxy_indicator_state` reads
"failed" at any age ("a failure does not age into something softer"), and
`launch_policy` raises `GeographyDisprovenError`. A HEALTHY proxy becomes
permanently unlaunchable and the message blames the proxy.

This project already fixed exactly this, twice, in the adjacent verify lane
(`services/verify/exit_guard.py::EXIT_OBSERVATION_URLS`,
`services/verify/checkers.py::ENGINE_EXIT_URLS`). These tests pin the same rule
at the operator-facing call site, and pin it as REACHABILITY ONLY rather than
as a vote:

  * a provider that answers WITH A COUNTRY is authoritative and ends the check,
    INCLUDING when the country is wrong — asking a second provider after an
    answer is how a fallback becomes a laundering route (test_ac4);
  * only a non-answer advances, in BOTH its shapes: nothing came back
    (test_ac1) and a 200 carrying no country (test_ac5).

BOTH transport branches are covered separately — the stdlib SOCKS path and the
aiohttp path — because a fix covering one is the shape this project forbids.

The SOCKS tests deliberately avoid TLS (`_ssl_context` patched to None, the
pattern `test_proxy_checker_socks.py:527-600` already ships) so they run in a
container without `cryptography`. The aiohttp tests cannot: an HTTP proxy is
spoken to with CONNECT and the geo endpoint is https://, so the listener has to
terminate TLS. They skip explicitly rather than pretending to have run.
"""
import datetime
import json
import socket
import ssl
import struct
import threading

import pytest

from src.models.proxy import Proxy
from src.services.browser import launch_policy
from src.services.browser.launch_policy import _proxy_timezone
from src.services.proxy.errors import GeographyDisprovenError
from src.services.proxy.freshness import proxy_indicator_state
from src.services.proxy.store import ProxyStore
from src.utils import proxy_checker

from tests.test_proxy_checker_socks import _listener, _recv_exactly

#: The two providers, in the order `check_proxy` asks them. ipwho.is FIRST is
#: the inverse of the verify lane's order and deliberately so: the shipped
#: reader already speaks ipwho's dialect, and the socks fixtures pin that
#: target (`test_proxy_checker_socks.py:260-262`, `_lanes.py:261`, and a TLS
#: cert whose SAN is `ipwho.is`).
_FIRST = "ipwho.is"
_SECOND = "ipinfo.io"

#: An ipwho.is-dialect answer: the code lives in `country_code`, `country` is
#: the human NAME, the zone is NESTED, coords are two floats.
_IPWHO_BODY = {
    "success": True,
    "ip": "203.0.113.7",
    "country": "Poland",
    "country_code": "PL",
    "timezone": {"id": "Europe/Warsaw"},
    "latitude": 52.23,
    "longitude": 21.01,
}

#: An ipinfo.io-dialect answer for the SAME exit. Every field is spelled
#: differently: `country` IS the code, there is no country NAME at all, the
#: zone is flat, and the coords are one comma-joined string. Reading this with
#: ipwho's key layout would yield country == "" (refusing a good exit) — the
#: mirror of the trap `exit_guard.py:618-623` records.
_IPINFO_BODY = {
    "ip": "203.0.113.7",
    "country": "PL",
    "region": "Mazovia",
    "city": "Warsaw",
    "loc": "52.23,21.01",
    "org": "AS5678 Example",
    "timezone": "Europe/Warsaw",
}


def _http(status: int, body: object = None) -> bytes:
    if body is None:
        return (
            f"HTTP/1.1 {status} Too Many Requests\r\n"
            "Content-Length: 0\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
    raw = json.dumps(body).encode()
    return (
        f"HTTP/1.1 {status} OK\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(raw)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode() + raw


# --------------------------------------------------------------------------
# The SOCKS harness. One listener, many tunnels: each provider the loop asks
# opens a NEW connection, and the target host arrives in the SOCKS request, so
# `seen["targets"]` is a direct record of WHICH providers were contacted and in
# what order — which is what AC4's "zero connections" is asserted on.
# --------------------------------------------------------------------------


def _socks_geo_server(srv: socket.socket, replies: dict, seen: dict) -> tuple:
    seen["targets"] = []
    stop = threading.Event()
    srv.settimeout(0.25)

    def serve() -> None:
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            try:
                handle(conn)
            except Exception as exc:                   # surfaced via the asserts
                seen.setdefault("error", repr(exc))
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def handle(conn: socket.socket) -> None:
        conn.settimeout(10)
        greeting = _recv_exactly(conn, 2)
        seen["greeting"] = greeting
        _recv_exactly(conn, greeting[1])               # the offered methods
        conn.sendall(b"\x05\x00")                      # no auth
        _ver, _cmd, _rsv, atyp = _recv_exactly(conn, 4)
        seen["atyp"] = atyp                            # 0x03 -> sent BY NAME
        host = _recv_exactly(conn, _recv_exactly(conn, 1)[0]).decode()
        port = struct.unpack(">H", _recv_exactly(conn, 2))[0]
        seen["targets"].append((host, port))
        conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 4 + b"\x00\x00")
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = conn.recv(4096)
            if not chunk:
                return
            request += chunk
        seen.setdefault("requests", []).append(request)
        conn.sendall(replies[host])

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread, stop


def _check_via_socks(monkeypatch, replies: dict, seen: dict):
    """Run the OPERATOR lane against a loopback SOCKS5 proxy whose tunnels are
    answered per-target from `replies`.

    No TLS: `_ssl_context` -> None keeps the tunnel cleartext so this runs
    without `cryptography`. What is under test is the PROVIDER LOOP, not the
    transport, and the transport already has its own shipped coverage.
    """
    srv, port = _listener()
    thread, stop = _socks_geo_server(srv, replies, seen)
    monkeypatch.setattr(proxy_checker, "_ssl_context", lambda: None)
    try:
        return proxy_checker.check_proxy_detailed_sync(
            f"socks5://127.0.0.1:{port}", timeout=10
        )
    finally:
        stop.set()
        thread.join(20)
        srv.close()


# --------------------------------------------------------------------------
# AC1 / AC2 — SOCKS branch: the first provider 429s, the second answers.
# --------------------------------------------------------------------------


def test_socks_first_provider_429_falls_through_to_the_second(monkeypatch):
    """AC1 on the stdlib SOCKS lane, which is persona's DEFAULT scheme.

    Asserted on the RETURNED GEO, never on a helper having been called: the
    consequence this ticket exists for is what gets stored, and a call-count
    assertion would pass against a loop that fetched and then dropped the
    answer.

    The second provider answers in ipinfo's dialect, so this also pins the
    normalisation: `country` carries the CODE there, the zone is flat, and the
    coords arrive as one `loc` string. `country_name` comes back EMPTY and that
    is accepted rather than mapped — ipinfo has no country-name field, and
    `ui/components/network_page.py:111-113` already guards on it.
    """
    seen: dict = {}
    ok, message, code, country, ip, tz, lat, lon = _check_via_socks(
        monkeypatch,
        {_FIRST: _http(429), _SECOND: _http(200, _IPINFO_BODY)},
        seen,
    )

    assert ok is True, f"{message} / server: {seen.get('error')}"
    assert code == "PL"
    assert tz == "Europe/Warsaw"
    assert (lat, lon) == (52.23, 21.01)   # parsed out of ipinfo's `loc` string
    assert ip == "203.0.113.7"
    assert country == ""                  # ipinfo has no country NAME field

    # BOTH were contacted, in order, and by NAME rather than pre-resolved on
    # the operator's own resolver.
    assert [host for host, _ in seen["targets"]] == [_FIRST, _SECOND]
    assert seen["atyp"] == 0x03
    assert all(port == 443 for _, port in seen["targets"])   # HTTPS-only

    # The endpoint is still never named in an operator-visible string.
    assert "127.0.0.1" not in message and "203.0.113.7" not in message


def test_socks_first_provider_429_alone_is_still_a_failure(monkeypatch):
    """The premise, inverted in-suite (AC2's shape, kept as a live assertion
    rather than a transcript): with only ONE provider reachable, a 429 fails
    closed exactly as before. The fallback is what turns the test above green,
    not a weakened status check.

    `urls` is not a parameter here — the single-provider case is expressed by
    the SECOND provider also refusing, which is the real-world shape anyway.
    """
    seen: dict = {}
    ok, message, code, country, ip, tz, lat, lon = _check_via_socks(
        monkeypatch, {_FIRST: _http(429), _SECOND: _http(429)}, seen
    )

    assert ok is False
    assert message == "Proxy returned status 429"
    assert (code, country, ip, tz) == ("", "", "", "")
    assert (lat, lon) == (None, None)
    assert [host for host, _ in seen["targets"]] == [_FIRST, _SECOND]


# --------------------------------------------------------------------------
# AC4 — an ANSWER ends the check, even a wrong-country one.
# --------------------------------------------------------------------------


def test_socks_a_wrong_country_answer_never_reaches_the_second_provider(
    monkeypatch,
):
    """The rule that stops this becoming a laundering route.

    `exit_guard.py:112-117`: a provider that answers is AUTHORITATIVE — asking
    a second one after a wrong-country answer is shopping for a friendlier
    oracle. Here the first provider answers `DE` while the second would say
    `PL`; the check must end on `DE` and the second must record ZERO
    connections.

    Note the second provider is scripted to answer, and answer DIFFERENTLY: a
    listener that would refuse anyway could not tell "never asked" from
    "asked and ignored".
    """
    seen: dict = {}
    ok, message, code, country, ip, tz, lat, lon = _check_via_socks(
        monkeypatch,
        {
            _FIRST: _http(200, dict(_IPWHO_BODY, country_code="DE",
                                    country="Germany",
                                    timezone={"id": "Europe/Berlin"})),
            _SECOND: _http(200, _IPINFO_BODY),
        },
        seen,
    )

    assert ok is True, f"{message} / server: {seen.get('error')}"
    assert code == "DE"                       # the ANSWER stands, wrong or not
    assert tz == "Europe/Berlin"
    assert country == "Germany"
    # THE assertion: the second provider was never contacted at all.
    contacted = [host for host, _ in seen["targets"]]
    assert contacted == [_FIRST]
    assert _SECOND not in contacted


def test_socks_a_lying_answer_still_ends_the_check_and_is_sanitised(monkeypatch):
    """The boundary between ANSWERING and being BELIEVED.

    A bogus code ("XYZ") is still an ANSWER — the loop must stop — and
    `_validate_geo` must still drop it, which is the shipped behaviour
    `test_socks5_geo_is_dropped_when_the_endpoint_lies` pins. This is why the
    country test is taken on the RAW payload BEFORE validation: testing it
    after would turn every lie into a reason to go and ask the next provider,
    i.e. exactly the shopping the rule above forbids.
    """
    seen: dict = {}
    ok, message, code, _country, _ip, tz, _lat, _lon = _check_via_socks(
        monkeypatch,
        {
            _FIRST: _http(200, dict(_IPWHO_BODY, country_code="XYZ",
                                    timezone="not-a-zone")),
            _SECOND: _http(200, _IPINFO_BODY),
        },
        seen,
    )

    assert ok is True, f"{message} / server: {seen.get('error')}"
    assert code == ""                          # 3 letters -> dropped
    assert tz == ""                            # no "/" -> dropped
    assert [host for host, _ in seen["targets"]] == [_FIRST]


# --------------------------------------------------------------------------
# AC5 — the SECOND shape of "no verdict": a 200 that carries no country.
# --------------------------------------------------------------------------


def test_socks_a_200_carrying_no_country_advances(monkeypatch):
    """`exit_guard.py:709-721` flags this as the easy one to miss: the reader
    coerces a missing country to "", so without this branch a partial body
    would reach the country comparison as empty and REFUSE a healthy exit while
    reporting it as unknown — actively misleading rather than merely unhelpful.
    """
    seen: dict = {}
    ok, message, code, _country, ip, tz, _lat, _lon = _check_via_socks(
        monkeypatch,
        {
            # 200, well-formed JSON object, an address and nothing else.
            _FIRST: _http(200, {"ip": "203.0.113.7"}),
            _SECOND: _http(200, _IPINFO_BODY),
        },
        seen,
    )

    assert ok is True, f"{message} / server: {seen.get('error')}"
    assert code == "PL"
    assert tz == "Europe/Warsaw"
    assert ip == "203.0.113.7"
    assert [host for host, _ in seen["targets"]] == [_FIRST, _SECOND]


def test_socks_a_name_without_a_code_advances_instead_of_stopping_on_POLAND(
    monkeypatch,
):
    """The `exit_guard.py:618-623` trap, reached from the OTHER direction.

    A degraded ipwho body — `country: "Poland"` with `country_code` MISSING,
    which is the shape a rate-limited provider tends to emit — must not be read
    as the ipinfo dialect. If the dialect swap were gated only on the key being
    absent, `country` would become the CODE and yield `"POLAND"`: truthy, so
    the loop STOPS on it and the second provider is never asked, and
    `_validate_geo` then drops the 6-letter value to `""`. The operator gets
    ok=True with no country from a provider that told us nothing.

    A name with no code is NO COUNTRY, so it advances — and the second
    provider's real answer is what comes back.
    """
    seen: dict = {}
    ok, message, code, _country, _ip, tz, _lat, _lon = _check_via_socks(
        monkeypatch,
        {
            _FIRST: _http(200, {"success": True, "ip": "203.0.113.7",
                                "country": "Poland"}),
            _SECOND: _http(200, _IPINFO_BODY),
        },
        seen,
    )

    assert ok is True, f"{message} / server: {seen.get('error')}"
    assert code == "PL"                        # from the SECOND provider
    assert code != "POLAND" and code != ""     # neither the trap nor its debris
    assert tz == "Europe/Warsaw"
    assert [host for host, _ in seen["targets"]] == [_FIRST, _SECOND]


def test_socks_the_providers_own_success_false_advances(monkeypatch):
    """ipwho.is reports its OWN rate limiting with a 200 and `success: false`.

    Before, that arm returned "Proxy geo lookup failed" outright — the same
    unlaunchable proxy by a different door. It is a provider saying it did not
    answer, so it advances like any other non-answer.
    """
    seen: dict = {}
    ok, message, code, _country, _ip, tz, _lat, _lon = _check_via_socks(
        monkeypatch,
        {
            _FIRST: _http(200, {"success": False, "message": "rate limited"}),
            _SECOND: _http(200, _IPINFO_BODY),
        },
        seen,
    )

    assert ok is True, f"{message} / server: {seen.get('error')}"
    assert code == "PL"
    assert tz == "Europe/Warsaw"
    assert [host for host, _ in seen["targets"]] == [_FIRST, _SECOND]


def test_socks_a_json_array_body_still_reports_the_specific_failure(monkeypatch):
    """The narrowing in `_geo_via_socks` must survive the loop.

    A 200 whose body is a top-level ARRAY reaches the documented
    "Proxy geo lookup failed" arm rather than tripping `.get()` into the
    generic catch-all — and it does NOT advance: a malformed answer from the
    provider we asked is that specific failure, not a reachability problem.
    Companion to `test_socks_geo_body_that_is_a_json_array_reports_the_specific_failure`,
    which pins the same contract on the pre-fallback path.
    """
    seen: dict = {}
    ok, message = _check_via_socks(
        monkeypatch,
        {
            _FIRST: _http(200, [{"success": True, "country_code": "PL"}]),
            _SECOND: _http(200, _IPINFO_BODY),
        },
        seen,
    )[:2]

    assert ok is False
    assert message == "Proxy geo lookup failed"
    assert message != "Proxy check failed"
    assert [host for host, _ in seen["targets"]] == [_FIRST]


# --------------------------------------------------------------------------
# AC3 — the CONSEQUENCE, end to end: the proxy stops being unlaunchable.
# --------------------------------------------------------------------------


def test_the_429_no_longer_makes_a_healthy_proxy_unlaunchable(
    tmp_path, monkeypatch
):
    """The whole point of the ticket, asserted past the fetch.

    Before: ipwho.is 429s -> ok=False -> app.py calls `mark_check_failed` ->
    `proxy_indicator_state` reads "failed" at any age -> `_proxy_timezone`
    raises `GeographyDisprovenError` telling the operator their WORKING proxy's
    geography is disproven. After: the second provider answers, the record goes
    through `mark_checked`, and the profile declares the EXIT's zone.

    The host zone is patched to a distinctive value the assertion would catch
    if the fallback were being reached — patched on `launch_policy`, where
    `_proxy_timezone` resolves `_host_timezone` in its OWN namespace (a patch
    on the `process` re-export is silently bypassed;
    `test_proxy_checker_lanes.py:296-298` records this).
    """
    seen: dict = {}
    ok, message, code, country, ip, tz, lat, lon = _check_via_socks(
        monkeypatch,
        {_FIRST: _http(429), _SECOND: _http(200, _IPINFO_BODY)},
        seen,
    )
    assert ok is True, f"{message} / server: {seen.get('error')}"

    store = ProxyStore(path=str(tmp_path / "proxies.json"))
    store.proxies["exit"] = Proxy(name="exit", url="socks5://gate.example.com:1080")
    assert store.mark_checked("exit", code, country, ip, tz, lat, lon) is True

    proxy = store.proxies["exit"]
    assert proxy.country_code == "PL"
    assert proxy.timezone == "Europe/Warsaw"
    assert proxy.last_check_ok is True

    import time

    state = proxy_indicator_state(proxy, time.time())
    assert state != "failed", "a 429 from one provider still condemns the proxy"
    assert state == "verified"

    monkeypatch.setattr(launch_policy, "_host_timezone", lambda: "America/Chicago")
    assert _proxy_timezone(proxy) == "Europe/Warsaw"


def test_the_baseline_the_test_above_is_measured_against(tmp_path, monkeypatch):
    """Kept explicit so the pair reads as a real before/after rather than an
    assertion that could pass vacuously: when NO provider answers, the check
    fails, `mark_check_failed` runs, and the launch is refused with the very
    error the ticket is about. The refusal itself is CORRECT and out of scope —
    the defect was that it fired on a FALSE disproof.
    """
    seen: dict = {}
    ok = _check_via_socks(
        monkeypatch, {_FIRST: _http(429), _SECOND: _http(429)}, seen
    )[0]
    assert ok is False

    store = ProxyStore(path=str(tmp_path / "proxies.json"))
    store.proxies["exit"] = Proxy(name="exit", url="socks5://gate.example.com:1080")
    # Geography from an EARLIER good check is on file; the failed check is what
    # disproves it. (mark_check_failed preserves the geo fields.)
    store.mark_checked("exit", "PL", "Poland", "203.0.113.7", "Europe/Warsaw",
                       52.23, 21.01)
    assert store.mark_check_failed("exit") is True

    proxy = store.proxies["exit"]
    import time

    assert proxy_indicator_state(proxy, time.time()) == "failed"
    monkeypatch.setattr(launch_policy, "_host_timezone", lambda: "America/Chicago")
    with pytest.raises(GeographyDisprovenError):
        _proxy_timezone(proxy)


# --------------------------------------------------------------------------
# THE EXHAUSTED CASE — nobody answered with a COUNTRY, but a body carried a
# usable TIMEZONE.
#
# This is the one place the port deliberately diverges from `exit_guard`, and
# the divergence is the ticket's own failure mode reappearing through a door
# the fallback opened. `exit_guard` is a MEASUREMENT gate: running out of
# providers costs it a measurement run. This is the OPERATOR lane, where the
# same refusal walks the ticket's chain — mark_check_failed -> "failed" at any
# age -> GeographyDisprovenError — and makes a HEALTHY proxy unlaunchable.
#
# The premise is the ticket's own, not an exotic hypothetical: the rate limit
# attaches to the EXIT's SHARED address ("not ours to clear"), and that
# reasoning does not stop at one provider — the same exit can be limited at
# both, and a rate-limited provider's degraded body is exactly this
# name-without-a-code / no-country-key shape.
#
# `launch_policy._proxy_timezone` reads `proxy.timezone` FIRST and derives from
# `country_code` only when there is no zone, so refusing here would throw away
# the very field the consumer wanted and then condemn the proxy for having no
# geography. The single-provider version returned these bodies as ok=True with
# their zone; this keeps that.
#
# ⚠️ Since PS-240 the LOCALE half refuses such a record at launch
# (`ExitCountryUnknownError` — there is no country to derive a locale from, and
# inventing `en-US` beside the recorded zone is the contradiction that ticket
# removed). What this file asserts is unchanged and still right: the CHECK must
# keep the zone and must not condemn a healthy exit. The proxy stays trusted and
# one re-check away from launching, rather than being reported as failed.
# --------------------------------------------------------------------------

#: The degraded shape a rate-limited ipwho.is emits: a country NAME, no code,
#: but a perfectly usable zone. Read as NO COUNTRY (that is what stops the
#: "POLAND" trap), so it advances — and if nothing better arrives it is the
#: best thing the check saw.
_DEGRADED_NAME_ONLY = {
    "success": True,
    "ip": "203.0.113.7",
    "country": "Poland",                       # a NAME, and no code at all
    "timezone": {"id": "Europe/Warsaw"},
    "latitude": 52.23,
    "longitude": 21.01,
}

#: The other degraded shape: no `country` key whatsoever, still zoned.
_DEGRADED_NO_COUNTRY_KEY = {
    "success": True,
    "ip": "203.0.113.7",
    "timezone": {"id": "Europe/Warsaw"},
}


@pytest.mark.parametrize(
    "degraded", [_DEGRADED_NAME_ONLY, _DEGRADED_NO_COUNTRY_KEY],
    ids=["name-without-a-code", "no-country-key"],
)
def test_socks_a_zone_survives_when_no_provider_answers_with_a_country(
    tmp_path, monkeypatch, degraded
):
    """A usable ZONE with an unusable COUNTRY must not condemn the proxy.

    Asserted past the fetch, exactly as AC3 is: the returned record goes
    through `mark_checked`, the indicator must not read "failed", and the
    profile must declare the EXIT's zone rather than raising
    `GeographyDisprovenError`. Fetch-only assertions would miss the whole
    point — the defect is what the refusal DOES downstream, not the refusal.

    The host zone is patched to a distinctive value on `launch_policy` (its
    own namespace; a patch on the `process` re-export is silently bypassed),
    so a fallback to the real host zone would fail this loudly.
    """
    seen: dict = {}
    ok, message, code, country, ip, tz, lat, lon = _check_via_socks(
        monkeypatch,
        {_FIRST: _http(200, degraded), _SECOND: _http(429)},
        seen,
    )

    assert ok is True, f"{message} / server: {seen.get('error')}"
    assert tz == "Europe/Warsaw"
    # No provider answered with a country, so there legitimately is none. The
    # zone is what makes the record usable, and it is what the launch path
    # reads first.
    assert code == ""
    # And the partial did NOT short-circuit the order: the second provider was
    # still asked, because a partial is not an answer.
    assert [host for host, _ in seen["targets"]] == [_FIRST, _SECOND]

    store = ProxyStore(path=str(tmp_path / "proxies.json"))
    store.proxies["exit"] = Proxy(name="exit", url="socks5://gate.example.com:1080")
    assert store.mark_checked("exit", code, country, ip, tz, lat, lon) is True

    proxy = store.proxies["exit"]
    assert proxy.timezone == "Europe/Warsaw"
    assert proxy.last_check_ok is True

    import time

    state = proxy_indicator_state(proxy, time.time())
    assert state != "failed", (
        "a body with a usable zone but no usable country still condemns the proxy"
    )

    monkeypatch.setattr(launch_policy, "_host_timezone", lambda: "America/Chicago")
    assert _proxy_timezone(proxy) == "Europe/Warsaw"


def test_socks_a_partial_with_no_zone_is_still_a_failure(monkeypatch):
    """The other half of the pair, so the rule above cannot widen into
    "any 200 passes".

    A partial is remembered only when it carried something the launch path can
    actually use. Both providers degraded AND zoneless leaves nothing to
    preserve, so the check fails and the refusal — which is CORRECT here —
    stands.
    """
    seen: dict = {}
    ok, message, code, country, ip, tz, lat, lon = _check_via_socks(
        monkeypatch,
        {
            _FIRST: _http(200, {"success": True, "ip": "203.0.113.7"}),
            _SECOND: _http(200, {"ip": "203.0.113.7"}),
        },
        seen,
    )

    assert ok is False
    assert message == "Proxy geo lookup failed"
    assert (code, country, ip, tz) == ("", "", "", "")
    assert (lat, lon) == (None, None)
    assert [host for host, _ in seen["targets"]] == [_FIRST, _SECOND]


def test_socks_a_partial_never_pre_empts_a_real_country(monkeypatch):
    """Remembering a partial must not become a way to STOP early.

    The first provider gives a zone with no usable country; the second gives a
    real one. The real answer wins — the partial is only ever the exhausted
    case's value, never a competitor to an answer.
    """
    seen: dict = {}
    ok, message, code, _country, _ip, tz, _lat, _lon = _check_via_socks(
        monkeypatch,
        {
            _FIRST: _http(200, dict(_DEGRADED_NAME_ONLY,
                                    timezone={"id": "Europe/Berlin"})),
            _SECOND: _http(200, _IPINFO_BODY),
        },
        seen,
    )

    assert ok is True, f"{message} / server: {seen.get('error')}"
    assert code == "PL"                        # the ANSWER, not the partial
    assert tz == "Europe/Warsaw"               # ditto — not "Europe/Berlin"
    assert [host for host, _ in seen["targets"]] == [_FIRST, _SECOND]


# --------------------------------------------------------------------------
# AC6 — the aiohttp branch, covered SEPARATELY. A fix covering one transport is
# the shape this project forbids, so these exist even where they cannot run.
# --------------------------------------------------------------------------

_HAS_AIOHTTP = proxy_checker.AIOHTTP_AVAILABLE
try:
    import cryptography  # noqa: F401

    _HAS_CRYPTO = True
except ImportError:                                    # pragma: no cover
    _HAS_CRYPTO = False

#: Unlike the SOCKS lane, TLS cannot be sidestepped here: an http:// proxy is
#: spoken to with CONNECT and the geo endpoints are https://, so the listener
#: must terminate TLS to answer at all. Skipped LOUDLY rather than quietly
#: passing where the deps are absent.
_needs_aiohttp_lane = pytest.mark.skipif(
    not (_HAS_AIOHTTP and _HAS_CRYPTO),
    reason="the aiohttp geo lane needs aiohttp (transport) and cryptography "
           "(the CONNECT listener must terminate TLS for BOTH provider names)",
)


def _multi_san_cert(tmp_path, hosts):
    """ONE cert valid for BOTH provider names.

    `test_proxy_checker_socks.py::_self_signed` mints a single-name cert, and
    this lane needs the SAME listener to present a valid identity for whichever
    provider the loop asks for next — so the SAN list carries both rather than
    the client's verification being weakened.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(h) for h in hosts]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    certfile = tmp_path / "geo-multi-cert.pem"
    keyfile = tmp_path / "geo-multi-key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return certfile, keyfile


def _check_via_aiohttp(tmp_path, monkeypatch, replies: dict, seen: dict):
    """A real forward HTTP proxy: aiohttp sends `CONNECT <provider>:443`, this
    answers 200, terminates TLS as that provider, and replies per-target.

    The CONNECT line is where the provider name arrives, so `seen["targets"]`
    is again a direct record of which providers were contacted — the same
    measurement AC4 needs on this branch.
    """
    import aiohttp.connector

    certfile, keyfile = _multi_san_cert(tmp_path, [_FIRST, _SECOND])
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(certfile), str(keyfile))

    seen["targets"] = []
    srv, port = _listener()
    srv.listen(8)
    srv.settimeout(0.25)
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    def handle(conn: socket.socket) -> None:
        try:
            conn.settimeout(10)
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                request += chunk
            line = request.split(b"\r\n")[0].decode("latin-1")
            seen.setdefault("first_lines", []).append(line)
            host = line.split(" ")[1].split(":")[0]
            seen["targets"].append(host)
            conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            tls = server_ctx.wrap_socket(conn, server_side=True)
            inner = b""
            while b"\r\n\r\n" not in inner:
                chunk = tls.recv(4096)
                if not chunk:
                    break
                inner += chunk
            tls.sendall(replies[host])
            tls.close()
        except Exception as exc:                       # surfaced via the asserts
            seen.setdefault("error", repr(exc))
            try:
                conn.close()
            except OSError:
                pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    # Point aiohttp's verified context at this listener's CA. The DEFAULT
    # context is replaced rather than verification being disabled: the tunnel's
    # certificate is still checked against the provider hostname, which is what
    # makes the HTTPS-only rule mean anything here.
    client_ctx = ssl.create_default_context(cafile=str(certfile))
    monkeypatch.setattr(
        aiohttp.connector, "_SSL_CONTEXT_VERIFIED", client_ctx, raising=True
    )
    try:
        return proxy_checker.check_proxy_detailed_sync(
            f"http://user:pass@127.0.0.1:{port}", timeout=10
        )
    finally:
        stop.set()
        thread.join(20)
        srv.close()


@_needs_aiohttp_lane
def test_aiohttp_first_provider_429_falls_through_to_the_second(
    tmp_path, monkeypatch
):
    """AC1 + AC6 on the aiohttp lane. Same assertion as the SOCKS twin, taken
    on the returned geo — the branch is different code and gets its own test
    rather than being assumed covered by the other."""
    seen: dict = {}
    ok, message, code, country, ip, tz, lat, lon = _check_via_aiohttp(
        tmp_path, monkeypatch,
        {_FIRST: _http(429), _SECOND: _http(200, _IPINFO_BODY)},
        seen,
    )

    assert ok is True, f"{message} / server: {seen.get('error')}"
    assert code == "PL"
    assert tz == "Europe/Warsaw"
    assert (lat, lon) == (52.23, 21.01)
    assert ip == "203.0.113.7"
    assert country == ""
    assert seen["targets"] == [_FIRST, _SECOND]
    # HTTPS on both: the tunnel is CONNECTed to 443 and TLS-verified against
    # the provider's own name, so a MITM on the exit cannot inject a country.
    assert all(":443" in line for line in seen["first_lines"])
    assert "127.0.0.1" not in message and "203.0.113.7" not in message


@_needs_aiohttp_lane
def test_aiohttp_a_wrong_country_answer_never_reaches_the_second_provider(
    tmp_path, monkeypatch
):
    """AC4 on the aiohttp lane: an answer is authoritative here too, so the
    second provider records ZERO connections."""
    seen: dict = {}
    ok, message, code, _country, _ip, tz, _lat, _lon = _check_via_aiohttp(
        tmp_path, monkeypatch,
        {
            _FIRST: _http(200, dict(_IPWHO_BODY, country_code="DE",
                                    country="Germany",
                                    timezone={"id": "Europe/Berlin"})),
            _SECOND: _http(200, _IPINFO_BODY),
        },
        seen,
    )

    assert ok is True, f"{message} / server: {seen.get('error')}"
    assert code == "DE"
    assert tz == "Europe/Berlin"
    assert seen["targets"] == [_FIRST]
    assert _SECOND not in seen["targets"]


@_needs_aiohttp_lane
def test_aiohttp_a_200_carrying_no_country_advances(tmp_path, monkeypatch):
    """AC5 on the aiohttp lane."""
    seen: dict = {}
    ok, message, code, _country, _ip, tz, _lat, _lon = _check_via_aiohttp(
        tmp_path, monkeypatch,
        {
            _FIRST: _http(200, {"ip": "203.0.113.7"}),
            _SECOND: _http(200, _IPINFO_BODY),
        },
        seen,
    )

    assert ok is True, f"{message} / server: {seen.get('error')}"
    assert code == "PL"
    assert tz == "Europe/Warsaw"
    assert seen["targets"] == [_FIRST, _SECOND]


@_needs_aiohttp_lane
def test_aiohttp_all_providers_refusing_is_still_a_failure(tmp_path, monkeypatch):
    """The premise inverted on this lane: the fallback is what turns the test
    above green, not a weakened status check."""
    seen: dict = {}
    ok, message, code, country, ip, tz, lat, lon = _check_via_aiohttp(
        tmp_path, monkeypatch, {_FIRST: _http(429), _SECOND: _http(429)}, seen
    )

    assert ok is False
    assert message == "Proxy returned status 429"
    assert (code, country, ip, tz) == ("", "", "", "")
    assert (lat, lon) == (None, None)
    assert seen["targets"] == [_FIRST, _SECOND]


@_needs_aiohttp_lane
def test_aiohttp_a_zone_survives_when_no_provider_answers_with_a_country(
    tmp_path, monkeypatch
):
    """The exhausted-partial rule on the aiohttp lane — the SOCKS twin's pair.

    Both branches get this from one `_resolve_geo`, but AC6 is not waivable and
    a fix covering one transport is the shape this project forbids, so the
    behaviour is pinned on each.
    """
    seen: dict = {}
    ok, message, code, _country, _ip, tz, _lat, _lon = _check_via_aiohttp(
        tmp_path, monkeypatch,
        {_FIRST: _http(200, _DEGRADED_NAME_ONLY), _SECOND: _http(429)},
        seen,
    )

    assert ok is True, f"{message} / server: {seen.get('error')}"
    assert tz == "Europe/Warsaw"
    assert code == ""
    assert seen["targets"] == [_FIRST, _SECOND]


@_needs_aiohttp_lane
def test_aiohttp_a_partial_with_no_zone_is_still_a_failure(tmp_path, monkeypatch):
    """The other half on this lane: nothing to preserve, so the refusal — which
    is correct — stands."""
    seen: dict = {}
    ok, message, code, country, ip, tz, lat, lon = _check_via_aiohttp(
        tmp_path, monkeypatch,
        {
            _FIRST: _http(200, {"success": True, "ip": "203.0.113.7"}),
            _SECOND: _http(200, {"ip": "203.0.113.7"}),
        },
        seen,
    )

    assert ok is False
    assert message == "Proxy geo lookup failed"
    assert (code, country, ip, tz) == ("", "", "", "")
    assert (lat, lon) == (None, None)
    assert seen["targets"] == [_FIRST, _SECOND]


def test_the_aiohttp_skip_rule_is_missing_deps_and_nothing_else():
    """A skip must mean "the container lacks a dependency", never "this branch
    is untested". Pinned so the marker cannot quietly widen into a way to skip
    the aiohttp lane on a machine that could run it."""
    condition = _needs_aiohttp_lane.args[0]
    assert condition is (not (_HAS_AIOHTTP and _HAS_CRYPTO))
    # And on a container that HAS both, the lane really does run.
    if _HAS_AIOHTTP and _HAS_CRYPTO:
        assert condition is False


# --------------------------------------------------------------------------
# The dialect readers, unit-level. These run everywhere.
# --------------------------------------------------------------------------


def test_normaliser_reads_both_dialects_without_confusing_them():
    """The trap `exit_guard.py:618-623` records, from both sides.

    Keyed on the payload's SHAPE, not on the URL, so changing the order cannot
    silently misread a provider. `country_code` present marks the ipwho dialect
    (where `country` is the human NAME); absent, `country` IS the code.
    """
    ip, code, name, tz, lat, lon = proxy_checker._geo_fields_from_payload(
        _IPWHO_BODY
    )
    assert (code, name, tz) == ("PL", "Poland", "Europe/Warsaw")
    assert (lat, lon) == (52.23, 21.01)
    assert ip == "203.0.113.7"
    # NOT "POLAND": reading ipwho with ipinfo's layout is the failure that
    # refuses a good exit WHILE naming the wrong country.
    assert code != "POLAND"

    ip, code, name, tz, lat, lon = proxy_checker._geo_fields_from_payload(
        _IPINFO_BODY
    )
    assert code == "PL"
    assert name == ""            # ipinfo has no country-name field; not mapped
    assert tz == "Europe/Warsaw"
    assert (lat, lon) == ("52.23", "21.01")   # unparsed; _validate_geo floats it


def test_a_country_NAME_with_no_code_is_read_as_no_country_not_as_a_code():
    """The discriminator is the VALUE's shape, not the key's absence.

    `{"country": "Poland"}` with no `country_code` is a DEGRADED ipwho body,
    not an ipinfo one — and it is the shape a rate-limited provider emits. Read
    as ipinfo it would yield `"POLAND"`, which is truthy enough to end the
    provider loop and is then discarded by `_validate_geo`: the caller stops on
    an answer that says nothing. `len == 2` tells the two dialects apart, and a
    name with no code comes back as NO COUNTRY so the loop advances.
    """
    _ip, code, name, tz, _lat, _lon = proxy_checker._geo_fields_from_payload(
        {"success": True, "ip": "203.0.113.7", "country": "Poland",
         "timezone": {"id": "Europe/Warsaw"}}
    )
    assert code == ""                 # NOT "POLAND"
    assert name == "Poland"           # the name is still reported, unchanged
    assert tz == "Europe/Warsaw"      # and the rest of the body still reads

    # And the two-letter case is unaffected: that IS ipinfo, and it answers.
    assert proxy_checker._geo_fields_from_payload({"country": "pl"})[1] == "PL"


@pytest.mark.parametrize("loc", [None, "", "52.23", 52.23, {"lat": 1}])
def test_a_loc_that_is_not_a_pair_yields_no_coordinates(loc):
    """Nothing that is not a comma-joined string is guessed at."""
    assert proxy_checker._coords_from_loc(loc) == (None, None)


@pytest.mark.parametrize(
    "loc", [None, "", "52.23", "not,coords", 52.23, {"lat": 1}, "999.0,-500.0"]
)
def test_an_unreadable_loc_degrades_to_no_coordinates(loc):
    """`process.py:587-593` builds the geolocation extension in DENY mode when
    coords are absent, and deny mode does NOT fall through to the operator's
    real host coordinates — so failing to parse `loc` degrades safely rather
    than leaking. Pinned because "safe" here is a property of the caller, not
    an accident of this function.

    Asserted PAST `_validate_geo` rather than on the split alone: the parser
    deliberately returns its halves unvalidated (`"not,coords"` comes back as
    two strings) so that ONE sanitiser decides what may be persisted, instead
    of a second dialect reader growing its own range checks.
    """
    lat, lon = proxy_checker._coords_from_loc(loc)
    assert proxy_checker._validate_geo("PL", "Europe/Warsaw", lat, lon)[2:] == (
        None,
        None,
    )


def test_bad_loc_values_are_dropped_by_the_shared_sanitiser():
    """A parsed `loc` is NOT trusted on its own: it is returned unvalidated and
    `_validate_geo` range-checks it, so a hostile provider cannot write
    out-of-range coordinates into the persisted fingerprint."""
    lat, lon = proxy_checker._coords_from_loc("999.0,-500.0")
    assert (lat, lon) == ("999.0", "-500.0")
    assert proxy_checker._validate_geo("PL", "Europe/Warsaw", lat, lon)[2:] == (
        None,
        None,
    )


def test_both_providers_are_https_and_neither_is_address_only():
    """The two standing constraints on the provider list, asserted rather than
    trusted to a comment: HTTPS only (cleartext would let a MITM on the exit
    inject a bogus country into the persisted fingerprint), and no address-only
    endpoint (`exit_guard.py:126-134` — it can never answer the geography
    question, so it would occupy a slot while proving nothing)."""
    import inspect

    source = inspect.getsource(proxy_checker.check_proxy)
    assert "https://ipwho.is/" in source
    assert proxy_checker._GEO_FALLBACK_PROVIDER.startswith("https://")
    assert "http://" not in source.replace("https://", "")
    assert "ipify" not in source and "ipify" not in proxy_checker._GEO_FALLBACK_PROVIDER
