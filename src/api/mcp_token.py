"""Local MCP bearer token: generated once, stored under the user's config dir,
shown in the UI so the operator can paste it into their MCP client.
"""

import os
import pathlib
import secrets

from ..core.config import _under_home


def _path() -> str:
    # Honour PERSONA_HOME like every other data file (an explicit
    # PERSONA_MCP_TOKEN_FILE still wins), so an isolated instance gets its OWN
    # token instead of reusing ~/.persona's.
    return _under_home("mcp_token", "PERSONA_MCP_TOKEN_FILE")


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
