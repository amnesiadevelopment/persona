"""The engine's release lookup must never authenticate on the real IP.

PS-216. A vendored dependency (``invisible_core.download._resolve_asset_url``,
reached through the ``invisible_playwright.download`` shim) reads
``STEALTHFOX_GITHUB_TOKEN or GITHUB_TOKEN``. When either is set it resolves the
release through ``api.github.com`` with ``requests`` and no ``proxies``
argument, so the request goes DIRECT — on the real IP, carrying an
``Authorization`` header — while persona's engine install has already resolved
an egress opener that governs only the two transfers below it. The operator
configured a proxy and this one call ignores it.

EVERY assertion here is on the REQUEST THE TRANSPORT WAS ASKED TO SEND, never
on whether a helper was called: the spy replaces ``HTTPAdapter.send``, which is
below ``requests.get``, so an implementation that leaks cannot pass. That is the
project's standing directive for this class of fix, and it is what makes the
falsification (AC6) meaningful — strip the scrub and these go red on the
observed request rather than on a mock's call count.

NO PACKET LEAVES during these tests: the spy records the PreparedRequest and
raises before any socket work, so ``api.github.com`` is never contacted even
though the tests run with real network available.
"""

import os
import subprocess
import sys

import pytest
import requests.adapters

from src.services.browser.env_policy import (
    VENDORED_CREDENTIAL_VARS,
    neutralise_vendored_credentials,
    scrub_vendored_credentials,
)

# The two names the vendored resolver reads, in its own precedence order.
SF_TOKEN = "STEALTHFOX_GITHUB_TOKEN"
GH_TOKEN = "GITHUB_TOKEN"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _NoPacketAllowed(Exception):
    """Raised by the transport spy so nothing can reach a socket."""


@pytest.fixture
def sent(monkeypatch):
    """Record every request the transport is asked to send, and send none.

    Patches ``HTTPAdapter.send`` — BELOW ``requests.get`` — so the recording is
    of the real PreparedRequest (final URL, final headers), not of an argument
    handed to a mock one layer up.
    """
    captured = []

    def _spy(self, request, **kwargs):
        captured.append(
            {
                "method": request.method,
                "url": request.url,
                "headers": dict(request.headers),
            }
        )
        raise _NoPacketAllowed(request.url)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", _spy)
    return captured


@pytest.fixture(autouse=True)
def _clean_token_env(monkeypatch):
    """Start every test from an environment with neither name set."""
    for var in (SF_TOKEN, GH_TOKEN):
        monkeypatch.delenv(var, raising=False)


def _resolve(tag="v150.0-2", asset="checksums.txt"):
    from invisible_playwright.download import _resolve_asset_url

    return _resolve_asset_url(tag, asset)


# --- AC1: with a token present, the lookup makes no authenticated request ----


@pytest.mark.parametrize("token_var", [SF_TOKEN, GH_TOKEN])
def test_no_api_request_is_sent_when_a_token_is_in_the_environment(
    token_var, monkeypatch, sent
):
    # AC1. The environment persona INHERITED carries a token; the startup scrub
    # has run. The resolver must take its offline branch, so the transport is
    # never asked to send anything at all.
    monkeypatch.setenv(token_var, "ghp_notarealtoken_0000000000")
    neutralise_vendored_credentials()

    url = _resolve()

    assert sent == [], f"a request was sent despite the scrub: {sent}"
    assert "api.github.com" not in url


@pytest.mark.parametrize("token_var", [SF_TOKEN, GH_TOKEN])
def test_no_authorization_header_is_ever_offered_to_the_transport(
    token_var, monkeypatch, sent
):
    # AC1, stated as the credential rather than the host: even if some future
    # rewrite reached the network for another reason, no request derived from
    # this path may carry the operator's GitHub credential.
    monkeypatch.setenv(token_var, "ghp_notarealtoken_0000000000")
    neutralise_vendored_credentials()

    _resolve()

    offered = [r for r in sent if "Authorization" in r["headers"]]
    assert offered == [], f"a credential was offered to the transport: {offered}"


def test_both_names_set_together_still_yields_no_request(monkeypatch, sent):
    # The resolver reads SF first and falls back to GH, so a fix that removed
    # only the first name would still leak through the second.
    monkeypatch.setenv(SF_TOKEN, "ghp_sf_0000000000")
    monkeypatch.setenv(GH_TOKEN, "ghp_gh_0000000000")
    neutralise_vendored_credentials()

    _resolve()

    assert sent == []


# --- AC3: the resolved URL is still the correct public one -------------------


@pytest.mark.parametrize("token_var", [SF_TOKEN, GH_TOKEN])
def test_resolved_url_with_a_token_present_is_the_public_release_url(
    token_var, monkeypatch, sent
):
    # AC3. Pinned rather than assumed: the no-token branch is only correct while
    # the release repo is public. If it ever goes private this URL 404s, and
    # this test is where that surfaces — not in a silent failed install.
    monkeypatch.setenv(token_var, "ghp_notarealtoken_0000000000")
    neutralise_vendored_credentials()

    url = _resolve(asset="checksums.txt")

    assert url.startswith("https://github.com/")
    assert "/releases/download/v150.0-2/checksums.txt" in url
    assert sent == []


def test_url_with_a_token_scrubbed_equals_the_url_with_no_token_at_all(
    monkeypatch, sent
):
    # The scrub must produce the SAME answer the clean environment produces —
    # that is the whole claim that it costs no capability.
    clean = _resolve()

    monkeypatch.setenv(GH_TOKEN, "ghp_notarealtoken_0000000000")
    neutralise_vendored_credentials()
    scrubbed = _resolve()

    assert scrubbed == clean
    assert sent == []


# --- AC4: with no token, behaviour is unchanged ------------------------------


def test_with_no_token_set_nothing_is_sent_and_the_url_is_public(sent):
    # AC4. This is today's already-correct path; it must stay byte-identical.
    url = _resolve()

    assert sent == []
    assert url == (
        "https://github.com/feder-cr/firefox_antidetect_patch"
        "/releases/download/v150.0-2/checksums.txt"
    )


def test_the_scrub_is_a_no_op_on_an_environment_that_has_neither_name():
    # AC4, on the scrub itself: nothing to remove, nothing reported removed.
    assert neutralise_vendored_credentials() == []


# --- The scrub's own contract ------------------------------------------------


def test_scrub_reports_only_the_names_it_actually_removed(monkeypatch):
    monkeypatch.setenv(GH_TOKEN, "ghp_gh_0000000000")

    removed = neutralise_vendored_credentials()

    assert removed == [GH_TOKEN]
    assert GH_TOKEN not in os.environ


def test_scrub_is_idempotent(monkeypatch):
    monkeypatch.setenv(SF_TOKEN, "ghp_sf_0000000000")

    assert neutralise_vendored_credentials() == [SF_TOKEN]
    assert neutralise_vendored_credentials() == []


def test_scrub_leaves_personas_own_environment_alone(monkeypatch):
    # Constraint 1 of the ticket, pinned: this slice must NOT reach for
    # scrub_current_process_environ(), which would strip USER / LOGNAME /
    # HOSTNAME / SSH_AUTH_SOCK / FONTCONFIG_* from persona ITSELF. Those names
    # are persona's own environment, not the browser child's, and removing them
    # here was never evaluated.
    keep = {
        "USER": "operator",
        "LOGNAME": "operator",
        "HOSTNAME": "operator-laptop",
        "SSH_AUTH_SOCK": "/tmp/ssh-XXXXcAgEnT/agent.1337",
        "FONTCONFIG_FILE": "/opt/persona/fonts.conf",
    }
    for name, value in keep.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(GH_TOKEN, "ghp_gh_0000000000")

    neutralise_vendored_credentials()

    assert GH_TOKEN not in os.environ
    for name, value in keep.items():
        assert os.environ.get(name) == value, f"{name} was scrubbed and must not be"


def test_scrub_takes_the_mapping_it_is_given_and_not_os_environ(monkeypatch):
    # The in-place helper must clean the mapping handed to it, so a caller
    # holding a copy cannot mutate the parent's environment through it.
    monkeypatch.setenv(GH_TOKEN, "ghp_parent_must_survive")
    copy = {GH_TOKEN: "ghp_child_0000000000", "KEEP": "1"}

    scrub_vendored_credentials(copy)

    assert copy == {"KEEP": "1"}
    assert os.environ.get(GH_TOKEN) == "ghp_parent_must_survive"


def test_the_two_vendored_names_are_the_ones_the_dependency_actually_reads():
    # Pins the list against the vendored source rather than against itself: if
    # a pin bump adds a third name, this fails instead of silently leaking it.
    import inspect

    import invisible_core.download as vendored

    body = inspect.getsource(vendored._github_token)

    for name in VENDORED_CREDENTIAL_VARS:
        assert name in body, f"{name} is not read by the vendored resolver"
    for name in ("STEALTHFOX_GITHUB_TOKEN", "GITHUB_TOKEN"):
        assert name in VENDORED_CREDENTIAL_VARS, f"vendored reads {name}, we do not scrub it"


# --- The startup seam actually applies it ------------------------------------


def test_starting_persona_removes_an_inherited_token_from_its_own_process():
    # Asserted on the ENVIRONMENT OF A REAL PROCESS that imported the entry
    # module, not on whether a function was called. A subprocess is used
    # deliberately: os.environ is process-global, so proving it in-process
    # would prove nothing about startup ordering.
    env = dict(os.environ)
    env[SF_TOKEN] = "ghp_sf_0000000000"
    env[GH_TOKEN] = "ghp_gh_0000000000"
    env["USER"] = "operator"
    env["PERSONA_UTF8_REEXEC"] = "1"  # don't re-exec inside the test

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, json, sys;"
            "sys.argv=['persona'];"
            "import src.main;"
            "print(json.dumps({"
            "'sf': os.environ.get('STEALTHFOX_GITHUB_TOKEN'),"
            "'gh': os.environ.get('GITHUB_TOKEN'),"
            "'user': os.environ.get('USER')}))",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )

    assert proc.returncode == 0, f"entry module failed to import:\n{proc.stderr[-2000:]}"
    payload = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            payload = __import__("json").loads(line)
    assert payload is not None, f"no payload in stdout:\n{proc.stdout[-2000:]}"

    assert payload["sf"] is None, "STEALTHFOX_GITHUB_TOKEN survived startup"
    assert payload["gh"] is None, "GITHUB_TOKEN survived startup"
    # and the scrub stayed narrow
    assert payload["user"] == "operator"
