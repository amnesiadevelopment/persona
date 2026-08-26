"""Answer ONE question for a loopback listener: is the process on the other end
of this accepted connection the browser this listener was started for?

Why this exists
---------------
persona stands up two local listeners per proxied launch, and both act with the
operator's authority once a client connects:

* the SOCKS5 bridge (``services/proxy/bridge.py``) re-applies the operator's
  proxy username/password on the way out, so any local caller got free
  authenticated egress through a paid exit that bills to the operator; and
* the mTLS terminator (``services/cert/terminator.py``) performs the mutual-TLS
  handshake to the admin host with the operator's client certificate, so any
  local caller got that handshake performed on its behalf.

Neither asked the caller for anything. The ephemeral random port is NOT a
control — any local process can enumerate loopback listeners — so it is not
counted as mitigation here.

Why the caller is VERIFIED and not CHALLENGED
---------------------------------------------
Neither listener can demand a credential:

* Chromium's ``--proxy-server`` cannot carry SOCKS5 username/password. That
  inability is the entire reason the bridge exists, so "require SOCKS5 auth"
  would delete the capability being protected.
* The terminator is reached by Chromium as a DIRECT TLS connection (host-resolver
  MAP + proxy bypass) and by Firefox via ``CONNECT``. Neither arrival shape
  carries a secret we control.

So we identify the peer instead of interrogating it.

The primitive, and a correction worth reading
---------------------------------------------
``SO_PEERCRED`` (and its ``LOCAL_PEERCRED`` cousin) is the reflexive answer, but
it is an **AF_UNIX** mechanism and does NOT work here: both listeners are TCP
(``AF_INET`` on 127.0.0.1). Measured on Linux, ``getsockopt(SO_PEERCRED)`` on an
accepted *TCP* connection returns ``pid=0, uid=-1, gid=-1`` — no error, just
meaningless values. A check built on it would have looked like a boundary while
enforcing nothing, which is the failure mode this module exists to avoid.

What works, and ports to all three platforms, is resolving the peer's socket to
an owning process. This module does it by **inversion**: rather than reading the
system-wide connection table (which needs root on macOS and answers a much
broader question than we asked), it enumerates the sockets owned by the browser
process tree we were told to serve, and checks whether the peer's endpoint is one
of them. We only ever inspect our own descendants, so no elevated privilege is
required on any platform.

The whole descendant TREE is authorized, not a single pid: neither browser
connects from the pid we hold. Chromium proxies through its network-service
child, and the Firefox engine's warm-up is a separate process under the launcher.

Fail-closed, with one deliberate exception — and one deliberate NON-exception
-----------------------------------------------------------------------------
An unverifiable peer is refused. There are three mechanism states, and the
difference between the last two is load-bearing:

* ENFORCEABLE — verify every peer, refuse anything unresolved.
* ABSENT — the platform genuinely cannot offer the primitive (``psutil`` is not
  in the bundle, or cannot read even our OWN sockets). Here we log loudly, once,
  and ALLOW: an honest, recorded absence rather than a claimed boundary that
  silently enforces nothing. A false refusal would break proxied browsing
  outright, which is a worse outcome than the exposure on a platform we cannot
  measure.
* UNUSABLE — ``psutil`` is present but too OLD to expose the API we call. This
  gets the OPPOSITE treatment: refuse, and name the remedy.

Why UNUSABLE must not share the ABSENT path
-------------------------------------------
``Process.net_connections()`` only exists from psutil 6.0.0; 5.9.x names it
``Process.connections()``. A declared floor of ``psutil>=5.9`` was therefore
satisfiable by a psutil where the method is missing — a lockfile, a
distro-packaged psutil, an offline wheelhouse — and the probe's bare ``except``
read the resulting ``AttributeError`` as "this platform has no mechanism". Both
listeners then degraded OPEN on a machine that was perfectly capable of
enforcing the boundary, with the control present in the tree and reporting
itself as installed. That is the exact failure this module exists to prevent,
arriving by packaging rather than by primitive.

So the escape hatch is scoped to what it was argued for: a platform that cannot
do this at all. A fixable dependency fault is not that, and it fails closed —
an operator gets a broken browser they can diagnose from the log, rather than an
exposure nobody can see. The floor is pinned at ``psutil>=6.0`` in both
``pyproject.toml`` and ``requirements.txt``, and a test asserts the declared
floor and the called API cannot drift apart again.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time

from .logging import get_logger

logger = get_logger("core.peerauth")

# Only a loopback peer can ever be legitimate: both listeners bind 127.0.0.1, so
# a non-loopback peer means the bind moved. Checked anyway — defense in depth.
_LOOPBACK_IPS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})

# An accepted connection may beat the launcher's `bind_to_process` call: both
# listeners are started BEFORE the browser is spawned (the browser needs their
# port numbers on its command line). The gap is microseconds in practice — bind
# happens on the line after spawn returns — but a browser that connects
# instantly must not be refused for winning a race. Waiting converts that race
# into a short delay instead of a false refusal. Refuse once it expires: a
# listener nobody ever claimed has no legitimate client.
_BIND_WAIT_SECONDS = 20.0

# A just-forked helper can be visible as a process before its socket shows up in
# the per-process table. Re-scan a few times before refusing, so a legitimate
# client is never refused for being new. The cost of a retry is a few ms; the
# cost of a false refusal is a broken browsing session.
_SCAN_ATTEMPTS = 8
_SCAN_BACKOFF_SECONDS = 0.05


def _psutil():
    """The psutil module, or None where it isn't available.

    Imported lazily rather than at module scope so that a bundle shipped without
    it degrades to the documented no-mechanism position instead of failing to
    import the whole proxy/cert layer.
    """
    try:
        import psutil  # noqa: PLC0415

        return psutil
    except Exception:  # pragma: no cover - depends on the bundle
        return None


def verification_available() -> bool:
    """Whether peer verification can be enforced on this installation."""
    return _psutil() is not None


_probe_lock = threading.Lock()
_probe_result: str | None = None

#: The mechanism works — verify every peer, refuse anything unresolved.
MECH_ENFORCEABLE = "enforceable"
#: The platform genuinely cannot offer the primitive (psutil is not in the
#: bundle, or cannot read even our OWN sockets). Degrade OPEN: see the module
#: docstring — refusing here would reject the real browser, not an impostor.
MECH_ABSENT = "absent"
#: psutil is present but too OLD to expose the API we call. This is a fixable
#: packaging fault on a platform that is perfectly capable, not a platform
#: limit, so it must NOT take the degrade-open path — that would turn a routine
#: dependency resolution into a silently disabled security control on a machine
#: where the boundary is fully enforceable. Fail CLOSED and name the remedy.
MECH_UNUSABLE = "unusable"


def mechanism_state() -> str:
    """Which of the three mechanism states this installation is in.

    Probed once and cached — the answer is a property of the installation, not
    of the connection.

    The ABSENT/UNUSABLE split is the whole point of this function. Collapsing
    them into one boolean is what let ``psutil>=5.9`` disable the gate on every
    listener while still reporting itself as installed: ``Process.net_connections``
    does not exist before psutil 6.0.0 (5.9.x names it ``Process.connections``),
    so the probe raised ``AttributeError``, which read as "this platform has no
    mechanism" and degraded open.
    """
    global _probe_result
    if _probe_result is not None:
        return _probe_result
    with _probe_lock:
        if _probe_result is None:
            state, definitive = _probe_mechanism()
            # Only a DEFINITIVE answer is cached. A probe that merely FAILED is
            # not evidence about the platform: under load the call can raise
            # transiently (fd pressure, a momentarily unreadable /proc entry),
            # and caching that would disable verification for the entire
            # process lifetime on a machine that can enforce it perfectly well.
            # Re-probe instead — the cost is one syscall on the next connection.
            if definitive:
                _probe_result = state
            return state
    return _probe_result


def _probe_mechanism() -> tuple[str, bool]:
    """``(state, definitive)``. ``definitive`` is False when the answer reflects
    a failed probe rather than a settled property of the installation."""
    ps = _psutil()
    if ps is None:
        return MECH_ABSENT, True
    # Distinguish "the API is not there" from "the API is there and failed".
    # Checked as an attribute rather than inferred from the raised exception so
    # that an AttributeError from INSIDE a working net_connections() is not
    # misread as a missing method.
    if not hasattr(ps.Process, "net_connections"):
        logger.error(
            "peer verification is DISABLED: psutil %s is too old — "
            "Process.net_connections() requires psutil >= 6.0. Local callers "
            "are NOT authenticated until the dependency is upgraded; both "
            "loopback listeners will refuse every connection until then",
            getattr(ps, "__version__", "?"),
        )
        # Definitive: the installed psutil either has the method or it does not.
        return MECH_UNUSABLE, True
    try:
        ps.Process(os.getpid()).net_connections(kind="tcp")
    except Exception:
        # We cannot enumerate even our OWN sockets. This is NOT definitive: the
        # call can fail transiently under load, and a locked-down platform is
        # indistinguishable from a momentary failure at this instant. Answer
        # ABSENT for THIS connection (degrade open rather than falsely refuse
        # the real browser) but do not burn it into the cache.
        return MECH_ABSENT, False
    return MECH_ENFORCEABLE, True


def mechanism_enforceable() -> bool:
    """Whether peer verification can actually be ENFORCED here."""
    return mechanism_state() == MECH_ENFORCEABLE


def _tree_endpoints(root_pid: int) -> set[tuple[int, int]] | None:
    """``(local_port, remote_port)`` for every TCP socket owned by ``root_pid``
    or any of its descendants.

    Returns None when the answer could not be determined at all (no mechanism,
    or the tree vanished) — the caller must treat that as "unknown", which is
    NOT the same as the empty set ("determined, and it owns no sockets"). The
    distinction matters: empty means refuse, unknown means we cannot claim to
    have checked.
    """
    ps = _psutil()
    if ps is None:
        return None
    try:
        root = ps.Process(root_pid)
    except Exception:
        return None

    try:
        procs = [root] + root.children(recursive=True)
    except Exception:
        procs = [root]

    endpoints: set[tuple[int, int]] = set()
    resolved_any = False
    for proc in procs:
        try:
            conns = proc.net_connections(kind="tcp")
        except Exception:
            # AccessDenied on one process (or it exited mid-scan) is normal and
            # must not poison the whole answer — some other process in the tree
            # is very likely the one that connected.
            continue
        resolved_any = True
        for conn in conns:
            laddr = getattr(conn, "laddr", None)
            raddr = getattr(conn, "raddr", None)
            if not laddr or not raddr:
                continue
            try:
                endpoints.add((int(laddr.port), int(raddr.port)))
            except (AttributeError, TypeError, ValueError):
                continue
    if not resolved_any:
        return None
    return endpoints


class _ScanCoalescer:
    """Runs ONE tree scan on behalf of every connection that can honestly share
    it, instead of one scan per connection.

    Why this exists
    ---------------
    ``_tree_endpoints`` costs O(processes x system connection table), not
    O(sockets the tree owns): every ``Process.net_connections()`` re-reads the
    whole table and filters it to that pid (Linux parses ``/proc/net/tcp`` per
    process; Windows calls ``GetExtendedTcpTable`` per process). Measured on a
    12-process / 240-socket tree, one scan costs ~95-127 ms — and adding 32
    children that own NO sockets at all still slowed the scan down, which is
    what proves the cost is per-process rather than per-socket.

    ``allows()`` ran that scan for every connection independently, so a page
    opening many connections at once paid it many times over. Measured through
    the real bridge with 64 concurrent clients: every one was admitted, but the
    median wait was 1.9 s and the p95 3.2 s. That is a page that never finishes
    loading.

    Why this does NOT weaken the guard
    ----------------------------------
    This is a COALESCER, not a cache. It never answers from an observation made
    before the caller arrived: a waiter is only handed a scan that STARTED at or
    after its own ``not_before`` moment, which is exactly the freshness
    ``allows()`` had when it called ``_tree_endpoints`` inline. A scan already
    in flight that began too early is not reused — the caller waits for the next
    one.

    A TTL cache was rejected deliberately: a local port is closed and REUSED by
    another process within seconds, so admitting on a remembered endpoint set
    would eventually admit a caller that is genuinely not the browser. The
    property being protected is "the peer is in the tree NOW", and only a scan
    that started after the connection arrived can establish it.

    What sharing COSTS, stated rather than left to be discovered
    ------------------------------------------------------------
    Sharing a scan also shares its slowness, and that is a real trade: before
    coalescing, a scan that blocked hurt exactly one connection; a shared one
    would hurt every connection to that tree. ``_tree_endpoints`` is the
    operation most likely to block on a stuck ``/proc`` read under load, or on
    ``GetExtendedTcpTable`` on the Windows path this ticket is about — so an
    unbounded share would trade "every connection pays ~100 ms" for "one bad
    scan stalls all of them", which is the very defect being fixed.

    So the share is BOUNDED. A scan is shareable only while it is younger than
    ``_SHARE_TIMEOUT_SECONDS``; past that it is treated as wedged and callers
    scan independently, exactly as they did before this class existed. The bound
    is on the SCAN's age, not on each caller's wait, so a wedged scan costs the
    tree that timeout ONCE rather than once per connection.

    The 2-scan worst case
    ---------------------
    A caller that arrives just AFTER a scan starts cannot be answered by it (it
    began before the caller's ``not_before``), so it waits for that scan and
    then for a fresh one: ~2 scan costs, against 1 for the old inline path. This
    is inherent to the freshness rule and is NOT removable without turning the
    coalescer into the cache described above. Coalescing is therefore a large
    win for simultaneous arrivals and can be a small median LOSS for arrivals
    staggered around the scan cost; see ``scripts/ps199_gate_concurrency.py``,
    which measures both rather than only the favourable one.
    """

    #: A waiter re-checks under the condition rather than sleeping forever, so a
    #: lost notify (or a scanner thread killed outright) cannot wedge a caller.
    _WAIT_SLICE_SECONDS = 1.0

    #: How old an in-flight scan may get before callers stop waiting for it and
    #: scan independently. Generous against the ~95-127 ms a real scan costs, so
    #: ordinary sharing is never given up on, but bounded so a wedged scan
    #: degrades to the pre-coalescer behaviour instead of stalling the listener.
    _SHARE_TIMEOUT_SECONDS = 2.0

    #: Completed scans are retained only to answer waiters that are already
    #: blocked; a caller arriving later can never use one (it needs a scan that
    #: started after IT did). A handful of live trees is the real working set —
    #: retaining a full endpoint set per dead pid is a slow leak that buys
    #: nothing.
    _MAX_RETAINED_TREES = 8

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        # root_pid -> (started_at, result) for the most recent COMPLETED scan.
        self._done: dict[int, tuple[float, set[tuple[int, int]] | None]] = {}
        # root_pid -> started_at of a scan currently running.
        self._running: dict[int, float] = {}

    def forget(self, root_pid: int) -> None:
        """Drop any remembered scan for ``root_pid``.

        Called when a gate binds: a pid is REUSED by the OS, so a completed scan
        for a previous tree that happened to share this pid must never be able
        to answer for the new one. Also the eviction point for a tree that has
        gone away.
        """
        with self._cond:
            self._done.pop(root_pid, None)

    def _publish(
        self,
        root_pid: int,
        started: float,
        result: set[tuple[int, int]] | None,
    ) -> None:
        """Record a completed scan, newest-start wins, and bound retention.

        An older-STARTED scan never overwrites a newer one: a slow shared scan
        can land after a fast independent one, and keeping the newer start time
        is both more useful and strictly safe (a stale start time can only cause
        an EXTRA scan, never an admission on older evidence).
        """
        previous = self._done.get(root_pid)
        if previous is None or started >= previous[0]:
            self._done[root_pid] = (started, result)
        excess = len(self._done) - self._MAX_RETAINED_TREES
        if excess > 0:
            # Oldest scans first, and never the one just published — waiters
            # blocked on this tree are about to read it.
            stale = [pid for pid in sorted(self._done, key=lambda p: self._done[p][0])
                     if pid != root_pid]
            for pid in stale[:excess]:
                self._done.pop(pid, None)

    def scan(
        self,
        root_pid: int,
        not_before: float,
        scanner=None,
    ) -> set[tuple[int, int]] | None:
        """The tree's endpoints, from a scan that STARTED at or after
        ``not_before`` (a ``time.monotonic()`` reading).

        Either runs the scan on this thread, or waits for an in-flight one that
        is already fresh enough for this caller.
        """
        scan_fn = scanner or _tree_endpoints
        shared = True
        with self._cond:
            while True:
                done = self._done.get(root_pid)
                if done is not None and done[0] >= not_before:
                    # A completed scan that began after we arrived answers us.
                    return done[1]
                running = self._running.get(root_pid)
                if running is not None:
                    if time.monotonic() - running >= self._SHARE_TIMEOUT_SECONDS:
                        # That scan is wedged, not merely slow. Waiting on it
                        # would convert one stuck enumeration into a stalled
                        # listener, so fall back to what every caller did before
                        # coalescing existed: scan independently. We do NOT
                        # clear its marker — it may still land, and its own
                        # finally clause owns that.
                        shared = False
                        started = time.monotonic()
                        break
                    # Someone is scanning. If their scan began after we arrived
                    # it will answer us; if it began before, we still wait for
                    # it to finish (only one scan per tree at a time) and then
                    # re-evaluate — the loop will start a fresh one for us.
                    self._cond.wait(timeout=self._WAIT_SLICE_SECONDS)
                    continue
                started = time.monotonic()
                self._running[root_pid] = started
                break

        # `result` is bound BEFORE the try so the finally clause can never read
        # an unbound name. Without this, a BaseException out of the scanner
        # (KeyboardInterrupt on a routine shutdown, SystemExit, a cancelled
        # task) skips `except Exception`, and the finally raises
        # UnboundLocalError — masking the real exception with one that looks
        # like our bug, and skipping notify_all() so every already-blocked
        # waiter falls through to the full _WAIT_SLICE_SECONDS poll instead of
        # being woken. In a change whose whole purpose is removing latency from
        # this path, that is a second of added delay on an error path.
        result: set[tuple[int, int]] | None = None
        try:
            result = scan_fn(root_pid)
        except Exception:
            # _tree_endpoints already swallows its own errors, but a scanner
            # that raises must not leave the tree permanently marked "running"
            # and every other caller waiting on a scan that will never land.
            result = None
        finally:
            with self._cond:
                if shared:
                    self._running.pop(root_pid, None)
                self._publish(root_pid, started, result)
                self._cond.notify_all()
        return result


#: Module-level: the cost being amortized is per TREE, and both listeners (the
#: SOCKS bridge and the mTLS terminator) are bound to the same browser pid, so
#: they can honestly share a scan. The endpoint set is a property of the tree,
#: not of the listener — each gate still applies its own listen-port filter to
#: the shared result.
_scan_coalescer = _ScanCoalescer()


class PeerGate:
    """Decides whether an accepted loopback connection may be served.

    One gate per listener. The listener is created before the browser exists, so
    the gate starts unclaimed and the launcher calls :meth:`bind_to_process` with
    the browser handle's pid once the process is up.
    """

    def __init__(self, label: str) -> None:
        self._label = label
        self._listen_port: int | None = None
        self._root_pid: int | None = None
        self._bound = threading.Event()
        self._warned_no_mechanism = False
        self._lock = threading.Lock()

    @property
    def root_pid(self) -> int | None:
        return self._root_pid

    def set_listen_port(self, port: int | None) -> None:
        """Tell the gate which port this listener bound.

        Optional but sharpening: with it, an authorized socket must be a
        connection to THIS listener (its remote port is ours), not merely a
        socket in the tree that happens to share the peer's local port.
        """
        self._listen_port = int(port) if port else None

    def bind_to_process(self, pid: int | None) -> None:
        """Declare the browser process this listener serves.

        ``pid`` of 0 or None means the engine runs INSIDE this process rather
        than as a child — the Firefox engine's non-fork path (Windows/macOS)
        runs the browser on a thread of the app and reports ``pid = 0``. Our own
        pid is the correct tree root there, and it is not a weakening: the
        browser genuinely is this process on that path.
        """
        root = int(pid) if pid else os.getpid()
        # A pid is reused by the OS. Drop any scan remembered for a previous
        # tree that happened to hold this pid, so it can never answer for the
        # new one — and evict the dead tree's endpoint set while we are here.
        _scan_coalescer.forget(root)
        self._root_pid = root
        self._bound.set()
        logger.debug("%s: peer gate bound to pid %s", self._label, root)

    def _wait_for_binding(self) -> bool:
        if self._bound.is_set():
            return True
        return self._bound.wait(timeout=_BIND_WAIT_SECONDS)

    def allows(self, peer: object) -> bool:
        """Whether ``peer`` (a ``getpeername``-style ``(host, port)`` tuple) is
        the browser this listener was started for.

        Refuses by default. Every rejection is logged at WARNING so a genuine
        misconfiguration is diagnosable and a real attempt is not invisible.
        """
        host, port = _split_peer(peer)
        if port is None:
            logger.warning(
                "%s: refusing a connection with an unreadable peer address", self._label
            )
            return False
        if host not in _LOOPBACK_IPS:
            logger.warning(
                "%s: refusing non-loopback peer %s:%s", self._label, host, port
            )
            return False

        state = mechanism_state()

        if state == MECH_UNUSABLE:
            # psutil is present but too old to expose the API we call. The
            # platform is perfectly capable, so this is a fixable packaging
            # fault and NOT the documented no-mechanism exception: degrading
            # open here would turn a routine dependency resolution into a
            # silently disabled control on a machine that can enforce it. Refuse
            # and name the remedy, so an operator gets a broken browser they can
            # diagnose rather than an exposure nobody can see.
            with self._lock:
                if not self._warned_no_mechanism:
                    self._warned_no_mechanism = True
                    logger.error(
                        "%s: REFUSING every connection — peer verification needs "
                        "psutil >= 6.0 (Process.net_connections) and the installed "
                        "psutil is older. Upgrade psutil to restore this listener",
                        self._label,
                    )
            return False

        if not self._wait_for_binding() or self._root_pid is None:
            logger.warning(
                "%s: refusing peer port %s — no browser process was ever "
                "claimed for this listener",
                self._label,
                port,
            )
            return False

        if state == MECH_ABSENT:
            # Documented, deliberate exception — see the module docstring. Log
            # once per listener so it is a recorded position, not a silent hole,
            # and never pretend the connection was verified.
            #
            # Returned AFTER the binding check (the review's defense-in-depth
            # suggestion), so even a no-mechanism install only serves callers
            # during the browser's actual lifetime.
            with self._lock:
                if not self._warned_no_mechanism:
                    self._warned_no_mechanism = True
                    logger.warning(
                        "%s: peer verification is UNAVAILABLE on this installation "
                        "(the process-socket table cannot be read) — local callers "
                        "are NOT being authenticated on this listener",
                        self._label,
                    )
            return True

        root_pid = self._root_pid
        for attempt in range(_SCAN_ATTEMPTS):
            # The scan must have STARTED no earlier than this attempt, so a
            # retry still observes state newer than the one that just failed —
            # the whole point of retrying. Taking the reading before the call
            # means a scan already in flight can answer us only if it began
            # after we got here, which is exactly the freshness an inline
            # _tree_endpoints() call had.
            attempt_started = time.monotonic()
            endpoints = _scan_coalescer.scan(root_pid, attempt_started)
            if endpoints is None:
                # Undetermined, NOT "determined empty". A process in the tree
                # exiting mid-scan (or a momentarily unreadable /proc entry under
                # load) must not be reported as "this peer is an impostor" —
                # falsely refusing the real browser breaks browsing outright.
                # Retry; only a scan that never resolves is a refusal.
                if attempt < _SCAN_ATTEMPTS - 1:
                    time.sleep(_SCAN_BACKOFF_SECONDS)
                    continue
                logger.warning(
                    "%s: refusing peer port %s — could not resolve the sockets of "
                    "browser process %s",
                    self._label,
                    port,
                    root_pid,
                )
                return False
            listen_port = self._listen_port
            if any(
                lport == port and (listen_port is None or rport == listen_port)
                for lport, rport in endpoints
            ):
                return True
            if attempt < _SCAN_ATTEMPTS - 1:
                time.sleep(_SCAN_BACKOFF_SECONDS)

        logger.warning(
            "%s: REFUSED a local connection from peer port %s — it does not "
            "belong to browser process %s or any of its children",
            self._label,
            port,
            root_pid,
        )
        return False


async def allows_async(gate: "PeerGate", peer: object) -> bool:
    """:meth:`PeerGate.allows` for an asyncio caller.

    The check blocks: it scans process sockets, sleeps between retries, and may
    wait for the listener to be claimed. The bridge runs every tunnel on ONE
    event loop, so calling the sync version there would stall every other
    in-flight tunnel for the duration — a few ms per connection normally, and up
    to the full bind wait on an unclaimed listener. Run it on a worker thread so
    the loop keeps serving.
    """
    return await asyncio.to_thread(gate.allows, peer)


def _split_peer(peer: object) -> tuple[str, int | None]:
    """``(host, port)`` from a getpeername()-style tuple, tolerating the IPv6
    4-tuple form and anything malformed (which yields a None port → refusal)."""
    if not isinstance(peer, (tuple, list)) or len(peer) < 2:
        return "", None
    host = peer[0]
    try:
        port = int(peer[1])
    except (TypeError, ValueError):
        return "", None
    if not isinstance(host, str):
        return "", None
    return host, port
