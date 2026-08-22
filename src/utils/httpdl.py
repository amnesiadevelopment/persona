"""Download / verify / atomic-replace primitives shared by the app and engine updaters.

Three update pipelines used to hand-roll the same sequence — download, verify a
sha256, atomically replace the live artifact, roll back on failure:

  * src/services/app_update/updater.py   (full installer / AppImage)
  * src/services/app_update/fast_update.py (Windows code-only app.zip swap)
  * src/services/engine/updater.py       (fingerprint-chromium engine)

Each copy drifted from the others, so a fix landed in one and was re-discovered
in the next (#195 was fixed in updater.py, missed in fast_update.py, re-fixed
there separately; 5e00d66 fixed a fail-OPEN verify, and bfc7cbf then found the
same fix inert on Linux). The shared pieces live here so there is ONE place to
fix.

THE MISSING-CHECKSUM POLICY (one contract, whole project)
---------------------------------------------------------
An absent digest FAILS CLOSED: an asset we cannot verify could be a swap, so we
refuse it. A present-but-wrong digest is ALWAYS rejected.

There is a per-call `allow_missing=True` opt-in in this module's signatures, and
as of PS-49 NOTHING IN persona PASSES IT. The one caller that did was the
engine's Linux predictable-URL fallback, whose asset was believed to live
outside the API that carries the digest; measured against upstream, that belief
was false twice over — the matched asset carries a sha256 in the very asset list
the code already read, and the fallback 404s on every release where it actually
fires. So the engine now verifies on every OS with no platform carved out, and
the exception this paragraph used to describe no longer exists. The parameter is
kept because its SEMANTICS are the project's vocabulary for "nothing was ever
published" versus "a digest arrived and is unusable" (see `digest_missing`), and
the engine's refusal is written in those terms — but it is a primitive with no
caller, not a live path. Do not re-open it: an unverified browser engine is the
regression PS-49 removed.

Availability of an update is never weighed against the integrity of what gets
executed (Invariant #0): a user who does not update has lost nothing, a user who
installs an unverified binary has lost the machine. So there is deliberately no
size-check fallback, no environment escape hatch, no "degraded" verify mode and
no retry-until-success loop here — those are the fail-open regression this
module exists to prevent.

TWO TRANSPORTS, ON PURPOSE
--------------------------
`curl_download` and `resumable_download` are not duplicates. The app updater
shells out to curl because it survives a flaky Tor circuit far better than
urllib (which can block for a whole timeout on a dead exit) and matches
install.sh; the engine updater uses urllib so it can drive a Range-preserving
opener and report byte-level progress. Both resume, and both now share this
module's verify + completion + atomic-replace logic.
"""

import errno
import hashlib
import http.client
import os
import shutil
import socket
import ssl
import subprocess
import time
import urllib.parse
import urllib.request

from ..core import platform as _platform

# curl exit codes that mean "HTTP 416 Range Not Satisfiable" — `-C -` asked to
# resume a file that is already complete, which IS the success case.
_CURL_RANGE_DONE = (33, 36)

_CHUNK = 1 << 20


class KeepRangeRedirect(urllib.request.HTTPRedirectHandler):
    """Re-attach the Range header after a redirect.

    GitHub release downloads 302 to a signed CDN URL, and urllib's default
    redirect handler builds the follow-up request WITHOUT the original headers —
    so the Range header is lost and the CDN returns the whole file (200) instead
    of the requested tail (206). That silently restarts the download from zero on
    every resume, which over Tor never finishes. Carry Range across the redirect
    so resume actually works.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            rng = req.headers.get("Range")
            if rng:
                new.add_header("Range", rng)
        return new


def range_opener() -> urllib.request.OpenerDirector:
    """An opener that preserves the Range header across redirects."""
    return urllib.request.build_opener(KeepRangeRedirect)


# --- the same opener, but reaching the target through a proxy ----------------
#
# MECHANISM ONLY — there is deliberately no policy here. Nothing below reads
# `app_egress_proxy` or decides whether a proxy should be used; a caller that
# already holds a verdict from `services/egress.py` hands the transport down.
# That split is the whole point: this module is SHARED (app_update/fast_update
# call it too), so resolving persona's egress policy in here would plant a
# second copy of that decision inside a mechanism — the drift `egress.py:88-91`
# exists to prevent. The authority stays in `egress`; this owns only "how do I
# physically open a socket through that transport".
#
# WHY NOT urllib's own ProxyHandler FOR SOCKS
# -------------------------------------------
# Handed a `socks5://` value, ProxyHandler emits a plain `CONNECT host:443
# HTTP/1.1` at a port that is waiting for a `\x05` greeting and never answers.
# socks5 is persona's DEFAULT scheme, so a ProxyHandler-only implementation
# would not leak — it would HANG, which is a different failure wearing the
# fix's clothes. This is the same defect class `egress.py:22-30` and
# `proxy_checker._is_socks_scheme` already document. So socks schemes get a
# real handshake (via PySocks, a declared dependency and already how
# `utils/proxy_checker.py` and `services/verify/socks_fetch.py` reach a SOCKS
# proxy); only http/https proxies go through ProxyHandler.
#
# REMOTE DNS IS NOT OPTIONAL
# --------------------------
# `rdns=True` is hard-wired below and is passed EXPLICITLY rather than left to
# a library default, so a future default change cannot silently turn this into
# a local resolution. Resolving the GitHub/CDN host on the operator's own
# resolver would trade an IP disclosure for a DNS one, which `egress.py`
# refuses. This is why PySocks' bundled `sockshandler` is NOT reused: its
# connect() carries a `socks4_no_rdns` fallback that silently retries with
# rdns=False, i.e. exactly the local resolution this must never perform. A
# configured `socks5://` is therefore spoken with remote DNS anyway — the same
# upgrade `proxy_checker._socks5_connect` already performs for the metadata
# poll (it sends atyp 0x03 whatever the scheme said), so the two arms of one
# policy cannot disagree about who resolves the name.

_SOCKS_PROXY_TYPES = {
    "socks4": "SOCKS4",
    "socks4h": "SOCKS4",
    "socks5": "SOCKS5",
    "socks5h": "SOCKS5",
}


def _proxy_parts(transport: str) -> tuple[str, str, int, str | None, str | None]:
    """Split a proxy URL into (scheme, host, port, username, password).

    A value with no scheme is read as http, matching `proxy_parser.parse_proxy`
    — the same reader `egress.resolve()` already used to decide this transport
    was usable at all, so the two cannot disagree about what the operator typed.
    """
    parsed = urllib.parse.urlparse(
        transport if "://" in transport else "http://" + transport
    )
    if not parsed.hostname or not parsed.port:
        raise ValueError("proxy URL is missing a host or a port")
    user = urllib.parse.unquote(parsed.username) if parsed.username else None
    password = urllib.parse.unquote(parsed.password) if parsed.password else None
    return parsed.scheme.lower(), parsed.hostname, parsed.port, user, password


def _real_timeout(timeout):
    """urllib hands a connection either a number or socket's sentinel object;
    PySocks wants a number or None."""
    return timeout if isinstance(timeout, (int, float)) else None


class _SocksHTTPConnection(http.client.HTTPConnection):
    """An HTTPConnection whose socket is opened THROUGH a SOCKS proxy.

    `_socks` — (proxy_type, host, port, user, password) — is attached by the
    handler below.
    """

    _socks: tuple = ()

    def connect(self):
        import socks  # PySocks; a declared dependency (requirements.txt)

        ptype, phost, pport, puser, ppass = self._socks
        self.sock = socks.create_connection(
            (self.host, self.port),
            _real_timeout(self.timeout),
            self.source_address,
            getattr(socks, ptype),
            phost,
            pport,
            True,  # rdns — the EXIT resolves the target name. Never False.
            puser,
            ppass,
            ((socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),),
        )


class _SocksHTTPSConnection(_SocksHTTPConnection):
    """The TLS counterpart: the same proxied socket, wrapped.

    `server_hostname` is the TARGET's name, not the proxy's, so certificate
    verification still checks the host we asked for.
    """

    default_port = 443

    def __init__(self, *args, context=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._ssl_context = context or ssl.create_default_context()

    def connect(self):
        super().connect()
        self.sock = self._ssl_context.wrap_socket(
            self.sock, server_hostname=self.host
        )


class SocksProxyHandler(urllib.request.HTTPHandler, urllib.request.HTTPSHandler):
    """Routes both http and https through a SOCKS proxy with a real handshake."""

    def __init__(self, parts: tuple, context=None):
        urllib.request.HTTPSHandler.__init__(self, context=context)
        self._parts = parts

    def _factory(self, cls):
        def build(host, port=None, timeout=0, **kwargs):
            conn = cls(host=host, port=port, timeout=timeout, **kwargs)
            conn._socks = self._parts
            return conn

        return build

    def http_open(self, req):
        return self.do_open(self._factory(_SocksHTTPConnection), req)

    def https_open(self, req):
        return self.do_open(
            self._factory(_SocksHTTPSConnection), req, context=self._context
        )


def proxied_range_opener(transport: str) -> urllib.request.OpenerDirector:
    """`range_opener()`'s PROXIED shape: the same Range-preserving opener, but
    every connection is made through `transport`.

    Not a second opener with its own redirect rules — `KeepRangeRedirect` rides
    along unchanged, because a resume over a slow circuit is exactly the case
    this transport exists for and losing Range across GitHub's 302-to-CDN would
    restart the download from zero on every attempt.
    """
    scheme, host, port, user, password = _proxy_parts(transport)
    if scheme in _SOCKS_PROXY_TYPES:
        handler = SocksProxyHandler(
            (_SOCKS_PROXY_TYPES[scheme], host, port, user, password)
        )
        return urllib.request.build_opener(handler, KeepRangeRedirect)
    # An http/https proxy speaks CONNECT natively, so urllib's own handler is
    # correct here — the SOCKS arm above exists because it is NOT correct there.
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": transport, "https": transport}),
        KeepRangeRedirect,
    )


# --- hashing -----------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    """The sha256 of an in-memory payload, lowercase hex."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    """The sha256 of a file, lowercase hex, read in chunks so a 200MB installer
    never lands in memory. THE one sha256-file helper for the update paths."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_digest(digest: str | None) -> str:
    """A bare lowercase hex digest from any of the forms our sources publish:
    the GitHub API's "sha256:abcd…", a sha256sum line's plain hex, or ''.

    Returns '' for anything unusable, which is NOT the same fact as "nothing was
    published" — see digest_missing.
    """
    return (digest or "").split(":", 1)[-1].strip().lower()


def digest_missing(digest: str | None) -> bool:
    """True only when NO digest was published at all: None or ''.

    This is deliberately NOT `not normalize_digest(digest)`. The two questions
    look the same and are not:

      * "sha256:", ":", "   " -> a digest ARRIVED and is unusable
      * None, ""             -> nothing was ever published

    Only the second is what an `allow_missing` opt-in was granted for. Collapsing
    them lets a malformed digest take the opt-in exit and be ACCEPTED, which is
    fail-open under a new name — the exact regression PS-6 exists to prevent.

    THE OPT-IN NOW HAS NO CALLER IN persona (PS-49). The one that had it was
    engine/updater.py's `allow_unverified = not digest and IS_LINUX`, and this
    predicate was written to match that gate CHARACTER FOR CHARACTER, because
    bfc7cbf was a real fix left inert when a guard and the check it protected
    disagreed about one word. That caller is gone — the engine path refuses an
    undigested asset on every OS instead — so the identity no longer has a
    second side to stay honest with.

    The distinction it draws still matters and is still tested. It is now the
    predicate the ENGINE REFUSAL itself is written in — `download_engine` asks
    `httpdl.digest_missing(digest)` and raises EngineUnverifiable — so the same
    one word decides whether an operator is told "no digest was published" or an
    unusable digest is simply rejected by the ordinary verify gate. Note this
    deliberately excludes whitespace-only ("   "), which is read as a digest that
    arrived: widening this to `.strip()` would hand "   " an acceptance that
    today fails closed.
    """
    return not digest


# --- the missing-checksum policy (see module docstring) ----------------------


def digest_ok(actual: str, digest: str | None, allow_missing: bool = False) -> bool:
    """Compare an already-computed hex digest against the expected one.

    No expected digest fails closed (returns False) unless the caller explicitly
    opts in with allow_missing. A present-but-wrong digest is always rejected,
    even under allow_missing — opting in covers "there is no digest to check",
    never "the digest did not match", and never "the digest was unusable".
    """
    if digest_missing(digest):
        return allow_missing  # nothing was ever published
    want = normalize_digest(digest)
    if not want:
        return False  # a digest arrived but is unusable — never accept it
    return (actual or "").strip().lower() == want


def verify_bytes(data: bytes, digest: str | None, allow_missing: bool = False) -> bool:
    """Verify an in-memory payload against a sha256 digest, fail-closed."""
    if digest_missing(digest):
        return allow_missing
    return digest_ok(sha256_bytes(data), digest)


def verify_file(path: str, digest: str | None, allow_missing: bool = False) -> bool:
    """Verify a file against a sha256 digest, fail-closed. An unreadable file is
    never accepted — we could not verify it, which is exactly the case we refuse."""
    if digest_missing(digest):
        return allow_missing
    try:
        return digest_ok(sha256_file(path), digest)
    except OSError:
        return False


# --- atomic replace, with rollback -------------------------------------------


def atomic_replace(
    src: str, dst: str, mode: int | None = 0o755, log=None
) -> bool:
    """Move `src` onto `dst` atomically, keeping a backup of `dst` so a failed
    swap restores the working artifact rather than losing it.

    This is the discipline the app updater already had for the AppImage — take a
    .bak, os.replace, restore the .bak if the replace raises "so we never lose a
    launchable app" — made available to every install path (the engine's Linux
    install had the same os.replace with no backup at all).

    `src` is fsync'd first so the bytes are on disk before they become the live
    artifact. A `dst` that does not exist yet (a first install) is not an error:
    there is simply nothing to move aside. Returns True only when `dst` is the
    new artifact.
    """

    def say(msg: str) -> None:
        if log is not None:
            try:
                log(msg)
            except Exception:
                pass

    backup = dst + ".bak"
    had_previous = os.path.exists(dst)
    if had_previous:
        try:
            if os.path.isdir(backup):
                shutil.rmtree(backup, ignore_errors=True)
            shutil.copy2(dst, backup)
        except Exception as e:
            say(f"Update: couldn't back up the current version ({e}); aborting.")
            return False
    try:
        # flush the staged bytes to disk before they become the live artifact
        fd = os.open(src, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass
    try:
        os.replace(src, dst)  # same fs; the old inode stays live while open
        if mode is not None:
            os.chmod(dst, mode)
    except Exception as e:
        say(f"Update: replacing {os.path.basename(dst)} failed: {e}; "
            "restoring backup.")
        if had_previous:
            try:
                os.replace(backup, dst)
            except Exception:
                pass
        return False
    if had_previous:
        try:
            os.remove(backup)  # the new artifact is in place; drop the backup
        except OSError:
            pass
    return True


# --- move-aside backup, for artifacts a copy must not duplicate --------------
#
# atomic_replace's .bak above is a shutil.copy2, which is file-only and pays for
# a whole second copy. Neither is acceptable for an engine tree:
#
#   * a Chromium build is ~300-600MB, so copying doubles peak disk on the very
#     path whose failure mode is a disk-full;
#   * copy2 drops the code signature / resource forks / permissions a macOS
#     .app needs (that is exactly why the installer shells out to `ditto`), so a
#     copied backup restores a bundle Gatekeeper refuses to launch — and a
#     rollback that produces a broken engine is not a rollback;
#   * a recursive copy is a second half-state to reason about, where a rename is
#     atomic.
#
# So the backup here is a RENAME: O(1), no extra disk, and byte-for-byte
# faithful because nothing is rewritten. The caller must place the backup on the
# same filesystem as the artifact (beside it is the easy way to guarantee that).


def discard_aside(path: str) -> None:
    """Best-effort delete of `path`, file or directory. Never raises.

    Used to drop a backup once the new artifact is in place, and to clear the
    way before a rename — os.replace refuses to overwrite a non-empty directory.
    """
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path) or os.path.islink(path):
            os.remove(path)
    except OSError:
        pass


def _dst_is_in_the_way(e: OSError) -> bool:
    """True when `e` from an os.replace says the DESTINATION is what refused
    the rename, rather than something about the source or the filesystem.

    os.replace overwrites a file, a symlink or an empty directory atomically on
    its own. It refuses two destination shapes: a non-empty directory
    (ENOTEMPTY/EEXIST), and a kind mismatch — replacing a directory with a file
    (ENOTDIR) or a file with a directory (EISDIR). Only those justify clearing
    the destination and retrying.

    Everything else — a lock (EACCES / Windows' ERROR_SHARING_VIOLATION=32), a
    full disk (ENOSPC), a cross-device rename (EXDEV) — is a failure the
    destination cannot fix. Clearing it there would destroy the live artifact
    for a rename that is going to fail anyway, which is exactly how a failed
    restore turns into no artifact at all.
    """
    return e.errno in (
        errno.ENOTEMPTY,
        errno.EEXIST,
        errno.ENOTDIR,
        errno.EISDIR,
    )


def move_aside(path: str, backup: str) -> bool:
    """Rename `path` out of the way to `backup`, so a failed install can put it
    back. The directory-capable counterpart to atomic_replace's .bak.

    Returns True when something was moved, False when `path` did not exist —
    a first install has no previous artifact to preserve, which is not an error.
    Raises OSError if the rename itself fails, so a caller that cannot take a
    backup finds out BEFORE it destroys the working build.
    """
    if not os.path.exists(path) and not os.path.islink(path):
        return False
    discard_aside(backup)  # a stale backup would make the rename fail
    os.replace(path, backup)
    return True


def restore_aside(backup: str, path: str) -> bool:
    """Move `backup` back onto `path`, undoing move_aside. Returns True only
    when the previous artifact is genuinely back at `path`.

    Best-effort and NEVER raises: this runs on a failure path that is already
    reporting an error, and a failed restore must not escalate a reported
    install failure into a crash.

    THE RETURN VALUE IS LOAD-BEARING, AND SO IS THE ORDER OF OPERATIONS.
    Whatever sits at `path` now is the half-promoted new artifact and has to go,
    but clearing it FIRST would turn a survivable failure into an unrecoverable
    one: if the rename then fails (an antivirus holding a freshly-written .exe
    raises PermissionError on Windows, and that is the canonical case), the
    destination is already destroyed and the backup is the only copy left. So
    the rename is ATTEMPTED first — os.replace overwrites a file, a symlink or
    an empty directory atomically on its own — and `path` is only cleared when
    it is provably what is blocking the rename (a non-empty directory, or a
    file/directory kind mismatch), as a second attempt.

    A False return means the backup was NOT put back and is STILL THERE. The
    caller must not delete it: it is the last surviving copy of the working
    artifact, and an operator can recover it by hand. Deleting it is the
    difference between "the upgrade failed" and "there is no engine at all".
    """
    try:
        if not os.path.exists(backup) and not os.path.islink(backup):
            return False
        try:
            os.replace(backup, path)
            return True
        except OSError as e:
            # Only clear `path` when it is PROVABLY what blocks the rename —
            # os.replace refuses a non-empty directory, and refuses to cross the
            # file/directory kind boundary. Any other error (a lock, a
            # permission, a full disk) says nothing about `path`, and clearing
            # it there would destroy the destination for a rename that was
            # never going to succeed anyway.
            if not _dst_is_in_the_way(e):
                return False
        discard_aside(path)
        os.replace(backup, path)
        return True
    except Exception:
        return False


# --- download: completion rule shared by both transports ---------------------


def download_is_complete(rc: int, have: int, total: int) -> bool:
    """True when a resumed download attempt left a COMPLETE file on disk.

    A known total is decisive on its own: the bytes are all there, whatever curl
    exited with (a transfer can be cut off after the last byte). Without a total
    we trust the exit code — 0, or a 416 from `-C -` resuming an already-complete
    file — plus a non-empty file.
    """
    if total:
        return have >= total
    return rc in (0,) + _CURL_RANGE_DONE and have > 0


def _size(path: str) -> int:
    try:
        return os.path.getsize(path) if os.path.exists(path) else 0
    except OSError:
        return 0


def curl_download(
    url: str,
    dst: str,
    *,
    timeout_args: list,
    attempts: int,
    total: int = 0,
    deadline: float | None = None,
    proxy_args: list | None = None,
) -> bool:
    """Fetch `url` to `dst` with curl, resuming across dropped connections.

    curl (not urllib) because it keeps a download alive over a flaky Tor circuit
    far better, and because `-C -` resumes from whatever is already on disk. The
    caller owns the timeout policy (`timeout_args` go straight into the command
    line) and any progress reporting; this owns the retry loop and the
    completion rule. Returns True once the file is completely on disk.

    `proxy_args` is caller-owned in exactly the same way and for a sharper
    reason: this function is SHARED (app_update's download and fast_update's
    `_download_small`), so resolving persona's egress policy in HERE would plant
    a second copy of that decision inside a mechanism — the drift
    `services/egress.py` exists to prevent. The authority stays in `egress`; the
    caller resolves the verdict and hands the argv down. Defaulting to empty
    keeps every existing caller byte-identical on the wire.
    """
    if not url or not dst:
        return False
    for _ in range(max(1, attempts)):
        if deadline is not None and time.monotonic() > deadline:
            break
        cmd = [
            "curl", "-fsSL",
            *(proxy_args or []),
            *timeout_args,
            "-C", "-",  # resume from where the partial left off
            "-o", dst,
            url,
        ]
        try:
            rc = subprocess.run(
                cmd, capture_output=True, **_platform.no_window_kwargs()
            ).returncode
        except FileNotFoundError:
            break  # no curl; nothing else to try
        if download_is_complete(rc, _size(dst), total):
            return True
        # otherwise: rc != 0 (timeout/slow/drop) or a short file -> loop + resume
    return False


def resumable_download(
    path: str,
    url: str,
    timeout: int,
    digest: str | None,
    progress=None,
    allow_missing: bool = False,
    max_attempts: int = 40,
    opener_factory=None,
) -> bool:
    """Download `url` to `path` over urllib, resuming across dropped connections
    (Tor), and verify its sha256 before publishing it.

    A missing digest fails closed — the .part is discarded and this returns
    False — unless the caller opts in with allow_missing (see the module
    docstring's policy). Returns True only for a complete, VERIFIED file.
    `progress(done, total)` is called as bytes arrive.

    `opener_factory` lets a caller supply its own opener builder; it MUST still
    preserve Range across redirects (see KeepRangeRedirect). Defaults to this
    module's range_opener.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    attempts = 0
    total = 0
    # GitHub 302s to a signed CDN URL; a range-preserving opener keeps the Range
    # header across that redirect so a resume gets the tail (206) instead of the
    # whole file (200), which over Tor would restart from zero every attempt.
    opener = (opener_factory or range_opener)()
    while attempts < max_attempts:
        attempts += 1
        have = _size(tmp)
        req = urllib.request.Request(url)
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            with opener.open(req, timeout=timeout) as resp:
                cr = resp.headers.get("Content-Range")
                if cr and "/" in cr:
                    try:
                        total = int(cr.rsplit("/", 1)[-1])
                    except ValueError:
                        total = 0
                else:
                    cl = int(resp.headers.get("Content-Length") or 0)
                    total = (have + cl) if cl else 0
                mode = "ab" if have and resp.status == 206 else "wb"
                if mode == "wb":
                    have = 0
                done = have
                with open(tmp, mode) as out:
                    while True:
                        chunk = resp.read(_CHUNK)
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        if progress is not None:
                            progress(done, total)
            if total and _size(tmp) < total:
                continue  # dropped early, resume
            if not verify_file(tmp, digest, allow_missing=allow_missing):
                # Either the digest did not match, or there was no digest to
                # check and the caller did not opt in — an unverifiable asset
                # could be a swap, so refuse it and drop the partial.
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                return False
            os.replace(tmp, path)
            return True
        except Exception:
            continue  # keep the partial for the next resume attempt
    return False
