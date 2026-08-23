"""The proxy guard, shown REFUSING — the outcome everything else depends on.

Direct egress works from this container: ``ipinfo.io`` and the whole JSON tier
answer 200 with no proxy at all. So a run that silently failed to attach to the
exit still looks like a complete, successful reading — pages render, verdicts
parse, the file is written, the exit code is 0. It would simply be a reading of
the OPERATOR'S REAL ADDRESS taken against a dozen fingerprinting services that
log it. That is an Invariant #0 problem, not a data-quality one.

This file exists because a guard observed only agreeing has not been observed.
Each way a run can fail to reach the exit is pointed at the guard here, and the
guard is asserted to REFUSE:

* the credential file is absent, or empty, or not a socks5 URL;
* the relay/proxy is stopped — nothing is listening;
* the connection times out;
* the exit answers, but from the WRONG COUNTRY;
* the observation itself is not a verdict (not JSON, no address).

And the two properties that must hold on the refusing paths too: no fallback to
a direct connection exists anywhere in the module, and no message ever carries
the credential.
"""

from __future__ import annotations

import json
import os

import pytest

from src.services.verify import exit_guard
from src.services.verify.exit_guard import (
    EXPECTED_COUNTRY,
    Exit,
    ExitNotProven,
    load_credential,
    observe_exit,
    prove_exit,
    redact,
)
from src.services.verify.socks_fetch import (
    FetchFailed,
    ProxyRefused,
    fetch_json,
    parse_socks5h,
)

CRED = "socks5://alice:s3cr3t@gate.example.com:10000"


# --- the credential ---------------------------------------------------------


def test_a_missing_credential_file_refuses_the_run(tmp_path):
    missing = str(tmp_path / "nope.txt")
    with pytest.raises(ExitNotProven) as exc:
        load_credential(missing)
    assert "refusing to fall back" in str(exc.value).lower()


def test_an_empty_credential_file_refuses_the_run(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("   \n")
    with pytest.raises(ExitNotProven) as exc:
        load_credential(str(path))
    assert "empty" in str(exc.value).lower()


def test_a_credential_that_is_not_socks5_refuses_the_run(tmp_path):
    path = tmp_path / "http.txt"
    path.write_text("http://user:pass@proxy.example:8080")
    with pytest.raises(ExitNotProven):
        load_credential(str(path))


def test_the_credential_scheme_is_rewritten_to_socks5h(tmp_path):
    """socks5 resolves the checker's hostname LOCALLY, leaking a DNS query
    naming the checker being read. socks5h sends the name for the exit."""
    path = tmp_path / "cred.txt"
    path.write_text(CRED + "\n")
    assert load_credential(str(path)).startswith("socks5h://")


def test_an_already_socks5h_credential_is_left_alone(tmp_path):
    path = tmp_path / "cred.txt"
    path.write_text("socks5h://alice:s3cr3t@gate.example.com:10000")
    assert load_credential(str(path)).count("socks5h") == 1


# --- socks5h is enforced, not preferred -------------------------------------


def test_plain_socks5_is_refused_by_the_fetcher():
    """The one that would leak DNS while measuring whether we leak DNS."""
    with pytest.raises(ProxyRefused) as exc:
        parse_socks5h("socks5://alice:s3cr3t@gate.example.com:10000")
    assert "socks5h" in str(exc.value)


def test_the_refusal_message_does_not_carry_the_credential():
    try:
        parse_socks5h(CRED)
    except ProxyRefused as exc:
        assert "s3cr3t" not in str(exc)
        assert "alice" not in str(exc)
    else:  # pragma: no cover
        pytest.fail("expected a refusal")


def test_a_plaintext_checker_url_is_refused():
    with pytest.raises(ProxyRefused):
        fetch_json("http://tls.peet.ws/api/all",
                   proxy_url="socks5h://127.0.0.1:1")


# --- the relay is stopped / nothing is listening ----------------------------


def test_a_dead_relay_fails_the_fetch_rather_than_going_direct():
    """Port 1 on loopback has nothing listening — the "relay stopped" case.

    The assertion that matters is not merely that it raised: it is that the
    checker was NOT fetched some other way. A fetcher that fell back to a
    direct connection would return a perfectly good 200 here.
    """
    with pytest.raises(FetchFailed) as exc:
        fetch_json(
            "https://ipinfo.io/json",
            proxy_url="socks5h://127.0.0.1:1",
            timeout=5,
        )
    # The failure names its cause rather than being a bare "error".
    assert ":" in str(exc.value)
    # And it failed AT THE RELAY, not somewhere past it. The loopback endpoint
    # that refused is named, which is what distinguishes "the relay is down"
    # from "the relay worked and the checker itself refused us" — two outcomes
    # that mean opposite things about whether the exit is usable.
    assert "127.0.0.1:1" in str(exc.value)


def test_a_dead_relay_refuses_the_whole_run():
    with pytest.raises(ExitNotProven) as exc:
        observe_exit("socks5h://127.0.0.1:1", timeout=5)
    message = str(exc.value).lower()
    assert "refusing to fall back to a direct connection" in message


def test_prove_exit_refuses_when_the_relay_is_dead(tmp_path):
    path = tmp_path / "cred.txt"
    path.write_text("socks5://alice:s3cr3t@127.0.0.1:1")
    with pytest.raises(ExitNotProven):
        prove_exit(credential_path=str(path), timeout=5)


# --- the exit answers, but it is the wrong one ------------------------------


def _observe(payload, monkeypatch):
    monkeypatch.setattr(
        exit_guard, "fetch_json", lambda url, **kw: payload
    )
    return observe_exit("socks5h://127.0.0.1:9")


def test_a_non_polish_exit_refuses_the_run(monkeypatch):
    """Geography is not one tagged red — a checker folds it into its
    cross-checks, so the surrounding readings become meaningless."""
    with pytest.raises(ExitNotProven) as exc:
        _observe({"ip": "8.8.8.8", "country": "US"}, monkeypatch)
    assert "US" in str(exc.value)
    assert EXPECTED_COUNTRY in str(exc.value)


def test_a_datacenter_exit_with_no_country_refuses_the_run(monkeypatch):
    with pytest.raises(ExitNotProven):
        _observe({"ip": "8.8.8.8"}, monkeypatch)


def test_an_observation_with_no_address_refuses_the_run(monkeypatch):
    """Country right, address missing: the exit is still unproven."""
    with pytest.raises(ExitNotProven) as exc:
        _observe({"country": "PL"}, monkeypatch)
    assert "no address" in str(exc.value).lower()


def test_an_observation_that_is_not_an_object_refuses_the_run(monkeypatch):
    with pytest.raises(ExitNotProven):
        _observe(["not", "an", "object"], monkeypatch)


def test_a_polish_exit_is_accepted_and_recorded(monkeypatch):
    observed = _observe(
        {
            "ip": "83.175.184.100",
            "country": "PL",
            "region": "Mazovia",
            "city": "Warsaw",
            "org": "AS9141 P4 Sp. z o.o.",
            "timezone": "Europe/Warsaw",
        },
        monkeypatch,
    )
    assert observed.country == "PL"
    assert observed.as_record()["city"] == "Warsaw"


def test_rotation_within_poland_is_not_a_fault(monkeypatch):
    """A DIFFERENT Polish address on a later run is the design, not an error:
    a fixed exit would permanently hide a coupling between a fingerprint
    reading and the address."""
    first = _observe({"ip": "83.175.184.100", "country": "PL"}, monkeypatch)
    second = _observe({"ip": "188.146.33.135", "country": "PL"}, monkeypatch)
    assert first.ip != second.ip
    assert first.country == second.country == "PL"


# --- PS-128: one dead oracle must not refuse a provably good exit -----------
#
# The Python twin of the engine-side rows in `test_verify_checkers.py`. Both
# sites walk a provider list; both must advance on a NON-ANSWER and stop dead
# on a wrong-country ANSWER. They are tested separately because they share no
# code — a defect fixed in one is not fixed in the other, which is exactly how
# the no-country branch below survived the first round.


def _ipwho_payload(country_code="PL", ip="95.49.113.111"):
    """The SECOND provider's dialect, matching the real body captured through
    the mobile exit on 2026-08-23.

    It differs from ipinfo's shape in three ways that all matter to the
    normaliser: ``country`` is the full NAME with the code in ``country_code``,
    the ISP is nested under ``connection``, and ``timezone`` is an OBJECT
    rather than a string. A double that flattened these would prove the
    normaliser works against a shape the provider never sends.
    """
    return {
        "ip": ip,
        "success": True,
        "country": "Poland",
        "country_code": country_code,
        "region": "Masovian Voivodeship",
        "city": "Warsaw",
        "connection": {
            "asn": 5617,
            "org": "Orange Polska Spolka Akcyjna",
            "isp": "Orange Polska Spolka Akcyjna",
        },
        "timezone": {"id": "Europe/Warsaw", "abbr": "CEST", "utc": "+02:00"},
    }


def _observe_scripted(monkeypatch, script):
    """Answer each provider DIFFERENTLY, and record who was actually asked.

    The `_observe` helper above hands the same payload to every provider, so
    it cannot see the loop at all — it would pass identically against the
    single-oracle version this replaced. `script` maps a substring of the
    provider URL to either a payload (returned) or an exception (raised).

    Returns `(result_or_exception, asked)` so a test can assert on the ANSWER
    and on WHICH providers were consulted — the second is what tells a real
    fall-through from a lucky identical payload.
    """
    asked = []

    def fake_fetch_json(url, **kw):
        asked.append(url)
        for fragment, behaviour in script.items():
            if fragment in url:
                if isinstance(behaviour, Exception):
                    raise behaviour
                return behaviour
        raise AssertionError(f"test script has no entry for {url}")

    monkeypatch.setattr(exit_guard, "fetch_json", fake_fetch_json)
    try:
        return observe_exit("socks5h://127.0.0.1:9"), asked
    except ExitNotProven as exc:
        return exc, asked


def test_a_rate_limited_first_provider_falls_through_to_the_second(monkeypatch):
    """THE REGRESSION THIS EXISTS FOR.

    ipinfo.io answered `HTTP 429 Rate limit hit` through the mobile exit — the
    limit attaches to the exit's SHARED address, so it is not ours to clear
    and cannot be retried around. Every reading that run was supposed to take
    was refused over an exit that was provably Polish.

    The assertion is that the observation SUCCEEDS and returns PL, not merely
    that a second URL was visited — which would pass even if the country never
    reached the caller.
    """
    observed, asked = _observe_scripted(
        monkeypatch,
        {
            "ipinfo.io": FetchFailed("HTTP 429 Rate limit hit"),
            "ipwho.is": _ipwho_payload(),
        },
    )

    assert isinstance(observed, Exit), f"refused a healthy exit: {observed}"
    assert observed.country == "PL"
    assert observed.ip == "95.49.113.111"
    # It really did fall through rather than reading the 429 as an answer.
    assert len(asked) == 2


def test_a_provider_that_answers_without_a_country_is_not_an_answer(
    monkeypatch
):
    """A 200 carrying valid JSON and NO country is a NON-ANSWER, so the guard
    must ask the next provider — the same rule the engine-side twin applies.

    This is the sharper half of the 429 case and the one that is easy to miss.
    The normaliser coerces a missing country to "", so without this branch the
    body falls through to the country comparison and refuses a HEALTHY Polish
    exit while reporting it as "(unknown)". That message is actively FALSE
    rather than merely unhelpful, which makes it worse than the rate limit the
    fallback was added to survive.
    """
    observed, asked = _observe_scripted(
        monkeypatch,
        {
            # Shape of a rate-limit/error body: parses fine, says nothing.
            "ipinfo.io": {"ip": "95.49.113.111", "error": "rate limited"},
            "ipwho.is": _ipwho_payload(),
        },
    )

    assert isinstance(observed, Exit), f"refused a healthy exit: {observed}"
    assert observed.country == "PL"
    assert len(asked) == 2


def test_the_second_providers_dialect_is_normalised_not_read_as_Poland(
    monkeypatch
):
    """ipwho.is says `"country": "Poland"` with the CODE in `country_code`.

    Read with ipinfo's key layout that yields "POLAND", which does not equal
    "PL" — so the guard would refuse a healthy Polish exit and say it was in
    the wrong country. The nested fields are asserted too: they must be
    REACHED rather than stringified, or the record beside every reading
    silently carries `{'id': 'Europe/Warsaw', ...}` as its timezone.
    """
    observed, _ = _observe_scripted(
        monkeypatch, {"ipinfo.io": _ipwho_payload()}
    )

    assert isinstance(observed, Exit), f"refused a healthy exit: {observed}"
    assert observed.country == "PL"
    assert observed.timezone == "Europe/Warsaw"
    assert observed.org == "Orange Polska Spolka Akcyjna"
    assert observed.city == "Warsaw"


def test_a_wrong_country_is_NOT_retried_against_a_friendlier_provider(
    monkeypatch
):
    """The fallback is redundancy for REACHABILITY, never a second opinion on
    geography. A provider that answers is authoritative: if the first says US,
    the run ends there. Asking the next one until a Polish answer turns up is
    how a fallback becomes a way to launder a bad exit.
    """
    observed, asked = _observe_scripted(
        monkeypatch,
        {
            "ipinfo.io": {"ip": "8.8.8.8", "country": "US"},
            # Polish, and must never be consulted.
            "ipwho.is": _ipwho_payload(),
        },
    )

    assert isinstance(observed, ExitNotProven)
    assert "US" in str(observed)
    assert len(asked) == 1, "a wrong country was re-shopped to another provider"


def test_every_provider_answering_without_a_country_refuses_the_run(
    monkeypatch
):
    """The list is exhausted, so nothing may be recorded — and the message
    says WHICH of the two causes it was. "Nothing could be reached" and "it
    answered and did not say" point at opposite halves (the proxy/credential
    vs. a rate-limit body), and collapsing them makes an operator chase the
    wrong one.
    """
    observed, asked = _observe_scripted(
        monkeypatch,
        {
            "ipinfo.io": {"ip": "95.49.113.111"},
            "ipwho.is": {"ip": "95.49.113.111"},
        },
    )

    assert isinstance(observed, ExitNotProven)
    assert len(asked) == 2, "the guard stopped before exhausting the list"
    assert "carried no country" in str(observed)
    # NOT the unreachable message: both providers answered.
    assert "could not observe the exit" not in str(observed)


# --- the credential never reaches a message ---------------------------------


def test_redact_strips_credentials_from_a_proxy_url():
    assert "s3cr3t" not in redact(f"could not connect to {CRED}")
    assert "***:***@" in redact(f"could not connect to {CRED}")


def test_a_failed_observation_message_is_redacted(monkeypatch):
    def boom(url, **kw):
        raise FetchFailed(f"ConnectionError: could not reach {CRED}")

    monkeypatch.setattr(exit_guard, "fetch_json", boom)
    with pytest.raises(ExitNotProven) as exc:
        observe_exit("socks5h://127.0.0.1:9")
    assert "s3cr3t" not in str(exc.value)


def test_the_recorded_exit_carries_no_credential():
    record = Exit(ip="1.2.3.4", country="PL").as_record()
    assert "password" not in json.dumps(record)
    assert "s3cr3t" not in json.dumps(record)


# --- there is no way to ask for a direct connection -------------------------


def test_no_fallback_to_direct_exists_in_the_guard():
    """A structural assertion, not a behavioural one: the module must not grow
    an escape hatch. Every fetch in it goes through a proxy argument."""
    source = open(exit_guard.__file__, encoding="utf-8").read()
    assert "proxy_url=proxy_url" in source
    for hatch in ("allow_direct", "no_proxy", "fallback_direct"):
        assert hatch not in source


def test_the_cli_offers_no_flag_to_read_over_a_direct_connection():
    from src.services.verify import checker_cli

    parser = checker_cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["read", "--allow-direct"])


def test_the_cli_refuses_with_exit_code_2_when_the_exit_is_unproven(
    tmp_path, capsys
):
    """2 is "we declined to measure", distinct from 1 ("we crashed") — and
    emphatically distinct from 0."""
    from src.services.verify import checker_cli

    out = tmp_path / "should-not-exist.json"
    code = checker_cli.main(
        ["read", "--credential", str(tmp_path / "absent.txt"), "-o", str(out)]
    )
    assert code == 2
    assert not out.exists(), "a refused run must not write a record"
    assert "REFUSED" in capsys.readouterr().err
