from __future__ import annotations

from typing import TYPE_CHECKING

import contextlib

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..core.logging import get_logger
from .routes import browser_router, profiles_router, proxy_router
from .mcp_token import get_or_create_token
from .schemas.common import SuccessResponse

if TYPE_CHECKING:
    from ..container import Container

logger = get_logger("api")

API_PREFIX = "/api/v1"


def _try_build_mcp(container: Container):
    """Build the MCP control server, or return None if its dependencies are
    unavailable. The MCP stack pulls platform-specific packages (e.g. pywin32 on
    Windows); when those are missing it must not take the whole app down — the
    server is off by default anyway, so the app stays fully usable without it."""
    try:
        from .mcp_server import build_mcp

        return build_mcp(container)
    except Exception as e:
        logger.warning("MCP control server unavailable, continuing without it: %s", e)
        return None


def create_app(container: Container) -> FastAPI:
    """Build and return the FastAPI application."""
    mcp = _try_build_mcp(container)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        if mcp is None:
            yield
            return
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="persona API",
        description="Local REST API for persona profile management",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.container = container

    token = get_or_create_token()

    @app.middleware("http")
    async def _mcp_auth(request: Request, call_next):
        if request.url.path.startswith("/mcp"):
            header = request.headers.get("authorization", "")
            if header != f"Bearer {token}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    if mcp is not None:
        app.mount("/mcp", mcp.streamable_http_app())

    app.include_router(profiles_router, prefix=API_PREFIX)
    app.include_router(browser_router, prefix=API_PREFIX)
    app.include_router(proxy_router, prefix=API_PREFIX)

    @app.get("/api/v1/health", response_model=SuccessResponse, tags=["health"])
    def health_check() -> SuccessResponse:
        return SuccessResponse(message="persona API is running")

    logger.info("FastAPI application created")
    return app
