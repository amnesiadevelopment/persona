"""Persona's OWN egress policy: every unattended request persona sends itself.

Two shapes, one authority: the two engine release-metadata polls (urllib, via
`fetch_json`) and `app_update`'s four `curl` sites (argv, via `curl_proxy_args`)
— the latter added by PS-66 and covered at the end of this file.

Why the module exists, which is the urllib arm's history
--------------------------------------------------------
Persona polls GitHub for release metadata twice at every startup, on a timer,
with no operator gesture. Before services/egress.py there was no construct
anywhere in the tree that decided how those requests should leave the host — and
the one scheme this codebase is built around broke QUIETLY: `urllib` honours
`https_proxy`, but handed a `socks5://` value it emits a plain
`CONNECT host:443 HTTP/1.1` at a SOCKS port that is waiting for a `\\x05`
greeting. It never answers. That is the same defect class the geo checker
already fixed once for aiohttp (see test_proxy_checker_socks.py, whose
fake-SOCKS5 harness this file mirrors).

The two assertions that carry this file:

* the DEFAULT is byte-identical to the old behaviour (an unset key must change
  nothing for any existing install — the whole blast radius), and
* once a policy IS set it is honoured for real: a SOCKS transport takes a REAL
  SOCKS handshake, and a transport that cannot be used means the request is NOT
  SENT rather than silently falling back to the operator's real IP.
"""
import asyncio
import json
import socket
import struct
import threading
import time

import pytest

from src.core import settings
from src.services import egress
from src.services.engine import firefox as ff
from src.services.engine import updater
from src.utils import proxy_checker


# --------------------------------------------------------------------------
# Harness — mirrors tests/test_proxy_checker_socks.py's _listener.
# --------------------------------------------------------------------------


def _listener() -> tuple[socket.socket, int]:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    return srv, srv.getsockname()[1]


def _recv_exactly(conn: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("client went away mid-handshake")
        buf += chunk
    return buf


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path, monkeypatch):
    """Every test states its own policy; none may read the developer's real
    settings.json or persist into it."""
    monkeypatch.setattr(settings, "_path", lambda: str(tmp_path / "settings.json"))


class _Resp:
    """The urlopen response shape both call sites consume."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# --------------------------------------------------------------------------
# AC1 — the red-first assertion: with a policy set, the fetch must NOT reach
# urllib.request.urlopen. This is the one that fails on main today.
# --------------------------------------------------------------------------


def test_configured_policy_keeps_fetch_latest_full_off_urlopen(monkeypatch):
    """AC1. With an egress policy configured, the Chromium metadata fetch must
    NOT go out through urllib.request.urlopen — that is the un-policied send
    this ticket exists to route."""
    settings.set_app_egress_proxy("socks5://127.0.0.1:9")

    def forbidden(*a, **k):
        raise AssertionError("fetch_latest_full reached urlopen despite a policy")

    monkeypatch.setattr(updater.urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(
        egress,
        "fetch_json_via_proxy_sync",
        lambda *a, **k: {"tag_name": "148.0.0.1", "assets": []},
    )

    tag, _url, _digest = updater.fetch_latest_full()
    assert tag == "148.0.0.1", "the document must come back through the policy"


def test_configured_policy_keeps_firefox_fetch_latest_off_urlopen(monkeypatch):
    """AC1, the other unattended fetch. Both call sites, or the policy is a
    setting one of them ignores."""
    settings.set_app_egress_proxy("socks5://127.0.0.1:9")

    def forbidden(*a, **k):
        raise AssertionError("fetch_latest reached urlopen despite a policy")

    monkeypatch.setattr(ff.urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(ff, "_expected_asset", lambda: "asset.zip")
    monkeypatch.setattr(
        egress,
        "fetch_json_via_proxy_sync",
        lambda *a, **k: [{"tag_name": "firefox-16", "assets": [{"name": "asset.zip"}]}],
    )

    tag, _compatible = ff.fetch_latest()
    assert tag == "firefox-16", "the document must come back through the policy"


# --------------------------------------------------------------------------
# AC3 — the blast-radius guarantee: an unset key changes NOTHING.
# --------------------------------------------------------------------------


def test_default_is_direct_and_unchanged_for_chromium(monkeypatch):
    """AC3. No key set => exactly the request this code made before the policy
    existed: same URL, same Accept header, and NO User-Agent added."""
    assert settings.app_egress_proxy() == "", "the default must be unset"
    assert egress.resolve() == (egress.DIRECT, "")

    seen = {}

    def capture(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.headers)
        seen["timeout"] = timeout
        return _Resp(b'{"tag_name": "148.0.0.1", "assets": []}')

    monkeypatch.setattr(updater.urllib.request, "urlopen", capture)
    # No proxy transport may be consulted at all on the default path.
    monkeypatch.setattr(
        egress,
        "fetch_json_via_proxy_sync",
        lambda *a, **k: pytest.fail("the default path must not use a proxy"),
    )

    tag, _url, _digest = updater.fetch_latest_full()

    assert tag == "148.0.0.1"
    assert seen["url"] == updater.RELEASES_API
    # urllib title-cases header keys it is given.
    assert seen["headers"].get("Accept") == "application/vnd.github+json"
    assert "User-agent" not in seen["headers"], (
        "the default must not add a User-Agent — that would change what an "
        "unset key does on the wire"
    )


def test_default_is_direct_and_unchanged_for_firefox(monkeypatch):
    """AC3, the other call site."""
    assert settings.app_egress_proxy() == ""

    seen = {}

    def capture(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.headers)
        return _Resp(b'[{"tag_name": "firefox-16", "assets": [{"name": "asset.zip"}]}]')

    monkeypatch.setattr(ff.urllib.request, "urlopen", capture)
    monkeypatch.setattr(ff, "_expected_asset", lambda: "asset.zip")
    monkeypatch.setattr(
        egress,
        "fetch_json_via_proxy_sync",
        lambda *a, **k: pytest.fail("the default path must not use a proxy"),
    )

    tag, _compatible = ff.fetch_latest()

    assert tag == "firefox-16"
    assert seen["url"] == ff.RELEASES_API
    assert seen["headers"].get("Accept") == "application/vnd.github+json"
    assert "User-agent" not in seen["headers"]


# --------------------------------------------------------------------------
# AC4 — fail closed once set: NO socket is opened, and no direct send occurs.
# --------------------------------------------------------------------------


def test_unusable_policy_opens_no_socket_at_all(monkeypatch):
    """AC4. A configured-but-unusable transport means the request is NOT SENT.
    Asserted with a socket.socket spy, because "did not fall back" has to be a
    fact about the network, not about a return value."""
    settings.set_app_egress_proxy("this is not a proxy url")

    opened = []
    real_socket = socket.socket

    def spy(*a, **k):
        opened.append(a)
        return real_socket(*a, **k)

    monkeypatch.setattr(socket, "socket", spy)
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda *a, **k: pytest.fail("fell back to a DIRECT send — the leak"),
    )

    with pytest.raises(egress.EgressRefused):
        egress.fetch_json(updater.RELEASES_API)

    assert opened == [], f"a socket was opened despite the refusal: {opened}"


def test_unusable_policy_makes_the_call_sites_fail_not_leak(monkeypatch):
    """AC4 at the call site. The refusal reaches the caller as a failure — the
    existing ('','','') / ('',False) failure result — never as a direct send."""
    settings.set_app_egress_proxy("this is not a proxy url")

    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda *a, **k: pytest.fail("fell back to a DIRECT send — the leak"),
    )
    monkeypatch.setattr(
        ff.urllib.request,
        "urlopen",
        lambda *a, **k: pytest.fail("fell back to a DIRECT send — the leak"),
    )
    monkeypatch.setattr(ff, "_expected_asset", lambda: "asset.zip")

    assert updater.fetch_latest_full() == ("", "", "")
    assert ff.fetch_latest() == ("", False)


def test_a_failing_proxy_is_never_retried_directly(monkeypatch):
    """AC4's sharp edge: the transport is CONFIGURED and parseable but the
    request through it fails. Falling back to a direct send here would be
    strictly worse than having no policy, because the operator believes they
    are covered."""
    settings.set_app_egress_proxy("socks5://127.0.0.1:9")

    def boom(*a, **k):
        raise OSError("proxy unreachable")

    monkeypatch.setattr(egress, "fetch_json_via_proxy_sync", boom)
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda *a, **k: pytest.fail("retried DIRECTLY after the proxy failed"),
    )

    with pytest.raises(OSError):
        egress.fetch_json(updater.RELEASES_API)


def test_refusal_is_surfaced_to_the_operator(caplog):
    """AC8. A silent skip is indistinguishable from "no new release", so the
    refusal must reach the log — without echoing the transport, which can embed
    credentials."""
    settings.set_app_egress_proxy("this is not a proxy url")

    with caplog.at_level("WARNING", logger="persona"):
        with pytest.raises(egress.EgressRefused):
            egress.fetch_json(updater.RELEASES_API)

    assert any(
        "NOT SENT" in r.getMessage() for r in caplog.records
    ), f"no refusal was logged: {[r.getMessage() for r in caplog.records]}"


# --------------------------------------------------------------------------
# PS-276 — the fail-closed warning must name the CAUSE, not just its class.
#
# The refusal above is only half the operator's notice. The other half is this
# arm: a transport that IS configured and parseable, whose request then fails.
# `fetch_json_via_proxy` raises five distinct failures and only TWO classes
# (three of them are ValueError), so a line carrying `type(e).__name__` alone
# reported "your proxy setting is unparseable", "your proxy demands auth" and
# "the release document grew past the cap" with byte-identical text. These
# polls are unattended — the operator never asked for the request — so this
# line is the only place the reason can appear, and the module docstring
# (:40-44) promises the reason IS logged.
#
# Third site of the class PS-160 (`21a300f`) fixed for exit_guard and bridge.
#
# THE MESSAGES ARE NOT INVENTED BY THESE TESTS. Each one is raised by the real
# `fetch_json_via_proxy` — from a real fake-SOCKS5 server, a real oversized
# body, a real non-200 status — driven through the real shipped
# `egress.fetch_json`, and the assertion is on the EMITTED LOG RECORD's text.
# Asserting "redact was called" would pass on a line that reached no log.
# --------------------------------------------------------------------------


def _socks_replying(raw_response: bytes) -> tuple[socket.socket, int, threading.Thread]:
    """A fake SOCKS5 exit that completes the handshake, absorbs the request and
    answers with `raw_response` verbatim.

    Mirrors the harness the Accept tests above use; the difference is that this
    one is pointed at responses that make `fetch_json_via_proxy` RAISE, which
    is the arm under test.
    """
    srv, port = _listener()

    def serve() -> None:
        conn, _ = srv.accept()
        conn.settimeout(10)
        try:
            greeting = _recv_exactly(conn, 2)
            _recv_exactly(conn, greeting[1])  # the method list
            conn.sendall(b"\x05\x00")  # no auth required
            _recv_exactly(conn, 4)
            _recv_exactly(conn, _recv_exactly(conn, 1)[0])  # host
            _recv_exactly(conn, 2)  # port
            conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 4 + b"\x00\x00")
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                request += chunk
            conn.sendall(raw_response)
        except Exception:  # pragma: no cover - the client's failure is the test
            pass
        finally:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return srv, port, thread


def _ok(body: bytes) -> bytes:
    return (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        b"Connection: close\r\n\r\n" + body
    )


def _proxy_failure_line(caplog) -> str:
    """The one PROXIED-arm warning, as it was actually EMITTED."""
    lines = [
        r.getMessage()
        for r in caplog.records
        if "request through the configured proxy failed" in r.getMessage()
    ]
    assert len(lines) == 1, f"expected exactly one failure line, got {lines}"
    return lines[0]


def _drive_through_socks(caplog, raw_response: bytes) -> str:
    """Run the REAL `egress.fetch_json` through a real SOCKS transport that
    answers `raw_response`, and return the warning it emitted."""
    srv, port, thread = _socks_replying(raw_response)
    settings.set_app_egress_proxy(f"socks5://127.0.0.1:{port}")
    try:
        with caplog.at_level("WARNING", logger="persona"):
            with pytest.raises(Exception):
                egress.fetch_json("http://api.github.com/x", timeout=10)
    finally:
        thread.join(15)
        srv.close()
    return _proxy_failure_line(caplog)


def test_the_proxy_failure_CAUSES_are_distinguishable_on_sight(caplog, monkeypatch):
    """PS-276 AC1 — and the assertion the whole ticket rests on.

    Not "a message is logged" (the old line logged one too) but that the four
    causes an operator acts on DIFFERENTLY produce four DIFFERENT lines. Three
    of these are `ValueError`, so on the old code they were byte-identical and
    this test fails with `4 causes -> 2 distinct lines`.

    Each cause is produced by the real transport, never by a raised stub:

    * `response too large`   — a real body past `_MAX_RELEASE_BODY`
    * `HTTP <status>`        — a real 407, the "your proxy wants auth" case
    * `not a JSON object`    — a real 200 whose body is a bare JSON string
    * `aiohttp not installed`— the real fail-closed guard on the http:// branch
    """
    lines: dict[str, str] = {}

    caplog.clear()
    oversized = b"x" * (proxy_checker._MAX_RELEASE_BODY + 1024)
    lines["too_large"] = _drive_through_socks(caplog, _ok(oversized))

    caplog.clear()
    lines["http_407"] = _drive_through_socks(
        caplog,
        b"HTTP/1.1 407 Proxy Authentication Required\r\n"
        b"Content-Length: 0\r\nConnection: close\r\n\r\n",
    )

    caplog.clear()
    lines["not_json_object"] = _drive_through_socks(caplog, _ok(b'"just a string"'))

    # The http:// branch's fail-closed guard. Patched on the module the code
    # actually reads, so the REAL `raise RuntimeError(...)` executes — the
    # exception is the shipped one, not one this test authored.
    caplog.clear()
    monkeypatch.setattr(proxy_checker, "AIOHTTP_AVAILABLE", False)
    settings.set_app_egress_proxy("http://127.0.0.1:9")
    with caplog.at_level("WARNING", logger="persona"):
        with pytest.raises(RuntimeError):
            egress.fetch_json("http://api.github.com/x", timeout=10)
    lines["no_aiohttp"] = _proxy_failure_line(caplog)

    distinct = set(lines.values())
    assert len(distinct) == len(lines), (
        f"{len(lines)} causes collapsed to {len(distinct)} distinct lines — the "
        f"operator cannot tell them apart:\n"
        + "\n".join(f"  {k}: {v}" for k, v in lines.items())
    )

    # And distinctness is not enough on its own: a line could differ by
    # accident while still not NAMING the cause. Each must carry its own words.
    assert "response too large" in lines["too_large"]
    assert "HTTP 407" in lines["http_407"]
    assert "not a JSON object" in lines["not_json_object"]
    assert "aiohttp not installed" in lines["no_aiohttp"]

    # The class survives too — the message is added, not substituted.
    assert "ValueError" in lines["too_large"]
    assert "RuntimeError" in lines["http_407"]


def test_the_failure_line_never_carries_the_proxy_credential(caplog):
    """PS-276 AC3. The message is UN-AUTHORED exception text landing in the
    DISK-BACKED daily log (`core/logging.py`'s FileHandler, which `ui/state.py`
    then seeds the Activity Log from), and `proxy_checker` builds a `proxy_url`
    embedding `username:password` on its aiohttp branch — so carrying the
    message without `redact` would trade a diagnosis problem for a
    credential-on-disk one.

    ⚠️ THE LEAK IS OBSERVED HERE, NOT ARGUED. The ticket rated it
    reachable-in-principle; this drives it. `parse_proxy` percent-DECODES the
    password, so a stored `%5B` becomes a literal `[`, which `yarl` rejects —
    and aiohttp's `InvalidURL` echoes the WHOLE credentialed URL as its
    message. The secret therefore reaches the message from the library itself,
    exactly as PS-160 drove its own leak through a real `open()` rather than a
    string the test wrote.

    The host must SURVIVE: redacting the whole line would restore the very
    defect this ticket fixes.
    """
    secret = "s3c[r3t"  # what the stored %5B decodes to
    caplog.clear()
    settings.set_app_egress_proxy("http://alice:s3c%5Br3t@127.0.0.1:1080")

    with caplog.at_level("WARNING", logger="persona"):
        with pytest.raises(Exception):
            egress.fetch_json("http://api.github.com/x", timeout=10)

    line = _proxy_failure_line(caplog)
    assert secret not in line, f"the proxy PASSWORD reached the log: {line}"
    assert "alice" not in line, f"the proxy USERNAME reached the log: {line}"
    assert "***:***@" in line, (
        f"nothing was redacted — the credential shape is not being neutralised: {line}"
    )
    # The diagnosis must survive the redaction, or the fix defeats itself.
    assert "127.0.0.1:1080" in line, f"the host was redacted away too: {line}"
    assert "InvalidURL" in line, f"the cause was lost: {line}"


def test_the_single_shared_redaction_rule_is_the_one_used(caplog, monkeypatch):
    """PS-276 AC3's other half: the rule must be `core.redaction.redact`, not a
    second regex grown here. `redaction.py`'s own docstring forbids the copy —
    "a redaction bug fixed in one copy and not the other is worse than no
    redaction, because the second copy still looks guarded".

    Asserted by DEFEATING the shared rule and watching the output change: if
    egress carried its own regex, neutering the shared one would leave the line
    redacted anyway and this test would fail.
    """
    monkeypatch.setattr(egress, "redact", lambda text: text)
    caplog.clear()
    settings.set_app_egress_proxy("http://alice:s3c%5Br3t@127.0.0.1:1080")

    with caplog.at_level("WARNING", logger="persona"):
        with pytest.raises(Exception):
            egress.fetch_json("http://api.github.com/x", timeout=10)

    assert "s3c[r3t" in _proxy_failure_line(caplog), (
        "neutering core.redaction.redact did not change the emitted line, so "
        "this module is not routing through the one shared rule"
    )


def test_carrying_the_message_did_not_weaken_fail_closed(caplog, monkeypatch):
    """PS-276 AC4. The logging change must not have touched the CONTRACT: the
    exception is still re-raised unchanged (identity, not just type), and
    nothing retries directly after the proxy failed.

    `test_a_failing_proxy_is_never_retried_directly` above pins the same
    property from the other side; this one pins it on the arm that now
    interpolates the message, where a `try/except` around the new f-string
    could have swallowed the re-raise.
    """
    settings.set_app_egress_proxy("socks5://127.0.0.1:9")
    original = OSError("proxy unreachable at socks5://alice:hunter2@gate.example:1080")

    def boom(*a, **k):
        raise original

    monkeypatch.setattr(egress, "fetch_json_via_proxy_sync", boom)
    monkeypatch.setattr(
        egress.urllib.request,
        "urlopen",
        lambda *a, **k: pytest.fail("retried DIRECTLY after the proxy failed"),
    )

    caplog.clear()
    with caplog.at_level("WARNING", logger="persona"):
        with pytest.raises(OSError) as excinfo:
            egress.fetch_json("http://api.github.com/x", timeout=10)

    assert excinfo.value is original, (
        "the bare `raise` no longer re-raises the ORIGINAL exception — the "
        "fail-closed contract is about the object callers catch, not its class"
    )
    # ...and the new line is still doing its job on the way past.
    line = _proxy_failure_line(caplog)
    assert "OSError: proxy unreachable" in line
    assert "hunter2" not in line, f"credential leaked on the re-raise path: {line}"


def test_the_REFUSE_arms_deliberate_omission_is_untouched(caplog):
    """PS-276 AC4's other edge, and explicitly OUT of this ticket's scope as a
    change — pinned here so a later "normalisation" of the two warnings cannot
    quietly undo it.

    The REFUSE arm (`egress.py:307-317`) does NOT log the transport-derived
    value, and argues why in a comment: it can embed credentials. Carrying the
    message on the PROXIED arm must not have been mistaken for a licence to
    echo the configured value on the REFUSE arm.
    """
    caplog.clear()
    egress._reset_curl_refusal_log()
    settings.set_app_egress_proxy("socks5://alice:hunter2@ not a proxy url")

    with caplog.at_level("WARNING", logger="persona"):
        with pytest.raises(egress.EgressRefused):
            egress.fetch_json("http://api.github.com/x", timeout=10)

    refusals = [r.getMessage() for r in caplog.records if "NOT SENT" in r.getMessage()]
    assert refusals, "the refusal stopped being logged"
    for line in refusals:
        assert "hunter2" not in line, f"the REFUSE arm started echoing a credential: {line}"
        assert "alice" not in line, f"the REFUSE arm started echoing a credential: {line}"


# --------------------------------------------------------------------------
# AC5 + AC6 — the premise-as-AC. The same probe that found the defect,
# inverted by the fix: a SOCKS5 GREETING on the wire, not `CONNECT `.
# --------------------------------------------------------------------------


def test_socks_policy_sends_a_socks_greeting_not_http_connect():
    """AC5/AC6, and THE regression assertion of this ticket.

    With `https_proxy=socks5://...`, urllib sends `CONNECT host:443 HTTP/1.1`
    at a SOCKS port and the server — waiting for a \\x05 greeting — never
    answers. This asserts what actually goes on the wire: the SOCKS5 version
    byte, and emphatically not an HTTP CONNECT.

    It also asserts the target went out as a DOMAIN NAME (atyp 0x03): resolving
    api.github.com locally would trade the IP disclosure for a DNS one, which
    is the leak proxy_checker refuses by name.
    """
    seen: dict[str, object] = {}
    srv, port = _listener()

    def serve() -> None:
        conn, _ = srv.accept()
        conn.settimeout(10)
        try:
            first = conn.recv(512)
            seen["first"] = first
            # Parse the greeting so the target can be read off the CONNECT that
            # follows — nmethods is byte 2.
            conn.sendall(b"\x05\x00")  # no auth required
            _ver, _cmd, _rsv, atyp = _recv_exactly(conn, 4)
            seen["atyp"] = atyp
            host = _recv_exactly(conn, _recv_exactly(conn, 1)[0])
            tport = struct.unpack(">H", _recv_exactly(conn, 2))[0]
            seen["target"] = (host.decode(), tport)
        except Exception as exc:
            seen["error"] = repr(exc)
        finally:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    settings.set_app_egress_proxy(f"socks5://127.0.0.1:{port}")
    try:
        # The listener hangs up after the handshake, so the fetch fails — what
        # is under test is what reached the wire before it did.
        with pytest.raises(Exception):
            egress.fetch_json(updater.RELEASES_API, timeout=10)
    finally:
        thread.join(15)
        srv.close()

    first = seen.get("first", b"")
    assert first, f"the SOCKS port received nothing at all ({seen.get('error')})"
    assert first[:1] == b"\x05", f"expected a SOCKS5 greeting, got {first[:16]!r}"
    assert not first.startswith(b"CONNECT "), (
        "urllib's env-var route speaks HTTP CONNECT at a SOCKS port — that is "
        "the defect this ticket fixes"
    )
    # AC6: the exit resolves the name, not us.
    assert seen.get("atyp") == 0x03, "the target must be sent as a domain name"
    assert seen.get("target") == ("api.github.com", 443)


# --------------------------------------------------------------------------
# The resolver itself — one authority, three verdicts.
# --------------------------------------------------------------------------


def test_resolver_verdicts():
    """AC7's companion: the decision has three outcomes and "configured but
    unusable" is deliberately NOT "direct" — that distinction is the whole
    reason this returns a verdict rather than an Optional string."""
    assert egress.resolve("") == (egress.DIRECT, "")
    assert egress.resolve("   ") == (egress.DIRECT, "")

    verdict, transport = egress.resolve("socks5://127.0.0.1:1080")
    assert (verdict, transport) == (egress.PROXIED, "socks5://127.0.0.1:1080")

    verdict, _reason = egress.resolve("not a proxy url")
    assert verdict == egress.REFUSE, (
        "a typo'd proxy must never degrade to a direct send — that is the case "
        "where the operator most believes they are covered"
    )


def test_both_call_sites_consult_the_same_authority(monkeypatch):
    """AC7. Not a grep but the behavioural version of it: one patch of the
    single resolver must divert BOTH fetches. A second copy of the decision in
    either call site would leave that site sending directly."""
    calls = []

    def only_authority(proxy=None):
        calls.append(proxy)
        return egress.REFUSE, "diverted by the test"

    monkeypatch.setattr(egress, "resolve", only_authority)
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda *a, **k: pytest.fail("updater bypassed the authority"),
    )
    monkeypatch.setattr(
        ff.urllib.request,
        "urlopen",
        lambda *a, **k: pytest.fail("firefox bypassed the authority"),
    )
    monkeypatch.setattr(ff, "_expected_asset", lambda: "asset.zip")

    assert updater.fetch_latest_full() == ("", "", "")
    assert ff.fetch_latest() == ("", False)
    assert len(calls) == 2, f"both fetches must consult the resolver, got {calls}"


def test_settings_roundtrip_and_default():
    """The store half: default "", set/clear roundtrip, and whitespace stripped
    so "direct" vs "refuse" can never turn on invisible characters."""
    assert settings.app_egress_proxy() == ""

    settings.set_app_egress_proxy("socks5://127.0.0.1:1080")
    assert settings.app_egress_proxy() == "socks5://127.0.0.1:1080"

    settings.set_app_egress_proxy("  socks5://127.0.0.1:1080  ")
    assert settings.app_egress_proxy() == "socks5://127.0.0.1:1080"

    settings.set_app_egress_proxy("   ")
    assert settings.app_egress_proxy() == ""
    assert egress.resolve() == (egress.DIRECT, "")

    settings.set_app_egress_proxy("")
    assert settings.app_egress_proxy() == ""


# --------------------------------------------------------------------------
# The transport underneath the policy. Round 1 shipped twelve tests that ALL
# configured a socks5:// proxy (or an unparseable value that returns REFUSE
# before any transport runs), so the entire aiohttp branch of
# fetch_json_via_proxy and the length-less EOF branch of _read_http_body had
# ZERO coverage — and each carried a defect that a passing suite could not see.
# `http://` and `https://` are accepted schemes (egress.resolve returns
# PROXIED for them), so this is a SUPPORTED configuration, not a hypothetical.
# --------------------------------------------------------------------------


def test_release_body_without_content_length_is_capped_at_the_release_size():
    """The length-less EOF branch must honour the max_body it was PARAMETERISED
    with, not the geo constant.

    `_http_get_head` sends `Connection: close`, which is exactly what makes a
    response with neither Content-Length nor chunked encoding legal — so this
    branch is reachable on the real path, not theoretical. A releases document
    arriving that way was still being cut off at the 256 KB geo cap, which is
    the precise failure _MAX_RELEASE_BODY was introduced to prevent: the live
    document already measures ~129 KB and grows with every release.
    """
    body = b"x" * (300 * 1024)  # between _MAX_GEO_BODY and _MAX_RELEASE_BODY
    assert proxy_checker._MAX_GEO_BODY < len(body) < proxy_checker._MAX_RELEASE_BODY

    async def read_it(max_body):
        reader = asyncio.StreamReader()
        reader.feed_data(body)
        reader.feed_eof()
        return await proxy_checker._read_http_body(reader, {}, max_body=max_body)

    out = asyncio.run(read_it(proxy_checker._MAX_RELEASE_BODY))
    assert out == body, (
        "a 300 KB release document with no Content-Length must survive — this "
        "branch was still enforcing the 256 KB geo cap"
    )

    # And the cap must still BE a cap: the geo caller's default is unchanged.
    with pytest.raises(ValueError):
        asyncio.run(read_it(proxy_checker._MAX_GEO_BODY))


def test_http_proxy_reads_a_body_split_across_records():
    """The aiohttp branch must read the body to completion.

    `StreamReader.read(n)` returns as soon as ANY data is buffered rather than
    filling to `n`, so a document arriving in more than one TLS record came
    back SHORT and json.loads raised — intermittently, depending on how the
    response happened to be segmented. For a ~129 KB GitHub document that is
    the normal path. This drives a real `http://` proxy (the branch every other
    test in this file skips) and deliberately writes the body in two pieces
    with a pause between them, which is the segmentation that reproduces it.
    """
    payload = json.dumps(
        {"tag_name": "148.0.0.1", "assets": [{"name": "chrome.zip"}]}
    ).encode()
    split = 17  # mid-key, so a truncated read cannot parse as valid JSON
    seen: dict[str, object] = {}
    srv, port = _listener()

    def serve() -> None:
        conn, _ = srv.accept()
        conn.settimeout(10)
        try:
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                request += chunk
            seen["request"] = request
            # No Content-Length and no chunked encoding: the body runs to EOF,
            # which is legal under the `Connection: close` we announce.
            conn.sendall(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Connection: close\r\n\r\n" + payload[:split]
            )
            time.sleep(0.25)  # the second record arrives while the read waits
            conn.sendall(payload[split:])
        except Exception as exc:  # pragma: no cover - surfaced via `seen`
            seen["error"] = repr(exc)
        finally:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    try:
        doc = proxy_checker.fetch_json_via_proxy_sync(
            f"http://127.0.0.1:{port}", "http://api.github.com/x", 15
        )
    finally:
        thread.join(15)
        srv.close()

    assert doc == json.loads(payload), (
        f"the body came back truncated or unparsed (server said {seen.get('error')})"
    )
    # The neutral UA is what reaches a third party — never the geo probe's.
    request = seen.get("request", b"")
    assert proxy_checker._NEUTRAL_USER_AGENT.encode() in request
    assert b"persona-proxy-check/1.0" not in request


# --------------------------------------------------------------------------
# The header the authority was asked for must survive the branch it takes.
#
# `fetch_json` declares `accept` with GitHub's versioned media type as its
# default and the DIRECT branch honours it — but the PROXIED branch dropped it
# and both of its sub-branches hardcoded the geo probe's "application/json".
# So turning the policy ON silently changed the request on the wire, which is
# precisely the disagreement-with-itself the single authority exists to
# prevent: `application/json` is the UNVERSIONED type GitHub's API-versioning
# guidance says not to rely on, and the drift landed only on operators who had
# configured a proxy. Both sub-branches are asserted, because a header threaded
# through only one of them would just relocate the drift.
#
# These read the header OFF THE WIRE rather than off a call signature: "the
# exit saw the same request" is a fact about bytes, not about a kwarg.
# --------------------------------------------------------------------------


_DIRECT_ACCEPT = "application/vnd.github+json"


def _accept_headers(request: bytes) -> list[str]:
    """Every Accept: value in a raw request, lowercased keys, order preserved."""
    return [
        line.split(b":", 1)[1].strip().decode("latin-1")
        for line in request.split(b"\r\n")
        if line.lower().startswith(b"accept:")
    ]


def test_socks_branch_sends_the_accept_the_caller_asked_for():
    """The SOCKS sub-branch must put the CALLER's Accept on the wire.

    An http:// target keeps this TLS-free (the tunnel negotiates TLS only for
    an https target), so the request bytes are readable directly — the contract
    under test is the header, not the transport's crypto.
    """
    seen: dict[str, object] = {}
    srv, port = _listener()
    body = b'[{"tag_name": "v1", "assets": []}]'

    def serve() -> None:
        conn, _ = srv.accept()
        conn.settimeout(10)
        try:
            greeting = _recv_exactly(conn, 2)
            _recv_exactly(conn, greeting[1])  # the method list
            seen["greeting"] = greeting
            conn.sendall(b"\x05\x00")  # no auth required
            _ver, _cmd, _rsv, atyp = _recv_exactly(conn, 4)
            seen["atyp"] = atyp
            host = _recv_exactly(conn, _recv_exactly(conn, 1)[0])
            struct.unpack(">H", _recv_exactly(conn, 2))[0]
            seen["host"] = host
            conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 4 + b"\x00\x00")
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                request += chunk
            seen["request"] = request
            conn.sendall(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Connection: close\r\n\r\n" + body
            )
        except Exception as exc:  # pragma: no cover - surfaced via `seen`
            seen["error"] = repr(exc)
        finally:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    settings.set_app_egress_proxy(f"socks5://127.0.0.1:{port}")
    try:
        doc = egress.fetch_json("http://api.github.com/x", timeout=10)
    finally:
        thread.join(15)
        srv.close()

    assert doc == json.loads(body), f"server said {seen.get('error')}"
    request = seen.get("request", b"")
    assert request, f"the proxy received no request at all ({seen.get('error')})"

    assert _accept_headers(request) == [_DIRECT_ACCEPT], (
        f"the exit saw Accept: {_accept_headers(request)}, but the caller asked "
        f"for {_DIRECT_ACCEPT!r} — enabling the policy changed the request"
    )
    # Still the SOCKS properties this ticket bought, unweakened by the header.
    assert seen.get("atyp") == 0x03
    assert seen.get("host") == b"api.github.com"


def test_http_proxy_branch_sends_the_accept_the_caller_asked_for():
    """The aiohttp sub-branch, same contract. `http://` is a SUPPORTED policy
    value (resolve() returns PROXIED for it), so this is the other half of the
    live production surface, not a hypothetical."""
    seen: dict[str, object] = {}
    srv, port = _listener()
    body = b'{"tag_name": "148.0.0.1", "assets": []}'

    def serve() -> None:
        conn, _ = srv.accept()
        conn.settimeout(10)
        try:
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                request += chunk
            seen["request"] = request
            conn.sendall(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Connection: close\r\n\r\n" + body
            )
        except Exception as exc:  # pragma: no cover - surfaced via `seen`
            seen["error"] = repr(exc)
        finally:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    settings.set_app_egress_proxy(f"http://127.0.0.1:{port}")
    try:
        doc = egress.fetch_json("http://api.github.com/x", timeout=15)
    finally:
        thread.join(15)
        srv.close()

    assert doc == json.loads(body), f"server said {seen.get('error')}"
    request = seen.get("request", b"")
    assert request, f"the proxy received no request at all ({seen.get('error')})"
    assert _accept_headers(request) == [_DIRECT_ACCEPT], (
        f"the exit saw Accept: {_accept_headers(request)}, but the caller asked "
        f"for {_DIRECT_ACCEPT!r} — enabling the policy changed the request"
    )


def test_the_geo_probe_still_asks_for_plain_json():
    """The other side of the same coin: threading `accept` must not drag the
    versioned GitHub type into the geo probe, which reaches an endpoint WE
    chose and has no opinion about GitHub's API versions. The defaults are what
    keep every pre-existing caller byte-identical."""
    import inspect

    for fn in (
        proxy_checker.fetch_json_via_proxy,
        proxy_checker.fetch_json_via_proxy_sync,
        proxy_checker._json_via_socks,
    ):
        assert (
            inspect.signature(fn).parameters["accept"].default == "application/json"
        ), f"{fn.__name__} would change what an existing caller sends"

    seen: dict[str, object] = {}
    srv, port = _listener()

    def serve() -> None:
        conn, _ = srv.accept()
        conn.settimeout(10)
        try:
            greeting = _recv_exactly(conn, 2)
            _recv_exactly(conn, greeting[1])
            conn.sendall(b"\x05\x00")
            _recv_exactly(conn, 4)
            _recv_exactly(conn, _recv_exactly(conn, 1)[0])
            _recv_exactly(conn, 2)
            conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 4 + b"\x00\x00")
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                request += chunk
            seen["request"] = request
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n" + b'{"ok": true}'
            )
        except Exception as exc:  # pragma: no cover - surfaced via `seen`
            seen["error"] = repr(exc)
        finally:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        # No `accept` passed — exactly how the geo caller invokes it.
        proxy_checker.fetch_json_via_proxy_sync(
            f"socks5://127.0.0.1:{port}", "http://ipwho.is/", 10
        )
    finally:
        thread.join(15)
        srv.close()

    assert _accept_headers(seen.get("request", b"")) == ["application/json"], (
        "an unspecified accept must still send the geo probe's own value"
    )


# --------------------------------------------------------------------------
# PS-66 — the app-update poll. `services/egress.py` was written when the two
# engine polls above were the whole population ("Both call sites consult it");
# `app_update` is a THIRD, and the most frequent unattended egress persona
# performs — `ui/app.py`'s `while True: check_for_update(); time.sleep(60)`
# daemon thread, sixty times more often than the hourly engine poll. Its
# transport is `curl` subprocesses rather than urllib, so it consults
# `curl_proxy_args()` (the argv ARM of the same `resolve()`), and these tests
# assert on the CONSTRUCTED ARGV — a return value is identical either way, so
# asserting on one would pass against an implementation that routed nothing.
# --------------------------------------------------------------------------

from src.services.app_update import fast_update as fu  # noqa: E402
from src.services.app_update import updater as au  # noqa: E402
from src.utils import httpdl  # noqa: E402

_PROXY = "socks5h://127.0.0.1:9050"


@pytest.fixture(autouse=True)
def _reset_refusal_throttle():
    """The refusal log throttles by state change, so it is process state. Tests
    that drive refusals would otherwise inherit each other's throttle and read
    a suppressed line as a missing one."""
    egress._reset_curl_refusal_log()
    yield
    egress._reset_curl_refusal_log()


class _Ran:
    """The subprocess.run result shape the updater's curl sites consume."""

    returncode = 0
    stdout = b""


def _capture_curl(monkeypatch, *, staged=None):
    """Run all four app-update curl sites and return the argv each one built.

    Uses the seam tests/test_update_size.py already established — monkeypatching
    `au.subprocess.run` with a fake that receives the command list.
    """
    seen = []

    def fake_run(cmd, **k):
        seen.append(list(cmd))
        return _Ran()

    monkeypatch.setattr(au.subprocess, "run", fake_run)

    au.latest_tag()
    au.remote_size("http://example.invalid/asset")
    au._curl_get("http://example.invalid/checksums.txt")

    # The download goes through the SHARED httpdl downloader, whose own
    # subprocess is a separate seam.
    def fake_dl_run(cmd, **k):
        seen.append(list(cmd))
        if staged is not None:
            staged.write_bytes(b"x" * 100)
        return _Ran()

    monkeypatch.setattr(httpdl.subprocess, "run", fake_dl_run)
    if staged is not None:
        monkeypatch.setattr(au, "staged_path", lambda tag="": str(staged))
        monkeypatch.setattr(au, "_clear_stale_staged", lambda keep: None)
        au.download_update("http://example.invalid/asset", size=100)

    return seen


# --------------------------------------------------------------------------
# AC1 — positive routing. The argv must ACTUALLY carry --proxy, at all four
# sites. This is the assertion that goes red when the routing is reverted.
# --------------------------------------------------------------------------


def test_configured_policy_puts_proxy_argv_on_every_app_update_curl(
    monkeypatch, tmp_path
):
    """AC1. With a policy set, each of the four app-update curl sites must send
    through it. Asserted on the constructed command line, because both the
    routed and the unrouted implementation return the same values."""
    settings.set_app_egress_proxy(_PROXY)
    staged = tmp_path / "staged.AppImage"

    seen = _capture_curl(monkeypatch, staged=staged)

    assert len(seen) == 4, f"expected all four sites to run, got {seen}"
    for cmd in seen:
        assert "--proxy" in cmd, f"site sent WITHOUT the configured proxy: {cmd}"
        assert cmd[cmd.index("--proxy") + 1] == _PROXY, (
            f"--proxy carried the wrong transport: {cmd}"
        )


def test_app_update_consults_the_one_authority_not_a_local_copy(monkeypatch, tmp_path):
    """AC1's companion, and the anti-drift assertion: patching the SINGLE
    resolver must divert every app-update site. A site that read the setting
    itself — a second copy of the decision — would keep sending on this patch."""
    settings.set_app_egress_proxy("")  # the resolver, not the setting, decides

    monkeypatch.setattr(
        egress, "resolve", lambda proxy=None: (egress.PROXIED, "socks5h://10.0.0.1:1")
    )
    seen = _capture_curl(monkeypatch, staged=tmp_path / "staged.AppImage")

    assert len(seen) == 4
    for cmd in seen:
        assert "--proxy" in cmd and "socks5h://10.0.0.1:1" in cmd, (
            f"site did not consult the shared authority: {cmd}"
        )


# --------------------------------------------------------------------------
# AC2 — REFUSE must not degrade to a direct send. Asserted as a fact about
# processes: nothing is spawned at all.
# --------------------------------------------------------------------------


def test_unusable_policy_spawns_no_curl_at_any_app_update_site(monkeypatch, tmp_path):
    """AC2. A configured-but-unparseable proxy means the request is NOT SENT.
    Spying on subprocess.run rather than on the return value, because every one
    of these sites returns its ordinary failure sentinel either way — which is
    exactly how a silent direct send would hide."""
    settings.set_app_egress_proxy("this is not a proxy url")

    def forbidden(cmd, **k):
        raise AssertionError(f"a request was SENT despite the refusal: {cmd}")

    monkeypatch.setattr(au.subprocess, "run", forbidden)
    monkeypatch.setattr(httpdl.subprocess, "run", forbidden)
    staged = tmp_path / "staged.AppImage"
    monkeypatch.setattr(au, "staged_path", lambda tag="": str(staged))
    monkeypatch.setattr(au, "_clear_stale_staged", lambda keep: None)

    # Each site still reports its existing failure value — the refusal does not
    # become a new exception the 60s poll was never written to catch.
    assert au.latest_tag() == ""
    assert au.remote_size("http://example.invalid/asset") == 0
    assert au._curl_get("http://example.invalid/checksums.txt") == ""
    assert au.download_update("http://example.invalid/asset", size=100) == ""


def test_refusal_reaches_the_log_for_the_curl_sites(caplog):
    """AC2's other half. A silent skip is indistinguishable from "no new
    release", so the refusal must be findable — without echoing the transport,
    which can embed credentials."""
    settings.set_app_egress_proxy("this is not a proxy url")

    with caplog.at_level("WARNING", logger="persona"):
        with pytest.raises(egress.EgressRefused):
            egress.curl_proxy_args()

    assert any("NOT SENT" in r.getMessage() for r in caplog.records), (
        f"no refusal was logged: {[r.getMessage() for r in caplog.records]}"
    )


# --------------------------------------------------------------------------
# AC3 — the log must not flood. This poll runs every 60 SECONDS, so a line per
# refusal is ~1,440 a day into a disk-backed log (the hourly engine poll that
# egress.fetch_json serves is 24). Throttled by STATE CHANGE, not dropped.
# --------------------------------------------------------------------------


def test_repeated_identical_refusals_log_once_not_once_per_poll(caplog):
    """AC3. Drive the refusal the way the 60s poll would and assert the warning
    is emitted ONCE, not per iteration."""
    settings.set_app_egress_proxy("this is not a proxy url")

    with caplog.at_level("WARNING", logger="persona"):
        for _ in range(25):
            with pytest.raises(egress.EgressRefused):
                egress.curl_proxy_args()

    refusals = [r for r in caplog.records if "NOT SENT" in r.getMessage()]
    assert len(refusals) == 1, (
        f"the 60s poll would flood the disk-backed log: {len(refusals)} lines "
        "for 25 refusals"
    )


def test_a_recovered_policy_logs_again(caplog):
    """AC3's boundary — a second outage AFTER A RECOVERY must not be swallowed.

    Named for the one re-arm it actually drives. It previously claimed the
    changed-reason re-arm too, which the test never exercised — see
    `test_a_second_distinct_bad_value_logs_again` below for that half.
    """
    with caplog.at_level("WARNING", logger="persona"):
        settings.set_app_egress_proxy("this is not a proxy url")
        with pytest.raises(egress.EgressRefused):
            egress.curl_proxy_args()

        # Recovery: a working policy clears the state...
        settings.set_app_egress_proxy(_PROXY)
        assert egress.curl_proxy_args() == ["--proxy", _PROXY]

        # ...so the NEXT outage is reported rather than swallowed.
        settings.set_app_egress_proxy("still not a proxy url")
        with pytest.raises(egress.EgressRefused):
            egress.curl_proxy_args()

    refusals = [r for r in caplog.records if "NOT SENT" in r.getMessage()]
    assert len(refusals) == 2, (
        f"a refusal after a recovery must still be findable, got {len(refusals)}"
    )


def test_a_second_distinct_bad_value_logs_again(caplog):
    """AC3's other boundary — the operator who typos TWICE, with no recovery in
    between, must still get a signal for the second one.

    This is the population most likely to be actively fixing the setting: they
    read the warning, edited the key, and got it wrong a different way. A
    throttle keyed on the REFUSAL REASON would answer them with silence, because
    `resolve()` has exactly one REFUSE string — both typos produce the identical
    reason, so the state would never change. Keying on the rejected VALUE is
    what makes this re-arm reachable at all; this test is what holds it that way.
    """
    with caplog.at_level("WARNING", logger="persona"):
        settings.set_app_egress_proxy("this is not a proxy url")
        with pytest.raises(egress.EgressRefused):
            egress.curl_proxy_args()

        # A DIFFERENT unusable value — no recovery in between, and the refusal
        # reason string is byte-identical to the first.
        settings.set_app_egress_proxy("also://not a proxy url")
        with pytest.raises(egress.EgressRefused):
            egress.curl_proxy_args()

    refusals = [r for r in caplog.records if "NOT SENT" in r.getMessage()]
    assert len(refusals) == 2, (
        "a second, distinct bad value is a NEW fact for the operator and must "
        f"be logged; got {len(refusals)} line(s) — the throttle is keying on "
        "the refusal reason, which never varies"
    )


# --------------------------------------------------------------------------
# AC4 — the default. An unset key must be BYTE-IDENTICAL to before this
# existed: that is the whole blast radius, and egress.py is explicit that a
# fail-closed default would brick the update path, security updates included.
# --------------------------------------------------------------------------


def test_unset_policy_leaves_every_app_update_argv_byte_identical(
    monkeypatch, tmp_path
):
    """AC4. The exact command lines this code built on `main` (fc27868), pinned.

    The flag SHAPES are load-bearing and differ per site: `-sI` with no -L and
    no -f (a 302 is the SUCCESS case — the tag is read out of the redirect),
    `-fsSLI` with -L (GitHub 302s to a CDN and the real Content-Length is in the
    final response). A well-meant flag cleanup here silently breaks version
    detection — the failure this path has already shipped once."""
    settings.set_app_egress_proxy("")
    staged = tmp_path / "staged.AppImage"

    seen = _capture_curl(monkeypatch, staged=staged)

    latest = "https://github.com/amnesiadevelopment/persona/releases/latest"
    assert seen == [
        ["curl", "-sI", "--connect-timeout", "15", "--max-time", "30", latest],
        ["curl", "-fsSLI", "--connect-timeout", "15", "--max-time", "30",
         "http://example.invalid/asset"],
        ["curl", "-fsSL", "--connect-timeout", "15", "--max-time", "30",
         "http://example.invalid/checksums.txt"],
        ["curl", "-fsSL", "--connect-timeout", "30", "--speed-limit", "1024",
         "--speed-time", "30", "-C", "-", "-o", str(staged),
         "http://example.invalid/asset"],
    ], "an unset key must change NOTHING on the wire"


# --------------------------------------------------------------------------
# AC5 — the shared helper stays a mechanism. `httpdl.curl_download` has two
# callers; putting the policy lookup INSIDE it would plant a second copy of the
# decision in a shared utility. The proxy argv is caller-owned, exactly like
# the timeout policy that function's own docstring already delegates.
# --------------------------------------------------------------------------


def test_shared_downloader_is_unrouted_by_default_so_fast_update_is_unchanged(
    monkeypatch, tmp_path
):
    """AC5. `fast_update._download_small` is NOT edited by this slice and its
    argv must be byte-identical to `main`: the new parameter defaults to empty,
    and curl_download itself consults no policy."""
    settings.set_app_egress_proxy(_PROXY)  # set, yet must not reach this caller
    dst = tmp_path / "app.zip"
    seen = []

    def fake_run(cmd, **k):
        seen.append(list(cmd))
        dst.write_bytes(b"x")
        return _Ran()

    monkeypatch.setattr(httpdl.subprocess, "run", fake_run)

    assert fu._download_small("http://example.invalid/app.zip", str(dst)) is True
    assert seen == [
        ["curl", "-fsSL", "--connect-timeout", "15", "--max-time", "180",
         "-C", "-", "-o", str(dst), "http://example.invalid/app.zip"],
    ], "the shared downloader grew a policy of its own — a second copy"
    assert "--proxy" not in seen[0]


# ==========================================================================
# PS-75 — the engine BINARY download, the third arm of this same authority.
#
# The two polls above ask "may I speak to GitHub, and how?". Until PS-75 the
# ~80-230MB archive those polls exist to LOCATE was then fetched by a transport
# that never asked, so ONE unattended startup sequence disagreed with itself
# about how persona's traffic leaves. `download_opener()` closes that.
#
# Two call sites, two separate implementations, so each gets its OWN routing
# assertion (AC5) — one passing says nothing about the other:
#   * Firefox   — engine_install._resumable_download, UNATTENDED at startup
#   * Chromium  — engine/updater._download_to, operator click
#
# Per the standing directive these assert on an OBSERVED CONNECTION, an
# OBSERVED FIRST BYTE, or an OBSERVED ABSENCE of one — never on a substring in
# generated argv or a handler present in a list.
# ==========================================================================

from src.services.browser import engine_install as eng  # noqa: E402

# Warm invisible_playwright HERE, at module scope, so it is in sys.modules
# before any test below installs a socket spy.
#
# This is not tidiness — it is what makes the AC4 refusal test assert anything
# at all. `_download_invisible` imports these two LAZILY, inside the function
# (engine_install.py:434), and that import does not survive a patched
# `socket.socket`: run first under the spy it dies inside invisible_core with
# "TypeError: function() argument 'code' must be code, not str", which
# invisible_playwright._pin re-raises as ImportError. That happens at :434,
# BEFORE the egress consultation at ~:480 — so the refusal test would record
# `opened == []` because the code under test never ran, not because a refusal
# was honoured. Green for a reason unrelated to the property.
#
# The two routing tests below hid this: `_wire_firefox_install` imports
# `invisible_playwright.download` while wiring, which warms sys.modules before
# their spy exists. The refusal test is the one Firefox test that does not call
# that fixture, so it was the one that failed in isolation while passing in a
# full-file run — i.e. it was passing on file ordering. Warming for the whole
# module removes the ordering dependency for every test here, not just that one.
import invisible_playwright.constants  # noqa: E402,F401
import invisible_playwright.download  # noqa: E402,F401


def _socks_listener_capturing(seen: dict, reply: bytes = b""):
    """A fake SOCKS5 server that records the FIRST BYTES it receives and the
    target of the CONNECT, then optionally answers.

    Mirrors the harness the metadata-poll tests above already use; the point is
    the same one, moved to the binary path: what actually reached the wire.

    It KEEPS ACCEPTING after the exchange it captures, and that is not
    incidental. Both downloaders retry — `resumable_download` up to 40 times,
    `_resumable_download` until its no-progress budget — so a one-shot listener
    leaves every later attempt blocking in connect() against an unaccepted
    backlog, and the test hangs instead of failing. Later connections are
    closed immediately so the retries fail FAST; only the first is recorded.
    """
    srv, port = _listener()

    def serve() -> None:
        first = True
        while True:
            try:
                conn, _ = srv.accept()
            except Exception:
                return  # the test closed the listener; we are done
            if not first:
                # drain the retries quickly rather than letting them block
                try:
                    conn.close()
                except Exception:
                    pass
                continue
            first = False
            conn.settimeout(10)
            try:
                seen["first"] = conn.recv(512)
                conn.sendall(b"\x05\x00")  # no-auth accepted
                _ver, _cmd, _rsv, atyp = _recv_exactly(conn, 4)
                seen["atyp"] = atyp
                host = _recv_exactly(conn, _recv_exactly(conn, 1)[0])
                tport = struct.unpack(">H", _recv_exactly(conn, 2))[0]
                seen["target"] = (host.decode(), tport)
                if reply:
                    # granted, then serve the HTTP exchange over the tunnel
                    conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 4 + b"\x00\x00")
                    seen["http"] = conn.recv(4096)
                    conn.sendall(reply)
            except Exception as exc:
                seen["error"] = repr(exc)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return srv, port, thread


# --------------------------------------------------------------------------
# AC1 + AC2 + AC3 — Firefox: the UNATTENDED path, and the sharp case.
# --------------------------------------------------------------------------


def _wire_firefox_install(monkeypatch, tmp_path, host="engine.example.com"):
    """Point the UNATTENDED Firefox install at `host` and at a scratch cache.

    Deliberately patches only the URL and the cache location — NOT the
    transport and NOT the opener. `_download_invisible` is left to resolve the
    egress policy itself, because that consultation is precisely what is under
    test: a test that composed the opener and handed it in would pass with the
    consultation deleted, which is the AC9 trap.
    """
    import invisible_playwright.download as ipdl

    cache = tmp_path / "cache"
    (cache / "firefox-20").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        ipdl, "_resolve_asset_url", lambda tag, name: f"http://{host}/{tag}/{name}"
    )
    monkeypatch.setattr(
        ipdl, "cache_dir_for_version", lambda v: cache / str(v)
    )
    return cache


def test_firefox_engine_download_speaks_socks_to_the_configured_proxy(
    monkeypatch, tmp_path
):
    """AC1/AC2/AC3 for the Firefox archive — the path that runs at startup with
    no operator gesture (ui/app.py → _auto_update_engine2_async → here).

    Driven through the PRODUCTION entry point `_download_invisible`, which
    resolves the policy itself. An earlier draft of this test passed the opener
    in from the test and stayed GREEN under AC9's falsification — it was
    exercising the mechanism I had just written rather than the wiring.

    AC1: the download must actually EGRESS through the configured transport —
    proven by a local listener RECEIVING the connection, not by a return value
    that reads the same either way.

    AC2: the first bytes must be a SOCKS5 greeting (0x05), NOT `CONNECT `. This
    is the constraint-#1 assertion: a ProxyHandler-only fix would emit CONNECT
    at a port awaiting a \\x05 greeting and HANG — a different failure wearing
    the fix's clothes. socks5 is persona's DEFAULT scheme, so this is the
    normal case, not an edge case.

    AC3: the target must go out as a DOMAIN NAME (atyp 0x03) so the EXIT
    resolves it. Resolving the GitHub/CDN host locally would trade an IP
    disclosure for a DNS one, which egress.py explicitly refuses.
    """
    _wire_firefox_install(monkeypatch, tmp_path)
    seen: dict = {}
    srv, port, thread = _socks_listener_capturing(seen)

    settings.set_app_egress_proxy(f"socks5://127.0.0.1:{port}")
    try:
        # The listener hangs up after the handshake, so the install fails —
        # what is under test is what reached the wire before it did.
        assert eng._download_invisible(version="firefox-20") is False
    finally:
        srv.close()
        thread.join(15)

    first = seen.get("first", b"")
    assert first, (
        "the engine download never reached the configured proxy at all — the "
        f"binary download is still egressing unrouted ({seen.get('error')})"
    )
    assert first[:1] == b"\x05", f"expected a SOCKS5 greeting, got {first[:16]!r}"
    assert not first.startswith(b"CONNECT "), (
        "a ProxyHandler-only fix speaks HTTP CONNECT at a SOCKS port, which "
        "hangs rather than leaks — constraint #1 of this ticket"
    )
    assert seen.get("atyp") == 0x03, (
        "the target must be sent as a NAME so the exit resolves it (socks5h); "
        "a local resolution trades an IP disclosure for a DNS one"
    )
    assert seen.get("target") == ("engine.example.com", 80)


def test_firefox_engine_download_actually_transfers_through_the_proxy(
    monkeypatch, tmp_path
):
    """AC1, the positive end-to-end on the unattended path: bytes that arrive
    through the tunnel are the bytes written to disk. Routing that connects but
    cannot deliver a file would satisfy a weaker assertion than this one.

    Served content is a checksums.txt that does NOT list this OS's asset, so
    the install stops right after it with "no checksum for this build" — which
    keeps the test fast (no 200MB archive leg to retry) while still proving the
    transfer itself came down the SOCKS tunnel and landed on disk.
    """
    cache = _wire_firefox_install(monkeypatch, tmp_path)
    body = b"deadbeef  some-other-asset.tar.gz\n"
    reply = (
        b"HTTP/1.1 200 OK\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\n\r\n"
        + body
    )
    seen: dict = {}
    srv, port, thread = _socks_listener_capturing(seen, reply=reply)

    settings.set_app_egress_proxy(f"socks5://127.0.0.1:{port}")
    try:
        assert eng._download_invisible(version="firefox-20") is False
    finally:
        srv.close()
        thread.join(15)

    assert seen.get("first", b"")[:1] == b"\x05", (
        f"nothing was spoken to the proxy ({seen.get('error')})"
    )
    # The request went out over the tunnel...
    assert b"checksums.txt" in seen.get("http", b""), (
        f"the tunnelled request was not the expected fetch: {seen.get('http')!r}"
    )
    # ...and the bytes that came back through it are on disk.
    sums = cache / "firefox-20-checksums.txt"
    assert sums.exists(), "nothing was written — the tunnel delivered no bytes"
    assert sums.read_bytes() == body, "the file did not come through the tunnel"


# --------------------------------------------------------------------------
# AC5 — the OTHER path. Separate implementation, so its own assertion.
# --------------------------------------------------------------------------


def test_chromium_engine_download_speaks_socks_to_the_configured_proxy(tmp_path):
    """AC5/AC1/AC2/AC3 for the Chromium asset, which goes through a DIFFERENT
    downloader (httpdl.resumable_download) than the Firefox archive above. One
    of these passing says nothing about the other, which is why both exist."""
    seen: dict = {}
    srv, port, thread = _socks_listener_capturing(seen)

    settings.set_app_egress_proxy(f"socks5://127.0.0.1:{port}")
    try:
        updater._download_to(
            str(tmp_path / "engine.bin"),
            "http://chromium.example.com/engine.AppImage",
            5,
            "00" * 32,
            None,
        )
    finally:
        thread.join(15)
        srv.close()

    first = seen.get("first", b"")
    assert first, (
        "the Chromium asset never reached the configured proxy — this path "
        f"is still egressing unrouted ({seen.get('error')})"
    )
    assert first[:1] == b"\x05", f"expected a SOCKS5 greeting, got {first[:16]!r}"
    assert not first.startswith(b"CONNECT ")
    assert seen.get("atyp") == 0x03
    assert seen.get("target") == ("chromium.example.com", 80)


# --------------------------------------------------------------------------
# AC4 — REFUSE must not degrade to a direct send, on EITHER path.
# --------------------------------------------------------------------------


def test_refused_policy_opens_no_socket_for_either_engine_download(monkeypatch):
    """AC4. A configured-but-unparseable proxy means NOTHING IS SENT.

    Spied on socket.socket rather than on the return value, because both paths
    return the same False for "refused" and "the download failed" — a return
    value cannot tell a refusal from a leak. Mirrors curl_proxy_args' raise-
    don't-return-[] semantics: the DIRECT answer is a usable opener, so handing
    one back on REFUSE would silently degrade "we cannot honour your proxy"
    into "send from the real IP", with 200MB behind it.
    """
    settings.set_app_egress_proxy("this is not a proxy url")

    opened = []
    real_socket = socket.socket

    def spy(*a, **k):
        opened.append(a)
        return real_socket(*a, **k)

    monkeypatch.setattr(socket, "socket", spy)

    # Firefox — the unattended path. The refusal is resolved before the
    # transfer, so it lands on the caller's existing False.
    assert (
        eng._download_invisible(version="firefox-20") is False
    ), "a refused policy must fail the install, not proceed"
    assert opened == [], f"a socket was opened despite the refusal: {opened}"

    # Chromium — the operator-click path.
    #
    # The host must RESOLVE but refuse the connection. An unresolvable host
    # (the original "http://x/e") dies in getaddrinfo BEFORE a socket object
    # is ever constructed, so `opened == []` held whether the refusal was
    # honoured or the code sent directly — green for a reason unrelated to
    # the property. 127.0.0.1:9 (discard) resolves, so an unrouted send
    # constructs a socket and this assertion sees it.
    assert (
        updater._download_to(
            "/tmp/ps75-never", "http://127.0.0.1:9/e", 5, "00" * 32, None
        )
        is False
    )
    assert opened == [], f"a socket was opened despite the refusal: {opened}"


def test_refusal_on_the_download_path_is_surfaced_to_the_operator(caplog):
    """AC4's other half. A silent skip is indistinguishable from "no update",
    so the refusal must reach the log — without echoing the transport, which
    can embed credentials."""
    egress._reset_curl_refusal_log()
    settings.set_app_egress_proxy("this is not a proxy url")

    with caplog.at_level("WARNING", logger="persona"):
        with pytest.raises(egress.EgressRefused):
            egress.download_opener()

    messages = [r.getMessage() for r in caplog.records]
    assert any("NOT STARTED" in m for m in messages), f"no refusal logged: {messages}"
    assert not any(
        "this is not a proxy url" in m for m in messages
    ), "the rejected value must never be logged — it can embed credentials"


# --------------------------------------------------------------------------
# AC6 — the blast radius. With NO policy, both paths are unchanged.
# --------------------------------------------------------------------------


def test_default_engine_download_is_direct_and_unproxied():
    """AC6. An unset key must change NOTHING: no proxy handler anywhere in the
    opener, and the same Range-preserving redirect handler as before.

    egress.py:32-40 is explicit that a fail-closed default would brick the
    update path — security updates included — for every existing install.
    """
    assert settings.app_egress_proxy() == "", "the default must be unset"

    opener = egress.download_opener()
    names = [type(h).__name__ for h in opener.handlers]

    assert names == [
        type(h).__name__ for h in httpdl.range_opener().handlers
    ], "the unset default must be byte-identical to the pre-PS-75 opener"
    assert not any("Socks" in n or "Proxy" in n for n in names), (
        f"the default path grew a proxy: {names}"
    )
    assert any(isinstance(h, httpdl.KeepRangeRedirect) for h in opener.handlers), (
        "resume across GitHub's 302-to-CDN must survive the default path"
    )


def test_default_firefox_download_still_resumes_with_a_range_header(tmp_path):
    """AC6/AC7 together, on the unattended path: with no policy set the request
    is the one this code always made — including the `Range` header that makes
    a resume fetch the tail instead of restarting from zero.

    A routing change that breaks resume over a slow circuit is a regression:
    this transport exists BECAUSE of slow circuits.
    """
    assert settings.app_egress_proxy() == ""

    full = b"0123456789"
    seen = {}
    srv, port = _listener()

    def serve():
        conn, _ = srv.accept()
        conn.settimeout(10)
        try:
            req = conn.recv(4096)
            seen["req"] = req
            start = 0
            for line in req.split(b"\r\n"):
                if line.lower().startswith(b"range:"):
                    seen["range"] = line
                    start = int(line.split(b"=")[1].split(b"-")[0])
            body = full[start:]
            head = (
                b"HTTP/1.1 206 Partial Content\r\nContent-Range: bytes "
                + f"{start}-{len(full) - 1}/{len(full)}".encode()
                + b"\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\n\r\n"
            )
            conn.sendall(head + body)
        except Exception as exc:
            seen["error"] = repr(exc)
        finally:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    dest = tmp_path / "archive.download"
    dest.write_bytes(full[:4])  # a partial from a dropped circuit
    try:
        ok = eng._resumable_download(
            f"http://127.0.0.1:{port}/firefox-20.tar.gz",
            str(dest),
            opener_factory=lambda: egress.download_opener(),
            timeout=5,
            stall_timeout=5,
        )
    finally:
        thread.join(15)
        srv.close()

    assert ok is True, f"the default (direct) resume failed ({seen.get('error')})"
    assert seen.get("range") == b"Range: bytes=4-", (
        f"the resume did not ask for the tail: {seen.get('req')!r}"
    )
    assert dest.read_bytes() == full


# --------------------------------------------------------------------------
# AC8 — the shared mechanism holds no policy, and the digest gate is untouched.
# --------------------------------------------------------------------------


def test_the_shared_transport_resolves_no_policy_of_its_own(tmp_path):
    """AC8's companion, and egress.py:88-91's invariant: `httpdl` is SHARED
    (app_update/fast_update call it too), so a policy resolved in there would
    be a second copy of this decision inside a mechanism. Set a policy, call
    the primitive directly, and it must still be unrouted — the authority is
    the CALLER's to consult.
    """
    settings.set_app_egress_proxy("socks5://127.0.0.1:9")

    names = [type(h).__name__ for h in httpdl.range_opener().handlers]
    assert not any("Socks" in n or "Proxy" in n for n in names), (
        f"the shared mechanism grew a policy of its own: {names}"
    )


def test_digest_verification_is_unchanged_by_routing(tmp_path, monkeypatch):
    """AC8. `verify_file` / fail-closed-on-missing-digest is PS-49 ground and
    must not move. A wrong digest is still rejected and the partial discarded,
    with the policy set and the transfer routed."""
    assert httpdl.verify_file(str(tmp_path / "nope"), "aa" * 32) is False
    assert httpdl.digest_missing(None) is True
    assert httpdl.digest_missing("") is True
    assert httpdl.digest_missing("   ") is False  # arrived-but-unusable

    # and end to end: a served file whose digest does not match is refused
    body = b"tampered"
    reply = (
        b"HTTP/1.1 200 OK\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\n\r\n"
        + body
    )
    seen: dict = {}
    srv, port, thread = _socks_listener_capturing(seen, reply=reply)
    dest = tmp_path / "engine.bin"
    settings.set_app_egress_proxy(f"socks5://127.0.0.1:{port}")
    try:
        ok = updater._download_to(
            str(dest), "http://chromium.example.com/e", 5, "aa" * 32, None
        )
    finally:
        thread.join(15)
        srv.close()

    assert ok is False, "a digest mismatch must be refused however it was routed"
    assert not dest.exists()
    assert not (tmp_path / "engine.bin.part").exists(), "the partial must be dropped"


# --------------------------------------------------------------------------
# AC1/AC2/AC3 over TLS — the scheme production ACTUALLY uses.
#
# Every routing test above drives `http://`, and the shipped scheme is `https`
# WITHOUT EXCEPTION: invisible_core/constants.py:113 builds the release URL as
# `https://github.com/...`, and GitHub then 302s to a signed HTTPS CDN URL. So
# the arm that ships was, until these tests, the arm no test entered —
# `_SocksHTTPSConnection.connect()` was never executed by the suite.
#
# That is the highest-consequence gap in this change: the TLS wrap is
# hand-written, and getting it wrong fails as a SILENT MITM, which announces
# itself to nobody (invariant #0). A suite that exercises `http` while shipping
# `https` is asserting on the arm that cannot fail (PS-11).
#
# The harness below is therefore a REAL SOCKS5 proxy RELAYING to a REAL TLS
# server: a genuine handshake happens end to end, over the tunnel, rather than
# a recorded byte sequence being replayed.
# --------------------------------------------------------------------------


def _self_signed(tmp_path, cn):
    """A self-signed cert for `cn`, as (server_pem, ca_pem).

    Same shape as tests/test_cert_manager.py's builder — SAN included, because
    hostname verification is precisely what must stay live through the tunnel.
    """
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subj = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subj)
        .issuer_name(subj)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1))
        .not_valid_after(datetime.datetime(2035, 1, 1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(cn)]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    srv_pem = tmp_path / "srv.pem"
    srv_pem.write_bytes(
        cert.public_bytes(serialization.Encoding.PEM)
        + key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    ca_pem = tmp_path / "ca.pem"
    ca_pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return srv_pem, ca_pem


def _tls_origin(srv_pem, seen, body):
    """A REAL TLS server that honours `Range` with a 206.

    It records the Host and Range it saw INSIDE the tunnel — which is only
    readable if the TLS handshake genuinely completed, so these two keys are
    themselves evidence that the wrap worked.
    """
    import ssl as _ssl

    ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(srv_pem))
    srv, port = _listener()
    srv.listen(5)

    def serve() -> None:
        while True:
            try:
                raw, _ = srv.accept()
            except Exception:
                return
            try:
                conn = ctx.wrap_socket(raw, server_side=True)
            except Exception as exc:
                seen.setdefault("tls_error", repr(exc))
                continue
            try:
                req = conn.recv(4096)
                start = 0
                for line in req.split(b"\r\n"):
                    low = line.lower()
                    if low.startswith(b"range:"):
                        seen["range@origin"] = line
                        start = int(line.split(b"=")[1].split(b"-")[0])
                    elif low.startswith(b"host:"):
                        seen["host@origin"] = line.split(b":", 1)[1].strip()
                chunk = body[start:]
                conn.sendall(
                    b"HTTP/1.1 206 Partial Content\r\nContent-Range: bytes "
                    + f"{start}-{len(body) - 1}/{len(body)}".encode()
                    + b"\r\nContent-Length: "
                    + str(len(chunk)).encode()
                    + b"\r\n\r\n"
                    + chunk
                )
            except Exception as exc:
                seen.setdefault("origin_error", repr(exc))
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return srv, port, thread


def _socks_relay_to(origin_port, seen):
    """A REAL SOCKS5 proxy that records the handshake and then RELAYS raw bytes
    to the origin, so the TLS session is negotiated through the tunnel rather
    than terminated at the proxy.

    Relaying (not replaying) is the point: it is what makes the assertions
    below evidence about `_SocksHTTPSConnection`, not about this harness.
    """
    srv, port = _listener()
    srv.listen(5)

    def pipe(a, b) -> None:
        try:
            while True:
                data = a.recv(65536)
                if not data:
                    break
                b.sendall(data)
        except Exception:
            pass
        finally:
            for s in (a, b):
                try:
                    s.close()
                except Exception:
                    pass

    def serve() -> None:
        first = True
        while True:
            try:
                conn, _ = srv.accept()
            except Exception:
                return
            if not first:
                # Same reason as _socks_listener_capturing: drain retries fast
                # rather than letting them block against an unaccepted backlog.
                try:
                    conn.close()
                except Exception:
                    pass
                continue
            first = False
            try:
                seen["first"] = conn.recv(512)
                conn.sendall(b"\x05\x00")  # no-auth
                _ver, _cmd, _rsv, atyp = _recv_exactly(conn, 4)
                seen["atyp"] = atyp
                host = _recv_exactly(conn, _recv_exactly(conn, 1)[0])
                tport = struct.unpack(">H", _recv_exactly(conn, 2))[0]
                seen["target"] = (host.decode(), tport)
                upstream = socket.create_connection(("127.0.0.1", origin_port))
                conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 4 + b"\x00\x00")
                threading.Thread(
                    target=pipe, args=(conn, upstream), daemon=True
                ).start()
                pipe(upstream, conn)
            except Exception as exc:
                seen.setdefault("relay_error", repr(exc))

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return srv, port, thread


def test_https_engine_download_completes_a_real_tls_handshake_through_socks(
    tmp_path, monkeypatch
):
    """AC1/AC2/AC3 on the scheme that actually ships.

    Drives the PRODUCTION entry point `updater._download_to` — not a
    hand-built opener with a context injected, which would exercise the
    mechanism instead of the wiring (the AC9 trap that already caught an
    earlier draft of the Firefox tests).

    The test CA is trusted via SSL_CERT_FILE rather than by passing a context
    in, precisely so the code under test keeps building its own default
    context: the security-critical `context=None` branch is the one that
    ships, so it is the one that must be executed here.

    Evidence, all observed rather than asserted-on-a-substring:
      * the SOCKS5 greeting (0x05) reached the proxy — not `CONNECT `
      * the target went out as a NAME (atyp 0x03) on port 443 — remote DNS
      * the Host and Range were readable INSIDE the tunnel, which is only
        possible if the TLS handshake genuinely completed
      * the verified bytes landed on disk
    """
    import hashlib

    host = "engine.example.com"
    full = b"ABCDEFGHIJ"
    seen: dict = {}

    srv_pem, ca_pem = _self_signed(tmp_path, host)
    osrv, oport, othread = _tls_origin(srv_pem, seen, full)
    psrv, pport, pthread = _socks_relay_to(oport, seen)

    # Make the DEFAULT context (the one the shipped code builds for itself)
    # trust our throwaway CA. Nothing about the code under test is stubbed.
    monkeypatch.setenv("SSL_CERT_FILE", str(ca_pem))

    dest = tmp_path / "asset.bin"
    settings.set_app_egress_proxy(f"socks5://127.0.0.1:{pport}")
    try:
        ok = updater._download_to(
            str(dest),
            f"https://{host}/firefox-20.tar.gz",
            15,
            hashlib.sha256(full).hexdigest(),
            None,
        )
    finally:
        psrv.close()
        osrv.close()
        pthread.join(15)
        othread.join(15)

    assert ok is True, (
        "the https engine download failed through the SOCKS tunnel "
        f"(tls={seen.get('tls_error')} relay={seen.get('relay_error')} "
        f"origin={seen.get('origin_error')})"
    )
    first = seen.get("first", b"")
    assert first[:1] == b"\x05", f"expected a SOCKS5 greeting, got {first[:16]!r}"
    assert not first.startswith(b"CONNECT "), (
        "an https URL must still take a real SOCKS handshake, not HTTP CONNECT"
    )
    assert seen.get("atyp") == 0x03, (
        "the https target must be sent as a NAME so the EXIT resolves it; a "
        "local resolution trades an IP disclosure for a DNS one"
    )
    assert seen.get("target") == (host, 443), (
        f"the tunnel was not opened to the https target: {seen.get('target')}"
    )
    # Readable only through a completed TLS session:
    assert seen.get("host@origin") == host.encode(), (
        "the origin never saw the request — the TLS wrap did not carry it"
    )
    assert dest.read_bytes() == full, "the verified bytes did not land on disk"


def test_https_over_socks_still_verifies_the_certificate(tmp_path, monkeypatch):
    """The other half of the TLS arm, and the one whose failure is SILENT.

    A working transport is not evidence of a VERIFYING one: if verification
    were off, or if `server_hostname` named the PROXY instead of the target,
    every download here would still succeed and nothing would ever report it.
    So this pins the failure explicitly — an UNTRUSTED cert must break the
    transfer.

    WHICH LINES THIS ACTUALLY GUARDS — measured, not assumed. Disabling the
    `context or ssl.create_default_context()` fallback in
    `_SocksHTTPSConnection.__init__` does NOT turn this test red, because that
    fallback is UNREACHABLE from production: `SocksProxyHandler.__init__` calls
    `HTTPSHandler.__init__(context=None)`, and urllib builds a VERIFYING
    context there (verify_mode=CERT_REQUIRED, check_hostname=True) before this
    class is ever constructed, so `context` is never None in the shipped path.
    The two lines this test genuinely falsifies are the reachable ones:
    the context handed down by `SocksProxyHandler.__init__` (disable
    verification there and this goes red), and `server_hostname=self.host` in
    `connect()` (point it at the proxy and this goes red with
    SSLV3_ALERT_BAD_CERTIFICATE). Recorded so a future maintainer does not
    "cover" the dead fallback and believe the live path is pinned.

    Same harness as above with the CA simply not trusted; the SOCKS handshake
    still completes, so this isolates certificate verification from routing.
    """
    import hashlib

    host = "engine.example.com"
    full = b"ABCDEFGHIJ"
    seen: dict = {}

    srv_pem, _ca_pem = _self_signed(tmp_path, host)
    osrv, oport, othread = _tls_origin(srv_pem, seen, full)
    psrv, pport, pthread = _socks_relay_to(oport, seen)

    # Deliberately do NOT trust the CA: point the default store at an empty one.
    empty_ca = tmp_path / "empty-ca.pem"
    empty_ca.write_bytes(b"")
    monkeypatch.setenv("SSL_CERT_FILE", str(empty_ca))

    dest = tmp_path / "asset.bin"
    settings.set_app_egress_proxy(f"socks5://127.0.0.1:{pport}")
    try:
        ok = updater._download_to(
            str(dest),
            f"https://{host}/firefox-20.tar.gz",
            15,
            hashlib.sha256(full).hexdigest(),
            None,
        )
    finally:
        psrv.close()
        osrv.close()
        pthread.join(15)
        othread.join(15)

    assert ok is False, (
        "an UNTRUSTED certificate was accepted through the SOCKS tunnel — "
        "the TLS wrap is not verifying, which fails as a silent MITM"
    )
    assert not dest.exists(), "unverified bytes were written to disk"
    # The routing itself still worked, so the refusal above is about the
    # certificate and not about a tunnel that never opened.
    assert seen.get("first", b"")[:1] == b"\x05", (
        "the proxy was never reached, so this proves nothing about TLS"
    )
    assert seen.get("target") == (host, 443)
