"""MCP server exposing persona management tools to an MCP client (Claude etc.).

Mounted into the existing FastAPI app at /mcp. These are MANAGEMENT tools only
(profiles, proxies, tags) — they never drive a browser, so they add no
automation fingerprint. Browser-control tools (CDP) are a separate, opt-in
layer added later.
"""

from __future__ import annotations

import os
import time

from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from ..core.config import PERSONA_HOME
from ..services.profile.coherence import IncoherentProfile
from .cdp_endpoint import _resolve_port
from .refusal_report import refusal_for_attempt

if TYPE_CHECKING:
    from ..core.container import Container


# sftp_get/sftp_put move bytes to/from a client-supplied local path. The client
# is off-machine (the LLM) and content it browses can carry prompt injection, so
# an unconfined path lets an attacker WRITE over the token/creds/binary or READ
# proxies.json / ssh_hosts.json / cert keys out to a remote host. Confine every
# local path to a dedicated dir under PERSONA_HOME and reject anything that
# escapes it.
_SFTP_DIR = os.path.join(PERSONA_HOME, "sftp")


def _confine_sftp_path(local_path: str) -> str:
    """Resolve ``local_path`` inside the dedicated sftp dir, or raise ValueError.

    Rejects absolute paths and any ``..`` traversal; the realpath must stay
    inside _SFTP_DIR. Returns the safe absolute path (parent dirs created).
    """
    os.makedirs(_SFTP_DIR, exist_ok=True)
    base = os.path.realpath(_SFTP_DIR)
    # A client path is always interpreted RELATIVE to the sftp dir; an absolute
    # path (or a drive-letter / leading slash) is rejected outright.
    if os.path.isabs(local_path) or (len(local_path) > 1 and local_path[1] == ":"):
        raise ValueError("sftp local_path must be relative to the sftp dir")
    candidate = os.path.realpath(os.path.join(base, local_path))
    if candidate != base and not candidate.startswith(base + os.sep):
        raise ValueError("sftp local_path escapes the sftp dir")
    os.makedirs(os.path.dirname(candidate), exist_ok=True)
    return candidate


def build_mcp(container: Container) -> FastMCP:
    mcp = FastMCP(
        "persona",
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
    )

    @mcp.tool()
    def list_profiles() -> list[dict]:
        """List all browser profiles with their proxy, OS, tags, and run state."""
        pm = container.profile_manager
        bl = container.browser_launcher
        return [
            {
                "name": p.name,
                "os": p.os_type,
                "proxy": p.proxy or "direct",
                "tags": p.tags,
                "running": bl.is_running(p.name),
            }
            for p in pm.list_profiles()
        ]

    @mcp.tool()
    def list_proxies() -> list[dict]:
        """List configured proxies with their coarse geo/check info.

        The exit IP (last_ip) is deliberately omitted: it's the identity's exit
        address, and the response goes to the connected LLM client off-machine —
        the coarse country_code is enough to tell proxies apart.
        """
        store = container.proxy_store
        return [
            {
                "name": px.name,
                "country": px.country_code,
                "ok": px.last_check_ok,
            }
            for px in store.list_proxies()
        ]

    @mcp.tool()
    def create_profile(
        name: str,
        proxy: str = "",
        os_type: str = "windows",
        tags: list[str] | None = None,
    ) -> dict:
        """Create a new profile. proxy is a proxy name (or empty for direct).

        Answers ``{"created": False, "error": "refused", "detail": ...}`` when a
        coherence rule refused the fields — the same structured-refusal shape
        ``launch_profile`` below already uses, rather than letting the exception
        escape our layer to an off-machine caller.

        WHY THIS HANDLER EXISTS (PS-187). ``os_type`` is now refused at create
        when it carries a spelling the engine will not honour (``win``,
        ``mac``, ``darwin``, ...). Before that rule this lane could not raise at
        all: it passes ``(name, proxy, os_type, tags)`` and no ``engine`` or
        ``device_type``, so the pair rules were structurally unreachable through
        it. The storage rule is the FIRST refusal that reaches an MCP caller,
        and the refusal message — which names the canonical spelling to use — is
        the most useful thing the rule produces. Discarding it as an uncaught
        exception would hand the automation client a stack trace where the REST
        lane gives a 400 with the reason and the dialog gives an inline message.
        """
        pm = container.profile_manager
        try:
            ok = pm.add_profile(name, proxy, os_type, tags=tags or [])
        except IncoherentProfile as e:
            return {
                "created": False,
                "name": name,
                "error": "refused",
                "detail": str(e),
            }
        return {"created": ok, "name": name}

    @mcp.tool()
    def assign_tag(profile_names: list[str], tag: str) -> dict:
        """Add a tag to the given profiles."""
        pm = container.profile_manager
        n = pm.assign_tag(profile_names, tag)
        return {"tagged": n, "tag": tag}

    @mcp.tool()
    def launch_profile(name: str) -> dict:
        """Launch the browser for a profile.

        Answers ``{"launched": False, "error": "launch refused", "kind": ...,
        "detail": ...}`` when a fail-closed guard refused the launch — the same
        refusal shape this tool already uses above, now covering the case that
        matters most.

        Answers ``{"launched": False, "error": ...}`` — the two-key
        precondition shape — when a check BEFORE the attempt refused it: no
        such profile, already running, or the Firefox engine not installed.
        """
        pm = container.profile_manager
        bl = container.browser_launcher
        profile = pm.profiles.get(name)
        if profile is None:
            return {"launched": False, "error": "no such profile"}
        if bl.is_running(name):
            return {"launched": False, "error": "already running"}
        # ENGINE READINESS — the precondition the UI and REST doors both perform
        # before start_thread, and the one this lane reached the launch path
        # without (PS-222).
        #
        # Falling through does NOT fail fast, and the failure is worse than a
        # late error. With no engine installed `_binary_path_override()`
        # (engine_install.py:325) returns None — "let the engine resolve its own
        # build" — so the child reaches `from invisible_playwright import
        # InvisiblePlaywright`, fails there, and emits LAUNCH_FAILED on the pipe
        # (invisible_launch.py:3311-3316), AFTER the profile was marked running
        # and a child was forked. That emission is a pipe message, not an
        # exception, so the `refusal_for_attempt` read below finds no verdict and
        # this tool answers {"launched": True} for a browser that never opened.
        # That is the same class of defect PS-82 fixed on this very lane: the
        # off-machine caller told the launch succeeded, with the real reason
        # announced only on a pipe it cannot see.
        #
        # This is NOT the download hazard the two guarding doors describe, and
        # the distinction is measured, not assumed (PS-222). That warning is
        # about `ensure_invisible_installed`, which genuinely downloads and which
        # the launch path correctly never calls — tests/test_process.py:113
        # (`test_needs_fetch_never_triggers_download`) is the shipped guard on
        # that. process.py's `_needs_fetch` flag (process.py:364) has ZERO
        # consumers: `_child` reads cfg key-by-key via .get() and never forwards
        # it, and the engine is handed an explicitly-constructed kwargs dict
        # (invisible_launch.py:3329+), never cfg — so the flag reaches no
        # vendored code and cannot trigger a fetch. Dead config, left in place
        # deliberately; removing it is a separate slice.
        #
        # Resolved on the EFFECTIVE engine, never profile.engine: a mobile
        # profile storing "firefox" actually launches chromium, and reading the
        # stored field would demand the Firefox engine for a launch that will
        # really use chromium. effective_engine's docstring makes install checks
        # follow it specifically; both guarding doors already do.
        #
        # Imported function-locally, like every other consumer: a module-level
        # import closes an import cycle that fails at import time (see the note
        # at effective_engine's own definition).
        from ..services.browser.process import effective_engine

        if effective_engine(profile) == "firefox":
            from ..services.browser.invisible_launch import is_invisible_installed

            if not is_invisible_installed():
                # This lane's OWN two-key precondition shape, mirroring the two
                # refusals directly above — not the four-key
                # {kind, detail} shape, which belongs to a verdict
                # `refusal_for_attempt` read back off the launcher after an
                # attempt actually ran. No attempt runs here, so there is no
                # verdict to report and no new refusal kind is minted.
                #
                # The sentence is deliberately NOT the REST lane's operator
                # sentence. refusal.py explains why a settled sentence copied
                # into a second module forks at the first edit — and REST's
                # remedy ("download it from the app first") addresses a human at
                # the app, which is not this lane's caller. Short lane-native
                # label, exactly like "no such profile" and "already running".
                return {"launched": False, "error": "firefox engine not installed"}
        # CHROMIUM IS DELIBERATELY NOT GUARDED HERE, and the omission is a
        # recorded decision rather than a gap (PS-222 required it be settled
        # either way, never by silence). REST — the other off-machine
        # programmatic door, and this lane's true peer — guards Firefox only;
        # only the UI, where an operator can watch the download progress that
        # makes the refusal legible, guards both. Matching REST keeps the two
        # API doors answering identically. The chromium asymmetry is real and
        # now spans BOTH API doors, which makes it one coherent second instance
        # to close in one slice rather than a half-covered arm here.
        #
        # It is also not free to add: the shipped MCP suites drive
        # chromium-effective profiles with no chromium installed
        # (tests/test_mcp_launch_refusal.py, tests/test_ps198_cert_trust_api_lanes.py),
        # so a chromium arm would refuse before the behaviour those files exist
        # to test could run — inverting suites that own other lanes' semantics.
        #
        # Stamped BEFORE the call: it is what tells a verdict THIS attempt
        # produced from one an earlier attempt left on record. See
        # api/refusal_report.py — the rule lives there so this lane and the REST
        # lane cannot grow two opinions about it.
        attempt_at = time.time()

        def _on_cert_trust(status: str | None) -> None:
            # The Firefox CA import SOFT-FAILS: the launch proceeds with the
            # certificate untrusted and the engine announces the outcome once.
            # This lane dropped that announcement entirely (PS-198) — and the
            # harm was not the silence. A verdict this lane never recorded left
            # the PREVIOUS session's `trusted` standing over a session that ran
            # with its CA untrusted, and it survived a restart: an affirmative
            # clean bill of health for the launch that disproved it.
            #
            # `status=None` is the launcher invalidating that stale verdict at
            # the START of the attempt. It rides the SAME callback as the
            # outcome deliberately: this lane cannot record a verdict without
            # also dropping the one it supersedes.
            pm.set_cert_trust_status(name, status)

        bl.start_thread(
            profile,
            log_callback=lambda _m: None,
            on_cert_trust=_on_cert_trust,
        )
        # A guard refuses inside start_thread, which swallows the exception,
        # records the verdict, and returns the same None a successful launch
        # returns. Without this read the tool answered {"launched": True} for a
        # profile that never opened — the product's loudest stop delivered to an
        # off-machine caller as a success, with no follow-up call able to recover
        # the reason. `kind` is the stable identifier to branch on; `detail` is
        # the settled operator sentence, passed through untouched.
        refusal = refusal_for_attempt(bl, name, attempt_at)
        if refusal is not None:
            return {
                "launched": False,
                "error": "launch refused",
                "kind": refusal.kind,
                "detail": refusal.detail,
            }
        return {"launched": True, "name": name}

    @mcp.tool()
    def stop_profile(name: str) -> dict:
        """Stop the browser for a profile."""
        bl = container.browser_launcher
        ok = bl.stop_profile(name)
        return {"stopped": ok, "name": name}

    def _engine_of(name: str) -> str:
        from ..services.browser.process import effective_engine

        pm = container.profile_manager
        profile = pm.profiles.get(name)
        if profile is None:
            raise ValueError("no such profile")
        if not container.browser_launcher.is_running(name):
            raise ValueError("profile is not running (launch it first)")
        return effective_engine(profile)

    def _ff_hook(name: str):
        """The FF session's published eval/goto hooks, or None for chromium.
        FF has no CDP; its live page is driven through this registry instead."""
        from ..services.browser.invisible_launch import get_ff_eval

        return get_ff_eval(name)

    async def _page(name: str):
        """Attach to a running chromium ai_control profile via CDP and return its
        page. Raises a clear error if the profile isn't AI-enabled/running. FF is
        handled separately (no CDP) via _ff_hook.
        """
        from playwright.async_api import async_playwright

        pm = container.profile_manager
        bl = container.browser_launcher
        profile = pm.profiles.get(name)
        if profile is None:
            raise ValueError("no such profile")
        if not getattr(profile, "ai_control", False):
            raise ValueError("profile is not AI-enabled (enable AI control first)")
        if not bl.is_running(name):
            raise ValueError("profile is not running (launch it first)")
        port = await _resolve_port(name, not_before=bl.started_at(name))
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        return pw, browser, page

    @mcp.tool()
    async def browser_navigate(name: str, url: str) -> dict:
        """Navigate the running profile's browser to a URL (chromium or FF)."""
        import asyncio

        if _engine_of(name) == "firefox":
            hook = _ff_hook(name)
            if hook is None:
                raise ValueError("firefox session not ready for control")
            res = await asyncio.to_thread(hook["goto"], url)
            return {"ok": True, **(res or {})}
        pw, browser, page = await _page(name)
        try:
            await page.goto(url)
            return {"ok": True, "url": page.url, "title": await page.title()}
        finally:
            await browser.close()
            await pw.stop()

    @mcp.tool()
    async def browser_content(name: str) -> dict:
        """Return the current page's URL, title, and visible text."""
        import asyncio

        if _engine_of(name) == "firefox":
            hook = _ff_hook(name)
            if hook is None:
                raise ValueError("firefox session not ready for control")
            text = await asyncio.to_thread(
                hook["eval"], "document.body ? document.body.innerText : ''")
            url = await asyncio.to_thread(hook["eval"], "location.href")
            title = await asyncio.to_thread(hook["eval"], "document.title")
            return {"url": url, "title": title, "text": (text or "")[:5000]}
        pw, browser, page = await _page(name)
        try:
            text = await page.inner_text("body")
            return {
                "url": page.url,
                "title": await page.title(),
                "text": text[:5000],
            }
        finally:
            await browser.close()
            await pw.stop()

    @mcp.tool()
    async def browser_click(name: str, selector: str) -> dict:
        """Click the element matching a CSS selector."""
        import asyncio

        if _engine_of(name) == "firefox":
            hook = _ff_hook(name)
            if hook is None:
                raise ValueError("firefox session not ready for control")
            expr = ("(()=>{const el=document.querySelector(" + repr(selector)
                    + ");if(!el)return false;el.click();return true;})()")
            ok = await asyncio.to_thread(hook["eval"], expr)
            return {"ok": bool(ok)}
        pw, browser, page = await _page(name)
        try:
            await page.click(selector, timeout=5000)
            return {"ok": True, "url": page.url}
        finally:
            await browser.close()
            await pw.stop()

    @mcp.tool()
    async def browser_type(name: str, selector: str, text: str) -> dict:
        """Type text into the element matching a CSS selector."""
        import asyncio

        if _engine_of(name) == "firefox":
            hook = _ff_hook(name)
            if hook is None:
                raise ValueError("firefox session not ready for control")
            expr = ("(()=>{const el=document.querySelector(" + repr(selector)
                    + ");if(!el)return false;el.focus();el.value=" + repr(text)
                    + ";el.dispatchEvent(new Event('input',{bubbles:true}));"
                    "return true;})()")
            ok = await asyncio.to_thread(hook["eval"], expr)
            return {"ok": bool(ok)}
        pw, browser, page = await _page(name)
        try:
            await page.fill(selector, text, timeout=5000)
            return {"ok": True}
        finally:
            await browser.close()
            await pw.stop()

    @mcp.tool()
    async def browser_evaluate(name: str, expression: str) -> dict:
        """Evaluate a JavaScript expression in the page and return the result
        (chromium over CDP, or firefox over its juggler eval hook)."""
        import asyncio

        if _engine_of(name) == "firefox":
            hook = _ff_hook(name)
            if hook is None:
                raise ValueError("firefox session not ready for control")
            return {"result": await asyncio.to_thread(hook["eval"], expression)}
        pw, browser, page = await _page(name)
        try:
            return {"result": await page.evaluate(expression)}
        finally:
            await browser.close()
            await pw.stop()

    # --- SSH / SFTP / tmux tools (route through the host's profile proxy) ---

    def _ssh_target(host_name: str):
        from ..services.proxy.errors import ProxyUnresolvedError
        from ..services.ssh.resolver import target_for

        host = container.ssh_host_store.get(host_name)
        if host is None:
            raise ValueError(f"SSH host {host_name!r} not found")
        try:
            return target_for(
                host, container.profile_manager, container.proxy_store
            )
        except ProxyUnresolvedError as e:
            # Fail closed: never connect DIRECT from the real IP. Surface as a
            # tool error to the client instead of routing around the proxy.
            raise ValueError(str(e)) from e

    @mcp.tool()
    def list_ssh_hosts() -> list[dict]:
        """List saved SSH hosts by name and the profile whose proxy is used.

        Only the name + profile are exposed: the response goes to the connected
        LLM client off-machine, and host/port/user together fingerprint and
        locate the operator's infra + login. ssh_exec resolves the real host
        server-side by name.
        """
        return [
            {"name": h.name, "profile": h.profile}
            for h in container.ssh_host_store.list()
        ]

    @mcp.tool()
    async def ssh_exec(host_name: str, command: str) -> dict:
        """Run a shell command on a saved SSH host (via its profile's proxy).
        Returns exit code, stdout, stderr."""
        import asyncio

        from ..services.ssh import client as ssh

        target = _ssh_target(host_name)
        code, out, err = await asyncio.to_thread(ssh.run_command, target, command)
        return {"exit": code, "stdout": out, "stderr": err}

    @mcp.tool()
    async def tmux_send(host_name: str, session: str, keys: str) -> dict:
        """Send a line of input to a tmux session on the SSH host (creating the
        session if needed)."""
        import asyncio

        from ..services.ssh import client as ssh

        target = _ssh_target(host_name)
        code, out, err = await asyncio.to_thread(
            ssh.tmux_send, target, session, keys
        )
        return {"ok": code == 0, "stderr": err}

    @mcp.tool()
    async def tmux_capture(host_name: str, session: str, lines: int = 200) -> dict:
        """Capture the visible output of a tmux session on the SSH host."""
        import asyncio

        from ..services.ssh import client as ssh

        target = _ssh_target(host_name)
        text = await asyncio.to_thread(ssh.tmux_capture, target, session, lines)
        return {"output": text}

    @mcp.tool()
    async def sftp_list(host_name: str, path: str = ".") -> dict:
        """List a directory on the SSH host over SFTP."""
        import asyncio

        from ..services.ssh import client as ssh

        target = _ssh_target(host_name)
        entries = await asyncio.to_thread(ssh.sftp_list, target, path)
        return {"path": path, "entries": entries}

    @mcp.tool()
    async def sftp_get(host_name: str, remote_path: str, local_path: str) -> dict:
        """Download a file from the SSH host into persona's sftp dir.

        local_path is relative to ~/.persona/sftp — absolute paths and '..' are
        rejected so a download can't overwrite the token/creds/binary.
        """
        import asyncio

        from ..services.ssh import client as ssh

        safe = _confine_sftp_path(local_path)
        target = _ssh_target(host_name)
        await asyncio.to_thread(ssh.sftp_get, target, remote_path, safe)
        return {"ok": True, "local_path": safe}

    @mcp.tool()
    async def sftp_put(host_name: str, local_path: str, remote_path: str) -> dict:
        """Upload a file from persona's sftp dir to the SSH host.

        local_path is relative to ~/.persona/sftp — absolute paths and '..' are
        rejected so creds/keys elsewhere on disk can't be exfiltrated.
        """
        import asyncio

        from ..services.ssh import client as ssh

        safe = _confine_sftp_path(local_path)
        target = _ssh_target(host_name)
        await asyncio.to_thread(ssh.sftp_put, target, safe, remote_path)
        return {"ok": True, "remote_path": remote_path}

    return mcp
