"""PS-199 E2: what does the peer gate COST, and does that cost explain a stall?

E1 showed one scan's cost rising with the tree. This isolates WHY and asks the
question that matters for the owner's symptom: what happens when MANY
connections are gated at once, which is exactly what a heavy multi-request page
does.

Three measurements:

  A. Isolate P from T. `_tree_endpoints` calls net_connections() once per
     process in the tree, and EACH of those calls parses the whole system TCP
     table (Linux: /proc/net/tcp per process; Windows: GetExtendedTcpTable per
     process). If that is true the cost is O(P x T), not O(sockets owned) — so
     adding processes that own NO sockets must still slow the scan down.

  B. Concurrency. The bridge gates on asyncio.to_thread (a bounded pool); the
     terminator gates on a thread per connection. Either way N simultaneous
     connections each run their own full scan. Measure wall-clock for N
     concurrent admits.

  C. The refusal cost, which is the worst case: _SCAN_ATTEMPTS full scans plus
     the backoff sleeps, paid before the caller is told no.

Run:  .venv/bin/python scripts/ps199_gate_concurrency.py
"""

from __future__ import annotations

import os
import socket
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import peerauth  # noqa: E402
from src.core.peerauth import PeerGate  # noqa: E402

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


# A child that holds `n` connections. n=0 => a process owning NO sockets.
CHILD = r"""
import socket, sys, time
port = int(sys.argv[1]); n = int(sys.argv[2])
socks = [socket.create_connection(("127.0.0.1", port)) for _ in range(n)]
print("READY", flush=True)
time.sleep(3600)
"""


class Tree:
    """A process tree of `n` children, each holding `socks` connections."""

    def __init__(self, n: int, socks: int, port: int) -> None:
        self.kids: list[subprocess.Popen] = []
        for _ in range(n):
            p = subprocess.Popen(
                [sys.executable, "-c", CHILD, str(port), str(socks)],
                stdout=subprocess.PIPE, text=True,
            )
            assert p.stdout is not None
            p.stdout.readline()
            self.kids.append(p)

    def close(self) -> None:
        for p in self.kids:
            p.kill()
            p.wait()


def scan_ms(trials: int = 5) -> float:
    times = []
    for _ in range(trials):
        t0 = time.perf_counter()
        peerauth._tree_endpoints(os.getpid())
        times.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(times)


def part_a(port: int) -> None:
    print("\n=== A. Is the cost O(processes x system table), not O(own sockets)? ===")
    print("   Children own ZERO sockets, so any slowdown is per-PROCESS overhead.")
    print(f"{'children':>9} {'own socks':>10} {'scan ms':>9}")
    base = None
    for n in (0, 4, 8, 16, 32):
        t = Tree(n, 0, port) if n else None
        try:
            ms = scan_ms()
            if base is None:
                base = ms
            print(f"{n:>9} {0:>10} {ms:>9.1f}")
        finally:
            if t:
                t.close()
    print("   => a rising column with ZERO sockets owned proves the per-process")
    print("      call re-parses the whole table: cost is O(P x T).")


def part_b(port: int) -> None:
    print("\n=== B. N concurrent connections, each gated independently ===")
    # A realistic mid-load browser tree: 12 processes, 240 sockets.
    tree = Tree(12, 20, port)
    try:
        gate = PeerGate("e2")
        gate.set_listen_port(port)
        gate.bind_to_process(os.getpid())
        one = scan_ms()
        print(f"   tree = 12 procs / 240 socks; ONE scan = {one:.1f} ms")
        print(f"{'concurrent':>11} {'wall ms':>9} {'per-conn ms':>12}")
        for n in (1, 8, 32, 64):
            # Gate n peer ports that are NOT in the tree would cost the refusal
            # path; use ports that ARE, to measure the ADMIT path (best case).
            import psutil
            live = []
            for kid in tree.kids:
                for c in psutil.Process(kid.pid).net_connections(kind="tcp"):
                    if c.raddr and c.raddr.port == port:
                        live.append(c.laddr.port)
            if not live:
                print("   INCONCLUSIVE: no live peer ports found")
                return
            peers = [("127.0.0.1", live[i % len(live)]) for i in range(n)]
            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=min(32, (os.cpu_count() or 1) + 4)) as ex:
                list(ex.map(gate.allows, peers))
            wall = (time.perf_counter() - t0) * 1000.0
            print(f"{n:>11} {wall:>9.1f} {wall / n:>12.1f}")
        print("   => each connection pays its own full scan; they contend for CPU.")
    finally:
        tree.close()


def part_c(port: int) -> None:
    print("\n=== C. The REFUSAL cost (worst case, paid before the caller is told no) ===")
    tree = Tree(12, 20, port)
    try:
        gate = PeerGate("e2-refuse")
        gate.set_listen_port(port)
        gate.bind_to_process(os.getpid())
        # A port that is certainly not in the tree.
        t0 = time.perf_counter()
        verdict = gate.allows(("127.0.0.1", 65530))
        dt = (time.perf_counter() - t0) * 1000.0
        print(f"   refusal verdict={verdict} took {dt:.0f} ms "
              f"({peerauth._SCAN_ATTEMPTS} scans + "
              f"{(peerauth._SCAN_ATTEMPTS - 1) * peerauth._SCAN_BACKOFF_SECONDS:.2f}s backoff)")
    finally:
        tree.close()


def main() -> None:
    print("platform:", sys.platform, "| mechanism:", peerauth.mechanism_state())
    srv, port = _sink()
    try:
        part_a(port)
        part_b(port)
        part_c(port)
    finally:
        srv.close()


if __name__ == "__main__":
    main()
