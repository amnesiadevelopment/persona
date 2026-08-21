"""Persona's OWN egress policy: the two unattended release-metadata fetches.

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
import socket
import struct
import threading

import pytest

from src.core import settings
from src.services import egress
from src.services.engine import firefox as ff
from src.services.engine import updater


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
