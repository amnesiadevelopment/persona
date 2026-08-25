"""Prove the run came out of the expected exit, BEFORE anything is recorded.

This is the first outcome of a checker run and every other outcome depends on
it. It is a separate module, run first, with the authority to stop everything.

The trap this exists for
------------------------
**Direct egress works from this container.** ``ipinfo.io`` and the whole JSON
tier answer ``200`` with no proxy at all. So a run that silently failed to
attach to the exit still looks like a complete, successful reading — the pages
render, the verdicts parse, the file is written, the exit code is 0. It would
simply be a reading of the OPERATOR'S REAL ADDRESS, taken against a dozen
third-party fingerprinting services that log it.

That is not a data-quality problem. It is an Invariant #0 problem, and it is
why "it returned 200" is not accepted here as evidence that the proxy was used.

So the run OBSERVES its own exit
--------------------------------
Not "points at a proxy and assumes". The guard fetches the observed source
address through the proxy and checks the answer against what the exit is
supposed to be: Polish, and reached through the credential we hold. Only then
may a reading be recorded, and the observed exit is recorded ALONGSIDE every
reading — which is also the only thing that makes the rotation analysis
possible at all (a fingerprint reading that moved when only the address moved
is a coupling; without the address in the record, nobody can tell).

Never a fallback to direct
--------------------------
A credential missing from EVERY channel, an unusable credential, a refused
connection, a timeout, an exit that is not Polish — each ENDS the run with a
recorded reason. A missing credential FILE alone does NOT: the credential
arrives on two channels and either can supply it (PS-145, see
`resolve_credential`). What ends the run is that no channel had one.
There is deliberately no code path in this module that produces a "well, try it
without the proxy" outcome, silently or otherwise. An inconclusive run is a
legitimate recordable result; a WRONG one is not, because a wrong one looks
like data.

Rotation is not a fault
-----------------------
The exit is a rotating mobile address and rotation WITHIN POLAND is the design
(owner decision — a fixed exit would permanently hide any coupling between a
fingerprint reading and the address). So this guard checks the COUNTRY, not the
address. It also does not rotate, retry around, or otherwise repair a dead
exit: rotation is the operator's, from the host. Report and stop.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ...core.redaction import redact  # noqa: F401  (re-exported: see below)
from .socks_fetch import DEFAULT_TIMEOUT, FetchFailed, ProxyRefused, fetch_json

# Where the operator's credential lives. Read at call time, never cached to
# disk, never logged, never placed in a record — see `redact`.
DEFAULT_CREDENTIAL_PATH = "/workspace/_secrets/test-proxy.txt"

# The SECOND channel the same credential arrives on. Read as a FALLBACK to the
# file, never instead of it — see `resolve_credential` for why the file wins.
#
# Two channels because one channel failing must not stop a live reading, and
# both have been observed failing INDEPENDENTLY on this fleet within one hour
# (PS-145). The failures were almost exactly complementary, which is why it
# went unnoticed for so long: whoever looked had one of them, and nobody had
# both.
#
#   * `persona-planner`  — file present (117 bytes), variable EMPTY (0 chars).
#   * `persona-reviewer` — freshly recreated: variable present (117 chars),
#     file GONE. At the time the file sat on the container overlay with no
#     host volume, so it died with the container.
#
# The operator has since bind-mounted the file from the host, which closed the
# second failure and made rotation immediate. This constant closes the FIRST
# one: the durable channel the operator maintains was previously invisible to
# this module (`grep -rn PERSONA_TEST_PROXY src/` returned nothing at all).
#
# ⚠️ AN EMPTY VALUE IS "ABSENT", NEVER "NO PROXY NEEDED". This is the single
# most important line attached to this constant, because the variable HAS been
# observed empty in production containers — so empty is the EXPECTED case here
# rather than an edge one. An empty proxy value is cheap to misuse: `curl -x ""`
# connects DIRECTLY and returns perfectly parseable JSON of the operator's real
# address with no error at all. That is the exact shape of the wrong-but-
# plausible reading this whole module exists to prevent, so "unset" and "set to
# nothing" are collapsed to the same refusing outcome.
ENVIRONMENT_CREDENTIAL_VAR = "PERSONA_TEST_PROXY"

# The exit this project is entitled to read checkers from. Poland, because
# persona derives a profile's timezone from proxy geography and every
# geography cross-check on every checker page is taken against it.
EXPECTED_COUNTRY = "PL"

# The SAME question, asked of more than one provider — because a single oracle
# makes the guard's availability the run's availability.
#
# PS-128 measured this failing closed on a HEALTHY exit. ipinfo.io answered
# `HTTP 429 Rate limit hit` through the proxy while the exit itself was
# provably Polish: ipwho.is and api.ipify.org, asked through the same
# credential in the same minute, both returned 95.49.113.111 / PL / Warsaw.
# The rate limit attaches to the EXIT's address, which is a shared mobile one
# that other tenants also use — so it is not ours to clear, cannot be retried
# around, and rotating is the operator's job. Every one of the four readings
# that run was supposed to take was refused, and the refusal message blamed
# the credential and the connection, which were both fine.
#
# The providers are tried IN ORDER and the first one that ANSWERS wins. They
# are redundant oracles for REACHABILITY only — they do not vote, and a
# provider that answers is authoritative:
#
#   * an answer naming a non-Polish country REFUSES the run. It is not a
#     reason to go and ask a friendlier provider until one agrees, which is
#     how a multi-provider guard turns into a way to launder a bad exit.
#   * only a provider that could not be reached, or that did not answer with
#     a verdict at all, advances to the next one. There are two shapes of
#     "no verdict" and BOTH advance: nothing came back (the 429 above), and
#     something came back carrying no country. The second is easy to miss —
#     the normaliser coerces a missing country to "", so a partial body that
#     is not caught in the loop reaches the country comparison as empty and
#     refuses a HEALTHY exit while reporting it as "(unknown)".
#
# Every entry is fetched through the proxy argument like every other fetch in
# this module. Adding providers widens what can be OBSERVED; it does not widen
# what is ACCEPTED, and there is still no path here that reaches the network
# without the credential.
# Only providers that can answer the GEOGRAPHY question belong here. An
# address-only endpoint (api.ipify.org) is left out because it can never
# answer it: it returns 200 with a perfectly good `{"ip": ...}` and no
# country, so it would occupy a slot in the order while being incapable of
# proving anything. It is no longer DANGEROUS to list — since the no-country
# branch above, such a body is treated as a non-answer and simply advances to
# the next provider — but a provider that can only ever be skipped does not
# belong in a list whose purpose is to answer.
EXIT_OBSERVATION_URLS = (
    "https://ipinfo.io/json",
    "https://ipwho.is/",
)


class ExitNotProven(RuntimeError):
    """The run may not record anything.

    Raised for every way the exit can fail to be what it must be. Deliberately
    ONE exception class for "no credential", "no connection", "wrong country":
    the caller's correct response to all of them is identical — stop, record
    the reason, do not fall back — and giving them separate classes invites a
    caller to handle one of them differently. The MESSAGE distinguishes them,
    because a human needs to know which; the CONTROL FLOW does not.
    """


@dataclass(frozen=True)
class Exit:
    """The exit a run was observed to come out of.

    Recorded alongside every reading in the run. Carries no credential — see
    the module docstring: this object is written to a file that is committed.
    """

    ip: str
    country: str
    region: str = ""
    city: str = ""
    org: str = ""
    timezone: str = ""

    def as_record(self) -> dict:
        return {
            "ip": self.ip,
            "country": self.country,
            "region": self.region,
            "city": self.city,
            "org": self.org,
            "timezone": self.timezone,
        }


# `redact` USED TO BE DEFINED HERE, and is now imported at the top of this
# module and re-exported. It moved to `core.redaction` in PS-160 so
# `proxy.bridge` — which also writes un-authored exception text to an
# operator-visible place, and may not import this package — runs the SAME rule
# rather than a second copy of the regex. A redaction bug fixed in one copy and
# not the other is worse than no redaction, because the second copy still looks
# guarded. The name stays importable from THIS module because that is where
# every existing caller reaches for it, and because this module's redaction
# discipline is the reason the rule exists at all.


# The two SOCKS5 stages, told apart by the class name PySocks raised.
#
# A SOCKS5 connection is negotiated in two stages, and they fail for opposite
# reasons. PySocks (1.7.1, pinned `PySocks>=1.7` in `requirements.txt`) raises
# a DIFFERENT class at each, and `socks_fetch.fetch` wraps the failure as
# `f"{type(exc).__name__}: {exc}"` — so the class name arrives in the
# `FetchFailed` text this module receives, and the stage is readable here:
#
# ⚠️ THAT LAST CLAUSE ONCE READ "without touching the fetcher", AND IT WAS
# FALSE. It is recorded here because it cost three shipped rounds. PySocks
# raises the per-stage class at the sites named below, and then DESTROYS it on
# the way out: `socksocket.connect` catches the negotiation in
# `except socket.error` (`socks.py:810-814`) and re-raises as
# `GeneralProxyError`, an arm that shadows the `except ProxyError` arm at
# `:817` because `ProxyError` subclasses `OSError`. Driven through a real
# relay, all eight connect-stage reply codes AND an auth rejection arrived
# here as `GeneralProxyError`, so this predicate matched NOTHING and the
# generic wording below was what an operator actually saw.
#
# The distinction reaches this module only because `socks_fetch` now recovers
# it — see `_reported_failure` there, which unwraps `ProxyError.socket_err`.
# This module reads a class name; it does not establish that the class name is
# the right one. Reading the code that RAISES tells you what is raised, not
# what arrives.
#
#   * AUTH     — `SOCKS5AuthError`: the relay rejected the credential itself.
#   * CONNECT  — `SOCKS5Error`, message formatted `"{:#04x}: {}"`: the relay
#     ACCEPTED the credential and then could not give this run an exit.
#   * relay unreachable — `ProxyConnectionError`: nothing was negotiated at
#     all, so neither stage was reached.
#
# The CONNECT one is the interesting case and the reason this distinction is
# drawn. The credential this project holds pins a sticky session token, and
# when that token dies the relay still authenticates it and then has no exit
# to allocate — auth succeeds, allocation fails. Reported as "the connection
# may be refused, timed out, or the credential unusable" that is actively
# misleading: it points at the relay and at the credential, both of which are
# fine, and it looks identical to a proxy that is simply down.
#
# The class name is read as the FIRST TOKEN of the wrapped text, never as a
# substring of it. That distinction is load-bearing rather than fastidious:
# `failures` also carries text this module did not author. `fetch_json` echoes
# the checker's own response body into its message (`socks_fetch.py:206-216`,
# `first 120 chars: ...`), that `FetchFailed` is caught below, and it `continue`s
# BEFORE `reached_any` is set — so a remote body reaches this predicate. A page
# that merely mentions `SOCKS5Error` (an error-index URL, a status page) would
# otherwise be read as this run's own SOCKS negotiation failing, and the guard
# would tell the operator authentication succeeded on a run where no SOCKS
# negotiation failed at all. Anchoring to the first token cannot be fooled that
# way, and it retires the "SOCKS5Error is not a substring of SOCKS5AuthError"
# argument rather than depending on it.
_CONNECT_STAGE_MARKER = "SOCKS5Error"

# Connect-stage reply codes that mean NO EXIT WAS ALLOCATED for this run.
#
# All eight codes below arrive AFTER authentication has succeeded (PySocks
# raises `SOCKS5AuthError` for the auth stage and only reaches the reply-code
# read at `socks.py:533` once auth is complete — `socks.py:505`), so "auth
# passed" is true for every one of them. What is NOT true for every one of them
# is "the relay had no exit to give this run":
#
#   * 0x01 general failure / 0x02 not allowed by ruleset — RELAY-side. The
#     relay declined to allocate. This is the shape a dead sticky session token
#     takes, so these are the only codes that may name it.
#   * 0x03 network unreachable / 0x04 host unreachable / 0x05 refused /
#     0x06 TTL expired — DESTINATION-side. An exit WAS allocated and the target
#     was unreachable or refused FROM it. Claiming "refused to allocate an
#     exit" here is false, and blaming a session token points the operator at
#     one that is working.
#   * 0x07 bad command / 0x08 bad address type — protocol-level disagreement,
#     neither of the above.
#
# Hence the stage is reported for all of them and the CAUSE only where it is
# known. The module's own standard is that an actively misleading message is a
# worse failure than a merely unhelpful one (see `_normalise_observation` and
# the `not reached_any` arm below); a confident wrong attribution on four of
# eight codes is exactly that failure.
_NO_EXIT_ALLOCATED_CODES = frozenset({0x01, 0x02})

# The DESTINATION-side group, named so the third group can exist in the code
# and not only in the comment above.
#
# The comment describes three groups; before this constant the code implemented
# two, and the third — `0x07`/`0x08`, "neither of the above" — fell into the
# `else` of a two-way branch and inherited prose written for THIS group. That
# prose says an exit WAS allocated and the session token is not implicated.
# For a protocol-level disagreement no exit need have been allocated at all,
# so that is a confident claim about something the code has not established —
# the same defect this module exists to remove, one group over.
#
# Membership is therefore stated POSITIVELY for both attributing arms, and
# anything in neither set — including a reply code that could not be parsed —
# gets the unattributed report `_connect_stage_code` already promises. A branch
# selected by "not the other one" attributes by default; these two sets
# attribute only on a code actually identified.
_DESTINATION_SIDE_CODES = frozenset({0x03, 0x04, 0x05, 0x06})


def _wrapped_class_name(text: str) -> str:
    """The exception class name ``socks_fetch`` prefixed, or ``""``.

    ``socks_fetch.fetch`` wraps an unknown exception as
    ``f"{type(exc).__name__}: {exc}"`` (`socks_fetch.py:185`), so the class
    name is the text BEFORE the first colon — a position, not a substring.
    Anything that is not a bare identifier there (``HTTP 429: ...``, a redacted
    URL, a checker's own prose) is not a wrapped class name and returns ``""``.
    """
    head = text.split(":", 1)[0].strip()
    return head if head.isidentifier() else ""


def _connect_stage_code(text: str) -> "int | None":
    """The SOCKS5 reply code in a wrapped ``SOCKS5Error``, or ``None``.

    PySocks formats the message ``"{:#04x}: {}"`` (`socks.py:533`), so the
    wrapped text reads ``SOCKS5Error: 0x04: Host unreachable``. ``None`` means
    the code could not be read, which is reported as an unattributed
    connect-stage failure rather than guessed at.
    """
    if _wrapped_class_name(text) != _CONNECT_STAGE_MARKER:
        return None
    rest = text.split(":", 2)
    if len(rest) < 2:
        return None
    try:
        return int(rest[1].strip(), 16)
    except ValueError:
        return None


def _to_socks5h(raw: str) -> "str | None":
    """Rewrite a socks5 credential to ``socks5h`` form, or ``None``.

    **THE ONE PLACE A SCHEME IS DECIDED IN THIS MODULE.** Both sources feed
    THIS function rather than each carrying its own copy of the rule, because
    the rule is a security property and a second copy is a second thing to get
    wrong: ``socks5h`` sends the hostname for the EXIT to resolve, while plain
    ``socks5`` resolves it LOCALLY and leaks a DNS query naming the very
    checker being read — from the operator's own resolver, which is precisely
    what routing through the exit is meant to prevent.

    ``None`` means "not a socks5 URL", reported by the caller as a source that
    could not be used. It never means "use it as-is": a scheme this module does
    not recognise is not a scheme it may guess at.

    The browser lane deliberately keeps the plain ``socks5`` scheme
    (``process.py``: Chromium rejects ``socks5h`` with
    ``ERR_NO_SUPPORTED_PROXIES`` and performs remote DNS itself). The two lanes
    differ ON PURPOSE — do not "fix" either to match the other.
    """
    if raw.startswith("socks5://"):
        return "socks5h://" + raw[len("socks5://"):]
    if raw.startswith("socks5h://"):
        return raw
    return None


# The source names, as they appear in a record. Short, stable and NOT
# credential-shaped: they name a CHANNEL, never a value.
SOURCE_FILE = "file"
SOURCE_ENVIRONMENT = "environment"


@dataclass(frozen=True)
class _Candidate:
    """One channel's answer, and WHY it answered that way.

    ``proxy_url`` is ``None`` for every unusable disposition, so "can this be
    used" is one check rather than a re-reading of the prose.

    ``origin`` is a PATH or a VARIABLE NAME — a coordinate, never a value — so
    it is safe in a refusal message. ``why`` is built from that coordinate and
    a fixed phrase: no branch here interpolates the credential itself, which
    is what keeps a second source from becoming the one path that bypasses
    :func:`redact`.

    ⚠️ ONE EXCEPTION, and it is the reason this paragraph is not a proof:
    ``_from_file``'s ``OSError`` arm interpolates the EXCEPTION TEXT, which
    this module did not author and cannot make promises about. The safety
    there does not come from the value being known-harmless — it comes from
    both readers of ``why`` passing it through :func:`redact` (the refusal in
    the ``not usable`` arm, and ``detail`` on the fallback arm). Anything that
    adds a THIRD reader of ``why`` must redact it too, and a branch that
    interpolates un-authored text without that is the leak this split exists
    to prevent. Asserted on output, not assumed — see the exit-guard tests.
    """

    source: str
    origin: str
    proxy_url: "str | None"
    why: str


@dataclass(frozen=True)
class Credential:
    """A usable proxy credential, and which channel it came from.

    ``proxy_url`` is CREDENTIAL-SHAPED and must never be logged or recorded.
    ``source`` and ``detail`` are deliberately safe to record — that split is
    the whole point of this object: the run can state which channel it used
    without the value travelling alongside the statement.
    """

    proxy_url: str
    source: str
    detail: str
    diverged: bool = False


def _from_file(path: str) -> _Candidate:
    """The operator's file — bind-mounted from the host, so it rotates live."""
    if not os.path.exists(path):
        return _Candidate(
            SOURCE_FILE, path, None, f"no proxy credential at {path}"
        )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read().strip()
    except OSError as exc:
        # The MESSAGE, not only the class. `OSError` alone cannot tell
        # permission-denied from is-a-directory from a genuine I/O error, and
        # this arm stops a run — so it stopped one without saying why. Shaped
        # `{type(exc).__name__}: {exc}` to match this module's own convention
        # (see `_wrapped_class_name` and the `socks_fetch` comment above).
        #
        # `exc` is exception text this module did not author, so it is the one
        # `why` that is not built purely from a coordinate — see `_Candidate`.
        # Both consumers of `why` pass it through `redact` (the refusal at the
        # `not usable` arm, and `detail` on the fallback arm), which is what
        # keeps that safe; it is asserted on output rather than assumed.
        return _Candidate(
            SOURCE_FILE, path, None,
            f"the proxy credential at {path} could not be read "
            f"({type(exc).__name__}: {exc})",
        )
    if not raw:
        return _Candidate(
            SOURCE_FILE, path, None,
            f"the proxy credential at {path} is empty",
        )
    url = _to_socks5h(raw)
    if url is None:
        return _Candidate(
            SOURCE_FILE, path, None,
            f"the proxy credential at {path} is not a socks5 URL",
        )
    return _Candidate(SOURCE_FILE, path, url, f"read from {path}")


def _from_environment(var: str = ENVIRONMENT_CREDENTIAL_VAR) -> _Candidate:
    """The environment variable — the durable channel, read as a FALLBACK.

    ⚠️ ``.strip()`` BEFORE the emptiness test, and an empty value is ABSENT.
    A variable set to ``""`` or to whitespace is treated exactly like one that
    was never exported, because the alternative is the failure this module
    exists to prevent: an empty proxy value does not error, it CONNECTS
    DIRECTLY, and the run would record the operator's real address as a
    perfectly well-formed reading. "Set to nothing" is not a weaker "set".
    """
    raw = os.environ.get(var)
    if raw is None:
        return _Candidate(SOURCE_ENVIRONMENT, var, None, f"{var} is unset")
    raw = raw.strip()
    if not raw:
        return _Candidate(
            SOURCE_ENVIRONMENT, var, None,
            f"{var} is set but empty (treated as absent: an empty proxy "
            "value would connect DIRECTLY)",
        )
    url = _to_socks5h(raw)
    if url is None:
        return _Candidate(
            SOURCE_ENVIRONMENT, var, None, f"{var} is not a socks5 URL"
        )
    return _Candidate(SOURCE_ENVIRONMENT, var, url, f"read from {var}")


def resolve_credential(
    path: str = DEFAULT_CREDENTIAL_PATH,
    *,
    env_var: str = ENVIRONMENT_CREDENTIAL_VAR,
) -> Credential:
    """Obtain the credential from EITHER channel, and say which one won.

    Two sources so that one channel failing does not stop a live reading. Both
    have been observed failing independently on this fleet — see
    :data:`ENVIRONMENT_CREDENTIAL_VAR` for the measurement.

    **Precedence: the FILE wins, the environment is the fallback.** Not
    arbitrary, and not alphabetical. The file is bind-mounted from the host, so
    the operator's rotation reaches a running container IMMEDIATELY; the
    environment variable is fixed when the container is created and cannot be
    updated in place, so it goes stale SILENTLY and a stale credential is
    exactly the thing that looks like it is working. When the two disagree, the
    channel that can be rotated is the one to believe — and the disagreement is
    REPORTED either way, never resolved quietly.

    **AN EXPLICIT ``path`` IS A PREFERENCE, NOT A PIN.** Naming a path (the
    CLI's ``--credential``) changes WHICH FILE is consulted first; it does not
    switch the environment channel off. So a run given a path to a file that
    turns out to be absent or unusable still proceeds on
    :data:`ENVIRONMENT_CREDENTIAL_VAR`, and the record says it did.

    Decided deliberately rather than inherited (PS-145 audit raised it as an
    open question). The resilience is the entire point of two channels — a
    named path that REFUSED when the file was missing would reintroduce, for
    exactly the operator who was most specific, the single point of failure
    this function exists to remove. The cost is real but bounded: an operator
    who names a path to PIN which credential is in use can be given the other
    one. That case is not silent — the chosen channel is in the run's record
    and on stderr — so it is visible rather than surprising, which is the
    property that made this the acceptable side of the trade.

    To pin a credential absolutely, unset the variable for the run
    (``env -u PERSONA_TEST_PROXY``) or pass ``env_var`` naming one that is
    not set: with one channel genuinely absent, the named file is the only
    one that can answer.

    Adding a second SOURCE is compatible with this module's rule; adding a
    second OUTCOME is not. There is still exactly one way for this function to
    return — with a socks5h credential — and exactly one way for it to fail —
    :class:`ExitNotProven`. No arrangement of the two channels produces a "try
    it without the proxy" result.
    """
    # Precedence is the ORDER of this tuple, stated once.
    candidates = (_from_file(path), _from_environment(env_var))
    usable = [c for c in candidates if c.proxy_url is not None]

    if not usable:
        # NEITHER CHANNEL — the run ends, with the same reason it always gave
        # plus which channel failed how. Every source is named even though one
        # sentence would do: "the file is missing" sends an operator to fix a
        # file when the variable was the empty one, and this ticket exists
        # because nobody had both halves of that picture at once.
        why = "; ".join(c.why for c in candidates)
        raise ExitNotProven(redact(
            f"no usable proxy credential — {why}. The run cannot reach the "
            "exit, and a checker reading taken over a direct connection would "
            "expose the operator's real address to every checker in the "
            "matrix. Refusing to fall back to a direct connection."
        ))

    winner = usable[0]
    others = [c for c in candidates if c is not winner]
    diverged = any(
        c.proxy_url is not None and c.proxy_url != winner.proxy_url
        for c in others
    )

    if diverged:
        # BOTH CHANNELS HELD A CREDENTIAL AND THEY WERE NOT THE SAME ONE.
        #
        # Loud, because silent precedence between two live credentials is how
        # an operator ends up debugging a reading taken through a proxy they
        # believed they had replaced. With a host mount on one side and a
        # container-frozen variable on the other, they can now genuinely
        # diverge — so this is a real state, not a defensive branch.
        #
        # It does NOT refuse. Both channels are the operator's own and a
        # disagreement is a staleness question, not evidence that either is
        # hostile; refusing would turn a rotation into an outage, which is the
        # opposite of what this ticket is for. It is reported instead, and the
        # value of neither channel appears in the report.
        detail = (
            f"SOURCES DISAGREE: the {SOURCE_FILE} ({path}) and "
            f"{SOURCE_ENVIRONMENT} ({env_var}) each hold a credential and "
            f"they are NOT the same one. Used the {winner.source} "
            f"({winner.origin}), which takes precedence because the file is "
            "mounted from the host and rotates live while the variable is "
            "frozen at container creation. If that is the wrong one, the "
            "other channel is the stale one."
        )
    elif len(usable) > 1:
        detail = (
            f"used the {winner.source} ({winner.origin}); the "
            f"{SOURCE_ENVIRONMENT} ({env_var}) holds the same credential"
        )
    else:
        unusable = "; ".join(c.why for c in others)
        detail = f"used the {winner.source} ({winner.origin}) — {unusable}"

    return Credential(
        proxy_url=winner.proxy_url,
        source=winner.source,
        detail=redact(detail),
        diverged=diverged,
    )


def load_credential(path: str = DEFAULT_CREDENTIAL_PATH) -> str:
    """The proxy credential in ``socks5h`` form, from either channel.

    Thin wrapper over :func:`resolve_credential` for callers that want only
    the URL. A caller that needs to RECORD which channel won should call
    :func:`resolve_credential` directly rather than re-deriving it here —
    re-deriving would read the channels a second time and could reach a
    different answer than the one actually used.

    A credential in NEITHER source raises :class:`ExitNotProven` — one of the
    ways a run ends without recording, not a condition to work around.
    """
    return resolve_credential(path).proxy_url


def _normalise_observation(payload: dict) -> dict:
    """One shape out of the providers' several.

    The guard compares a two-letter country code, and the providers do not
    agree on how to give one. ipinfo answers ``country: "PL"``; ipwho.is
    answers ``country: "Poland"`` with the code in ``country_code``, and nests
    ``org`` under ``connection`` and the zone under ``timezone.id``.

    Reading ipwho.is with ipinfo's key layout yields ``country == "POLAND"``,
    which does not equal ``"PL"`` — so the guard would refuse a perfectly good
    Polish exit and say it was in the wrong country. That is a WORSE failure
    than the 429 this change exists to survive, because the message would be
    actively misleading rather than merely unhelpful. Hence normalising here,
    once, rather than letting each provider's dialect reach the comparison.
    """
    country = str(payload.get("country_code") or payload.get("country") or "")
    org = payload.get("org")
    timezone = payload.get("timezone")
    # ipwho.is nests these; ipinfo has them flat. A dict here is the nested
    # dialect, so reach in for the field rather than stringifying the object.
    connection = payload.get("connection")
    if not org and isinstance(connection, dict):
        org = connection.get("isp") or connection.get("org")
    if isinstance(timezone, dict):
        timezone = timezone.get("id")
    return {
        "ip": str(payload.get("ip") or ""),
        "country": country,
        "region": str(payload.get("region") or ""),
        "city": str(payload.get("city") or ""),
        "org": str(org or ""),
        "timezone": str(timezone or ""),
    }


def observe_exit(
    proxy_url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    url: str | None = None,
    urls: "tuple[str, ...] | None" = None,
    expected_country: str = EXPECTED_COUNTRY,
) -> Exit:
    """Ask, through the proxy, what address the world sees — and check it.

    Returns the observed :class:`Exit` only when it is in the expected country.
    Raises :class:`ExitNotProven` otherwise, including when the observation
    itself could not be made.

    More than one provider is asked, in order, until one ANSWERS — see
    :data:`EXIT_OBSERVATION_URLS` for why a single oracle made the guard's
    availability the run's availability. An answer is authoritative: a
    provider reporting the wrong country ends the run then and there, and is
    never retried against a friendlier provider.

    ``url`` (singular) still selects exactly one provider, so an existing
    caller or test that pins the endpoint keeps its meaning.
    """
    candidates = (url,) if url else (urls or EXIT_OBSERVATION_URLS)

    observation = None
    failures = []
    # The RAW wrapped exception text from the unreachable arm, kept beside
    # `failures` rather than the stage being re-parsed out of it later.
    #
    # Two different collections on purpose. `failures` is OPERATOR PROSE: it is
    # redacted, prefixed with the provider URL, and on a non-verdict answer it
    # carries the checker's OWN response body. Reading a stage out of THAT lets
    # remote text decide what this guard reports. This list carries only what
    # `socks_fetch` itself wrapped, so the class name is still at the position
    # `_wrapped_class_name` expects (the URL prefix would displace it) and
    # redaction cannot rewrite the token being matched.
    #
    # Nothing from here reaches a message — only the parsed class name and
    # reply code do — so it is not a second path for a credential to escape.
    stages: "list[str]" = []
    reached_any = False
    for candidate in candidates:
        try:
            payload = fetch_json(candidate, proxy_url=proxy_url, timeout=timeout)
        except (FetchFailed, ProxyRefused) as exc:
            # Could not be reached at all.
            raw = str(exc)
            failures.append(f"{candidate}: {redact(raw)}")
            stages.append(raw)
            continue

        reached_any = True

        if not isinstance(payload, dict):
            # Answered, but not with an object — so not with a verdict.
            failures.append(
                f"{candidate}: answered with {type(payload).__name__}, "
                "not an object"
            )
            continue

        candidate_observation = _normalise_observation(payload)

        if not candidate_observation["country"]:
            # Answered, but carried no country — a rate-limit body, an error
            # page, or a dialect this normaliser cannot read. Not an answer,
            # so try the next provider rather than refusing the run on it.
            #
            # This is the SECOND non-answer condition, and it is the one that
            # is easy to miss: the normaliser coerces a missing country to
            # "", so without this the run would fall through to the country
            # comparison and refuse a HEALTHY Polish exit while reporting it
            # as "(unknown)" — actively misleading rather than merely
            # unhelpful, which is the worse failure this module warns about.
            failures.append(f"{candidate}: answered, but carried no country")
            continue

        observation = candidate_observation
        break

    if observation is None:
        detail = f"{len(failures)} provider(s) tried ({'; '.join(failures)})"
        # TWO DIFFERENT CAUSES, AND THE MESSAGE SAYS WHICH — the engine-side
        # twin draws the same distinction. "Nothing could be reached" and "it
        # answered and did not say" mean opposite things about whether the
        # exit is usable: the first points at the proxy or the credential, the
        # second at a rate-limit body or an unreadable dialect. Collapsing
        # them is what makes an operator chase the wrong half.
        if not reached_any:
            # THREE causes now, not two — and the third is the one that used
            # to be reported as the other two. A connect-stage refusal means
            # the relay took the credential and then did not hand this run an
            # exit, so the generic wording below blames the relay and the
            # credential while both are demonstrably working. See
            # `_CONNECT_STAGE_MARKER` for how the stage is known here at all.
            codes = [
                _connect_stage_code(raw)
                for raw in stages
                if _wrapped_class_name(raw) == _CONNECT_STAGE_MARKER
            ]
            if codes:
                # WHAT IS KNOWN FOR EVERY CODE vs WHAT IS KNOWN FOR SOME.
                # Authentication passing is a property of the CLASS: PySocks
                # only reaches the reply-code read once the auth exchange has
                # completed, so it is true of all eight codes and is stated
                # unconditionally. Which side failed is a property of the
                # CODE, so the cause is named only where the code carries it —
                # see `_NO_EXIT_ALLOCATED_CODES`. Asserting "no exit was
                # allocated" on a destination-side code would be a confident
                # falsehood, which this module treats as worse than saying
                # less.
                seen = ", ".join(
                    f"{c:#04x}" if c is not None else "unreported"
                    for c in codes
                )
                opening = (
                    f"could not observe the exit through the proxy — {detail}. "
                    "The proxy AUTHENTICATED this run and the SOCKS5 CONNECT "
                    f"stage then failed (reply {seen}): the relay is up and "
                    "the credential was accepted, so neither is the fault. "
                )
                if any(c in _NO_EXIT_ALLOCATED_CODES for c in codes):
                    raise ExitNotProven(
                        opening
                        + "The relay declined to allocate an exit for this "
                        "run, and the likeliest cause is a stale sticky "
                        "session token in the credential, which it can no "
                        "longer resolve to a live exit. Refusing to fall back "
                        "to a direct connection: re-minting the session token "
                        "is the operator's, from the host."
                    )
                if codes and all(c in _DESTINATION_SIDE_CODES for c in codes):
                    raise ExitNotProven(
                        opening
                        + "That reply is raised AFTER an exit was allocated — "
                        "it reports the destination as unreachable or "
                        "refusing from that exit, so the session token is not "
                        "implicated and re-minting it would not help. "
                        "Refusing to fall back to a direct connection: "
                        "rotation is the operator's, from the host."
                    )
                # NEITHER OF THE ABOVE — and the message says only that.
                #
                # Reached by a protocol-level code (0x07 bad command, 0x08 bad
                # address type) and by a reply that could not be parsed. The
                # two arms above each rest on a code actually identified as
                # theirs; nothing identifies these, so nothing about WHICH
                # SIDE failed may be stated. `opening` is still fully earned —
                # the CLASS establishes auth passed for every code — so this
                # arm names the stage and stops, which is the "merely
                # unhelpful" answer the module prefers to a confident wrong
                # one. Saying "re-minting would not help" here would be the
                # inverse of this ticket's purpose on a run where the code
                # supports neither verdict.
                raise ExitNotProven(
                    opening
                    + "That reply does not say which side failed, so neither "
                    "the exit allocation nor the session token can be "
                    "attributed from it. Refusing to fall back to a direct "
                    "connection: rotation is the operator's, from the host."
                )
            raise ExitNotProven(
                f"could not observe the exit through the proxy — {detail}, "
                "none answered. The connection may be refused, timed out, or "
                "the credential unusable. Refusing to fall back to a direct "
                "connection: rotation is the operator's, from the host."
            )
        raise ExitNotProven(
            f"the exit could not be proven — {detail}, and every one that "
            "answered carried no country. The exit is unproven, so nothing "
            "may be recorded against it."
        )

    country = observation["country"].upper()
    ip = observation["ip"]
    if not ip:
        raise ExitNotProven(
            "the exit observation carried no address, so the exit is unproven"
        )
    if country != expected_country.upper():
        raise ExitNotProven(
            f"the exit is in {country or '(unknown)'}, expected "
            f"{expected_country}. A checker folds geography into its "
            "cross-checks — timezone-against-IP, locale-against-IP — so a "
            "reading taken from the wrong country is not a worse reading, it "
            "is a meaningless one. Stopping rather than recording it."
        )
    # Built from the NORMALISED observation, not from the raw payload: the
    # raw one is whichever provider's dialect answered, so reading `timezone`
    # off it would stringify ipwho.is's nested object rather than its `id`.
    return Exit(
        ip=ip,
        country=country,
        region=observation["region"],
        city=observation["city"],
        org=observation["org"],
        timezone=observation["timezone"],
    )


def prove_exit(
    *,
    credential_path: str = DEFAULT_CREDENTIAL_PATH,
    timeout: float = DEFAULT_TIMEOUT,
) -> "tuple[str, Exit, Credential]":
    """The whole precondition, in one call.

    Returns ``(proxy_url, observed_exit, credential)``.

    Raises :class:`ExitNotProven` if the run may not record. Callers are
    expected to let that propagate — catching it to continue anyway is the one
    thing this module exists to prevent.

    THE THIRD MEMBER IS THE PROVENANCE, and it is returned rather than left
    for the caller to look up. A caller that re-derived it would read the
    channels a SECOND time and could get a different answer than the one this
    run actually used — the file is mounted live, so it can change between the
    two reads, and a record naming the wrong channel is worse than one naming
    none. It travels WITH the result for the same reason the observed exit
    does.

    It carries the credential VALUE as well as the channel, so it is not a
    record-safe object on its own: write ``credential.source`` and
    ``credential.detail``, never the object. See :class:`Credential`.
    """
    credential = resolve_credential(credential_path)
    proxy_url = credential.proxy_url
    return proxy_url, observe_exit(proxy_url, timeout=timeout), credential


__all__ = [
    "DEFAULT_CREDENTIAL_PATH",
    "ENVIRONMENT_CREDENTIAL_VAR",
    "EXPECTED_COUNTRY",
    "Credential",
    "Exit",
    "ExitNotProven",
    "SOURCE_ENVIRONMENT",
    "SOURCE_FILE",
    "load_credential",
    "observe_exit",
    "prove_exit",
    "redact",
    "resolve_credential",
]
