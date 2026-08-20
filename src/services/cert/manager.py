"""Tie a profile's mTLS certificate to a running browser: start a terminator and
report what each engine needs to route the admin host through it.

Only used when a profile actually has a certificate assigned. The browser's
proxy is pointed at the terminator, which presents the certificate to the admin
host and forwards everything else to the profile's real proxy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ...core.logging import get_logger
from . import terminator as term

logger = get_logger("cert.manager")


@dataclass
class CertSession:
    port: int
    proxy_url: str      # http://127.0.0.1:<port> — Firefox's proxy (CONNECT+MITM)
    spki_b64: str       # Chromium: --ignore-certificate-errors-spki-list
    ca_path: str        # Firefox: import into the profile's cert9.db
    admin_host: str     # Chromium: host-resolver MAP <admin_host> -> 127.0.0.1:port
    admin_port: int     # the admin URL's port (default 443)
    _term: "term.Terminator"

    def bind_to_process(self, pid: int | None) -> None:
        """Declare the browser process allowed to use this terminator.

        The terminator binds before the browser exists (the engine needs its port
        on the command line / in its proxy config), so the launcher claims it
        once the process is up. Until then the listener serves nobody."""
        self._term.bind_to_process(pid)

    def stop(self) -> None:
        self._term.stop()


def start_cert_session(
    cert,
    upstream_socks: str | None,
    work_dir: str,
    verify_upstream: bool = True,
) -> CertSession | None:
    """Start a terminator for ``cert`` and return the session, or None when
    there's no certificate or it can't be prepared (the launch then proceeds
    without mTLS rather than failing)."""
    if cert is None:
        return None
    if not cert.url:
        logger.error("certificate %r has no admin URL; skipping", cert.name)
        return None
    if not cert.p12_path or not os.path.isfile(cert.p12_path):
        logger.error("certificate %r: p12 missing at %s", cert.name, cert.p12_path)
        return None

    host, port_ = term.host_port(cert.url)
    if not host:
        logger.error("certificate %r: unparseable admin URL %r", cert.name, cert.url)
        return None

    # The decrypted client key is written UNENCRYPTED, under an mkstemp-unique
    # name, and is only wiped by Terminator.stop(). Any session that doesn't
    # reach stop() therefore leaves the operator's real key on disk with nothing
    # that will ever remove it. Sweep the directory before writing to it so the
    # key's lifetime belongs to the directory rather than to one object's happy
    # path: reaching here means a new session is starting, and the previous one
    # is over (work_dir is per-profile, and a second launch for the same profile
    # can't get here while the first is live — src/ui/actions/browser.py:21-29).
    term.sweep_key_material(work_dir)

    try:
        leaf = term.make_leaf(host, work_dir)
        client_pem = term.client_pem_from_p12(
            cert.p12_path, cert.password, work_dir
        )
        # Terminator() loads the leaf chain and can raise (a corrupt/unreadable
        # leaf), as can start(). That happens AFTER the key is on disk but
        # BEFORE a session object exists, so the caller's cleanup — keyed to the
        # returned session — would have nothing to stop. Guard it here and wipe.
        t = term.Terminator(
            host, leaf, client_pem,
            upstream_socks=upstream_socks,
            verify_upstream=verify_upstream,
            admin_port=port_,
        )
        port = t.start()
    except Exception as e:
        logger.exception("certificate %r: could not prepare terminator: %s",
                         cert.name, e)
        term.sweep_key_material(work_dir)
        return None

    # Log only the cert name + loopback port; the admin host:port is internal
    # infra that shouldn't land in the persistent / Activity log.
    logger.info("mTLS terminator for %r on 127.0.0.1:%s", cert.name, port)
    return CertSession(
        port=port,
        proxy_url=f"http://127.0.0.1:{port}",
        spki_b64=leaf.spki_b64,
        ca_path=leaf.ca_path,
        admin_host=host,
        admin_port=port_,
        _term=t,
    )
