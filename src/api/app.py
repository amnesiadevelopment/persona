from __future__ import annotations

from typing import TYPE_CHECKING

import contextlib
import hmac

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..core.logging import get_logger
from .routes import browser_router, profiles_router, proxy_router, trash_router
from .mcp_token import get_or_create_token
from .schemas.common import SuccessResponse

if TYPE_CHECKING:
    from ..container import Container

logger = get_logger("api")

API_PREFIX = "/api/v1"


def _ensure_win32_stubs() -> None:
    """mcp's FastMCP unconditionally imports mcp.os.win32.utilities, which imports
    pywin32 (pywintypes/win32api/win32con/win32job). flet build doesn't bundle
    that platform-conditional transitive dep, so the built Windows app raised
    "No module named 'pywintypes'" and the whole MCP layer failed to mount. Those
    win32 bits are only used by mcp's STDIO client; persona serves streamable-http
    and never touches them. When pywin32 is genuinely present (dev, or a bundle
    that carried it) this is a no-op; when it's absent, register harmless stub
    modules so the import chain succeeds and the http server still mounts."""
    import sys
    import types

    for name in ("pywintypes", "win32api", "win32con", "win32job"):
        if name in sys.modules:
            continue
        try:
            __import__(name)
        except Exception:
            sys.modules[name] = types.ModuleType(name)


def _try_build_mcp(container: Container):
    """Build the MCP control server, or return None if its dependencies are
    unavailable. The MCP stack pulls platform-specific packages (e.g. pywin32 on
    Windows); when those are missing it must not take the whole app down — the
    server is off by default anyway, so the app stays fully usable without it."""
    try:
        _ensure_win32_stubs()
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
        # The default /docs, /redoc, /openapi.json match neither /mcp nor /api/v1,
        # so the bearer + DNS-rebinding Host guards below never run for them — any
        # local process / rebound page could enumerate every route + schema. This
        # is a local management API, not a public one; disable the doc endpoints.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.container = container

    token = get_or_create_token()

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        path = request.url.path
        # /health is the only open endpoint. Everything else — /mcp AND the
        # functionally identical /api/v1 REST twin (profile CRUD, browser launch,
        # proxy CRUD, import/export) — requires the bearer token. The REST side
        # used to be wide open, so any local process (or, without a Host check, a
        # DNS-rebinding web page) could drive the browser and read proxy creds.
        protected = path.startswith("/mcp") or (
            path.startswith("/api/v1") and not path.startswith("/api/v1/health")
        )
        if protected:
            # Block DNS-rebinding: the server binds 127.0.0.1, so a legitimate
            # request's Host is loopback. A rebound attacker domain won't match
            # and can't read the token file to forge one either.
            host = (request.headers.get("host") or "").split(":")[0]
            if host not in ("127.0.0.1", "localhost", "[::1]", "::1"):
                return JSONResponse(
                    {"error": "forbidden host"}, status_code=403
                )
            header = request.headers.get("authorization", "")
            # Constant-time compare: a plain `!=` short-circuits at the first
            # differing byte, leaking the matched-prefix length as a timing
            # oracle a co-resident process could use to recover the token.
            supplied = (
                header[len("Bearer "):] if header.startswith("Bearer ") else ""
            )
            if not hmac.compare_digest(supplied, token):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    if mcp is not None:
        app.mount("/mcp", mcp.streamable_http_app())

    app.include_router(profiles_router, prefix=API_PREFIX)
    app.include_router(browser_router, prefix=API_PREFIX)
    app.include_router(proxy_router, prefix=API_PREFIX)
    app.include_router(trash_router, prefix=API_PREFIX)

    @app.get("/api/v1/health", response_model=SuccessResponse, tags=["health"])
    def health_check() -> SuccessResponse:
        return SuccessResponse(message="persona API is running")

    logger.info("FastAPI application created")
    return app
