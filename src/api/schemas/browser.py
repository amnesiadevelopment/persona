from pydantic import BaseModel, Field


class CdpWebSockets(BaseModel):
    """Connection endpoints for automation frameworks (AdsPower-style `ws`)."""

    puppeteer: str = Field(
        ...,
        description=(
            "Browser-level CDP WebSocket URL: "
            "ws://127.0.0.1:<port>/devtools/browser/<guid>"
        ),
    )
    playwright: str = Field(
        ..., description="Same WS URL; pass to Playwright connect_over_cdp"
    )
    selenium: str = Field(
        ..., description="host:port for Selenium options.debugger_address"
    )


class BrowserCdpInfo(BaseModel):
    """CDP connection info for a running, automation-enabled profile."""

    name: str
    debug_port: int
    ws: CdpWebSockets


class BrowserStatusResponse(BaseModel):
    name: str
    is_running: bool
    cdp: BrowserCdpInfo | None = None


class RunningBrowsersResponse(BaseModel):
    running: list[str]
    count: int


class LaunchResponse(BaseModel):
    success: bool
    message: str
    # Present when launched in automation mode (a CDP endpoint is exposed);
    # null for a plain launch.
    cdp: BrowserCdpInfo | None = None
