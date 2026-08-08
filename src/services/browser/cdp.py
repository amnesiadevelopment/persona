"""CDP (remote debugging) port helper for AI-controlled profiles.

Only profiles with ai_control=True are launched with a debugging port. The
browser is launched with ``--remote-debugging-port=0``: the kernel assigns an
unpredictable ephemeral port, so a co-resident process cannot guess it and
take the browser over (which would bypass the MCP bearer token guarding the
rest of the local API). Chromium writes the actual port it bound to the first
line of ``<user-data-dir>/DevToolsActivePort``; readers resolve it from there
instead of deriving it from the profile name.

SECURITY NOTE (ai_control exposes an UNAUTHENTICATED control channel): the
DevTools HTTP/WS endpoint is served by chromium itself, NOT by FastAPI, so the
MCP bearer token does not protect it. The ephemeral port is the guard against a
name-based guess, BUT the port is still discoverable by a co-resident, same-user
process via a loopback scan or /proc/net/tcp — and the DevToolsActivePort file is
readable by the same user. There is no way to hide a listening loopback port from
a same-user process, so file permissions add nothing here. Enabling ai_control is
therefore a deliberate trade-off: it opens an unauthenticated CDP channel any
process running as this user can drive. Leave ai_control off for profiles that
don't need automation. (A future hardening would be --remote-debugging-pipe, but
playwright's connect_over_cdp attaches over TCP, not a pipe.)
"""

import os

from ...core.config import DATA_DIR


def read_cdp_port(name: str, *, not_before: float | None = None) -> int:
    """Return the debugging port chromium bound for ``name``.

    Reads ``<profile_dir>/DevToolsActivePort`` (first line is the port). When
    ``not_before`` is given, a file older than that timestamp is treated as a
    stale leftover from a previous run and rejected — so we never attach to a
    port the current process didn't open.
    """
    path = os.path.join(DATA_DIR, name, "DevToolsActivePort")
    try:
        if not_before is not None and os.path.getmtime(path) < not_before:
            raise RuntimeError(
                f"DevToolsActivePort for {name!r} is stale (pre-launch)"
            )
        with open(path, encoding="utf-8") as f:
            first = f.readline().strip()
        return int(first)
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"no live CDP port for {name!r} (DevToolsActivePort unreadable)"
        ) from exc
