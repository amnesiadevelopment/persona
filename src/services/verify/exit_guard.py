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
A missing credential file, an unusable credential, a refused connection, a
timeout, an exit that is not Polish — each ENDS the run with a recorded reason.
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

from .socks_fetch import DEFAULT_TIMEOUT, FetchFailed, ProxyRefused, fetch_json

# Where the operator's credential lives. Read at call time, never cached to
# disk, never logged, never placed in a record — see `redact`.
DEFAULT_CREDENTIAL_PATH = "/workspace/_secrets/test-proxy.txt"

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


def redact(text: str) -> str:
    """Strip anything credential-shaped out of a message before it is shown.

    The credential must not reach a log, a commit, a ticket, a test fixture or
    a captured artefact. Messages in this module are built from exception text
    that can contain a proxy URL, so every one of them goes through here.

    Applied to the WHOLE message rather than to the parts believed to be
    risky: the risky part is the one nobody thought of.
    """
    import re

    # scheme://user:pass@host -> scheme://***:***@host
    return re.sub(r"(\w+://)[^/@\s]+:[^/@\s]+@", r"\1***:***@", text)


# The two SOCKS5 stages, told apart by the class name PySocks raised.
#
# A SOCKS5 connection is negotiated in two stages, and they fail for opposite
# reasons. PySocks (1.7.1, pinned `PySocks>=1.7` in `requirements.txt`) raises
# a DIFFERENT class at each, and `socks_fetch.fetch` wraps an unknown exception
# as `f"{type(exc).__name__}: {exc}"` — so the class name survives into the
# `FetchFailed` text this module already receives, and the stage is readable
# here without touching the fetcher:
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
# Substring matching is enough to separate the two and does NOT collide:
# "SOCKS5Error" is not a substring of "SOCKS5AuthError" (the class names
# diverge at the character after `SOCKS5`), so an auth failure cannot be read
# as a connect-stage one. Only the connect stage gets its own wording — an
# auth failure genuinely IS "the credential unusable", which the existing
# message already says.
_CONNECT_STAGE_MARKER = "SOCKS5Error"


def _names_connect_stage_failure(failures: "list[str]") -> bool:
    """Did the proxy get PAST authentication and fail to allocate an exit?

    Reads the failure text this module already collected — see
    :data:`_CONNECT_STAGE_MARKER` for why the class name is in there and why
    matching it cannot catch an authentication failure by accident.

    True if ANY provider's failure names the connect stage: every provider is
    reached through the same credential, so one connect-stage refusal
    attributes the whole round, and a sibling timeout does not make it less
    true.
    """
    return any(_CONNECT_STAGE_MARKER in failure for failure in failures)


def load_credential(path: str = DEFAULT_CREDENTIAL_PATH) -> str:
    """Read the proxy credential and return it in ``socks5h`` form.

    The file supplies the plain ``socks5://`` form. The scheme is rewritten
    HERE, deliberately and in one place, rather than each call site guessing:
    ``socks5h`` sends the hostname for the EXIT to resolve, while ``socks5``
    resolves it locally and leaks a DNS query naming the checker being read.

    A missing or empty file raises :class:`ExitNotProven` — it is one of the
    ways a run ends without recording, not a condition to work around.
    """
    if not os.path.exists(path):
        raise ExitNotProven(
            f"no proxy credential at {path}: the run cannot reach the exit, "
            "and a checker reading taken over a direct connection would expose "
            "the operator's real address to every checker in the matrix. "
            "Refusing to fall back to a direct connection."
        )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read().strip()
    except OSError as exc:
        raise ExitNotProven(
            f"could not read the proxy credential at {path}: "
            f"{type(exc).__name__}"
        ) from exc
    if not raw:
        raise ExitNotProven(
            f"the proxy credential at {path} is empty; refusing to fall back "
            "to a direct connection"
        )
    if raw.startswith("socks5://"):
        return "socks5h://" + raw[len("socks5://"):]
    if raw.startswith("socks5h://"):
        return raw
    raise ExitNotProven(
        "the proxy credential is not a socks5 URL (a scheme this run can "
        "route a checker through); refusing to fall back to a direct "
        "connection"
    )


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
    reached_any = False
    for candidate in candidates:
        try:
            payload = fetch_json(candidate, proxy_url=proxy_url, timeout=timeout)
        except (FetchFailed, ProxyRefused) as exc:
            # Could not be reached at all.
            failures.append(f"{candidate}: {redact(str(exc))}")
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
            # to be reported as the other two. A dead sticky session token
            # authenticates fine and then has no exit to allocate, so the
            # generic wording below blames the relay and the credential while
            # both are working. See `_CONNECT_STAGE_MARKER` for how the stage
            # is known here at all.
            if _names_connect_stage_failure(failures):
                raise ExitNotProven(
                    f"could not observe the exit through the proxy — {detail}. "
                    "The proxy AUTHENTICATED this run and then refused to "
                    "allocate an exit for it (SOCKS5 connect stage): the "
                    "relay is up and the credential was accepted, so neither "
                    "is the fault. The likeliest cause is a stale sticky "
                    "session token in the credential, which the relay can no "
                    "longer resolve to a live exit. Refusing to fall back to "
                    "a direct connection: re-minting the session token is the "
                    "operator's, from the host."
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
) -> "tuple[str, Exit]":
    """The whole precondition, in one call: ``(proxy_url, observed_exit)``.

    Raises :class:`ExitNotProven` if the run may not record. Callers are
    expected to let that propagate — catching it to continue anyway is the one
    thing this module exists to prevent.
    """
    proxy_url = load_credential(credential_path)
    return proxy_url, observe_exit(proxy_url, timeout=timeout)


__all__ = [
    "DEFAULT_CREDENTIAL_PATH",
    "EXPECTED_COUNTRY",
    "Exit",
    "ExitNotProven",
    "load_credential",
    "observe_exit",
    "prove_exit",
    "redact",
]
