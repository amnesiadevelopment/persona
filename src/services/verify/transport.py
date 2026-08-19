"""The real dual-engine adapter: hand back an ``evaluate`` for a LIVE profile.

This slice adds **no new channel**. It uses only the eval transport persona
already ships, on both engines:

* **chromium** — attaches over CDP with playwright, exactly as
  ``src/api/mcp_server.py`` does (``_resolve_port`` -> ``connect_over_cdp`` ->
  ``page.evaluate``). The debugging port only exists for a profile launched
  with ``ai_control``; this module NEVER launches and NEVER persists that flag.
  A profile without a port gets a clear error telling the operator what to do.
* **firefox** — looks up the eval hook a running FF session publishes
  (``register_ff_eval`` / ``get_ff_eval``). FF speaks juggler, not CDP, so this
  registry is its only channel.

Both ends bottom out in playwright's ``page.evaluate``, and both AWAIT a
returned Promise — which is why an asynchronous probe is expressible as one
expression string with no per-engine harness.

Two things a caller must know:

**The FF registry is per-PROCESS.** ``get_ff_eval`` reads an in-memory dict
populated by the process that launched the session. A standalone CLI run in a
separate shell will not find a firefox session launched by the desktop app —
the error says so explicitly rather than pretending the profile is down.

**playwright is imported function-locally, always.** It is a git-pinned
dependency that is NOT importable in the dev/CI container; a module-level
import here would make the whole ``verify`` package unimportable in exactly the
environment its tests run in. ``mcp_server.py`` already uses this shape.
"""

from __future__ import annotations

from typing import Any


class TransportUnavailable(RuntimeError):
    """The live-page eval channel for this profile cannot be reached.

    Always carries an actionable message: which profile, which engine, and what
    the operator should do about it.
    """


class Transport:
    """A live ``evaluate`` channel plus the engine it speaks to.

    Usable as a context manager; :meth:`close` is idempotent.
    """

    engine: str = ""
    profile: str = ""

    def evaluate(self, expression: str) -> Any:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:
        pass

    def __enter__(self) -> "Transport":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class _FirefoxTransport(Transport):
    def __init__(self, profile: str, hook: dict) -> None:
        self.engine = "firefox"
        self.profile = profile
        self._eval = hook["eval"]

    def evaluate(self, expression: str) -> Any:
        return self._eval(expression)


class _ChromiumTransport(Transport):
    """One CDP attachment reused for the whole probe run.

    Owns a private event loop so the caller can stay synchronous. Re-attaching
    per probe would be dozens of connections and would defeat the requirement
    that a run observes ONE page, once.
    """

    def __init__(self, profile: str, port: int) -> None:
        import asyncio

        self.engine = "chromium"
        self.profile = profile
        self._port = port
        self._closed = False
        self._loop = asyncio.new_event_loop()
        self._pw = None
        self._browser = None
        self._page = None
        try:
            self._loop.run_until_complete(self._attach())
        except BaseException:
            self.close()
            raise

    async def _attach(self) -> None:
        # Function-local: playwright is not importable in the dev/CI container.
        from playwright.async_api import async_playwright

        try:
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self._port}"
            )
        except Exception as exc:
            raise TransportUnavailable(
                f"could not attach to chromium profile {self.profile!r} on CDP "
                f"port {self._port}: {type(exc).__name__}: {exc}. The port may "
                "belong to a previous run — restart the profile in automation "
                "mode and try again."
            ) from exc
        if not self._browser.contexts:
            raise TransportUnavailable(
                f"chromium profile {self.profile!r} has no browser context to "
                "observe (is a window open?)"
            )
        ctx = self._browser.contexts[0]
        # Matches mcp_server._page: use the live tab, opening one only if the
        # context has none. Nothing is navigated — probes read the page as the
        # operator left it.
        self._page = ctx.pages[0] if ctx.pages else await ctx.new_page()

    def evaluate(self, expression: str) -> Any:
        if self._closed:
            raise TransportUnavailable("transport is closed")
        return self._loop.run_until_complete(self._page.evaluate(expression))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._loop.run_until_complete(self._teardown())
        except Exception:
            pass
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _teardown(self) -> None:
        # browser.close() on a connect_over_cdp browser DISCONNECTS; it does not
        # kill the operator's browser. Same call mcp_server makes.
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None


def resolve_engine(profile_name: str) -> str:
    """The engine a profile actually launches on.

    Follows ``effective_engine`` rather than the stored ``engine`` field — a
    mobile profile always launches on chromium regardless of what is stored.
    """
    from ..browser.process import effective_engine
    from ..profile.manager import ProfileManager

    profile = ProfileManager().profiles.get(profile_name)
    if profile is None:
        raise TransportUnavailable(f"no such profile: {profile_name!r}")
    return effective_engine(profile)


def _firefox_transport(profile_name: str) -> Transport:
    from ..browser.invisible_launch import get_ff_eval

    hook = get_ff_eval(profile_name)
    if not hook or not callable(hook.get("eval")):
        raise TransportUnavailable(
            f"firefox profile {profile_name!r} has no live eval hook. Either "
            "the profile is not running, or it was launched by a DIFFERENT "
            "process — the firefox eval registry is in-memory and per-process, "
            "so a session started by the desktop app is not reachable from a "
            "separate CLI run. Record from the process that owns the session."
        )
    return _FirefoxTransport(profile_name, hook)


def _chromium_transport(profile_name: str) -> Transport:
    from ..browser.cdp import read_cdp_port

    try:
        port = read_cdp_port(profile_name)
    except RuntimeError as exc:
        raise TransportUnavailable(
            f"chromium profile {profile_name!r} has no debug port — it is "
            "either not running, or was launched without automation. Launch it "
            f"in automation mode (AI control) and try again. ({exc})"
        ) from exc
    return _ChromiumTransport(profile_name, port)


def evaluate_for(profile_name: str) -> Transport:
    """Return a live :class:`Transport` for an ALREADY-RUNNING profile.

    Never launches anything and never mutates stored profile state. Use as a
    context manager so the channel is released::

        with evaluate_for("acc") as tr:
            results = run_probes(tr.evaluate, ("window", "worker"))

    Raises :class:`TransportUnavailable` with an actionable message when the
    channel does not exist.

    The chromium path drives its own event loop, so it must not be constructed
    from inside a running one.
    """
    import asyncio

    engine = resolve_engine(profile_name)
    if engine == "firefox":
        return _firefox_transport(profile_name)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise TransportUnavailable(
            "the chromium transport owns its own event loop and cannot be "
            "created from inside a running one; call it from a synchronous "
            "context (or a worker thread)"
        )
    return _chromium_transport(profile_name)


__all__ = [
    "Transport",
    "TransportUnavailable",
    "evaluate_for",
    "resolve_engine",
]
