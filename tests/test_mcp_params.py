import asyncio

from src.api.mcp_server import build_mcp
from src.core.container import Container


def _tool_params():
    mcp = build_mcp(Container())
    tools = asyncio.run(mcp.list_tools())
    return {t.name: set(t.inputSchema.get("properties", {})) for t in tools}


def test_browser_tools_use_name_param():
    params = _tool_params()
    for tool in (
        "browser_navigate",
        "browser_content",
        "browser_click",
        "browser_type",
        "browser_evaluate",
    ):
        assert "name" in params[tool], f"{tool} must take 'name'"
        assert "profile" not in params[tool], f"{tool} still takes 'profile'"


def test_management_and_browser_share_name_param():
    params = _tool_params()
    # launch/stop and browser tools all key off the same 'name'
    assert "name" in params["launch_profile"]
    assert "name" in params["stop_profile"]
    assert "name" in params["browser_navigate"]
