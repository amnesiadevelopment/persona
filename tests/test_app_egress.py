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
