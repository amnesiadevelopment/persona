"""Both loopback listeners must serve the browser they were started for — and
nobody else on the machine.

The SOCKS5 bridge re-applies the operator's proxy credentials on the way out, and
the mTLS terminator performs the mutual-TLS handshake with the operator's client
certificate. Before this, any local process could drive either one. These tests
prove BOTH halves: the legitimate client still works, and a process that is not
the browser is refused *before* any upstream work is done on its behalf.

A control that cannot be shown to refuse has not been tested, so the refusals
here assert on the CONSEQUENCE (no upstream connection, no handshake), not on
whether some helper was called.
"""

import os
import socket
import ssl
import subprocess
import sys
import threading
import time

import pytest

from src.core import peerauth
from src.core.peerauth import PeerGate
from src.services.cert import terminator as term
from src.services.proxy.bridge import ProxyBridge


# --------------------------------------------------------------------------
# The primitive: why this is not SO_PEERCRED
# --------------------------------------------------------------------------

def test_so_peercred_is_meaningless_on_a_tcp_socket():
    """Pins the reason the obvious primitive was NOT used.

    SO_PEERCRED / LOCAL_PEERCRED are AF_UNIX mechanisms. On an AF_INET socket
    the kernel does not fail the call — it returns a zeroed credential — so a
    check built on it would read as a boundary while authenticating nothing.
    Both listeners here are TCP, which is why peer verification resolves the
    peer to a process instead.

    If a future platform ever returns a real pid here, this test fails and the
    simpler primitive becomes available — that is a result worth being told.
    """
    peercred = getattr(socket, "SO_PEERCRED", None)
    if peercred is None:
        pytest.skip("SO_PEERCRED not exposed on this platform")

    import struct

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        conn, _ = srv.accept()
        try:
            raw = conn.getsockopt(
                socket.SOL_SOCKET, peercred, struct.calcsize("3i")
            )
            pid, _uid, _gid = struct.unpack("3i", raw)
            assert pid == 0, (
                "SO_PEERCRED returned a real pid on TCP — the AF_UNIX primitive "
                "may now be usable here"
            )
        except OSError:
            pass  # a platform that refuses outright is equally unusable
        finally:
            conn.close()
            client.close()
    finally:
        srv.close()


# --------------------------------------------------------------------------
# The gate itself
# --------------------------------------------------------------------------

def _sleeper() -> subprocess.Popen:
    """A live process that is NOT this one and owns no connection to us."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])


def _reap(proc: subprocess.Popen) -> None:
    """Kill AND wait. kill() alone leaves a zombie for the rest of the session,
    which the gate then has to walk on every subsequent scan."""
    try:
        proc.kill()
        proc.wait(timeout=5)
    except Exception:
        pass


def test_gate_refuses_a_peer_outside_the_browser_tree():
    gate = PeerGate("test")
    other = _sleeper()
    try:
        gate.bind_to_process(other.pid)
        # A plausible-looking loopback peer that simply is not that process's.
        assert gate.allows(("127.0.0.1", 65000)) is False
    finally:
        _reap(other)


def test_gate_refuses_a_non_loopback_peer():
    gate = PeerGate("test")
    gate.bind_to_process(os.getpid())
    assert gate.allows(("10.0.0.7", 4444)) is False


def test_gate_refuses_a_malformed_peer():
    gate = PeerGate("test")
    gate.bind_to_process(os.getpid())
    assert gate.allows(None) is False
    assert gate.allows(("127.0.0.1",)) is False


def test_gate_refuses_while_unclaimed(monkeypatch):
    """An unclaimed listener has no legitimate client.

    The wait is shortened here so the test does not pay the real bind grace
    period; the refusal, not the duration, is the behaviour under test.
    """
    monkeypatch.setattr(peerauth, "_BIND_WAIT_SECONDS", 0.2)
    gate = PeerGate("test")
    assert gate.allows(("127.0.0.1", 65000)) is False


def test_gate_allows_a_descendant_of_the_browser(tmp_path):
    """Authorization covers the whole process TREE, not one pid.

    Neither engine connects from the pid the launcher holds: Chromium proxies
    through its network-service child. A gate that only accepted the root pid
    would refuse the real browser.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    listen_port = srv.getsockname()[1]

    # parent -> grandchild, and the GRANDCHILD is what connects
    code = (
        "import subprocess,sys,time\n"
        "g = subprocess.Popen([sys.executable,'-c',"
        f"\"import socket,time;c=socket.create_connection(('127.0.0.1',{listen_port}));time.sleep(6)\"])\n"
        "g.wait()\n"
    )
    parent = subprocess.Popen([sys.executable, "-c", code])
    grandchildren = []
    try:
        conn, peer = srv.accept()
        gate = PeerGate("test")
        gate.set_listen_port(listen_port)
        gate.bind_to_process(parent.pid)
        try:
            import psutil

            grandchildren = psutil.Process(parent.pid).children(recursive=True)
        except Exception:
            grandchildren = []
        assert gate.allows(peer) is True, (
            "the browser's own grandchild was refused — this would break "
            "proxied browsing"
        )
        conn.close()
    finally:
        # Tear down the whole tree: an orphaned grandchild would outlive this
        # test still holding a live loopback connection.
        for gc in grandchildren:
            try:
                gc.kill()
            except Exception:
                pass
        _reap(parent)
        srv.close()


# --------------------------------------------------------------------------
# The SOCKS5 bridge
# --------------------------------------------------------------------------

class RecordingUpstream(threading.Thread):
    """An upstream proxy that records whether it was ever contacted."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.connections = 0
        self._srv = socket.socket()
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(5)
        self._srv.settimeout(0.25)
        self.port = self._srv.getsockname()[1]
        self.url = f"socks5://alice:secret@127.0.0.1:{self.port}"
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except (OSError, socket.timeout):
                continue
            self.connections += 1
            try:
                conn.close()
            except OSError:
                pass

    def stop(self) -> None:
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass


def test_bridge_refuses_a_stranger_and_opens_no_upstream_tunnel():
    """The negative case: a local process that is not the browser gets nothing.

    Asserted on the consequence that matters — the operator's proxy credentials
    are never spent — plus the fact that the bridge does not even answer the
    SOCKS5 greeting, so the refusal leaks nothing about what it protects.
    """
    upstream = RecordingUpstream()
    upstream.start()
    other = _sleeper()
    bridge = ProxyBridge(upstream.url)
    port = bridge.start()
    try:
        # The listener belongs to some OTHER process; this test is the attacker.
        bridge.bind_to_process(other.pid)

        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.sendall(b"\x05\x01\x00")  # SOCKS5 greeting, no-auth
        client.settimeout(5)
        try:
            reply = client.recv(2)
        except (socket.timeout, ConnectionResetError, OSError):
            reply = b""
        client.close()

        assert reply == b"", (
            "the bridge answered the SOCKS5 greeting for an unauthorized caller"
        )
        time.sleep(0.3)
        assert upstream.connections == 0, (
            "the bridge opened an upstream tunnel for an unauthorized caller — "
            "the operator's proxy credentials were spent on it"
        )
    finally:
        bridge.stop()
        _reap(other)
        upstream.stop()


def test_bridge_serves_its_own_browser():
    """The positive case: the authorized client is not refused."""
    upstream = RecordingUpstream()
    upstream.start()
    bridge = ProxyBridge(upstream.url)
    port = bridge.start()
    try:
        bridge.bind_to_process(os.getpid())
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.sendall(b"\x05\x01\x00")
        client.settimeout(5)
        reply = client.recv(2)
        client.close()
        assert reply == b"\x05\x00", (
            "the bridge refused its own browser — proxied browsing would break"
        )
    finally:
        bridge.stop()
        upstream.stop()


def test_bridge_refusal_is_the_mechanism_not_the_test(monkeypatch):
    """Falsification: with the gate forced open, the negative test's assertion
    flips. This binds the test above to the check, so an inert implementation
    cannot pass it."""
    monkeypatch.setattr(PeerGate, "allows", lambda self, peer: True)

    upstream = RecordingUpstream()
    upstream.start()
    other = _sleeper()
    bridge = ProxyBridge(upstream.url)
    port = bridge.start()
    try:
        bridge.bind_to_process(other.pid)
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.sendall(b"\x05\x01\x00")
        client.settimeout(5)
        reply = client.recv(2)
        client.close()
        assert reply == b"\x05\x00", (
            "with the gate disabled the stranger should have been served — "
            "if not, the negative test proves nothing about the gate"
        )
    finally:
        bridge.stop()
        _reap(other)
        upstream.stop()


# --------------------------------------------------------------------------
# The mTLS terminator
# --------------------------------------------------------------------------

def _leaf_and_bogus_client_pem(tmp_path):
    """A real leaf (the Terminator loads it at construction) and a client PEM
    path that does NOT exist.

    The missing client PEM is deliberate: if a refused connection ever reached
    the MITM path, load_cert_chain would raise on it. Reaching the certificate
    at all is the failure this test is about.
    """
    leaf = term.make_leaf("admin.example.com", tmp_path)
    return leaf, str(tmp_path / "does-not-exist-client.pem")


def test_terminator_refuses_a_stranger_before_any_tls(tmp_path):
    """A local process that is not the browser gets no mutual-TLS handshake
    performed with the operator's certificate."""
    leaf, client_pem = _leaf_and_bogus_client_pem(tmp_path)
    other = _sleeper()
    t = term.Terminator(
        "admin.example.com", leaf, client_pem, verify_upstream=False
    )
    port = t.start()
    try:
        t.bind_to_process(other.pid)

        raw = socket.create_connection(("127.0.0.1", port), timeout=5)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        raw.settimeout(5)
        with pytest.raises((ssl.SSLError, OSError, socket.timeout)):
            # The terminator must close without completing a TLS handshake, so
            # the client never gets a session — and the certificate is untouched.
            wrapped = ctx.wrap_socket(raw, server_hostname="admin.example.com")
            wrapped.do_handshake()
            wrapped.close()
        try:
            raw.close()
        except OSError:
            pass
    finally:
        t.stop()
        _reap(other)


def test_terminator_serves_its_own_browser(tmp_path):
    """The positive case: the authorized client completes TLS with the leaf.

    Uses the CONNECT arrival shape (Firefox's path); the Chromium direct-TLS
    path is covered by the terminator's own end-to-end suite.
    """
    leaf = term.make_leaf("admin.example.com", tmp_path)
    client_pem = str(tmp_path / "unused-client.pem")
    t = term.Terminator(
        "admin.example.com", leaf, client_pem, verify_upstream=False
    )
    port = t.start()
    try:
        t.bind_to_process(os.getpid())
        raw = socket.create_connection(("127.0.0.1", port), timeout=5)
        raw.settimeout(5)
        # A CONNECT to a NON-admin host is a plain tunnel: it proves the caller
        # was accepted without involving the client certificate at all.
        raw.sendall(b"CONNECT 127.0.0.1:9 HTTP/1.1\r\n\r\n")
        hdr = b""
        try:
            while b"\r\n\r\n" not in hdr:
                chunk = raw.recv(1024)
                if not chunk:
                    break
                hdr += chunk
        except (socket.timeout, OSError):
            pass
        raw.close()
        assert b"200" in hdr, (
            f"the terminator refused its own browser (got {hdr!r}) — mTLS "
            "browsing would break"
        )
    finally:
        t.stop()


def test_terminator_refusal_is_the_mechanism_not_the_test(tmp_path, monkeypatch):
    """Falsification for the terminator: with the gate forced open, the same
    stranger is accepted and gets a CONNECT response."""
    monkeypatch.setattr(PeerGate, "allows", lambda self, peer: True)

    leaf = term.make_leaf("admin.example.com", tmp_path)
    other = _sleeper()
    t = term.Terminator(
        "admin.example.com", leaf, str(tmp_path / "unused.pem"),
        verify_upstream=False,
    )
    port = t.start()
    try:
        t.bind_to_process(other.pid)
        raw = socket.create_connection(("127.0.0.1", port), timeout=5)
        raw.settimeout(5)
        raw.sendall(b"CONNECT 127.0.0.1:9 HTTP/1.1\r\n\r\n")
        hdr = b""
        try:
            while b"\r\n\r\n" not in hdr:
                chunk = raw.recv(1024)
                if not chunk:
                    break
                hdr += chunk
        except (socket.timeout, OSError):
            pass
        raw.close()
        assert b"200" in hdr, (
            "with the gate disabled the stranger should have been served — "
            "if not, the terminator's negative test proves nothing"
        )
    finally:
        t.stop()
        _reap(other)


# --------------------------------------------------------------------------
# The mechanism switch itself
#
# The gate's verdict was tested above; whether the gate is ON was not. A
# dependency floor one major version too low (`psutil>=5.9`) made
# `Process.net_connections` — which only exists from psutil 6.0.0 — absent on a
# perfectly legal resolution. The probe's bare `except` read that as "this
# platform has no mechanism" and took the degrade-open branch, so both listeners
# served any local caller while the control sat in the tree reporting itself as
# installed. These tests pin the switch, not the verdict.
# --------------------------------------------------------------------------

@pytest.fixture
def clean_probe():
    """The mechanism probe is cached in a module global. Reset it around any
    test that fakes psutil, or the fake leaks into every later test."""
    peerauth._probe_result = None
    yield
    peerauth._probe_result = None


class _ProcessWithoutNetConnections:
    """psutil 5.9.x's Process: it has connections(), NOT net_connections()."""

    def __init__(self, pid=None):
        self.pid = pid or os.getpid()

    def connections(self, kind="tcp"):  # the pre-6.0 spelling
        return []

    def children(self, recursive=False):
        return []


def _fake_psutil(process_cls, version="5.9.8"):
    import types

    return types.SimpleNamespace(Process=process_cls, __version__=version)


def test_declared_psutil_floor_matches_the_api_the_code_calls():
    """The floor and the called API must never drift apart again.

    peerauth calls Process.net_connections(), which landed in psutil 6.0.0.
    Any floor below that is satisfiable by a psutil where the method is missing,
    which silently disables peer verification on both listeners.
    """
    import psutil

    assert hasattr(psutil.Process, "net_connections"), (
        f"the resolved psutil ({psutil.__version__}) has no "
        "Process.net_connections — peer verification cannot be enforced"
    )

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    declared = []
    for rel in ("pyproject.toml", "requirements.txt"):
        path = os.path.join(root, rel)
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip().strip('",')
                if stripped.startswith("psutil"):
                    declared.append((rel, stripped))

    assert declared, "no psutil requirement declared anywhere"
    for rel, spec in declared:
        assert ">=" in spec, f"{rel}: expected a floor constraint, got {spec!r}"
        floor = spec.split(">=", 1)[1].strip()
        major = int(floor.split(".")[0])
        assert major >= 6, (
            f"{rel} declares {spec!r}, but Process.net_connections() requires "
            "psutil >= 6.0. Below that floor the peer-authentication gate "
            "degrades and both loopback listeners stop authenticating callers."
        )


def test_gate_refuses_when_psutil_is_too_old_to_enumerate(clean_probe, monkeypatch):
    """A psutil without net_connections() must NOT become an allow-all.

    This is the exact regression the review caught: the missing method raised,
    the probe cached "no mechanism", and the gate served every stranger. The
    platform here is fully capable — the fault is packaging — so the honest
    answer is to refuse and name the remedy, not to degrade open.
    """
    monkeypatch.setattr(
        peerauth, "_psutil", lambda: _fake_psutil(_ProcessWithoutNetConnections)
    )

    assert peerauth.mechanism_state() == peerauth.MECH_UNUSABLE
    assert peerauth.mechanism_enforceable() is False

    gate = PeerGate("test")
    gate.set_listen_port(9999)
    gate.bind_to_process(os.getpid())

    assert gate.allows(("127.0.0.1", 65000)) is False, (
        "a psutil too old to expose net_connections() silently disabled the "
        "gate — the stranger was served on both listeners"
    )


def test_absent_psutil_still_degrades_open(clean_probe, monkeypatch):
    """The deliberate exception must SURVIVE the fix above.

    Where the mechanism is genuinely absent, refusing would reject the real
    browser and break proxied browsing outright — a worse outcome than the
    exposure on a platform we cannot measure. This is the documented position;
    it is pinned so that tightening the too-old case never quietly removes it.
    """
    monkeypatch.setattr(peerauth, "_psutil", lambda: None)

    assert peerauth.mechanism_state() == peerauth.MECH_ABSENT

    gate = PeerGate("test")
    gate.bind_to_process(os.getpid())
    assert gate.allows(("127.0.0.1", 65000)) is True


def test_degraded_gate_still_requires_a_claimed_listener(clean_probe, monkeypatch):
    """Even with no mechanism at all, a listener nobody claimed serves nobody.

    Defense in depth: it narrows the degraded window to the browser's actual
    lifetime instead of leaving the listener open from the moment it binds.

    The bind wait is shortened here rather than paid in full: the refusal is the
    behaviour under test, not the duration.
    """
    monkeypatch.setattr(peerauth, "_psutil", lambda: None)
    monkeypatch.setattr(peerauth, "_BIND_WAIT_SECONDS", 0.2)

    gate = PeerGate("test")  # never bound to a process
    assert gate.allows(("127.0.0.1", 65000)) is False


def test_transiently_failed_probe_is_not_cached(clean_probe, monkeypatch):
    """A probe that merely FAILED is not evidence about the installation.

    ``net_connections()`` can raise transiently — fd pressure, a momentarily
    unreadable ``/proc`` entry under load. Caching that answer burns
    "no mechanism" in for the whole process lifetime, so a single blip at the
    wrong moment silently downgrades BOTH listeners to degrade-open on a machine
    that can enforce the boundary perfectly well, for as long as the app runs.

    The distinction under test is between the two returns of ``_probe_mechanism``:
    a failed probe answers for THIS connection only and must be re-probed, while
    a settled property of the installation is cached. Collapsing them is
    invisible on a green suite, which is why it is pinned here.
    """
    calls = {"n": 0}

    class _FlakyProcess:
        def __init__(self, pid=None):
            self.pid = pid or os.getpid()

        def net_connections(self, kind="tcp"):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("transient: too many open files")
            return []

        def children(self, recursive=False):
            return []

    monkeypatch.setattr(
        peerauth, "_psutil", lambda: _fake_psutil(_FlakyProcess, version="6.1.0")
    )

    # The blip: this connection degrades open rather than falsely refusing the
    # real browser. That part is the endorsed behaviour and is not the defect.
    assert peerauth.mechanism_state() == peerauth.MECH_ABSENT
    assert peerauth._probe_result is None, (
        "a transiently-failed probe was cached — one momentary failure now "
        "disables peer verification on both listeners for the entire process "
        "lifetime, on a machine that is fully capable of enforcing it"
    )

    # The blip passes. The very next question must be answered by a fresh probe.
    assert peerauth.mechanism_state() == peerauth.MECH_ENFORCEABLE
    assert peerauth.mechanism_enforceable() is True
    assert peerauth._probe_result == peerauth.MECH_ENFORCEABLE, (
        "the recovered, definitive answer was not cached"
    )


def test_gate_re_enforces_after_a_transient_probe_failure(clean_probe, monkeypatch):
    """The CONSEQUENCE of the test above, asserted on the gate's verdict.

    The caching distinction only matters because of what it does to callers: a
    stranger served forever after one blip. So this asserts the security outcome
    rather than the module global — after the transient failure clears, the
    stranger is refused again.
    """
    calls = {"n": 0}

    class _FlakyProcess:
        def __init__(self, pid=None):
            self.pid = pid or os.getpid()

        def net_connections(self, kind="tcp"):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("transient: too many open files")
            return []  # resolvable, and the tree owns no socket to us

        def children(self, recursive=False):
            return []

    monkeypatch.setattr(
        peerauth, "_psutil", lambda: _fake_psutil(_FlakyProcess, version="6.1.0")
    )
    monkeypatch.setattr(peerauth, "_SCAN_ATTEMPTS", 2)
    monkeypatch.setattr(peerauth, "_SCAN_BACKOFF_SECONDS", 0.0)

    gate = PeerGate("test")
    gate.set_listen_port(9999)
    gate.bind_to_process(os.getpid())

    # During the blip: degrade open, as endorsed — a false refusal here would
    # break the real browser.
    assert gate.allows(("127.0.0.1", 65000)) is True

    # After it: the gate must be enforcing again. If the failed probe had been
    # cached, this stranger would be served for the rest of the session.
    assert gate.allows(("127.0.0.1", 65000)) is False, (
        "the gate stayed degraded-open after a transient probe failure — a "
        "single blip permanently disabled peer authentication on this listener"
    )


def test_definitive_probe_answers_are_cached_once(clean_probe, monkeypatch):
    """The other half of the distinction: a settled answer is probed ONCE.

    Without this, "never cache anything" would pass the tests above while
    re-probing on every accepted connection. Pinned so the fix cannot be
    satisfied by simply deleting the cache.
    """
    probes = {"n": 0}
    real_probe = peerauth._probe_mechanism

    def counting_probe():
        probes["n"] += 1
        return real_probe()

    monkeypatch.setattr(peerauth, "_probe_mechanism", counting_probe)

    first = peerauth.mechanism_state()
    for _ in range(5):
        assert peerauth.mechanism_state() == first
    assert probes["n"] == 1, (
        f"a definitive mechanism answer was re-probed {probes['n']} times — it "
        "is a property of the installation, not of the connection"
    )


def test_degraded_gate_serves_its_own_browser_without_delay(clean_probe, monkeypatch):
    """A CLAIMED listener answers the degraded path immediately.

    The bind wait only applies to a listener nobody claimed, so the legitimate
    client never pays it. Pinned because the cost of getting this wrong is a
    20s stall in front of the real browser rather than a visible failure.
    """
    monkeypatch.setattr(peerauth, "_psutil", lambda: None)

    gate = PeerGate("test")
    gate.bind_to_process(os.getpid())

    started = time.monotonic()
    verdict = gate.allows(("127.0.0.1", 65000))
    elapsed = time.monotonic() - started

    assert verdict is True
    assert elapsed < 1.0, (
        f"the claimed degraded path blocked for {elapsed:.1f}s — the real "
        "browser should never wait on the bind grace period"
    )


# --------------------------------------------------------------------------
# Scan coalescing (PS-199)
#
# The gate's cost is O(processes x system connection table), not O(sockets the
# tree owns): every Process.net_connections() re-reads the whole table and
# filters it to one pid. Measured on a 12-process / 240-socket tree, one scan
# costs ~95-127ms, and 64 concurrent connections through the real bridge each
# paid their own — median 1.9s, p95 3.2s. Every one was ADMITTED, so the defect
# is a stall, not a refusal.
#
# The remedy shares one scan between callers that can honestly use it. These
# tests pin the freshness rule that keeps that from being a weakening, because
# the cheap version of this fix (a TTL cache) is a real security regression: a
# local port is closed and REUSED within seconds, so answering from a remembered
# endpoint set eventually admits a caller that is not the browser.
# --------------------------------------------------------------------------

def test_a_scan_that_started_before_the_caller_arrived_is_never_reused():
    """THE security property of coalescing, stated as a test.

    A waiter may only be handed a scan that STARTED at or after its own arrival
    — exactly the freshness an inline _tree_endpoints() call had. A completed
    scan that began earlier must NOT answer a caller that arrived later, because
    the peer's port may have been closed and reassigned to another process in
    between. If this ever passes with one scan, the coalescer has become a cache
    and the gate now admits on stale evidence.
    """
    coalescer = peerauth._ScanCoalescer()
    calls: list[float] = []

    def scanner(root_pid):
        calls.append(time.monotonic())
        return {(1234, 9999)}

    first_arrival = time.monotonic()
    coalescer.scan(4242, first_arrival, scanner=scanner)
    assert len(calls) == 1

    # A caller that arrives strictly AFTER the first scan completed.
    time.sleep(0.01)
    later_arrival = time.monotonic()
    coalescer.scan(4242, later_arrival, scanner=scanner)

    assert len(calls) == 2, (
        "a caller was answered from a scan that finished before it arrived — "
        "the coalescer has degraded into a TTL cache, and a reused local port "
        "now admits a process that is not the browser"
    )


def test_concurrent_callers_share_one_scan_instead_of_one_each():
    """The actual fix, asserted as the consequence it exists for.

    This is what turns a page issuing many parallel requests from "each request
    pays a full process-table walk" into "they share one". Asserting merely that
    the gate still admits would not distinguish the fixed state from the broken
    one, so this counts SCANS.
    """
    coalescer = peerauth._ScanCoalescer()
    calls: list[float] = []
    lock = threading.Lock()

    def scanner(root_pid):
        with lock:
            calls.append(time.monotonic())
        time.sleep(0.2)          # a scan is expensive; that is the premise
        return {(1234, 9999)}

    n = 16
    barrier = threading.Barrier(n)
    results: list[object] = [None] * n

    def caller(i: int) -> None:
        barrier.wait()           # all arrive together, as a page's requests do
        results[i] = coalescer.scan(4242, time.monotonic(), scanner=scanner)

    threads = [threading.Thread(target=caller, args=(i,)) for i in range(n)]
    started = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    elapsed = time.monotonic() - started

    assert all(r == {(1234, 9999)} for r in results), (
        "a coalesced caller got the wrong endpoint set"
    )
    assert len(calls) < n, (
        f"{len(calls)} scans ran for {n} simultaneous callers — they are not "
        "being coalesced, which is the whole defect PS-199 measured"
    )
    # n serialized 0.2s scans would be ~3.2s.
    assert elapsed < n * 0.2, (
        f"{n} callers took {elapsed:.2f}s — no better than one scan each"
    )


def test_a_process_spawned_after_the_gate_was_bound_is_still_admitted():
    """Pins the hypothesis PS-199 led with, which measurement REFUTED.

    The ticket proposed that the guard computes its notion of "the browser and
    its children" once, so a tab opened later is refused. It does not: the tree
    is re-resolved per connection. This is pinned because coalescing is exactly
    the kind of change that COULD introduce the staleness the ticket feared, and
    a regression here would present to a user as a new tab that cannot connect.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    listen_port = srv.getsockname()[1]

    gate = PeerGate("test")
    gate.set_listen_port(listen_port)
    gate.bind_to_process(os.getpid())      # bound BEFORE the child exists

    # Warm the coalescer, so any cached answer predates the child entirely.
    gate.allows(("127.0.0.1", 65000))

    child = subprocess.Popen(
        [sys.executable, "-c",
         f"import socket,time;c=socket.create_connection(('127.0.0.1',{listen_port}));time.sleep(6)"]
    )
    try:
        conn, peer = srv.accept()
        try:
            assert gate.allows(peer) is True, (
                "a process spawned after the gate was bound was refused — this "
                "is the new-tab failure PS-199 describes, now real"
            )
        finally:
            conn.close()
    finally:
        _reap(child)
        srv.close()


def test_coalesced_gate_still_refuses_a_peer_outside_the_tree_and_names_why():
    """Coalescing must not soften the refusal, and the log must stay diagnostic.

    Two assertions, and the second is not decoration: the two refusal paths mean
    different things on a user's machine. "does not belong to ... or any of its
    children" says the tree was read and the peer genuinely was not in it, while
    "could not resolve the sockets of" says enumeration itself failed — the
    Windows-specific failure mode. Telling them apart in a log is how the next
    person diagnoses a report from a platform they cannot run.
    """
    import logging

    gate = PeerGate("test")
    other = _sleeper()
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Capture()
    peerauth.logger.addHandler(handler)
    try:
        gate.bind_to_process(other.pid)
        assert gate.allows(("127.0.0.1", 65000)) is False
    finally:
        peerauth.logger.removeHandler(handler)
        _reap(other)

    joined = "\n".join(records)
    assert "does not belong to" in joined, (
        f"the refusal did not name WHICH check failed; logged: {joined!r}"
    )
    assert "could not resolve the sockets" not in joined, (
        "the tree was readable, so the enumeration-failure refusal must not be "
        "the one that fired — these two are not interchangeable"
    )


def test_a_scanner_that_raises_does_not_wedge_later_callers():
    """A failed scan must clear its in-flight marker.

    If it did not, the tree would stay marked "a scan is running" forever and
    every subsequent connection would block waiting for a result that will never
    arrive — converting a transient enumeration error into a permanently dead
    listener.
    """
    coalescer = peerauth._ScanCoalescer()

    def boom(root_pid):
        raise OSError("enumeration failed")

    assert coalescer.scan(4242, time.monotonic(), scanner=boom) is None

    def works(root_pid):
        return {(7, 8)}

    started = time.monotonic()
    assert coalescer.scan(4242, time.monotonic(), scanner=works) == {(7, 8)}
    assert time.monotonic() - started < 5, (
        "a caller after a failed scan blocked — the in-flight marker leaked"
    )


def test_a_scanner_that_raises_a_baseexception_does_not_mask_it_or_lose_the_wakeup():
    """The BaseException boundary, which `except Exception` does not cover.

    KeyboardInterrupt / SystemExit / CancelledError are not Exceptions. Before
    this was fixed, one out of the scanner skipped the handler, left `result`
    unbound, and the finally clause raised UnboundLocalError — three separate
    harms, and this test pins all three:

    1. The ORIGINAL exception must survive. A Ctrl-C during a scan is a routine
       desktop shutdown path; reporting it as UnboundLocalError turns an
       ordinary quit into what looks like a bug in this module.
    2. notify_all() must still run, or every ALREADY-BLOCKED waiter falls
       through to the full _WAIT_SLICE_SECONDS poll instead of being woken —
       a second of added latency in a change whose purpose is removing it.
    3. The failed scan must still be published, so the next caller is not
       forced to redo work that already completed.
    """
    coalescer = peerauth._ScanCoalescer()
    root = 4242

    # The waiter must be blocked AT THE MOMENT the BaseException lands — it is
    # the one that loses its wakeup — so the raising scan is held open until the
    # waiter is demonstrably parked on the condition.
    in_scan = threading.Event()
    waiter_parked = threading.Event()
    woke_at: list[float] = []

    def waiter():
        in_scan.wait(10)                  # only contend once a scan is running
        waiter_parked.set()
        coalescer.scan(root, time.monotonic(), scanner=lambda r: {(3, 4)})
        woke_at.append(time.monotonic())

    blocked = threading.Thread(target=waiter, daemon=True)
    blocked.start()

    raised_at: list[float] = []

    def interrupt(root_pid):
        in_scan.set()
        waiter_parked.wait(10)
        time.sleep(0.2)                   # let the waiter reach _cond.wait()
        raised_at.append(time.monotonic())
        raise KeyboardInterrupt("user quit mid-scan")

    with pytest.raises(KeyboardInterrupt):
        coalescer.scan(root, time.monotonic(), scanner=interrupt)

    blocked.join(timeout=10)
    assert woke_at, "the blocked waiter never returned at all"
    assert woke_at[0] - raised_at[0] < coalescer._WAIT_SLICE_SECONDS, (
        "a blocked waiter was not woken and had to time out its poll slice — "
        "notify_all() was skipped, which is the latency this change removes"
    )
    assert root in coalescer._done, (
        "the failed scan was never published, so its work is discarded and the "
        "next caller starts over"
    )
    assert not coalescer._running, (
        "the in-flight marker leaked after a BaseException"
    )


def test_a_wedged_scan_does_not_stall_every_other_caller_for_that_tree():
    """The blast radius of sharing, bounded.

    Before coalescing, a scan that blocked hurt exactly ONE connection. Sharing
    a scan shares its slowness too, so without a bound this change would convert
    a single stuck enumeration into a stalled listener — precisely the stall
    PS-199 is about. _tree_endpoints has no timeout and is the operation most
    likely to block on a stuck /proc read, or on GetExtendedTcpTable on the
    Windows path this ticket concerns.

    Past _SHARE_TIMEOUT_SECONDS a caller stops waiting and scans independently,
    which is exactly what it did before this class existed.
    """
    coalescer = peerauth._ScanCoalescer()
    coalescer._SHARE_TIMEOUT_SECONDS = 0.3      # keep the test quick
    wedge = threading.Event()

    def hangs(root_pid):
        wedge.wait(30)                           # a scan stuck in the kernel
        return {(1, 2)}

    threading.Thread(
        target=lambda: coalescer.scan(99, time.monotonic(), scanner=hangs),
        daemon=True,
    ).start()
    time.sleep(0.1)

    try:
        started = time.monotonic()
        result = coalescer.scan(99, time.monotonic(), scanner=lambda r: {(3, 4)})
        elapsed = time.monotonic() - started
    finally:
        wedge.set()

    assert result == {(3, 4)}, (
        "the fallback caller was answered from the wedged scan rather than its "
        "own — a wedged scan must not be able to answer anyone"
    )
    assert elapsed < 5, (
        f"a caller blocked {elapsed:.1f}s behind ONE wedged scan — sharing has "
        "turned a per-connection slowness into an all-connections stall"
    )


def test_completed_scans_do_not_accumulate_for_trees_that_are_gone():
    """Retention is bounded, because a dead pid's endpoint set buys nothing.

    A completed scan can only ever answer a waiter that is ALREADY blocked (a
    caller arriving later needs a scan that started after IT did), so retaining
    one per pid that has since exited is a pure leak: every profile launched in
    an app session left a permanent entry holding a full endpoint set.
    """
    coalescer = peerauth._ScanCoalescer()
    big = {(i, i + 1) for i in range(240)}       # a browser-sized tree

    for pid in range(200):
        coalescer.scan(pid, time.monotonic(), scanner=lambda r: big)

    assert len(coalescer._done) <= coalescer._MAX_RETAINED_TREES, (
        f"{len(coalescer._done)} completed scans retained after 200 distinct "
        "trees — dead pids are never evicted"
    )


def test_binding_a_gate_forgets_any_scan_remembered_for_that_pid():
    """A pid is REUSED by the OS, and that is a freshness hole, not tidiness.

    If a scan for a previous tree that happened to hold this pid survived the
    bind, a waiter on the NEW tree could be answered by an endpoint set that
    describes a completely different process.
    """
    stale = {(1234, 9999)}
    peerauth._scan_coalescer.scan(
        os.getpid(), time.monotonic(), scanner=lambda r: stale
    )
    assert os.getpid() in peerauth._scan_coalescer._done

    PeerGate("test").bind_to_process(os.getpid())

    assert os.getpid() not in peerauth._scan_coalescer._done, (
        "a scan for a previous tree survived a re-bind of the same pid — it "
        "can now answer for a different process"
    )
