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

Fail-closed, with one deliberate exception
------------------------------------------
An unverifiable peer is refused. The single exception is a platform where the
mechanism is genuinely absent (``psutil`` missing from the bundle): there we log
loudly, once, and allow — an honest, recorded absence rather than a claimed
boundary that silently enforces nothing. A false refusal would break proxied
browsing outright, which is a worse outcome than the exposure on a platform we
cannot measure.
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
_probe_result: bool | None = None


def mechanism_enforceable() -> bool:
    """Whether peer verification can actually be ENFORCED here.

    Probes our own process: if we cannot enumerate even our OWN sockets, the
    primitive is unavailable on this installation (a sandboxed/locked-down
    platform), and refusing on that basis would reject the real browser rather
    than an impostor. Probed once and cached — the answer is a property of the
    platform, not of the connection.
    """
    global _probe_result
    if _probe_result is not None:
        return _probe_result
    with _probe_lock:
        if _probe_result is None:
            ps = _psutil()
            if ps is None:
                _probe_result = False
            else:
                try:
                    ps.Process(os.getpid()).net_connections(kind="tcp")
                    _probe_result = True
                except Exception:
                    _probe_result = False
    return _probe_result


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

        if not mechanism_enforceable():
            # Documented, deliberate exception — see the module docstring. Log
            # once per listener so it is a recorded position, not a silent hole,
            # and never pretend the connection was verified.
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

        if not self._wait_for_binding() or self._root_pid is None:
            logger.warning(
                "%s: refusing peer port %s — no browser process was ever "
                "claimed for this listener",
                self._label,
                port,
            )
            return False

        root_pid = self._root_pid
        for attempt in range(_SCAN_ATTEMPTS):
            endpoints = _tree_endpoints(root_pid)
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
