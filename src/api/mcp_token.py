"""Local MCP bearer token: generated once, stored under the user's config dir,
shown in the UI so the operator can paste it into their MCP client.
"""

import os
import pathlib
import secrets

TOKEN_DIR = os.path.expanduser("~/.persona")
TOKEN_FILE = os.path.join(TOKEN_DIR, "mcp_token")


def _path() -> str:
    return os.environ.get("PERSONA_MCP_TOKEN_FILE", TOKEN_FILE)


def get_or_create_token() -> str:
    """Return the persistent local MCP token, creating it on first use."""
    path = _path()
    existing = read_token()
    if existing:
        return existing
    token = secrets.token_urlsafe(24)
    pathlib.Path(os.path.dirname(path)).mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(token)
    os.chmod(path, 0o600)
    return token


def read_token() -> str:
    """Return the stored token, or '' if none exists yet."""
    try:
        with open(_path(), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""
