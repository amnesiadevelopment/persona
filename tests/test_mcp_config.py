from src.api import mcp_config


def test_mcp_url_has_trailing_slash():
    assert mcp_config.mcp_url().endswith("/mcp/")


def test_add_command_includes_token():
    cmd = mcp_config.claude_add_command("TOK123")
    assert "claude mcp add" in cmd
    assert "TOK123" in cmd
    assert "/mcp/" in cmd


def test_config_json_valid_and_has_token():
    import json

    cfg = json.loads(mcp_config.client_config_json("TOK123"))
    server = cfg["mcpServers"]["persona"]
    assert server["type"] == "http"
    assert server["url"].endswith("/mcp/")
    assert server["headers"]["Authorization"] == "Bearer TOK123"
