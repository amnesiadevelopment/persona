"""Helpers for showing the operator how to connect their MCP client (Claude)."""

import json

from ..core.config import API_HOST, API_PORT


def mcp_url() -> str:
    return f"http://{API_HOST}:{API_PORT}/mcp/"


def claude_add_command(token: str) -> str:
    """The `claude mcp add` one-liner for Claude Code."""
    return (
        f'claude mcp add --transport http persona {mcp_url()} '
        f'--header "Authorization: Bearer {token}"'
    )


def client_config_json(token: str) -> str:
    """The mcpServers JSON snippet for manual configuration."""
    return json.dumps(
        {
            "mcpServers": {
                "persona": {
                    "type": "http",
                    "url": mcp_url(),
                    "headers": {"Authorization": f"Bearer {token}"},
                }
            }
        },
        indent=2,
    )
