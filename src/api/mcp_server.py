"""MCP server exposing persona management tools to an MCP client (Claude etc.).

Mounted into the existing FastAPI app at /mcp. These are MANAGEMENT tools only
(profiles, proxies, tags) — they never drive a browser, so they add no
automation fingerprint. Browser-control tools (CDP) are a separate, opt-in
layer added later.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from .cdp_endpoint import _resolve_port

if TYPE_CHECKING:
    from ..core.container import Container


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
        """Create a new profile. proxy is a proxy name (or empty for direct)."""
        pm = container.profile_manager
        ok = pm.add_profile(name, proxy, os_type, tags=tags or [])
        return {"created": ok, "name": name}

    @mcp.tool()
    def assign_tag(profile_names: list[str], tag: str) -> dict:
        """Add a tag to the given profiles."""
        pm = container.profile_manager
        n = pm.assign_tag(profile_names, tag)
        return {"tagged": n, "tag": tag}

    @mcp.tool()
    def launch_profile(name: str) -> dict:
        """Launch the browser for a profile."""
        pm = container.profile_manager
        bl = container.browser_launcher
        profile = pm.profiles.get(name)
        if profile is None:
            return {"launched": False, "error": "no such profile"}
        if bl.is_running(name):
            return {"launched": False, "error": "already running"}
        bl.start_thread(profile, log_callback=lambda _m: None)
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
        port = await _resolve_port(name)
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
        from ..services.ssh.resolver import target_for

        host = container.ssh_host_store.get(host_name)
        if host is None:
            raise ValueError(f"SSH host {host_name!r} not found")
        return target_for(host, container.profile_manager, container.proxy_store)

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
        """Download a file from the SSH host to a local path."""
        import asyncio

        from ..services.ssh import client as ssh

        target = _ssh_target(host_name)
        await asyncio.to_thread(ssh.sftp_get, target, remote_path, local_path)
        return {"ok": True, "local_path": local_path}

    @mcp.tool()
    async def sftp_put(host_name: str, local_path: str, remote_path: str) -> dict:
        """Upload a local file to the SSH host."""
        import asyncio

        from ..services.ssh import client as ssh

        target = _ssh_target(host_name)
        await asyncio.to_thread(ssh.sftp_put, target, local_path, remote_path)
        return {"ok": True, "remote_path": remote_path}

    return mcp
