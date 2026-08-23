from __future__ import annotations

import dataclasses
import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from ...core.logging import get_logger
from ..cdp_endpoint import cdp_info_for
from ..dependencies import get_browser_launcher, get_event_bus, get_profile_manager
from ..helpers import require_profile
from ..refusal_report import refusal_for_attempt
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
            cdp = await cdp_info_for(name, not_before=bl.started_at(name))
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
    from ...services.browser.invisible_launch import is_invisible_installed
    from ...services.browser.process import effective_engine

    engine = effective_engine(profile)
    # Check-only, like the UI launch guard: falling through would let the
    # engine start its own blocking, non-resumable download mid-launch.
    if engine == "firefox" and not is_invisible_installed():
        raise HTTPException(
            status_code=409,
            detail="Firefox engine not installed yet — download it from the app first",
        )

    # An automation launch opens a remote-debugging port that chromium — not
    # FastAPI — serves, so the API bearer token does not protect it and any
    # same-user process can find it (see services/browser/cdp.py). That channel
    # is exactly what the operator's ai_control flag governs, so this lane
    # refuses rather than composing ai_control=True over a stored False. The
    # MCP lane already refuses in the same words (api/mcp_server.py:164).
    #
    # Only engines that actually open that port are gated: the Firefox engine
    # never reads ai_control (services/browser/invisible_launch.py) and is
    # driven through its eval-hook registry behind the API's own auth, so there
    # is no unauthenticated channel to gate and refusing would only remove
    # working automation. If Firefox ever grows a remote-debugging transport,
    # extend this condition instead of leaving it silently under-covering.
    # `engine` is the *effective* engine (resolved above), not profile.engine:
    # a mobile profile storing engine="firefox" launches chromium and so must
    # be gated.
    if automation and engine != "firefox" and not profile.ai_control:
        raise HTTPException(
            status_code=409,
            detail="profile is not AI-enabled (enable AI control first)",
        )

    # Automation mode needs remote debugging on for an external script to
    # attach. The profile already permits it (guarded above); replace rather
    # than mutate so the persisted ai_control flag is never written here.
    launch_profile = (
        dataclasses.replace(profile, ai_control=True) if automation else profile
    )

    def _on_ready() -> None:
        bus.emit()

    def _on_stop() -> None:
        bus.emit()

    # Stamped BEFORE the call so the refusal read below can tell a verdict this
    # attempt produced from one left on record by an earlier attempt. See
    # api/refusal_report.py for why the attempt — not the dict — is the
    # discriminator.
    attempt_at = time.time()
    bl.start_thread(launch_profile, _api_log, on_ready=_on_ready, on_stop=_on_stop)

    # A fail-closed guard refuses INSIDE start_thread, which swallows the
    # exception, records the verdict, and returns the same None a successful
    # launch returns. Without this read the lane composed success=True below and
    # the caller was told a profile opened that never did — the guard's loud stop
    # degraded to a silent no-op for the audience least able to see the
    # server-side log the sentence went to, and most likely to retry in a loop.
    # Answered 409 to match this route's other launch refusals (already running,
    # engine missing, not AI-enabled). Placed BEFORE the CDP wait deliberately:
    # nothing is coming up to attach to, so waiting would only add ~15s to an
    # answer already known.
    refusal = refusal_for_attempt(bl, name, attempt_at)
    if refusal is not None:
        logger.info(
            "API launch refused for %s: %s", name, refusal.kind
        )
        bus.emit()
        raise HTTPException(
            status_code=409,
            detail={
                "error": "launch refused",
                # The stable identifier, for the caller's own branching. The
                # short human label is deliberately not carried: it exists for
                # an operator scanning a list of cards, not for a machine.
                "kind": refusal.kind,
                # The settled operator sentence, passed through untouched —
                # restating it here would fork it at the first edit
                # (services/browser/refusal.py).
                "detail": refusal.detail,
            },
        )

    logger.info("API launched browser for: %s (automation=%s)", name, automation)
    bus.emit()

    cdp: BrowserCdpInfo | None = None
    # Only the chromium engine exposes a Chrome DevTools (CDP) port. The Firefox
    # engine speaks Juggler via Playwright, not CDP, so waiting for a CDP
    # endpoint there just times out (~15s) even though the window is already up.
    if automation and engine != "firefox":
        try:
            cdp = await cdp_info_for(name, not_before=bl.started_at(name))
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
        return await cdp_info_for(name, not_before=bl.started_at(name))
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
