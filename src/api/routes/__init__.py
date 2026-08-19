from .browser import router as browser_router
from .profiles import router as profiles_router
from .proxy import router as proxy_router
from .trash import router as trash_router

__all__ = ["browser_router", "profiles_router", "proxy_router", "trash_router"]
