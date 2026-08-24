"""A REAL SOCKS5 relay on loopback, so the verify lane can be driven end to end.

Why this exists
---------------
PS-126 shipped three times and failed QA each time because every test asserted
on a ``FetchFailed`` string the TEST AUTHOR wrote::

    _observe_raising(monkeypatch, FetchFailed("SOCKS5Error: 0x01: ..."))

That pins the guard's wording given a text, and says nothing about whether that
text ever occurs. It did not. PySocks re-wraps a negotiation failure as
``GeneralProxyError`` on the way out (``socks.py:810-814``), so the string above
never reached the guard on any real run — the feature was unreachable in
production while forty tests reported green. The project's own knowledge
article PS-11 ("Tests that assert on what was written, not on what happens")
names this failure mode; this module is the answer to it.

So this relay speaks the actual protocol: it completes the RFC1929
username/password exchange and then answers the CONNECT request with a reply
code the test chooses. A test using it exercises real PySocks negotiation
through real :mod:`socks_fetch` into real :mod:`exit_guard`, and the exception
is one PySocks RAISED rather than one a test invented.

No network is involved. It binds ``127.0.0.1:0``, so it needs no fixed port,
and every socket is closed when the fixture tears down.

What it deliberately does NOT do
--------------------------------
It never proxies anything. Every mode here ends in a failure, because failure
is the whole subject: the guard's job is to REFUSE, and a relay that could
succeed would invite a test that proves the exit is real by pointing at a
loopback socket.
"""

from __future__ import annotations

import socket
import threading

#: Answer the CONNECT request with ``reply_code`` after auth SUCCEEDS. This is
#: the connect stage — the shape a dead sticky session token takes.
MODE_REPLY = "reply"

#: Reject the RFC1929 exchange. The auth stage, which must stay distinguishable
#: from the connect stage above.
MODE_AUTH_FAIL = "auth_fail"

#: Complete auth, then never answer the CONNECT request. The negotiation
#: TIMEOUT case — a contrast row, because PySocks reports it with the same
#: outer class as a connect failure and it must not acquire connect wording.
MODE_HANG = "hang"


class Socks5Relay:
    """A loopback SOCKS5 relay that fails in a chosen, specific way.

    Use as a context manager::

        with Socks5Relay(reply_code=0x01) as relay:
            observe_exit(relay.proxy_url, ...)

    ``proxy_url`` carries a credential-shaped user/password on purpose: it is
    what lets a redaction assertion be made against a REAL run's output rather
    than against a hand-built string.
    """

    #: Username/password offered in ``proxy_url``. Credential-SHAPED so
    #: redaction can be asserted on real output, and obviously fake so it can
    #: never be mistaken for the operator's actual credential.
    USERNAME = "relay-user"
    PASSWORD = "relay-s3cr3t"

    def __init__(
        self,
        *,
        reply_code: int = 0x01,
        mode: str = MODE_REPLY,
        hang_seconds: float = 30.0,
    ) -> None:
        self.reply_code = reply_code
        self.mode = mode
        self.hang_seconds = hang_seconds
        self._closed = threading.Event()
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(8)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def proxy_url(self) -> str:
        """A ``socks5h://`` URL pointing at this relay, with a credential."""
        return (
            f"socks5h://{self.USERNAME}:{self.PASSWORD}@127.0.0.1:{self.port}"
        )

    def __enter__(self) -> "Socks5Relay":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self._closed.set()
        try:
            self._server.close()
        except OSError:
            pass

    # --- the protocol ------------------------------------------------------

    def _serve(self) -> None:
        while not self._closed.is_set():
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(
                target=self._handle, args=(conn,), daemon=True
            ).start()

    @staticmethod
    def _recv_exactly(conn: socket.socket, count: int) -> bytes:
        buffer = b""
        while len(buffer) < count:
            chunk = conn.recv(count - len(buffer))
            if not chunk:
                raise ConnectionError("client closed mid-negotiation")
            buffer += chunk
        return buffer

    def _handle(self, conn: socket.socket) -> None:
        try:
            # --- greeting: VER NMETHODS METHODS ---
            self._recv_exactly(conn, 1)  # VER
            method_count = self._recv_exactly(conn, 1)[0]
            self._recv_exactly(conn, method_count)
            # Demand username/password, which is what makes auth a real stage
            # here rather than something skipped with 0x00.
            conn.sendall(b"\x05\x02")

            # --- RFC1929: VER ULEN UNAME PLEN PASSWD ---
            self._recv_exactly(conn, 1)  # VER (0x01)
            username_length = self._recv_exactly(conn, 1)[0]
            self._recv_exactly(conn, username_length)
            password_length = self._recv_exactly(conn, 1)[0]
            self._recv_exactly(conn, password_length)

            if self.mode == MODE_AUTH_FAIL:
                conn.sendall(b"\x01\x01")  # status != 0 -> SOCKS5AuthError
                return
            conn.sendall(b"\x01\x00")  # auth SUCCEEDED

            # --- request: VER CMD RSV ATYP DST.ADDR DST.PORT ---
            self._recv_exactly(conn, 3)  # VER CMD RSV
            address_type = self._recv_exactly(conn, 1)[0]
            if address_type == 0x01:  # IPv4
                self._recv_exactly(conn, 4)
            elif address_type == 0x03:  # domain name — the socks5h form
                name_length = self._recv_exactly(conn, 1)[0]
                self._recv_exactly(conn, name_length)
            elif address_type == 0x04:  # IPv6
                self._recv_exactly(conn, 16)
            self._recv_exactly(conn, 2)  # DST.PORT

            if self.mode == MODE_HANG:
                # Auth is complete and no reply is coming: the client's own
                # socket timeout fires. Bounded so a wedged test cannot hold
                # the thread forever.
                self._closed.wait(self.hang_seconds)
                return

            # VER REP RSV ATYP BND.ADDR BND.PORT
            conn.sendall(
                b"\x05"
                + bytes([self.reply_code])
                + b"\x00"
                + b"\x01"
                + b"\x00\x00\x00\x00"
                + b"\x00\x00"
            )
        except (OSError, ConnectionError):
            # A test that tore down mid-negotiation is not a relay fault.
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass


__all__ = [
    "MODE_AUTH_FAIL",
    "MODE_HANG",
    "MODE_REPLY",
    "Socks5Relay",
]
