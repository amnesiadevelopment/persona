"""A spy that answers "did the code under test open an outbound TCP socket?"
— and gets the same answer on all three platforms.

WHY THIS EXISTS, AND WHY IT IS NOT JUST A COUNTER.

Several tests assert a fail-closed guarantee of the form "with nowhere safe to
send it, the request is NOT SENT — no socket is opened at all". The obvious
implementation counts `socket.socket(AF_INET/AF_INET6, SOCK_STREAM)`
constructions and exempts `AF_UNIX`, on the reasoning that asyncio builds an
AF_UNIX socketpair for its event-loop self-pipe on every run and that plumbing
is not egress.

That exemption is correct, and it is written in POSIX. **Windows has no
AF_UNIX.** `socket.socketpair()` there is a pure-Python emulation over loopback
TCP — it opens a listener, connects a client to it, and accepts — so the event
loop's own self-pipe constructs THREE AF_INET/SOCK_STREAM sockets and the
counter attributes all three to the code under test. The guard is fine; the
instrument is not. (Corroborated by the tests' own second assertion: the
listener on the blocked port never saw a connection.)

So the exemption is re-expressed here in terms every platform can state: exempt
**the sockets the event loop's self-pipe plumbing itself created**, identified
by construction *during* a `socket.socketpair()` call rather than by address
family. On POSIX that is exactly the set AF_UNIX used to name; on Windows it is
the same set, correctly named for once. Nothing is exempted by address family,
so a real AF_INET connection is still counted on every platform.

THE SPY IS A REAL SUBCLASS OF socket.socket, WHICH IS LOAD-BEARING. asyncio's
Windows proactor asks `isinstance(conn, socket.socket)` (windows_events.py) —
patching the name with a plain callable makes that raise
"TypeError: isinstance() arg 2 must be a type", which is a second, independent
Windows-only failure in the same tests. A subclass keeps the name a type.
"""

import socket


class SocketSpy:
    """Record outbound TCP socket constructions, minus event-loop plumbing.

    Install with ``spy = SocketSpy(monkeypatch)``; read ``spy.opened`` (a list
    of ``(family, type)`` tuples) or ``spy.tcp_sockets`` (its length).

    Delegating rather than stubbing is deliberate: the code under test behaves
    normally, so a failure here means "a socket was opened", not "the spy broke
    the test".
    """

    def __init__(self, monkeypatch):
        self.opened: list[tuple] = []
        #: Depth, not a bool: socketpair() is not re-entrant today, but a
        #: counter cannot be un-set early by a nested call if that changes.
        self._plumbing = 0
        real_socket = socket.socket
        real_socketpair = socket.socketpair
        spy = self

        class _Spy(real_socket):  # type: ignore[misc,valid-type]
            def __init__(inner, family=socket.AF_INET, type=socket.SOCK_STREAM,
                         *args, **kwargs):
                # Count only genuine outbound TCP, and only when it is not the
                # loop building its own self-pipe. On Windows that self-pipe IS
                # AF_INET/SOCK_STREAM, which is the whole reason this class
                # exists; on POSIX it is AF_UNIX and would not have matched
                # anyway, so this is a no-op there and the two agree.
                if (
                    not spy._plumbing
                    and family in (socket.AF_INET, socket.AF_INET6)
                    and type == socket.SOCK_STREAM
                ):
                    spy.opened.append((family, type))
                super().__init__(family, type, *args, **kwargs)

        def _spy_socketpair(*args, **kwargs):
            spy._plumbing += 1
            try:
                return real_socketpair(*args, **kwargs)
            finally:
                spy._plumbing -= 1

        monkeypatch.setattr(socket, "socket", _Spy)
        monkeypatch.setattr(socket, "socketpair", _spy_socketpair)

    @property
    def tcp_sockets(self) -> int:
        return len(self.opened)
