"""Who was speaking to the checker — proven from the checker's own answer.

This module exists because of a defect the first live reading produced and its
own author caught (PS-59, closed by PS-62). The TLS/JSON tier answered cleanly,
returned well-formed JA3/JA4/HTTP-2 data, and **none of it described persona**:
it described this repository's Python fetcher. The tell was in the reading::

    user_agent: curl/8.14.1

Tagged FINGERPRINT, as those rows first were, a later Python or OpenSSL upgrade
would have read as **persona's fingerprint moving**, and somebody would have
gone hunting a masking regression that never happened.

The rule this module enforces
------------------------------
**A row is recorded as persona's only once the response shows it came from the
engine.** A row that cannot demonstrate its own origin is HARNESS or
UNOBTAINABLE — never FINGERPRINT. The tag is not decided by which code path
issued the request (that is a belief about our own code); it is decided by
what the checker says it saw (that is evidence).

Why the transport cannot be trusted to speak for itself
--------------------------------------------------------
"The engine made this request, therefore these rows are the engine's" is
exactly the reasoning that produced the original defect, one level up: the
JSON tier was *known* to be fetched by ``socks_fetch`` and the rows were still
tagged FINGERPRINT. Deriving the tag from the response instead means a
mis-wired transport, a proxy that silently answered from somewhere else, or a
future refactor that swaps the client underneath, all land as HARNESS or
UNOBTAINABLE rather than as a false claim about persona.

What the witness is, and what it is NOT
----------------------------------------
The witness is the ``user_agent`` the checker echoes back — the same field that
exposed the original mistake, which is the ticket's own reason for naming it.

It is **not a security control and must not be read as one.** A scripting
client can send any User-Agent it likes, so this cannot detect a *hostile*
client impersonating a browser. That is not the failure being guarded against.
The failure being guarded against is **our own instrument being mistaken for
our own product** — a mix-up between two clients that are both ours and neither
of which is lying about itself. For that, the echoed UA is decisive, and it is
the most direct witness available: it is the checker's report of what it
actually received.

``http_version`` is recorded alongside as **corroboration**, because the two
clients differ there too (the engine negotiates HTTP/2; ``socks_fetch``
negotiated HTTP/1.1 on the live run). It is deliberately NOT part of the
verdict: HTTP/1.1 is a legitimate outcome of a downgrade or a proxy, so
demoting a genuine engine row on that basis would manufacture the opposite
error. It is evidence carried in the row for a reader, not a second gate.
"""

from __future__ import annotations

import re

# --- the three answers ------------------------------------------------------

ENGINE = "engine"
SCRIPTING_CLIENT = "scripting_client"
UNKNOWN = "unknown"

# A real browser engine announces a layout engine. persona ships a patched
# Firefox (Gecko) and a Chromium (AppleWebKit/Chrome), so both are named here:
# a matrix that recognised only the engine it happened to run today would
# quietly record the other one as UNKNOWN and drop every row it produced.
_ENGINE_MARKERS = (
    r"gecko/",
    r"firefox/",
    r"chrome/",
    r"chromium/",
    r"applewebkit/",
    r"safari/",
    r"edg/",
)

# Scripting clients, by their default announcements. Checked FIRST and
# deliberately so: ``python-requests`` and ``curl`` never carry a layout-engine
# token, but a client that carried both would be claiming to be two things at
# once, and the honest reading of that is "the instrument", not "the product".
# Getting this precedence backwards is how a harness row would be promoted to a
# fingerprint row — the exact defect this module exists to prevent.
_SCRIPTING_MARKERS = (
    r"curl/",
    r"wget",
    r"python-requests",
    r"python-urllib",
    r"urllib",
    r"aiohttp",
    r"httpx",
    r"go-http-client",
    r"libwww-perl",
    r"okhttp",
    r"java/",
    r"axios/",
    r"node-fetch",
    r"postmanruntime",
    r"scrapy",
    r"guzzle",
)


def classify_user_agent(user_agent: object) -> str:
    """Say which kind of client the checker saw, from the UA it echoed back.

    Returns :data:`ENGINE`, :data:`SCRIPTING_CLIENT` or :data:`UNKNOWN`.

    :data:`UNKNOWN` is a real answer and not a failure to try — an empty,
    missing or unrecognised UA means the row cannot demonstrate its own origin,
    and the caller's duty is then to record it as UNOBTAINABLE rather than to
    guess. Guessing in either direction is a defect: guessing ENGINE publishes
    a false fingerprint, guessing SCRIPTING_CLIENT buries a real one.
    """
    if not isinstance(user_agent, str):
        return UNKNOWN
    text = user_agent.strip().lower()
    if not text:
        return UNKNOWN
    for marker in _SCRIPTING_MARKERS:
        if re.search(marker, text):
            return SCRIPTING_CLIENT
    for marker in _ENGINE_MARKERS:
        if re.search(marker, text):
            return ENGINE
    return UNKNOWN


def describe(origin: str, user_agent: object, http_version: object = None) -> str:
    """One sentence a reader of the record can act on, without a code trip.

    The UA is quoted in full because it is the evidence, and a row that merely
    said "origin unproven" would send the next reader back to a live run to
    find out what was actually seen.
    """
    shown = user_agent if isinstance(user_agent, str) and user_agent.strip() else "(none)"
    corroboration = ""
    if isinstance(http_version, str) and http_version.strip():
        corroboration = f"; the checker recorded {http_version}"
    if origin == ENGINE:
        return (
            f"the checker saw a browser engine (user_agent: {shown}"
            f"{corroboration}), so this row describes persona"
        )
    if origin == SCRIPTING_CLIENT:
        return (
            f"the checker saw a scripting client (user_agent: {shown}"
            f"{corroboration}), so this row describes THE INSTRUMENT, not "
            "persona"
        )
    return (
        f"the checker's answer does not show which client it came from "
        f"(user_agent: {shown}{corroboration}), so nothing about persona's "
        "fingerprint may be read from it"
    )


__all__ = [
    "ENGINE",
    "SCRIPTING_CLIENT",
    "UNKNOWN",
    "classify_user_agent",
    "describe",
]
