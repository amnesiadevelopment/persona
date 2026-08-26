"""Drive persona's CHROMIUM engine through the exit, for the SAME readers.

Chromium is one of the two engines persona ships and no checker had ever been
read under it. This module is the missing half. It deliberately adds no reader,
no pattern and no record shape: it produces the same
``{checker_id: {"text"| "error"}}`` mapping ``browser_tier.read_page_texts``
produces, so PS-59's extraction, sorts, refusal paths and record format are
reached unchanged and the two engines cannot drift into two dialects.

Four things make this NOT a flag-sized change, each measured here rather than
assumed.

1. Chromium cannot authenticate to a SOCKS5 proxy
--------------------------------------------------
Playwright refuses the launch outright (*"Browser does not support socks5 proxy
authentication"*) and ``--proxy-server`` cannot carry a credential. So the
credential is stripped into a LOOPBACK RELAY and the browser is pointed at
that. The relay is persona's OWN ``services/proxy/bridge.ProxyBridge`` — the
one PS-25 hardened — and not a second, weaker listener beside it. Its peer gate
is claimed with :meth:`ProxyBridge.bind_to_process` the moment the browser pid
exists, exactly as ``services/browser/process.py`` does it; until then the
listener serves nobody, which is what stops any co-resident process helping
itself to the operator's paid exit.

That is also why ``socks5h`` is normalised to ``socks5`` on the browser's
command line: Chromium's SOCKS5 client already resolves at the proxy, and
``socks5h`` is a curl-ism it rejects with ``ERR_NO_SUPPORTED_PROXIES``. The
remote-DNS property the scheme asks for is preserved, not dropped.

2. The engine is persona's Chromium, never the stock one
---------------------------------------------------------
Stock Chromium is present in this container at ``/usr/bin/chromium`` and IS NOT
THE PRODUCT — a reading taken under it describes stock Chromium and answers
nothing about persona, the same trap ``browser_tier`` records for Firefox. Only
``ENGINE_DIR/fpchrome.AppImage`` is launched, and its absence is an error that
says so rather than a fallback.

3. It is reached over CDP, and the port must be OPENED
-------------------------------------------------------
``verify/transport.py`` attaches to an ALREADY-RUNNING profile and documents
that it never launches and never persists ``ai_control`` — so it cannot be used
here, where there is no operator profile and nothing is running. This module
launches its own throwaway engine with ``--remote-debugging-port=0`` and reads
the bound port back out of ``<user-data-dir>/DevToolsActivePort``. Nothing in
the operator's profile store is created, read or mutated: the user-data-dir is
a temporary directory that is removed at the end of the run.

The port is ephemeral rather than fixed for the reason ``process.py`` records:
a name-derived port is guessable by a co-resident process, and the CDP channel
is unauthenticated.

4. It needs a DISPLAY, and its absence is nearly invisible
-----------------------------------------------------------
The Firefox engine raises ``requires Xvfb`` and marks every row unobtainable
with that reason. Chromium is worse: without a display it can die in ways that
read as a browser fault. So a display is ensured up front and its absence is
refused with the install line, rather than discovered as thirty unobtainable
rows. Headless mode is NOT used — persona ships a headed browser under a
display, and ``--headless`` presents a different surface to exactly the
fingerprinting checkers this tier exists to read.

The anti-leak switches are mirrored from the product's own launch
-----------------------------------------------------------------
DoH off, non-proxied UDP forbidden, QUIC disabled, DNS prefetch off. These are
copied from ``services/browser/process.py`` because a reading taken through an
engine that leaks past its proxy is not a worse reading, it is a misleading
one — and unlike the product, this tier has no window for a human to notice.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlparse

from ..browser.process_group import (
    popen_in_new_session,
    reap_process_group,
)

# How long to wait for the engine to publish its DevToolsActivePort. The
# AppImage extracts itself on first run, which is slow and happens exactly
# once; a short timeout here would read as "chromium is broken" on a cold
# container.
CDP_READY_TIMEOUT = 90.0

# Display numbers are probed from here upward when DISPLAY is unset.
_DISPLAY_BASE = 90

# The "this venue has no exit" sentinel, distinct from every real
# ``--proxy-server`` value. It exists so a no-proxy launch is something the run
# ASKED FOR and the command line STATES, rather than the absence of a flag:
# chromium with no proxy flag at all falls back to the SYSTEM proxy, which is
# neither "no proxy" nor a proxy this run chose. Only the loopback differential
# uses it — see :func:`_proxy_server_and_bridge`.
NO_PROXY = "__no_proxy__"


class ChromiumUnavailable(RuntimeError):
    """persona's chromium engine could not be launched or reached here.

    The chromium counterpart of :class:`browser_tier.EngineUnavailable`, and it
    means the same thing to a caller: the whole tier is unobtainable for ONE
    shared reason, which is recorded on every row rather than crashing the run.
    """


def _engine_binary() -> str:
    """persona's chromium, or a refusal naming what is missing.

    Never falls back to a chromium found on PATH. A stock browser would launch
    happily and produce a complete-looking record of something that is not the
    product — the failure this whole subsystem exists to prevent.
    """
    from ...core import platform as _platform
    from ...core.config import ENGINE_DIR

    path = os.path.join(ENGINE_DIR, _platform.fingerprint_chromium_filename())
    if not os.path.isfile(path):
        raise ChromiumUnavailable(
            f"persona's chromium engine is not installed at {path}. It is "
            "provisioned by the app's own engine updater "
            "(services/engine/updater.download_engine); a chromium found on "
            "PATH is NOT the product and is deliberately not used instead."
        )
    return path


def _ensure_display() -> "tuple[str, subprocess.Popen | None]":
    """Return ``(display, owned_xvfb_or_None)``.

    An inherited ``DISPLAY`` is used as-is and never torn down. Otherwise an
    Xvfb is started and OWNED by this run, so a reading never depends on a
    display someone else happened to leave behind.
    """
    existing = os.environ.get("DISPLAY", "").strip()
    if existing:
        return existing, None

    if not shutil.which("Xvfb"):
        raise ChromiumUnavailable(
            "no DISPLAY and no Xvfb binary: persona's chromium is a HEADED "
            "browser and cannot render a checker page here. Install it with "
            "`sudo apt-get install -y xvfb`. Refusing to fall back to "
            "--headless, which presents a different surface to precisely the "
            "fingerprinting checkers this tier reads."
        )

    for offset in range(_DISPLAY_BASE, _DISPLAY_BASE + 20):
        if os.path.exists(f"/tmp/.X{offset}-lock"):
            continue
        display = f":{offset}"
        proc = popen_in_new_session(
            ["Xvfb", display, "-screen", "0", "1920x1080x24"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Its own process group, so the teardown can signal the GROUP.
            # Xvfb is not a leaf either — it is the X server every browser
            # child here connects to, and a surviving one holds its lock file
            # and keeps the display allocated (PS-192). The helper records the
            # pgid at launch, while the leader is verifiably alive — see
            # process_group.remember_group for why re-resolving later fails.
        )
        # Give it a moment to bind, then confirm it did not die immediately.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            if os.path.exists(f"/tmp/.X{offset}-lock"):
                return display, proc
            time.sleep(0.2)
        if proc.poll() is None:
            proc.terminate()
    raise ChromiumUnavailable("could not start an Xvfb display for chromium")


def _proxy_server_and_bridge(proxy_url: str, *, allow_no_proxy: bool = False):
    """``(--proxy-server value | NO_PROXY, bridge or None)``.

    Mirrors ``services/browser/process._proxy_arg``: a credentialled upstream
    gets persona's hardened loopback relay and the browser never sees the
    credential. Returns the bridge so the caller can claim its peer gate and
    stop it.

    ``allow_no_proxy`` is the LOOPBACK-VENUE waiver, and it is off by default
    for a reason that is the whole point of this tier: a checker run that went
    out unproxied would read the operator's REAL address while every verdict
    parsed and every row landed as READ — the silent wrong reading this module
    exists to prevent. So an empty credential is refused unless the caller says
    explicitly that there is no exit to use.

    The one caller that says so is the local-page differential
    (:mod:`layer_differential`), which serves its page from 127.0.0.1: there is
    no exit in the picture, nothing to leak, and no credential in the container
    (PS-10/PS-69). It returns :data:`NO_PROXY` rather than ``""`` so the flag
    the launch emits is an explicit ``--no-proxy-server`` — chromium's default
    with no flag at all is to read the SYSTEM proxy, which is neither "no
    proxy" nor a proxy this run chose.
    """
    from ..proxy.bridge import ProxyBridge

    if not proxy_url.strip():
        if allow_no_proxy:
            return NO_PROXY, None
        raise ChromiumUnavailable(
            "no proxy credential was given, and this run did not ask for a "
            "no-proxy launch. A checker reading taken without the exit "
            "describes the operator's REAL address while looking perfectly "
            "clean, so it is refused rather than defaulted. Pass "
            "allow_no_proxy=True only for a venue that has no exit at all "
            "(the loopback differential)."
        )

    parsed = urlparse(
        proxy_url if "://" in proxy_url else "socks5://" + proxy_url
    )
    if not parsed.hostname:
        raise ChromiumUnavailable(
            "the proxy credential is not in a form the engine can take"
        )
    if parsed.username:
        bridge = ProxyBridge(proxy_url)
        port = bridge.start()
        # Only the LOCAL port is ever named. The upstream hostname identifies
        # the provider and often carries session/geo labels.
        return f"socks5://127.0.0.1:{port}", bridge
    return f"socks5://{parsed.hostname}:{parsed.port or 1080}", None


def sandbox_available() -> bool:
    """Whether chromium's sandbox can actually work on this host.

    Chromium's zygote needs an unprivileged user namespace. Many hardened
    containers (and Ubuntu 23.10+ with the AppArmor restriction) forbid them,
    and chromium's response is to die with ``No usable sandbox!`` before it
    opens a debug port — measured here as rc=133 with no
    ``DevToolsActivePort``, which reads as "the browser is broken" rather than
    as "this host forbids the sandbox".

    Probed rather than assumed, and the probe is the same syscall chromium
    needs. A False here is a fact about the HOST, never about the engine.
    """
    import ctypes

    # CLONE_NEWUSER. unshare(2) returns 0 when the namespace was created; the
    # child of that namespace is this process, so it is undone by simply not
    # using it — but a failure is what we are actually testing for.
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:  # pragma: no cover - non-glibc hosts
        return False
    pid = os.fork()
    if pid == 0:  # pragma: no cover - exercised in a child process
        # In the child: attempt it and report through the exit status, so the
        # parent's own namespace is never touched whatever the answer.
        os._exit(0 if libc.unshare(0x10000000) == 0 else 1)
    try:
        _pid, status = os.waitpid(pid, 0)
    except OSError:  # pragma: no cover
        return False
    return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


class SandboxUnavailable(ChromiumUnavailable):
    """The host forbids chromium's sandbox and no waiver was given.

    Its own class because the operator's response is specific and the reading
    it would produce is DIFFERENT, not merely absent: an unsandboxed engine can
    be run deliberately, but a record taken under one must say so.
    """


# The /dev/shm floor this tier insists on, in bytes.
#
# Chromium puts its renderer transport surfaces, its GPU command buffers and
# the font-data service's memory in /dev/shm. Docker's default is 64 MiB, and
# under that ceiling chromium does not degrade — it dies mid-page, which is
# what PS-133 was filed as.
#
# 256 MiB is a MARGIN, not a measured knee, and the docstring says so rather
# than implying a precision the measurement does not have. What was measured
# (PS-133, on this container, live against pixelscan.net through the exit) is
# the endpoint pair only:
#
#   * at 64 MiB /dev/shm saturates (peak use pins to the ceiling) and the page
#     dies with `TargetClosedError`, on EVERY seed tried — 4242, 1337, 1, 7,
#     99999 and 31337, six of six;
#   * with ``--disable-dev-shm-usage`` the same six configurations complete the
#     same page, six of six.
#
# Values BETWEEN 64 MiB and 256 MiB were never tested, because raising the
# ceiling needs CAP_SYS_ADMIN this container does not have. So the true knee is
# somewhere at or above 64 MiB and this constant sits deliberately clear of it.
MIN_DEV_SHM_BYTES = 256 * 1024 * 1024


def dev_shm_bytes() -> "int | None":
    """Total capacity of ``/dev/shm`` in bytes, or ``None`` if unreadable.

    CAPACITY, not free space: the ceiling is what chromium runs into, and a
    reading taken when some other process happens to hold a few MiB would make
    the probe's answer depend on who else is on the box.

    ``None`` means the question could not be asked (no ``/dev/shm``, or a
    platform where it means nothing). It is deliberately NOT reported as zero,
    so a caller can tell "this host has no shm to speak of" from "the probe did
    not run" instead of refusing a launch on a missing answer.

    Windows is exactly "a platform where it means nothing", and it reaches that
    answer by a different route than the others: ``os.statvfs`` does not EXIST
    there, so the call raises ``AttributeError`` rather than ``OSError``. That
    is not an error condition to report, it is the question being inapplicable
    — so the absence is checked up front instead of being caught as a failure.
    """
    statvfs = getattr(os, "statvfs", None)
    if statvfs is None:
        # No statvfs at all (Windows). The question does not apply here.
        return None
    try:
        st = statvfs("/dev/shm")
    except OSError:
        return None
    return int(st.f_blocks) * int(st.f_frsize)


class DevShmTooSmall(ChromiumUnavailable):
    """``/dev/shm`` is below :data:`MIN_DEV_SHM_BYTES` and no waiver was given.

    Its own class for the same reason :class:`SandboxUnavailable` is: the
    operator's response is specific, and the failure it prevents is not a
    missing reading but a WRONG one.

    This is the class PS-133 exists to make impossible to mistake for something
    else. On a 64 MiB host chromium dies part-way through a page with
    ``TargetClosedError: Target page, context or browser has been closed`` —
    which carries no cause at all, and which the run then attributes to
    whatever configuration happened to be in the chair. PS-128 read that shape
    as a property of fingerprint seed 4242, filed it as a seed-derived renderer
    crash, and the attribution held up across three runs precisely because the
    real cause was constant and invisible. The cause is in chromium's stderr
    (``font_data_service_impl.cc: Check failed: . : No space left on device``),
    which no record captured.
    """


def _launch_args(
    binary: str,
    profile_dir: str,
    *,
    seed: int,
    declared_machine: str,
    proxy_server: str,
    lang: str = "en-US",
    timezone: str = "",
    allow_unsandboxed: bool = False,
    allow_small_dev_shm: bool = False,
    extension_dirs: "list[str] | None" = None,
) -> "list[str]":
    """The command line, mirroring the product's own chromium launch.

    ``--fingerprint-platform`` is the declared machine and it is the whole
    reason a machine can be varied on this engine at all: chromium HONORS it
    (``services/browser/process.py`` records that the Firefox engine does not,
    reporting Windows regardless — #211).

    ``extension_dirs`` is persona's MASKING LAYER, and its absence was the
    defect PS-103 exists to fix: this tier used to launch the engine binary
    with these flags and load no extension at all, so every reading described
    the packaged engine rather than what an operator's profile presents. The
    dirs are built by the shipped builders (see :mod:`masking_layer`) and are
    loaded exactly as ``spawn_browser`` loads them.

    ``--disable-extensions-except`` rides alongside ``--load-extension``
    deliberately. Without it, chromium's extension-disabling machinery can drop
    an unpacked extension that was nonetheless named on the command line — and
    a masking layer that is silently not running while the flag says it is
    would be the same wrong-subject reading in a new disguise.

    ``allow_unsandboxed`` adds ``--no-sandbox``, and it is OFF by default and
    never inferred. persona's own launch path passes that flag NOWHERE — a
    grep of ``src/`` finds no occurrence — so adding it silently here would
    mean the verification tier ran the engine with a security boundary the
    product keeps, and then recorded the result as though it were the product's
    behaviour. The operator asks for it explicitly or the run refuses.

    ``timezone`` is the EXIT'S zone, and its absence was the defect PS-132
    exists to fix — the same shape as the missing masking layer above, one
    axis over. ``services/browser/process.py`` pins a concrete zone on every
    product launch; this tier pinned none, so the engine fell back to the
    HOST clock and a reading taken behind a Warsaw exit reported the
    container's own UTC. That is not a cosmetic gap in the record: a checker
    cross-checks timezone against the exit address for free, so the harness
    manufactured ``timezone_spoofed`` / ``fingerprint_inconsistent`` verdicts
    against a product that had set the zone correctly all along.

    Measured on the real engine rather than assumed (PS-132, chromium
    148.0.7778.215): with the flag the page reports the zone on all three
    surfaces that carry it — ``Intl...resolvedOptions().timeZone``, the offset
    ``Date`` reports, and formatting derived from it — and with the flag
    absent all three read the host clock. It also BEATS an inherited ``TZ``
    environment variable, which is why ``env_policy`` can leave ``TZ`` alone.

    Empty means "pass no flag" and is the honest default for a venue with no
    exit — the loopback differential has no address for a zone to agree with,
    so inventing one there would be a fact about nothing.

    ``allow_small_dev_shm`` adds ``--disable-dev-shm-usage``, on exactly the
    same terms and for the same reason. It moves chromium's shared-memory
    surfaces off ``/dev/shm`` and onto disk, which is what lets a run complete
    on a host whose ``/dev/shm`` is below :data:`MIN_DEV_SHM_BYTES`. It is OFF
    by default because it is a WORKAROUND, not a default: the flag is not on
    persona's own launch path either, and a record taken under it must say so.
    The alternative — inferring it whenever ``/dev/shm`` looks small — is what
    would turn PS-133's reproducible crash into an intermittent one, since the
    run would silently change surface depending on the host it landed on.

    ORDER MATTERS on Linux and it is not cosmetic: ``--appimage-extract-and-run``
    is consumed by the AppImage RUNTIME, not by chromium, and it must stay the
    first argument. Measured under PS-133 while building the repro — putting
    another flag ahead of it makes the runtime fall back to a FUSE mount and
    die ``rc=127 'fuse: device not found'`` before chromium is reached at all.
    """
    from ...core import platform as _platform

    args = [binary]
    if _platform.IS_LINUX:
        args.append("--appimage-extract-and-run")
    if allow_unsandboxed:
        # Requested explicitly, recorded in the header, and never a fallback.
        args.append("--no-sandbox")
    if timezone:
        # The exit's zone, mirrored from the product's own launch
        # (``process.py``: ``args.append(f"--timezone={_profile_timezone(...)}")``).
        # Emitted only when there IS one: see the docstring on why an empty
        # value passes no flag rather than inventing a zone for a venue that
        # has no exit.
        args.append(f"--timezone={timezone}")
    if allow_small_dev_shm:
        # Requested explicitly, recorded in the header, and never a fallback —
        # exactly as --no-sandbox above. Appended AFTER
        # --appimage-extract-and-run, which the AppImage runtime consumes and
        # which must remain first (see the docstring: rc=127 otherwise).
        args.append("--disable-dev-shm-usage")
    args += [
        f"--user-data-dir={profile_dir}",
        f"--fingerprint={seed}",
        f"--fingerprint-platform={declared_machine}",
        "--fingerprint-brand=Chrome",
        f"--lang={lang}",
        f"--accept-lang={lang},{lang.split('-')[0]}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-search-engine-choice-screen",
        # Ephemeral, unguessable, and read back from DevToolsActivePort. The
        # CDP channel is unauthenticated, so a fixed or name-derived port would
        # be drivable by any co-resident process.
        "--remote-debugging-port=0",
        "--remote-allow-origins=*",
        # The anti-leak block, copied from the product's launch. A reading
        # taken through an engine that resolves or streams past its proxy
        # describes the operator's real address while looking perfect.
        #
        # NO_PROXY is the loopback venue saying there is no exit at all, and it
        # is STATED rather than left off: chromium with no proxy flag reads the
        # SYSTEM proxy, which is neither "no proxy" nor a proxy this run chose.
        (
            "--no-proxy-server"
            if proxy_server == NO_PROXY
            else f"--proxy-server={proxy_server}"
        ),
        "--dns-over-https-mode=off",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--dns-prefetch-disable",
        "--disable-quic",
        "--disable-features=DnsOverHttps,EnableQuic",
    ]
    if _platform.IS_LINUX:
        # Software GL keeps the GPU process alive so the fingerprint WebGL
        # spoofer populates a believable vendor/renderer; --disable-gpu leaves
        # a blank WebGL that flags as fake.
        #
        # This host has no GPU, and that is NOT an exemption. The owner
        # withdrew that reading (2026-08-22, PS-10): there will be no dev-VM
        # and no GPU machine in the loop, and the engine is expected to
        # present a plausible GPU wherever it runs — in the claimed strings
        # AND in the pixels a checker renders. So what this flag buys is a
        # populated gpu_claimed; it does NOT make gpu_rendered the product's
        # own. Both are recorded as PRODUCT rows (vector=gpu_claimed /
        # gpu_rendered, tagged FINGERPRINT), and a red on either is a finding
        # against undetectable-masking rather than something the record
        # excuses as environmental.
        args += [
            "--use-gl=angle",
            "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader",
            "--password-store=basic",
            "--use-mock-keychain",
        ]
    # persona's masking layer. Appended last so it is the final thing before
    # the start URL and reads as one block, exactly as it does in
    # ``spawn_browser``. Both flags carry the SAME comma-joined list: the
    # ``--disable-extensions-except`` half is what stops chromium quietly
    # dropping an unpacked extension the other half named.
    dirs = [d for d in (extension_dirs or []) if d]
    if dirs:
        joined = ",".join(dirs)
        args.append(f"--disable-extensions-except={joined}")
        args.append(f"--load-extension={joined}")
    args.append("about:blank")
    return args


class _SyncPage:
    """A synchronous face over one CDP page.

    Exposes only what the shared checker loop uses — ``goto``, ``inner_text``,
    ``close`` — with the same signatures as the playwright sync page the
    Firefox tier hands back, so ONE loop drives both engines and neither can
    acquire a reading path the other lacks.
    """

    def __init__(self, session: "ChromiumSession", page) -> None:
        self._session = session
        self._page = page

    def goto(self, url: str, *, timeout: float = 0, wait_until: str = "load"):
        return self._session._run(
            self._page.goto(url, timeout=timeout, wait_until=wait_until)
        )

    def inner_text(self, selector: str) -> str:
        return self._session._run(self._page.inner_text(selector))

    def close(self) -> None:
        try:
            self._session._run(self._page.close())
        except Exception:
            pass


class ChromiumSession:
    """A live persona-chromium reachable over CDP, as a context manager.

    Owns a private event loop so the caller stays synchronous, exactly as
    ``verify/transport._ChromiumTransport`` does. Everything it creates — the
    relay, the display, the user-data-dir, the browser — is torn down in
    :meth:`close`, including on a failure part-way through construction.
    """

    engine = "chromium"

    # ``allow_no_proxy`` is the loopback-venue waiver, forwarded to
    # :func:`_proxy_server_and_bridge`. Off by default and never inferred, for
    # the same reason ``allow_unsandboxed`` is: a checker reading taken without
    # the exit describes the operator's REAL address while every verdict parses
    # and every row lands as READ. The one caller that passes it is the
    # local-page differential, whose page is served from 127.0.0.1 and has no
    # exit in the picture at all.

    # ``timezone`` is the EXIT'S zone, and it is the caller's to supply for the
    # reason the credential is: this session knows the proxy URL but never the
    # geography behind it. The checker run has already PROVEN its exit (
    # ``exit_guard.Exit`` carries the zone it observed), so the value handed
    # down here is measured rather than guessed. Empty passes no flag — see
    # :func:`_launch_args`.

    def __init__(
        self,
        proxy_url: str,
        *,
        seed: int = 0,
        declared_machine: str = "windows",
        timezone: str = "",
        allow_unsandboxed: bool = False,
        allow_no_proxy: bool = False,
        allow_small_dev_shm: bool = False,
        install_layer: bool = True,
        include_geo: bool = False,
    ) -> None:
        import asyncio

        from .masking_layer import absent_layer

        self.seed = seed
        self.declared_machine = declared_machine
        self.timezone = timezone
        self.allow_unsandboxed = allow_unsandboxed
        self.allow_no_proxy = allow_no_proxy
        self.allow_small_dev_shm = allow_small_dev_shm
        self.install_layer = install_layer
        self.include_geo = include_geo
        # Whether this launch REALLY dropped the sandbox, read back off the
        # command line in :meth:`_start` rather than echoed from the request.
        # The two can differ — a session that refuses before launching never
        # ran anything to disclose — and a record that reported the REQUEST
        # would be describing a surface that was never presented, which is the
        # exact defect PS-103 exists to close. False until argv proves
        # otherwise.
        self.sandbox_waived = False
        # Whether this launch REALLY worked around a small /dev/shm, read back
        # off the command line in :meth:`_start` for the same reason
        # ``sandbox_waived`` is: the record must describe the surface that was
        # PRESENTED, never the one that was requested. False until argv proves
        # otherwise.
        self.dev_shm_waived = False
        # The host's /dev/shm capacity in bytes, probed in :meth:`_start`, or
        # None where the question could not be asked. Carried on the session so
        # the record can state the NUMBER rather than only the verdict: "64 MiB"
        # tells a later reader what to change, where "too small" does not.
        self.dev_shm_bytes: "int | None" = None
        # What persona's masking layer actually did, for the record header.
        # Initialised to an ABSENT report rather than to None, so a session that
        # dies during construction still hands the caller a truthful answer
        # instead of a value every consumer has to special-case.
        self.layer_report = absent_layer(
            "the chromium session did not get as far as building the layer"
        )
        self._closed = False
        self._proc = None
        self._bridge = None
        self._xvfb = None
        self._profile_dir = None
        self._loop = asyncio.new_event_loop()
        self._pw = None
        self._browser = None
        self._context = None
        try:
            self._start(proxy_url)
        except BaseException:
            self.close()
            raise

    # -- construction -----------------------------------------------------

    def _start(self, proxy_url: str) -> None:
        binary = _engine_binary()
        # Probed BEFORE anything is started, so the refusal costs no browser
        # launch and names the real cause instead of arriving as rc=133 with no
        # debug port — which is what this looks like when it is not checked.
        if not self.allow_unsandboxed and not sandbox_available():
            raise SandboxUnavailable(
                "this host forbids the unprivileged user namespace chromium's "
                "sandbox needs, so persona's chromium dies before opening a "
                "debug port (measured: rc=133, 'No usable sandbox!'). persona's "
                "own launch path never passes --no-sandbox, so it is NOT "
                "assumed here: re-run with allow_unsandboxed=True "
                "(--allow-unsandboxed-chromium) to read anyway. A record taken "
                "that way is tagged with it, because an engine running without "
                "its sandbox is not presenting the product's surface."
            )
        # Probed BEFORE anything is started, for the same reason and in the same
        # place as the sandbox probe above: the refusal must cost no browser
        # launch and must NAME ITS CAUSE, because the failure it prevents does
        # not announce itself. Chromium on a too-small /dev/shm does not refuse
        # to start — it dies PART-WAY THROUGH A PAGE with `TargetClosedError`,
        # a message that carries no cause, and the run then attributes the
        # death to whatever configuration was in the chair. That is not
        # hypothetical: PS-128 read this exact shape as a property of
        # fingerprint seed 4242 and filed PS-133 against it, and the
        # attribution survived three runs because the real cause was constant
        # and never recorded. Measured under PS-133: at 64 MiB every seed tried
        # died (4242, 1337, 1, 7, 99999, 31337 — six of six), and with the
        # workaround every one of them completed the same page.
        shm = dev_shm_bytes()
        self.dev_shm_bytes = shm
        if (
            not self.allow_small_dev_shm
            and shm is not None
            and shm < MIN_DEV_SHM_BYTES
        ):
            raise DevShmTooSmall(
                f"this host's /dev/shm is {shm // (1024 * 1024)} MiB, below "
                f"the {MIN_DEV_SHM_BYTES // (1024 * 1024)} MiB this tier "
                "insists on. Chromium puts its renderer transport, GPU command "
                "buffers and font-data service in /dev/shm, and below that "
                "ceiling it does not degrade — it dies MID-PAGE with "
                "'TargetClosedError: Target page, context or browser has been "
                "closed', whose text names no cause (the cause is in chromium's "
                "stderr: font_data_service_impl.cc 'No space left on device'). "
                "A reading taken there does not fail cleanly; it attributes the "
                "death to whatever configuration was being read, which is "
                "exactly how PS-128 came to report a renderer crash as a "
                "property of fingerprint seed 4242. So it is REFUSED rather "
                "than attempted. Fix the host (docker run --shm-size=1g, or "
                "mount -o remount,size=1g /dev/shm), or re-run with "
                "allow_small_dev_shm=True (--allow-small-dev-shm) to launch "
                "with --disable-dev-shm-usage, which trades shm for disk and "
                "is tagged on the record."
            )
        display, self._xvfb = _ensure_display()
        proxy_server, self._bridge = _proxy_server_and_bridge(
            proxy_url, allow_no_proxy=self.allow_no_proxy
        )
        self._profile_dir = tempfile.mkdtemp(prefix="persona-verify-chromium-")

        # Imported here rather than at module scope for the same reason the
        # engine itself is: `build_chromium_layer` reaches into
        # `services/browser`, and importing an engine's spoof builders must not
        # be a cost of merely importing this module or of printing --help.
        from .masking_layer import absent_layer, build_chromium_layer

        # persona's masking layer, BUILT BEFORE THE PROCESS STARTS because
        # chromium takes it on the command line and has no post-launch
        # equivalent of Firefox's add_init_script. The dirs live inside the
        # session's own user-data-dir, so close() removes them with everything
        # else rather than leaking a spoof set into /tmp.
        if self.install_layer:
            extension_dirs, self.layer_report = build_chromium_layer(
                self._profile_dir,
                self.seed,
                os_type=self.declared_machine,
                include_geo=self.include_geo,
            )
        else:
            # The differential's control arm: the packaged engine with NONE of
            # persona's layer. Deliberate, never a fallback, and the record
            # says which arm it was.
            extension_dirs = []
            self.layer_report = absent_layer(
                "install_layer=False: this reading is of the PACKAGED ENGINE "
                "ONLY, with none of persona's masking layer. It is the control "
                "arm of a differential, not a reading of the product."
            )

        args = _launch_args(
            binary,
            self._profile_dir,
            seed=self.seed,
            declared_machine=self.declared_machine,
            proxy_server=proxy_server,
            timezone=self.timezone,
            allow_unsandboxed=self.allow_unsandboxed,
            allow_small_dev_shm=self.allow_small_dev_shm,
            extension_dirs=extension_dirs,
        )
        # Read back off the COMMAND LINE, not off the request. _launch_args is
        # the single place that decides whether the flag is passed, so asking
        # argv makes the disclosure a fact about the process that ran instead
        # of a second copy of the decision that could drift from it.
        self.sandbox_waived = "--no-sandbox" in args
        self.dev_shm_waived = "--disable-dev-shm-usage" in args
        env = dict(os.environ, DISPLAY=display)
        try:
            self._proc = popen_in_new_session(
                args,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                # PS-192: chromium is a process TREE (zygote, gpu-process, one
                # renderer per tab). Its own session makes that tree a group
                # the teardown can address; without it `terminate()` reaps the
                # parent and orphans ~35 processes per launch, which then
                # exhaust the machine and degrade later launches into a
                # contentless TargetClosedError. The helper also RECORDS the
                # pgid while the leader is verifiably alive, which is what
                # keeps the teardown working after the parent has exited.
            )
        except OSError as exc:
            raise ChromiumUnavailable(
                f"could not start persona's chromium: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        # Claim the relay for THIS browser now that it has a pid. Chromium does
        # not connect from this pid — its network service is a child — so the
        # gate authorises the whole descendant tree. Until this call the
        # listener serves nobody.
        if self._bridge is not None:
            self._bridge.bind_to_process(self._proc.pid)

        port = self._wait_for_cdp_port()
        self._attach(port)

    def _wait_for_cdp_port(self) -> int:
        """Read the port chromium bound, or refuse with why it never did."""
        path = os.path.join(self._profile_dir, "DevToolsActivePort")
        deadline = time.monotonic() + CDP_READY_TIMEOUT
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                stderr = b""
                try:
                    stderr = self._proc.stderr.read() or b""
                except Exception:
                    pass
                raise ChromiumUnavailable(
                    f"persona's chromium exited before opening a debug port "
                    f"(rc={self._proc.returncode}): "
                    f"{stderr.decode('utf-8', 'replace')[-400:]}"
                )
            try:
                with open(path, encoding="utf-8") as handle:
                    first = handle.readline().strip()
                if first:
                    return int(first)
            except (OSError, ValueError):
                pass
            time.sleep(0.25)
        raise ChromiumUnavailable(
            "persona's chromium never published a CDP port "
            f"({path} unreadable after {CDP_READY_TIMEOUT:.0f}s)"
        )

    def _attach(self, port: int) -> None:
        async def _go():
            # Function-local: playwright is not importable in the dev/CI
            # container, and a module-level import would make the whole verify
            # package unimportable exactly where its tests run.
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}"
            )
            if not self._browser.contexts:
                raise ChromiumUnavailable(
                    "persona's chromium exposed no browser context to read"
                )
            self._context = self._browser.contexts[0]

        try:
            self._loop.run_until_complete(_go())
        except ChromiumUnavailable:
            raise
        except Exception as exc:
            raise ChromiumUnavailable(
                f"could not attach to persona's chromium over CDP on port "
                f"{port}: {type(exc).__name__}: {exc}"
            ) from exc

    # -- use ---------------------------------------------------------------

    def _run(self, coro):
        if self._closed:
            raise ChromiumUnavailable("the chromium session is closed")
        return self._loop.run_until_complete(coro)

    def new_page(self) -> _SyncPage:
        """A fresh tab, with the same call shape the Firefox tier returns."""
        return _SyncPage(self, self._run(self._context.new_page()))

    def __enter__(self) -> "ChromiumSession":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- teardown ----------------------------------------------------------

    def close(self) -> None:
        """Idempotent, and every step is independently guarded.

        A failure tearing one thing down must not orphan the next: the relay
        holds an authenticated route to the operator's paid exit, and the
        display and browser are real processes.
        """
        if self._closed:
            return
        self._closed = True

        async def _teardown():
            if self._browser is not None:
                try:
                    # On a connect_over_cdp browser this DISCONNECTS.
                    await self._browser.close()
                except Exception:
                    pass
            if self._pw is not None:
                try:
                    await self._pw.stop()
                except Exception:
                    pass

        try:
            if self._loop.is_closed():
                pass
            else:
                self._loop.run_until_complete(_teardown())
        except Exception:
            pass
        finally:
            self._browser = None
            self._pw = None
            try:
                if not self._loop.is_closed():
                    self._loop.close()
            except Exception:
                pass

        for closer in (
            self._stop_browser,
            self._stop_bridge,
            self._stop_xvfb,
            self._remove_profile_dir,
        ):
            try:
                closer()
            except Exception:
                pass

    def _stop_browser(self) -> None:
        if self._proc is None:
            return
        # PS-192: signal the GROUP, and do it even when poll() says the parent
        # is already gone. The parent's exit status is not evidence about its
        # children — a chromium whose parent died still has a zygote, a
        # gpu-process and every renderer alive and reparented to init. The
        # terminate -> wait -> kill escalation is preserved inside the reaper.
        reap_process_group(self._proc, timeout=15)
        try:
            if self._proc.stderr is not None:
                self._proc.stderr.close()
        except Exception:
            pass
        self._proc = None

    def _stop_bridge(self) -> None:
        if self._bridge is not None:
            self._bridge.stop()
            self._bridge = None

    def _stop_xvfb(self) -> None:
        # Only ever the one this run started; an inherited DISPLAY is None here
        # and is deliberately left alone.
        if self._xvfb is not None:
            # PS-192: group teardown. A surviving Xvfb holds /tmp/.X<n>-lock,
            # so a leaked one burns a display number on every run until the
            # 20-slot search in _ensure_display runs out.
            reap_process_group(self._xvfb, timeout=10)
            self._xvfb = None

    def _remove_profile_dir(self) -> None:
        if self._profile_dir:
            shutil.rmtree(self._profile_dir, ignore_errors=True)
            self._profile_dir = None


__all__ = [
    "CDP_READY_TIMEOUT",
    "MIN_DEV_SHM_BYTES",
    "ChromiumSession",
    "ChromiumUnavailable",
    "DevShmTooSmall",
    "SandboxUnavailable",
    "dev_shm_bytes",
    "sandbox_available",
]
