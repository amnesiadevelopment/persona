"""MCP server exposing persona management tools to an MCP client (Claude etc.).

Mounted into the existing FastAPI app at /mcp. These are MANAGEMENT tools only
(profiles, proxies, tags) — they never drive a browser, so they add no
automation fingerprint. Browser-control tools (CDP) are a separate, opt-in
layer added later.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from ..services.browser.cdp import cdp_port_for

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
        """List configured proxies with their geo/check info."""
        store = container.proxy_store
        return [
            {
                "name": px.name,
                "country": px.country_code,
                "last_ip": px.last_ip,
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

    async def _page(name: str):
        """Attach to a running ai_control profile via CDP and return its page.
        Raises a clear error if the profile isn't AI-enabled/running.
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
        port = cdp_port_for(name)
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        return pw, browser, page

    @mcp.tool()
    async def browser_navigate(name: str, url: str) -> dict:
        """Navigate the AI-controlled profile's browser to a URL."""
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
        pw, browser, page = await _page(name)
        try:
            await page.fill(selector, text, timeout=5000)
            return {"ok": True}
        finally:
            await browser.close()
            await pw.stop()

    @mcp.tool()
    async def browser_evaluate(name: str, expression: str) -> dict:
        """Evaluate a JavaScript expression in the page and return the result."""
        pw, browser, page = await _page(name)
        try:
            return {"result": await page.evaluate(expression)}
        finally:
            await browser.close()
            await pw.stop()

    return mcp
