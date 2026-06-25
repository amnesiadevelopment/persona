from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from ...core.logging import get_logger
from ..cdp_endpoint import cdp_info_for
from ..dependencies import get_browser_launcher, get_event_bus, get_profile_manager
from ..helpers import require_profile
from ..schemas.browser import (
    BrowserCdpInfo,
    BrowserStatusResponse,
    LaunchResponse,
    RunningBrowsersResponse,
)
from ..schemas.common import ErrorResponse, SuccessResponse

if TYPE_CHECKING:
    from ...core.events import EventBus
    from ...interfaces import IBrowserLauncher, IProfileManager

logger = get_logger("api.browser")

router = APIRouter(prefix="/browser", tags=["browser"])


def _api_log(msg: str) -> None:
    logger.info("[browser] %s", msg)


@router.get("", response_model=RunningBrowsersResponse)
def list_running(
    bl: IBrowserLauncher = Depends(get_browser_launcher),
) -> RunningBrowsersResponse:
    names = sorted(bl.running_profile_names())
    return RunningBrowsersResponse(running=names, count=len(names))


@router.get(
    "/{name}/status",
    response_model=BrowserStatusResponse,
    responses={404: {"model": ErrorResponse}},
)
async def browser_status(
    name: str,
    pm: IProfileManager = Depends(get_profile_manager),
    bl: IBrowserLauncher = Depends(get_browser_launcher),
) -> BrowserStatusResponse:
    require_profile(name, pm)
    running = bl.is_running(name)
    cdp: BrowserCdpInfo | None = None
    if running:
        # Best-effort: only profiles launched with automation expose a CDP port.
        try:
            cdp = await cdp_info_for(name)
        except Exception:
            cdp = None
    return BrowserStatusResponse(name=name, is_running=running, cdp=cdp)


@router.post(
    "/{name}/launch",
    response_model=LaunchResponse,
    status_code=202,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def launch_browser(
    name: str,
    automation: bool = True,
    pm: IProfileManager = Depends(get_profile_manager),
    bl: IBrowserLauncher = Depends(get_browser_launcher),
    bus: EventBus = Depends(get_event_bus),
) -> LaunchResponse:
    require_profile(name, pm)
    if bl.is_running(name):
        raise HTTPException(status_code=409, detail="Browser already running")

    profile = pm.profiles[name]
    # API launches default to automation mode: force remote debugging on so an
    # external script can attach, without mutating the persisted ai_control
    # flag (a manual open from the UI stays CDP-free, which is less detectable).
    launch_profile = (
        dataclasses.replace(profile, ai_control=True) if automation else profile
    )

    def _on_ready() -> None:
        bus.emit()

    def _on_stop() -> None:
        bus.emit()

    bl.start_thread(launch_profile, _api_log, on_ready=_on_ready, on_stop=_on_stop)
    logger.info("API launched browser for: %s (automation=%s)", name, automation)
    bus.emit()

    cdp: BrowserCdpInfo | None = None
    if automation:
        try:
            cdp = await cdp_info_for(name)
        except Exception as exc:
            logger.warning("CDP endpoint not ready for %s: %s", name, exc)

    return LaunchResponse(
        success=True,
        message=f"Browser launching for '{name}'",
        cdp=cdp,
    )


@router.get(
    "/{name}/cdp",
    response_model=BrowserCdpInfo,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def browser_cdp(
    name: str,
    pm: IProfileManager = Depends(get_profile_manager),
    bl: IBrowserLauncher = Depends(get_browser_launcher),
) -> BrowserCdpInfo:
    """Resolve the CDP endpoint for an already-running automation profile."""
    require_profile(name, pm)
    if not bl.is_running(name):
        raise HTTPException(status_code=409, detail="Browser is not running")
    try:
        return await cdp_info_for(name)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail="CDP endpoint not available (profile not launched for automation)",
        ) from exc


@router.post(
    "/{name}/stop",
    response_model=SuccessResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def stop_browser(
    name: str,
    pm: IProfileManager = Depends(get_profile_manager),
    bl: IBrowserLauncher = Depends(get_browser_launcher),
    bus: EventBus = Depends(get_event_bus),
) -> SuccessResponse:
    require_profile(name, pm)
    if not bl.is_running(name):
        raise HTTPException(status_code=409, detail="Browser is not running")

    bl.stop_profile(name)
    logger.info("API stopped browser for: %s", name)
    bus.emit()
    return SuccessResponse(message=f"Browser stopped for '{name}'")
