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
    sent. Raised rather than returned so a refusal can never be mistaken for a
    DOCUMENT: an empty release list reads as "no update available", and a
    caller that got one back would have no way to tell the two apart.

    Be precise about how far that guarantee reaches, because it does NOT
    survive every call site. `updater.fetch_latest_full` and
    `firefox.fetch_latest` both wrap their fetch in a blanket `except
    Exception` that returns their existing ('','','') / ('',False) failure
    sentinel — that predates this module and is what AC4 pins — so at those two
    sites a refusal DOES land on the same value as "nothing found". What the
    exception buys there is narrower but still real: the failure arm is taken
    rather than the success arm, so no caller proceeds as if it holds a valid
    (empty) document, and nothing falls back to a direct send. The operator's
    distinguishing signal at those sites is the WARNING logged below, not the
    return value. A new caller that needs to tell "refused" from "nothing
    found" must catch this type explicitly rather than inspect the result."""


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


#: Trap-2 throttle state, for the CURL arm only. `fetch_json`'s callers poll
#: hourly, so a warning per refusal is 24 lines a day; `app_update`'s poll runs
#: every 60 SECONDS for the life of the process, so the identical line would be
#: ~1,440 a day into a log this module's own comment notes reaches disk. The
#: refusal must stay FINDABLE — a silent skip is indistinguishable from "no new
#: release", which is the whole point of logging it — so this throttles by STATE
#: CHANGE rather than dropping the log: the first refusal is always logged, a
#: repeat of the SAME reason is not, and a changed reason (or a recovery, which
#: clears the state) logs again. This is presentation, not policy: `resolve()`
#: above remains the only place the decision is made.
_last_curl_refusal: str | None = None


def _reset_curl_refusal_log() -> None:
    """Forget the last-logged refusal, so the next one logs again. Exists for
    tests, which run many refusals in one process and would otherwise inherit
    each other's throttle state."""
    global _last_curl_refusal
    _last_curl_refusal = None


def curl_proxy_args(proxy: str | None = None) -> list[str]:
    """`resolve()`'s CURL arm: the argv fragment a curl call site must splice in
    to honour the policy. Returns [] for DIRECT, ["--proxy", url] for PROXIED,
    and raises EgressRefused — without returning argv — for REFUSE.

    This exists because `fetch_json` is not a drop-in for every persona-owned
    request: `app_update`'s transport is `curl` subprocesses, not urllib. That
    is an ADVANTAGE rather than a complication — `curl --proxy socks5h://…`
    performs a real SOCKS5 handshake with remote DNS natively, which is exactly
    the case this module's docstring documents urllib getting wrong (a plain
    CONNECT emitted at a SOCKS port that never answers). The charter's
    socks5h-only rule is satisfied by the transport itself.

    It is a second SHAPE of the answer, never a second COPY of the decision:
    the verdict comes from `resolve()` above, so a change there moves both arms
    together. A call site that built its own `--proxy` argv from the setting
    would be the drift this module exists to prevent.

    Raising on REFUSE rather than returning [] is load-bearing: [] is DIRECT's
    answer, so returning it would silently degrade "we cannot honour your proxy"
    into "send from the real IP" — the precise failure this module was written
    to make impossible.
    """
    global _last_curl_refusal
    verdict, transport = resolve(proxy)

    if verdict == REFUSE:
        if _last_curl_refusal != transport:
            # As in fetch_json: the transport string is NOT logged — it can
            # embed credentials, and this line reaches the disk-backed log.
            logger.warning(
                "App egress: request NOT SENT — %s. Persona will not fall back "
                "to a direct connection; fix or clear the app egress proxy "
                "setting. (Further identical refusals are not repeated.)",
                transport,
            )
            _last_curl_refusal = transport
        raise EgressRefused(transport)

    # Recovery re-arms the log, so a proxy that breaks, is fixed, and breaks
    # again is reported the second time too.
    _last_curl_refusal = None
    return ["--proxy", transport] if verdict == PROXIED else []


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
            # `accept` must ride along: the direct branch below honours it, and
            # a header that survives one branch but not the other would mean
            # turning the policy ON changes the request on the wire.
            return fetch_json_via_proxy_sync(transport, url, timeout, accept=accept)
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
