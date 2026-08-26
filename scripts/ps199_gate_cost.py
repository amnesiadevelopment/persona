"""PS-199 E1/E3: measure the peer gate's REAL cost and its verdict on a
newly-spawned child.

Two questions, both measured rather than reasoned about:

E3 (the ticket's Lead A): is a process spawned AFTER the gate was bound
    admitted? The researcher's source read says yes (the scan is live). This
    measures it end-to-end instead of re-reading the code.

E1 (my lead): what does ONE admit COST, and how does that scale with the size
    of the process tree and the number of sockets it owns? The gate rescans the
    WHOLE tree on every attempt, up to _SCAN_ATTEMPTS times, per connection.

Run:  .venv/bin/python scripts/ps199_gate_cost.py
"""

from __future__ import annotations

import os
import socket
import statistics
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import peerauth  # noqa: E402
from src.core.peerauth import PeerGate  # noqa: E402


#: Accepted sockets, kept alive for the life of the process. See _sink().
_HELD: list[list[socket.socket]] = []


def _sink() -> tuple[socket.socket, int]:
    """A listener that accepts and holds, so connections to it stay ESTABLISHED
    and therefore appear in the per-process socket table."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(128)
    held: list[socket.socket] = []

    def _accept() -> None:
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            held.append(c)

    threading.Thread(target=_accept, daemon=True).start()
    # NOTE: a `socket` has no __dict__, so the accepted sockets cannot be
    # parked on it. Keep them alive in a module-level list instead — if they
    # are garbage-collected the connections close and vanish from the socket
    # table, which would silently shrink the very thing being measured.
    _HELD.append(held)
    return srv, srv.getsockname()[1]


CHILD = r"""
import socket, sys, time
port = int(sys.argv[1]); n = int(sys.argv[2])
socks = []
for _ in range(n):
    s = socket.create_connection(("127.0.0.1", port))
    socks.append(s)
print("READY", flush=True)
time.sleep(3600)
"""


def measure(n_children: int, socks_per_child: int, trials: int = 7) -> dict:
    """Spawn a tree of n_children, each holding socks_per_child connections,
    then time _tree_endpoints against it."""
    srv, port = _sink()
    kids = []
    try:
        for _ in range(n_children):
            p = subprocess.Popen(
                [sys.executable, "-c", CHILD, str(port), str(socks_per_child)],
                stdout=subprocess.PIPE, text=True,
            )
            assert p.stdout is not None
            p.stdout.readline()  # wait for READY
            kids.append(p)

        times = []
        size = None
        for _ in range(trials):
            t0 = time.perf_counter()
            eps = peerauth._tree_endpoints(os.getpid())
            times.append((time.perf_counter() - t0) * 1000.0)
            size = len(eps) if eps is not None else None
        return {
            "children": n_children,
            "socks_per_child": socks_per_child,
            "total_socks": n_children * socks_per_child,
            "endpoints": size,
            "median_ms": statistics.median(times),
            "max_ms": max(times),
        }
    finally:
        for p in kids:
            p.kill()
            p.wait()
        srv.close()


def e3_new_child_admitted() -> None:
    """Lead A, measured: bind the gate FIRST, spawn a child AFTER, and ask the
    gate about that child's connection."""
    print("\n=== E3: is a child spawned AFTER bind_to_process admitted? ===")
    srv, port = _sink()
    gate = PeerGate("e3")
    gate.set_listen_port(port)
    gate.bind_to_process(os.getpid())   # bind BEFORE the child exists
    time.sleep(0.2)

    kid = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(port), "1"],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert kid.stdout is not None
        kid.stdout.readline()
        # Find the child's connection to our sink and ask the gate about it.
        import psutil
        peer_port = None
        for c in psutil.Process(kid.pid).net_connections(kind="tcp"):
            if c.raddr and c.raddr.port == port:
                peer_port = c.laddr.port
                break
        if peer_port is None:
            print("  INCONCLUSIVE: could not find the child's socket")
            return
        t0 = time.perf_counter()
        verdict = gate.allows(("127.0.0.1", peer_port))
        dt = (time.perf_counter() - t0) * 1000.0
        print(f"  child pid={kid.pid} spawned AFTER bind, peer port={peer_port}")
        print(f"  gate.allows -> {verdict}   ({dt:.1f} ms)")
        print("  => Lead A (stale process set) is", "REFUTED" if verdict else "SUPPORTED")
    finally:
        kid.kill()
        kid.wait()
        srv.close()


def main() -> None:
    print("platform:", sys.platform, "| mechanism:", peerauth.mechanism_state())
    import psutil
    print("psutil:", psutil.__version__)

    e3_new_child_admitted()

    print("\n=== E1: cost of ONE _tree_endpoints scan ===")
    print(f"{'children':>9} {'socks':>7} {'endpoints':>10} {'median ms':>10} {'max ms':>9}")
    for kids, socks in [(0, 0), (1, 5), (4, 5), (8, 10), (16, 20), (24, 40)]:
        r = measure(kids, socks)
        print(f"{r['children']:>9} {r['total_socks']:>7} {str(r['endpoints']):>10} "
              f"{r['median_ms']:>10.1f} {r['max_ms']:>9.1f}")

    print("\n  NOTE: allows() runs this up to "
          f"{peerauth._SCAN_ATTEMPTS} times per connection "
          f"(backoff {peerauth._SCAN_BACKOFF_SECONDS}s), so a REFUSAL costs "
          f"~{peerauth._SCAN_ATTEMPTS} scans + "
          f"{(peerauth._SCAN_ATTEMPTS - 1) * peerauth._SCAN_BACKOFF_SECONDS:.2f}s of sleep.")


if __name__ == "__main__":
    main()
