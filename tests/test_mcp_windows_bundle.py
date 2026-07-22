"""The built Windows app raised "No module named 'pywintypes'" and the MCP
control server silently failed to mount (/mcp 404) — the whole management layer
dead on Windows. mcp's FastMCP unconditionally imports mcp.os.win32.utilities,
which imports pywin32; flet build doesn't bundle that platform-conditional
transitive dep. persona's MCP build must survive its absence: the win32 bits are
only used by the stdio client, and persona serves streamable-http.
"""
import builtins

import src.api.app as app_mod
from src.core.container import Container


def test_mcp_mounts_when_pywin32_missing(monkeypatch):
    # Force any not-yet-imported win32 module to fail, simulating the built app
    # where pywin32 didn't bundle.
    for mod in ("mcp", "mcp.server", "mcp.server.fastmcp"):
        import sys
        sys.modules.pop(mod, None)
    real_import = builtins.__import__

    def blocked(name, *a, **k):
        if name in ("pywintypes", "win32api", "win32con", "win32job"):
            raise ModuleNotFoundError(f"No module named '{name}'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blocked)

    app = app_mod.create_app(Container())
    routes = [getattr(r, "path", "") for r in app.routes]
    assert any("/mcp" in r for r in routes), (
        "MCP must mount even when pywin32 is missing (streamable-http needs no win32)"
    )
