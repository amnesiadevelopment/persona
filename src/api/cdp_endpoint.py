"""Resolve the browser-level CDP WebSocket endpoint for an automation profile.

fingerprint-chromium exposes the DevTools endpoint on
http://127.0.0.1:<port>/json/version (the port comes from cdp_port_for). The
`webSocketDebuggerUrl` field there is the browser-level URL Playwright/Puppeteer
attach to. The port binds a beat after process start, so we poll briefly.
"""

from __future__ import annotations

import asyncio

import httpx

from ..services.browser.cdp import cdp_port_for
from .schemas.browser import BrowserCdpInfo, CdpWebSockets


async def fetch_browser_ws_url(port: int, *, timeout_s: float = 15.0) -> str:
    """Poll /json/version until it serves the browser-level CDP WS URL.

    Uses trust_env=False so the loopback request is not routed through the
    Tor HTTP(S)_PROXY env vars (which would make it fail in Whonix).
    """
    url = f"http://127.0.0.1:{port}/json/version"
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    last_exc: Exception | None = None
    async with httpx.AsyncClient(trust_env=False) as client:
        while loop.time() < deadline:
            try:
                resp = await client.get(url, timeout=2.0)
                resp.raise_for_status()
                return resp.json()["webSocketDebuggerUrl"]
            except Exception as exc:  # retry on connection-refuse / JSON / key
                last_exc = exc
                await asyncio.sleep(0.25)
    raise RuntimeError(
        f"DevTools endpoint on port {port} not ready within {timeout_s}s"
    ) from last_exc


def build_cdp_info(name: str, port: int, ws_url: str) -> BrowserCdpInfo:
    return BrowserCdpInfo(
        name=name,
        debug_port=port,
        ws=CdpWebSockets(
            puppeteer=ws_url, playwright=ws_url, selenium=f"127.0.0.1:{port}"
        ),
    )


async def cdp_info_for(name: str) -> BrowserCdpInfo:
    """Resolve full CDP info for a running automation profile by its name."""
    port = cdp_port_for(name)
    ws_url = await fetch_browser_ws_url(port)
    return build_cdp_info(name, port, ws_url)
