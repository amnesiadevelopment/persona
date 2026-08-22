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

# How long to wait for the engine to publish its DevToolsActivePort. The
# AppImage extracts itself on first run, which is slow and happens exactly
# once; a short timeout here would read as "chromium is broken" on a cold
# container.
CDP_READY_TIMEOUT = 90.0

# Display numbers are probed from here upward when DISPLAY is unset.
_DISPLAY_BASE = 90


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
        proc = subprocess.Popen(
            ["Xvfb", display, "-screen", "0", "1920x1080x24"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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


def _proxy_server_and_bridge(proxy_url: str):
    """``(--proxy-server value, bridge or None)``.

    Mirrors ``services/browser/process._proxy_arg``: a credentialled upstream
    gets persona's hardened loopback relay and the browser never sees the
    credential. Returns the bridge so the caller can claim its peer gate and
    stop it.
    """
    from ..proxy.bridge import ProxyBridge

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


def _launch_args(
    binary: str,
    profile_dir: str,
    *,
    seed: int,
    declared_machine: str,
    proxy_server: str,
    lang: str = "en-US",
    allow_unsandboxed: bool = False,
) -> "list[str]":
    """The command line, mirroring the product's own chromium launch.

    ``--fingerprint-platform`` is the declared machine and it is the whole
    reason a machine can be varied on this engine at all: chromium HONORS it
    (``services/browser/process.py`` records that the Firefox engine does not,
    reporting Windows regardless — #211).

    ``allow_unsandboxed`` adds ``--no-sandbox``, and it is OFF by default and
    never inferred. persona's own launch path passes that flag NOWHERE — a
    grep of ``src/`` finds no occurrence — so adding it silently here would
    mean the verification tier ran the engine with a security boundary the
    product keeps, and then recorded the result as though it were the product's
    behaviour. The operator asks for it explicitly or the run refuses.
    """
    from ...core import platform as _platform

    args = [binary]
    if _platform.IS_LINUX:
        args.append("--appimage-extract-and-run")
    if allow_unsandboxed:
        # Requested explicitly, recorded in the header, and never a fallback.
        args.append("--no-sandbox")
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
        f"--proxy-server={proxy_server}",
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

    def __init__(
        self,
        proxy_url: str,
        *,
        seed: int = 0,
        declared_machine: str = "windows",
        allow_unsandboxed: bool = False,
    ) -> None:
        import asyncio

        self.seed = seed
        self.declared_machine = declared_machine
        self.allow_unsandboxed = allow_unsandboxed
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
        display, self._xvfb = _ensure_display()
        proxy_server, self._bridge = _proxy_server_and_bridge(proxy_url)
        self._profile_dir = tempfile.mkdtemp(prefix="persona-verify-chromium-")

        args = _launch_args(
            binary,
            self._profile_dir,
            seed=self.seed,
            declared_machine=self.declared_machine,
            proxy_server=proxy_server,
            allow_unsandboxed=self.allow_unsandboxed,
        )
        env = dict(os.environ, DISPLAY=display)
        try:
            self._proc = subprocess.Popen(
                args,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
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
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=15)
            except Exception:
                self._proc.kill()
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
            self._xvfb.terminate()
            self._xvfb = None

    def _remove_profile_dir(self) -> None:
        if self._profile_dir:
            shutil.rmtree(self._profile_dir, ignore_errors=True)
            self._profile_dir = None


__all__ = [
    "CDP_READY_TIMEOUT",
    "ChromiumSession",
    "ChromiumUnavailable",
    "SandboxUnavailable",
    "sandbox_available",
]
