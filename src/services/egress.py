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
from ..core.redaction import redact
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


def _configured_value(proxy: str | None = None) -> str:
    """The raw configured egress value `resolve()` judges — read from settings
    when `proxy` is omitted, otherwise the caller's own string, stripped.

    Split out of `resolve()` so the refusal-log throttle can key on the value
    that was REJECTED without re-deriving it from a different source. Reading
    the setting twice would be a second copy of "what is configured", which is
    the drift this module exists to prevent; one reader keeps them in step.
    """
    return settings.app_egress_proxy() if proxy is None else (proxy or "").strip()


def resolve(proxy: str | None = None) -> tuple[str, str]:
    """THE resolver: how should persona's own request leave? Returns
    (verdict, transport).

    This is the single place that decision is made. EVERY call site consults it
    — the two engine polls via `fetch_json`, `app_update`'s four curl sites via
    `curl_proxy_args` — and none holds a copy; no second copy may grow in the UI
    layer either. A policy implemented twice is one that disagrees with itself,
    which is the drift argument `proxy_checker._http_get_head` and
    `_is_socks_scheme` are already built on.

    Deliberately phrased as "every" rather than a count: this sentence has
    already been falsified once by growth (it read "Both call sites" when the
    two engine polls were the whole population, and PS-66 made that six), and a
    number here goes stale the next time an arm is added while the invariant it
    is really asserting — that no site routes without asking — does not.

    `proxy` exists for testing and for a future caller that already holds the
    value; when omitted the configured setting is read. Three outcomes:

    * ("direct", "")     — no policy configured. Send exactly as before.
    * ("proxied", url)   — send through `url`, or not at all.
    * ("refuse", reason) — a policy IS configured but is unusable, so nothing
      may be sent. Deliberately distinct from "direct": the difference between
      "no one asked for a proxy" and "someone asked and we cannot honour it" is
      the whole reason this returns a verdict instead of an Optional string.
    """
    value = _configured_value(proxy)
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
#: repeat of the SAME rejected SETTING is not, and a DIFFERENT rejected setting
#: (or a recovery, which clears the state) logs again. This is presentation,
#: not policy: `resolve()` above remains the only place the decision is made.
#:
#: It keys on the rejected VALUE, not on the refusal reason, and that choice is
#: load-bearing rather than incidental: `resolve()` has exactly ONE REFUSE
#: string, so a reason-keyed throttle could never re-arm on a changed reason —
#: the branch would be unreachable by construction and the comment describing it
#: would be fiction. Keying on the value makes it genuinely reachable, and it
#: tracks the operator who most needs the signal: someone who reads the warning,
#: edits the setting, and gets it wrong a SECOND way is actively trying to fix
#: this, and a reason-keyed throttle would answer their new typo with silence.
#: The value is used as an identity here and is NEVER logged — it can embed
#: credentials, and this line reaches the disk-backed log.
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
        # Keyed on the REJECTED VALUE, not on `transport` (the reason): there is
        # only one REFUSE reason, so a reason-keyed throttle could never re-arm.
        # See the comment on _last_curl_refusal above.
        offending = _configured_value(proxy)
        if _last_curl_refusal != offending:
            # As in fetch_json: neither the transport string nor the offending
            # value is logged — both can embed credentials, and this line
            # reaches the disk-backed log.
            logger.warning(
                "App egress: request NOT SENT — %s. Persona will not fall back "
                "to a direct connection; fix or clear the app egress proxy "
                "setting. (Further identical refusals are not repeated.)",
                transport,
            )
            _last_curl_refusal = offending
        raise EgressRefused(transport)

    # Recovery re-arms the log, so a proxy that breaks, is fixed, and breaks
    # again is reported the second time too.
    _last_curl_refusal = None
    return ["--proxy", transport] if verdict == PROXIED else []


def download_opener(proxy: str | None = None):
    """`resolve()`'s BULK-DOWNLOAD arm: the urllib opener a resumable download
    must use to honour the policy. Returns a Range-preserving opener that sends
    DIRECTLY for DIRECT, one that sends through the transport for PROXIED, and
    raises EgressRefused — without returning an opener — for REFUSE.

    This exists because neither existing arm fits the engine BINARY download.
    `fetch_json` reads a whole document into memory, which is right for a
    release-metadata poll and wrong for a ~80-230MB archive; `curl_proxy_args`
    serves a curl transport, and these two call sites are urllib downloads
    whose byte-level progress callback, stall watchdog and 206/range-start
    resume logic are the pinned behaviour of this path, not incidental detail.
    So this is a third SHAPE of the answer — never a third COPY of the
    decision. The verdict comes from `resolve()` above, so a change there moves
    all three arms together.

    Until this existed the two engine release-metadata polls asked "may I speak
    to GitHub, and how?" and the ~80-230MB archive those polls exist to LOCATE
    was then fetched by a transport that never asked — the two halves of one
    unattended startup sequence disagreeing about how persona's traffic leaves.

    Raising on REFUSE rather than returning a direct opener is load-bearing for
    exactly the reason `curl_proxy_args` raises rather than returning []: the
    DIRECT answer is a perfectly usable opener, so handing one back on REFUSE
    would silently degrade "we cannot honour your proxy" into "send from the
    real IP" — with a 200MB transfer behind it.

    The mechanism (how a socket is opened through a SOCKS or HTTP proxy) lives
    in `utils/httpdl`; the POLICY lives here. A call site that built its own
    proxied opener from the setting would be the drift this module prevents.
    """
    global _last_curl_refusal
    from ..utils import httpdl

    verdict, transport = resolve(proxy)

    if verdict == REFUSE:
        # Throttled on the same state as the curl arm, and deliberately sharing
        # it: the engine's unattended poll and app_update's 60-second poll both
        # refuse on the SAME configured value, so keying them together reports
        # a broken setting once rather than once per transport shape. Keyed on
        # the rejected VALUE, never the reason — see _last_curl_refusal.
        offending = _configured_value(proxy)
        if _last_curl_refusal != offending:
            # Neither the transport string nor the offending value is logged —
            # both can embed credentials, and this reaches the disk-backed log.
            logger.warning(
                "App egress: download NOT STARTED — %s. Persona will not fall "
                "back to a direct connection; fix or clear the app egress proxy "
                "setting. (Further identical refusals are not repeated.)",
                transport,
            )
            _last_curl_refusal = offending
        raise EgressRefused(transport)

    # Recovery re-arms the log, so a proxy that breaks, is fixed, and breaks
    # again is reported the second time too.
    _last_curl_refusal = None
    if verdict == PROXIED:
        return httpdl.proxied_range_opener(transport)
    # DIRECT is byte-identical to what these call sites did before this arm
    # existed — the same range_opener, so the Range header, the redirect
    # handling and the resume behaviour are unchanged for every install that
    # has no policy set. That is the whole blast radius of this change.
    return httpdl.range_opener()


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
            #
            # The MESSAGE rides along, not just the class. Four distinct
            # failures reach this arm and they carry only TWO classes (three of
            # them are ValueError), so a class-only line reported "your proxy
            # demands auth", "the release document grew past the cap" and "that
            # was not a JSON document" with byte-identical text. This is the
            # fail-closed path's ONLY notice to an operator whose unattended
            # update poll stopped — they never asked for the request — so the
            # reason this module's docstring (:40-44) promises to log has to
            # actually be in it.
            #
            # (`fetch_json_via_proxy` declares a fifth, "no usable proxy
            # transport", which cannot arrive here: `resolve()` above tests the
            # same `parse_proxy(...) is None` condition first and returns REFUSE,
            # so that input never reaches this try. Recorded because a reader
            # counting `raise` statements in the transport will expect five.)
            #
            # Through `redact` because this is un-authored exception text landing
            # in the DISK-BACKED daily log, which `ui/state.py` then seeds the
            # Activity Log from. That is not a theoretical hazard here: settings
            # store the proxy percent-ENCODED, `parse_proxy` decodes it, and a
            # password decoding to a character `yarl` rejects (e.g. `%5B` -> `[`)
            # makes aiohttp raise `InvalidURL` whose message is the WHOLE
            # credentialed proxy URL. Observed on this path; without `redact` the
            # password lands on disk verbatim. Same single answer PS-160 used for
            # the two sibling sites — a second regex here is exactly what
            # redaction.py's docstring forbids.
            logger.warning(
                "App egress: request through the configured proxy failed (%s) — "
                "NOT retrying directly.",
                redact(f"{type(e).__name__}: {e}"),
            )
            raise

    req = urllib.request.Request(url, headers={"Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)
