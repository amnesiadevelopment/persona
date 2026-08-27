"""PS-199 E4: drive the REAL ProxyBridge with a burst of concurrent clients and
see what a caller actually experiences.

E2 measured the gate in isolation. This measures the thing the owner touches:
connections through the real bridge, with a real SOCKS5 upstream, while a
realistic browser-sized process tree makes each peer scan expensive.

The question this answers, and it is the one the ticket turns on:
under burst, is a legitimate connection REFUSED (=> "unable to connect") or
merely SLOW (=> a page that never finishes loading)? Those are the ticket's two
symptoms, and a single mechanism producing both would settle "one defect or
two".

Run:  .venv/bin/python scripts/ps199_bridge_burst.py [n_concurrent]
"""

from __future__ import annotations

import os
import socket
import statistics
import struct
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import peerauth  # noqa: E402
from src.services.proxy.bridge import ProxyBridge  # noqa: E402

# What a browser is willing to wait for a local proxy before giving up. Firefox
# does not wait forever on a proxy that has accepted the TCP connection but is
# not speaking; the point of the measurement is where the gate's latency lands
# relative to a bound like this.
CLIENT_TIMEOUT = 10.0


class ConcurrentUpstream(threading.Thread):
    """A SOCKS5 upstream that serves MANY connections at once.

    Deliberately trivial and fast: it must not be the bottleneck, or the
    measurement would be about this harness rather than about the gate.
    """

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(512)
        self.port = self.srv.getsockname()[1]
        self.url = f"socks5://alice:secret@127.0.0.1:{self.port}"
        self.served = 0
        self._lock = threading.Lock()

    def run(self) -> None:
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(30)
            n = conn.recv(2)[1] if False else None  # placeholder, real read below
        except Exception:
            pass
        try:
            # greeting: ver, nmethods, methods
            hdr = _recvn(conn, 2)
            if len(hdr) < 2:
                return
            _recvn(conn, hdr[1])
            conn.sendall(b"\x05\x02")           # choose user/pass
            # auth: ver, ulen, user, plen, pass
            a = _recvn(conn, 2)
            if len(a) < 2:
                return
            _recvn(conn, a[1])
            plen = _recvn(conn, 1)
            _recvn(conn, plen[0])
            conn.sendall(b"\x01\x00")           # auth ok
            # request: ver cmd rsv atyp ...
            r = _recvn(conn, 4)
            if len(r) < 4:
                return
            atyp = r[3]
            if atyp == 3:
                ln = _recvn(conn, 1)[0]
                _recvn(conn, ln)
            elif atyp == 1:
                _recvn(conn, 4)
            _recvn(conn, 2)
            conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            with self._lock:
                self.served += 1
            time.sleep(2)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass


def _recvn(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        try:
            chunk = sock.recv(n - len(data))
        except Exception:
            return data
        if not chunk:
            return data
        data += chunk
    return data


CHILD = r"""
import socket, sys, time
port = int(sys.argv[1]); n = int(sys.argv[2])
socks = [socket.create_connection(("127.0.0.1", port)) for _ in range(n)]
print("READY", flush=True)
time.sleep(3600)
"""

_HELD: list[list[socket.socket]] = []


def _sink() -> tuple[socket.socket, int]:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(256)
    held: list[socket.socket] = []

    def _accept() -> None:
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            held.append(c)

    threading.Thread(target=_accept, daemon=True).start()
    _HELD.append(held)
    return srv, srv.getsockname()[1]


def one_client(bridge_port: int) -> tuple[str, float]:
    """One full SOCKS5 negotiation through the bridge.

    Returns (outcome, seconds). Outcome is 'ok', 'refused' (the bridge closed
    the connection without answering — what the peer gate does on a refusal),
    'timeout', or 'error:...'.
    """
    t0 = time.perf_counter()
    try:
        s = socket.create_connection(("127.0.0.1", bridge_port), timeout=CLIENT_TIMEOUT)
    except Exception as exc:
        return f"connect-error:{type(exc).__name__}", time.perf_counter() - t0
    try:
        s.settimeout(CLIENT_TIMEOUT)
        s.sendall(b"\x05\x01\x00")
        resp = _recvn(s, 2)
        if len(resp) < 2:
            # The bridge accepted the TCP connection and then closed it without
            # a greeting. That is exactly the peer gate's refusal shape.
            return "refused", time.perf_counter() - t0
        req = b"\x05\x01\x00\x03" + bytes([len("example.com")]) + b"example.com" + struct.pack("!H", 443)
        s.sendall(req)
        rep = _recvn(s, 10)
        if len(rep) < 2:
            return "refused-after-greeting", time.perf_counter() - t0
        return ("ok" if rep[1] == 0 else f"reply:{rep[1]}"), time.perf_counter() - t0
    except socket.timeout:
        return "timeout", time.perf_counter() - t0
    except Exception as exc:
        return f"error:{type(exc).__name__}", time.perf_counter() - t0
    finally:
        try:
            s.close()
        except OSError:
            pass


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 64
    cpu = os.cpu_count() or 1
    print(f"platform={sys.platform} mechanism={peerauth.mechanism_state()} cpu={cpu}")
    print(f"asyncio.to_thread default pool = min(32, cpu+4) = {min(32, cpu + 4)} threads")
    print(f"client timeout = {CLIENT_TIMEOUT}s, concurrent clients = {n}")

    sink, sink_port = _sink()
    # A browser-sized tree so each peer scan costs what it costs in the field.
    kids = []
    for _ in range(12):
        p = subprocess.Popen(
            [sys.executable, "-c", CHILD, str(sink_port), "20"],
            stdout=subprocess.PIPE, text=True,
        )
        assert p.stdout is not None
        p.stdout.readline()
        kids.append(p)
    print(f"process tree: {len(kids)} children x 20 sockets")

    t0 = time.perf_counter()
    peerauth._tree_endpoints(os.getpid())
    print(f"ONE peer scan against this tree: {(time.perf_counter() - t0) * 1000:.0f} ms")

    up = ConcurrentUpstream()
    up.start()
    bridge = ProxyBridge(up.url)
    port = bridge.start()
    bridge.bind_to_process(os.getpid())   # our own sockets => the ADMIT path

    try:
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=n) as ex:
            results = list(ex.map(lambda _: one_client(port), range(n)))
        wall = time.perf_counter() - t0

        outcomes: dict[str, int] = {}
        for kind, _ in results:
            outcomes[kind] = outcomes.get(kind, 0) + 1
        lat = sorted(d for _, d in results)

        print(f"\n=== {n} concurrent connections through the REAL bridge ===")
        print(f"wall clock      : {wall:.2f} s")
        print(f"outcomes        : {outcomes}")
        print(f"latency median  : {statistics.median(lat):.2f} s")
        print(f"latency p95     : {lat[int(len(lat) * 0.95) - 1]:.2f} s")
        print(f"latency max     : {max(lat):.2f} s")
        print(f"upstream served : {up.served}/{n}")
        bad = sum(v for k, v in outcomes.items() if k != "ok")
        print(f"\nNOT-ok          : {bad}/{n}")
        if bad:
            print("  => a legitimate caller was refused or timed out under burst.")
        else:
            print("  => all admitted, but read the latency: a page issuing many")
            print("     requests pays this per connection.")
    finally:
        bridge.stop()
        for p in kids:
            p.kill()
            p.wait()
        sink.close()


if __name__ == "__main__":
    main()
