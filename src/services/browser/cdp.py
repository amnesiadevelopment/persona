"""CDP (remote debugging) port helper for AI-controlled profiles.

Only profiles with ai_control=True are launched with a debugging port. The
port is derived from the profile name so the MCP browser tools know where to
attach without extra bookkeeping.
"""

import zlib

_BASE = 9222
_SPAN = 100


def cdp_port_for(name: str) -> int:
    return _BASE + zlib.crc32(name.encode("utf-8")) % _SPAN
