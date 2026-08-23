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

# Asked through the proxy. ipinfo answers the country directly and was the
# endpoint the manual reconnaissance used, so a later run can compare.
EXIT_OBSERVATION_URL = "https://ipinfo.io/json"

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
#     a verdict at all (the 429 above), advances to the next one.
#
# Every entry is fetched through the proxy argument like every other fetch in
# this module. Adding providers widens what can be OBSERVED; it does not widen
# what is ACCEPTED, and there is still no path here that reaches the network
# without the credential.
# Only providers that can answer the GEOGRAPHY question belong here. An
# address-only endpoint (api.ipify.org) was deliberately left out: it answers
# 200 with a perfectly good `{"ip": ...}` and no country, which this guard
# correctly treats as an unproven exit — so listing it would turn a healthy
# run into a refusal for a reason that has nothing to do with the exit.
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

    payload = None
    failures = []
    for candidate in candidates:
        try:
            payload = fetch_json(candidate, proxy_url=proxy_url, timeout=timeout)
            break
        except (FetchFailed, ProxyRefused) as exc:
            # Could not be reached, or did not answer with a verdict. This is
            # the ONLY condition that advances to the next provider.
            failures.append(f"{candidate}: {redact(str(exc))}")

    if payload is None:
        raise ExitNotProven(
            "could not observe the exit through the proxy — "
            f"{len(failures)} provider(s) tried, none answered "
            f"({'; '.join(failures)}). The connection may be refused, timed "
            "out, or the credential unusable. Refusing to fall back to a "
            "direct connection: rotation is the operator's, from the host."
        )

    if not isinstance(payload, dict):
        raise ExitNotProven(
            f"the exit observation endpoint answered with "
            f"{type(payload).__name__}, not an object; the exit is unproven"
        )

    payload = _normalise_observation(payload)

    country = str(payload.get("country") or "").upper()
    ip = str(payload.get("ip") or "")
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
    return Exit(
        ip=ip,
        country=country,
        region=str(payload.get("region") or ""),
        city=str(payload.get("city") or ""),
        org=str(payload.get("org") or ""),
        timezone=str(payload.get("timezone") or ""),
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
