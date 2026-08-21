"""Persona's OWN egress policy — how the APPLICATION's requests leave the host.

This module answers exactly one question, in exactly one place: *when persona
itself reaches out to a third party, how should that request leave?* It is not
about a profile's proxy (that is per-profile, in the proxy store, and governs
what a BROWSER SESSION looks like from the outside); it is about the traffic the
app generates on its own behalf, unattended, whether or not any profile exists.

Why this needed an authority at all
-----------------------------------
Persona polls GitHub for release metadata twice at every startup, on a timer,
with no operator gesture — `_check_engines_periodic` (hourly) and
`_auto_update_engine2_async`. Both used a bare `urllib.request.urlopen`, and
there was no construct anywhere in the tree that decided how those should be
routed. That is a narrow disclosure — it says "someone runs persona", not "this
real IP owns that profile identity" — but the absence of a decision-maker is
what made it unfixable in one place, and it is the reason this file exists.

The env-var rebuttal, and why it only half-survives
---------------------------------------------------
`urlopen` already honours `http_proxy`/`https_proxy`, so "unproxied" would be
an imprecise complaint: an operator who exports `https_proxy` really does route
the HTTP(S) case today. But with `https_proxy=socks5://...` urllib emits a plain
`CONNECT host:443 HTTP/1.1` — at a SOCKS port, which is waiting for a `\\x05`
greeting and never answers. So for Tor / `ssh -D` / SOCKS5 — the schemes this
codebase is built around, and socks5 is persona's DEFAULT — the env-var route
does not silently work, it silently FAILS, and nothing notices or refuses. That
is byte-for-byte the defect class `proxy_checker._is_socks_scheme` documents for
aiohttp. Routing through `proxy_checker`'s real handshake is what fixes it.

The default is DIRECT, and that is deliberate
---------------------------------------------
An unset key changes nothing for anyone: `fetch_json` then performs precisely
the request the call sites performed before this module existed. The charter's
fail-closed rule governs a PROFILE's declared geography, where refusing costs a
single launch. Here, defaulting to refuse would mean no persona in the world
could check for updates until its operator configured a proxy — bricking the
update path, security updates included, for every existing install.

Fail-closed applies ONCE a policy is set, and there it is genuine: a request
that cannot go through the configured transport is NOT SENT, and the reason is
logged. It never falls back to a direct send. An operator who configured a proxy
and silently got a real-IP request would be worse off than one who configured
nothing at all, because they would believe they were covered.
"""

import json
import logging
import urllib.request

from ..core import settings
from ..utils.proxy_checker import fetch_json_via_proxy_sync
from ..utils.proxy_parser import parse_proxy

logger = logging.getLogger("persona")

#: Verdicts the resolver can return. DIRECT is not "no policy" — it is the
#: policy of sending directly, which is what an unset key deliberately means.
DIRECT = "direct"
PROXIED = "proxied"
REFUSE = "refuse"


class EgressRefused(Exception):
    """The configured transport could not carry this request, so it was NOT
    sent. Raised rather than returned so a caller cannot accidentally treat a
    refusal as an empty result — for the update checker, an empty release list
    reads as "no update available", which would freeze the update path silently
    instead of failing visibly."""


def resolve(proxy: str | None = None) -> tuple[str, str]:
    """THE resolver: how should persona's own request leave? Returns
    (verdict, transport).

    This is the single place that decision is made. Both call sites consult it;
    neither holds a copy, and no second copy may grow in the UI layer — a policy
    implemented twice is one that disagrees with itself, which is the drift
    argument `proxy_checker._http_get_head` and `_is_socks_scheme` are already
    built on.

    `proxy` exists for testing and for a future caller that already holds the
    value; when omitted the configured setting is read. Three outcomes:

    * ("direct", "")     — no policy configured. Send exactly as before.
    * ("proxied", url)   — send through `url`, or not at all.
    * ("refuse", reason) — a policy IS configured but is unusable, so nothing
      may be sent. Deliberately distinct from "direct": the difference between
      "no one asked for a proxy" and "someone asked and we cannot honour it" is
      the whole reason this returns a verdict instead of an Optional string.
    """
    value = settings.app_egress_proxy() if proxy is None else (proxy or "").strip()
    if not value:
        return DIRECT, ""
    # Configured-but-unparseable must NEVER degrade to direct. A typo'd proxy is
    # the case where the operator most believes they are covered.
    if parse_proxy(value) is None:
        return REFUSE, "app egress proxy is not a usable proxy URL"
    return PROXIED, value


def fetch_json(
    url: str,
    timeout: int = 20,
    accept: str = "application/vnd.github+json",
    proxy: str | None = None,
) -> dict | list:
    """Fetch `url` as JSON, through whatever `resolve()` says. THE fetch entry
    point for persona's own metadata requests.

    The DIRECT branch is intentionally byte-identical to the `urlopen` the call
    sites used before this module existed — same Request, same Accept header,
    same `json.load` off the response, and notably NO User-Agent added, because
    adding one would change what an unset key does. "The default changes
    nothing" is a guarantee that has to hold on the wire, not just in prose.

    The PROXIED branch hands off to `proxy_checker`, which owns the real SOCKS
    handshake and resolves the target at the exit as a domain name (atyp 0x03) —
    so enabling this does not trade an IP disclosure for a DNS one.

    Raises EgressRefused when policy forbids the send; every other failure
    propagates as whatever the transport raised, so callers keep their existing
    error handling.
    """
    verdict, transport = resolve(proxy)

    if verdict == REFUSE:
        # The operator must be able to find out why their update check stopped;
        # a silent skip here would be indistinguishable from "no new release".
        # The transport string is NOT logged — it can embed credentials, and
        # this line reaches the disk-backed daily log.
        logger.warning(
            "App egress: request NOT SENT — %s. Persona will not fall back to a "
            "direct connection; fix or clear the app egress proxy setting.",
            transport,
        )
        raise EgressRefused(transport)

    if verdict == PROXIED:
        try:
            return fetch_json_via_proxy_sync(transport, url, timeout)
        except Exception as e:
            # Fail CLOSED: the request either went through the configured
            # transport or it did not happen. Retrying directly here is the one
            # thing this module exists to make impossible.
            logger.warning(
                "App egress: request through the configured proxy failed (%s) — "
                "NOT retrying directly.",
                type(e).__name__,
            )
            raise

    req = urllib.request.Request(url, headers={"Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)
