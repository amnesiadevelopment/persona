import pytest

from src.utils.proxy_parser import (
    ProxyUrlUnparseable,
    build_proxy_url,
    engine_proxy_dict,
    parse_proxy,
    parse_proxy_server,
    split_proxy_url,
)
from src.utils.validation import validate_proxy_format


def test_build_no_auth():
    assert build_proxy_url("socks5", "1.2.3.4", "1080") == "socks5://1.2.3.4:1080"


def test_build_user_pass():
    assert (
        build_proxy_url("http", "h.com", "8080", "u", "p")
        == "http://u:p@h.com:8080"
    )


def test_build_user_only():
    assert build_proxy_url("socks5", "h", "1080", "u") == "socks5://u@h:1080"


def test_split_full():
    got = split_proxy_url("socks5://u:p@1.2.3.4:1080")
    assert got == {
        "scheme": "socks5",
        "host": "1.2.3.4",
        "port": "1080",
        "username": "u",
        "password": "p",
    }


def test_split_no_auth():
    got = split_proxy_url("http://h.com:8080")
    assert got["scheme"] == "http"
    assert got["host"] == "h.com"
    assert got["port"] == "8080"
    assert got["username"] == ""
    assert got["password"] == ""


def test_split_empty():
    assert split_proxy_url("")["scheme"] == "socks5"


def test_roundtrip():
    url = "socks5://user:pass@10.0.0.1:1080"
    f = split_proxy_url(url)
    rebuilt = build_proxy_url(
        f["scheme"], f["host"], f["port"], f["username"], f["password"]
    )
    assert rebuilt == url


def test_built_url_is_valid_proxy_server():
    url = build_proxy_url("socks5", "1.2.3.4", "1080", "u", "p")
    assert parse_proxy_server(url) == "socks5://1.2.3.4:1080"


import pytest


@pytest.mark.parametrize("pw", ["p/ss", "p#ss", "p?ss", "p@ss", "p:ss", "p ss", "пароль"])
def test_special_char_password_round_trips_and_parses(pw):
    # audit6 #8: a password with URL-reserved characters must survive build ->
    # split (shows the real password) AND build -> launch-parse (host stays
    # intact, creds decode). Assembling raw creds broke urlparse: `/#?` emptied
    # the host (→ fail-closed no-proxy), `@` was rejected at save.
    url = build_proxy_url("socks5", "1.2.3.4", "1080", "user", pw)

    # split round-trips to the real credential
    f = split_proxy_url(url)
    assert f["host"] == "1.2.3.4"
    assert f["port"] == "1080"
    assert f["username"] == "user"
    assert f["password"] == pw

    # the launch parser sees an intact host:port and the DECODED credentials
    cfg = parse_proxy(url)
    assert cfg is not None, f"launcher rejected the built url for pw={pw!r}"
    assert cfg["server"] == "socks5://1.2.3.4:1080"
    assert cfg["username"] == "user"
    assert cfg["password"] == pw


def test_special_char_password_reaches_socks_bridge_decoded():
    # The chromium credential-stripping bridge must send the DECODED password to
    # the upstream SOCKS5 proxy, not the %XX form (audit6 #8).
    from src.services.proxy.bridge import ProxyBridge

    url = build_proxy_url("socks5", "1.2.3.4", "1080", "user", "p/s#s?x")
    b = ProxyBridge(url)
    assert b._up_user == "user"
    assert b._up_pass == "p/s#s?x"


# ---------------------------------------------------------------------------
# PS-217: engine_proxy_dict — the ONE owner of the engine proxy dict.
#
# The function it replaces fell through to `{"server": <raw url>}` with the
# credentials NEVER EXTRACTED for anything but a literal `socks5://`. An
# authenticated proxy then refused the connection and the user saw a page that
# would not load, indistinguishable from a dropped network. Each test below
# names the axis it pins.
# ---------------------------------------------------------------------------


def test_engine_proxy_dict_extracts_credentials_for_socks5():
    # The baseline that always worked. Here so a regression on the happy path
    # is not hidden by the new-axis tests below.
    assert engine_proxy_dict("socks5://bob:secret@1.2.3.4:1080") == {
        "server": "socks5://1.2.3.4:1080",
        "username": "bob",
        "password": "secret",
    }


def test_engine_proxy_dict_keeps_credentials_for_socks5h_scheme():
    # AXIS 1 (scheme). `socks5h` is the form THIS PROJECT reads its credential
    # in (verify/socks_fetch.parse_socks5h refuses anything else), and the old
    # launch regex was anchored on the literal `socks5://` — so the scheme the
    # tree actually uses was exactly the one that lost its credentials.
    #
    # Asserts the CREDENTIALS SURVIVE, not merely that a dict came back: the
    # bug returned a dict too. It just had no username in it.
    got = engine_proxy_dict("socks5h://bob:secret@1.2.3.4:1080")
    assert got["username"] == "bob"
    assert got["password"] == "secret"
    # ...and normalised to socks5 for the engine: `socks5h` in a server= value
    # is a curl-ism the browser rejects outright.
    assert got["server"] == "socks5://1.2.3.4:1080"


def test_engine_proxy_dict_keeps_credentials_for_uppercase_scheme():
    # AXIS 1, second spelling. A case-varied scheme is a legal URL and failed
    # identically. urlsplit lowercases the scheme, so this closes for free.
    got = engine_proxy_dict("SOCKS5://bob:secret@1.2.3.4:1080")
    assert got["username"] == "bob"
    assert got["password"] == "secret"
    assert got["server"] == "socks5://1.2.3.4:1080"


def test_engine_proxy_dict_password_containing_at_does_not_misdirect_the_host():
    # AXIS 3, and the SHARPEST of the three: `[^@]+` stopped at the FIRST `@`,
    # so this URL used to yield password "se" AND server
    # "socks5://cret@1.2.3.4:1080" — the browser pointed at a DIFFERENT PROXY
    # ADDRESS with a truncated password. That is not a dropped credential, it
    # is a misdirected connection.
    #
    # The host assertion is the one that matters here; the password assertion
    # alone would pass on a fix that still corrupted the host.
    got = engine_proxy_dict("socks5://bob:se@cret@1.2.3.4:1080")
    assert got["server"] == "socks5://1.2.3.4:1080", "host must not absorb password text"
    assert got["username"] == "bob"
    assert got["password"] == "se@cret"


def test_engine_proxy_dict_username_containing_colon_round_trips_from_build():
    # AXIS 2 (`:` in the username). `[^:]+` cannot match one, so the whole
    # regex failed and the credentials were dropped.
    #
    # Driven THROUGH build_proxy_url rather than hand-written, because that is
    # how such a credential is actually stored: a raw `us:er` is ambiguous by
    # RFC 3986 and percent-encoding is the only way to express it. This pins
    # the round trip the product really performs — the dialog saves, the
    # launcher reads.
    url = build_proxy_url("socks5", "1.2.3.4", "1080", "us:er", "pw")
    got = engine_proxy_dict(url)
    assert got["username"] == "us:er", "the colon must survive decoding"
    assert got["password"] == "pw"
    assert got["server"] == "socks5://1.2.3.4:1080"


def test_engine_proxy_dict_keeps_credentials_for_http_proxy():
    # The UI offers socks5/http/https (ui/dialogs/proxy.py), and the engine
    # takes http(s) through Playwright's own proxy= kwarg. Every one of those
    # ALSO lost its credentials to the old socks5-only regex — a wider blast
    # radius than the scheme axis alone suggests.
    #
    # This is also the regression guard on the fix: a "refuse anything that is
    # not socks5" remedy would fail-close a configuration the product ships a
    # dropdown for.
    got = engine_proxy_dict("http://bob:pw@1.2.3.4:8080")
    assert got == {
        "server": "http://1.2.3.4:8080",
        "username": "bob",
        "password": "pw",
    }


def test_engine_proxy_dict_no_credentials_is_not_a_refusal():
    # A proxy legitimately carrying no auth must still launch.
    assert engine_proxy_dict("socks5://1.2.3.4:1080") == {
        "server": "socks5://1.2.3.4:1080"
    }


def test_engine_proxy_dict_absent_proxy_means_direct_not_refused():
    # None/empty is the DIRECT profile, the one case that legitimately yields
    # no dict. Keeping it distinct from a refusal is what lets the launch path
    # tell "this profile has no proxy" from "this profile's proxy is broken".
    assert engine_proxy_dict(None) is None
    assert engine_proxy_dict("") is None
    assert engine_proxy_dict("None") is None


@pytest.mark.parametrize(
    "url,expected",
    [
        # The bare-IP case. urlsplit reports scheme='' here — the ONLY one of
        # the three that a falsy-scheme check would have caught.
        ("1.2.3.4:8080", {"server": "http://1.2.3.4:8080"}),
        # A NAMED host: urlsplit reports scheme='localhost', so this looked
        # like an unknown-scheme URL rather than a scheme-less one.
        ("localhost:8080", {"server": "http://localhost:8080"}),
        # An underscore hostname — validation.py allows these explicitly
        # ("real provider gateways like gate_us.smartproxy.com use them").
        ("gate_us.smartproxy.com:7000", {"server": "http://gate_us.smartproxy.com:7000"}),
    ],
)
def test_engine_proxy_dict_defaults_a_missing_scheme_rather_than_refusing(url, expected):
    # REGRESSION GUARD (audit of PR #179). Refusing these made the launch path
    # reject what the SAVE path accepts: validate_proxy_format("1.2.3.4:8080")
    # is (True, "") and is pinned that way in tests/test_validation.py, and
    # store.resolve hands the ref on with the scheme still absent. A profile
    # saved exactly as the validator permits could then not launch at all.
    #
    # That is the mirror image of the contradiction validation.py:27-30 says it
    # exists to prevent ("so a proxy that connects at runtime isn't rejected on
    # save") — and the mirror is the worse direction, because it surfaces at
    # launch time on a profile the user already saved and checked.
    assert engine_proxy_dict(url) == expected


def test_engine_proxy_dict_defaulting_the_scheme_EXTRACTS_credentials():
    # The scheme-less AUTHENTICATED case, and the reason defaulting is strictly
    # better than refusing rather than merely more permissive.
    #
    # urlsplit reports scheme='bob' for this input, so it never looked
    # scheme-less at all. The merge-base regex (anchored on a literal
    # "socks5://") did not match it either and returned {"server": <raw url>}
    # with the credentials NEVER EXTRACTED — the exact PS-217 defect. So
    # defaulting the scheme CLOSES one more instance of finding 1; it does not
    # trade it away.
    assert engine_proxy_dict("bob:pw@1.2.3.4:1080") == {
        "server": "http://1.2.3.4:1080",
        "username": "bob",
        "password": "pw",
    }


def test_engine_proxy_dict_scheme_less_still_survives_an_at_in_the_password():
    # The delimiter axis must keep holding once the scheme is defaulted —
    # userinfo splits at the LAST "@" (RFC 3986), so the host is not corrupted.
    assert engine_proxy_dict("bob:se@cret@1.2.3.4:1080") == {
        "server": "http://1.2.3.4:1080",
        "username": "bob",
        "password": "se@cret",
    }


def test_engine_proxy_dict_default_scheme_matches_what_the_url_already_meant():
    # THE DECISION, PINNED. `http` is not a taste call: it is what a
    # scheme-less proxy already resolved to on BOTH engines before PS-217
    # touched anything, so no stored profile changes meaning.
    #
    # parse_proxy_server is what Chromium's --proxy-server is built from for
    # this same input (process._proxy_arg, unauthenticated branch), so this
    # asserts the two engines AGREE rather than asserting a constant twice —
    # if either side's default drifts, this goes red.
    #
    # NOTE the deliberate limit of the claim: _proxy_arg's AUTHENTICATED branch
    # prepends socks5:// instead, because it hands the URL to ProxyBridge which
    # speaks a real SOCKS5 handshake upstream. That asymmetry is real, is
    # load-bearing on the Chromium side, and is out of PS-217's scope; it is
    # documented at _DEFAULT_ENGINE_PROXY_SCHEME rather than silently changed.
    assert engine_proxy_dict("1.2.3.4:8080")["server"] == parse_proxy_server("1.2.3.4:8080")


@pytest.mark.parametrize(
    "bad",
    [
        "ftp://bob:pw@1.2.3.4:1080",   # a scheme no engine path can take
        "socks5://bob:pw@1.2.3.4",    # no port
        "socks5://bob:pw@:1080",      # no host
        "socks5://host:notaport",     # unreadable port
        "1.2.3.4",                    # a bare host: defaulting the scheme is
                                      # not enough to make this usable, and a
                                      # proxy with no port cannot be dialled
    ],
)
def test_engine_proxy_dict_refuses_rather_than_dropping_credentials(bad):
    # THE CORE PS-217 PROPERTY: a URL this code cannot fully read must NOT
    # become an unauthenticated connection. The old code returned
    # {"server": <raw url>} for every one of these and the launch continued.
    with pytest.raises(ProxyUrlUnparseable):
        engine_proxy_dict(bad)


# Inputs whose refusal message is built from something DERIVED from the URL.
#
# THE OLD VERSION OF THIS TEST USED ONLY `ftp://user:pw@host:port` AND COULD NOT
# FAIL. That URL has a REAL scheme, so urlsplit puts the userinfo in
# `.username` and it never reaches the message — the one shape that was never at
# risk was the only one asserted. Every case below was MEASURED to publish
# credential material before _safe_to_quote.
#
# The third element records HOW the input reaches the parser, because the two
# groups are reachable by different call sites and neither is hypothetical:
#
#   True  — validate_proxy_format ACCEPTS it, so a user can save it. Asserted
#           below, so if the validator ever tightens, the case is re-picked
#           rather than silently becoming untestable.
#   False — the validator rejects it, but `browser_tier.py` and
#           `ps217_second_tab_ui.py` pass a raw proxy_url straight in with no
#           validate gate, so the parser still receives these. They are kept
#           precisely because the save gate is NOT what protects this function.
#
# The secrets are distinctive strings so a leak greps unambiguously.
CREDENTIAL_ECHO_CASES = [
    # userinfo lands in .scheme, NOT .username — the leak the reviewer found.
    ("hunter2user:hunter2pw://x@1.2.3.4:1080", ["hunter2user", "hunter2pw"], True),
    (
        "hunter2user:hunter2pw://x@host.example.com:1080",
        ["hunter2user", "hunter2pw"],
        True,
    ),
    # urllib's port error quotes the offending substring, which here is a
    # fragment of the PASSWORD.
    (
        "socks5://hunter2user:hunter2pw://x@1.2.3.4:1080",
        ["hunter2user", "hunter2pw"],
        True,
    ),
    ("socks5://hunter2user:hunter2pw@1.2.3.4:notaport", ["hunter2user", "hunter2pw"], False),
    # a real scheme (the original case) — still must not echo.
    ("ftp://hunter2user:hunter2pw@1.2.3.4:1080", ["hunter2user", "hunter2pw"], True),
    # malformed IPv6: urlsplit raises before any field is readable.
    ("socks5://hunter2user:hunter2pw@[bad:ipv6:1080", ["hunter2user", "hunter2pw"], False),
    # passwordless — the username is still a credential.
    ("socks5://hunter2user@1.2.3.4:notaport", ["hunter2user"], False),
]


@pytest.mark.parametrize("bad,secrets,save_reachable", CREDENTIAL_ECHO_CASES)
def test_engine_proxy_dict_refusal_never_echoes_the_credential(
    bad, secrets, save_reachable
):
    # The refusal message reaches logs and a pipe the UI renders. It carries
    # the credential, so it must never contain it — the same rule
    # verify/socks_fetch.parse_socks5h states for its own refusal.
    try:
        engine_proxy_dict(bad)
    except ProxyUrlUnparseable as e:
        for secret in secrets:
            assert secret not in str(e), f"refusal echoed {secret!r}: {e}"
        # The message must still be USEFUL. A blanket "" would trivially pass
        # the assertion above while telling the user nothing.
        assert "proxy URL" in str(e) or "proxy scheme" in str(e)
    else:  # pragma: no cover - the case above must raise
        raise AssertionError("expected a refusal")


@pytest.mark.parametrize("bad,secrets,save_reachable", CREDENTIAL_ECHO_CASES)
def test_credential_echo_cases_have_the_reachability_they_claim(
    bad, secrets, save_reachable
):
    # Pins the third element above so the reachability annotation cannot rot
    # into a comment that is no longer true — the failure mode this whole
    # rework is about. A case marked save-reachable is a proxy a user can
    # actually store; the rest reach the parser through the ungated call sites.
    ok, _ = validate_proxy_format(bad)
    assert ok is save_reachable, (
        f"{bad!r} save-reachability is {ok}, annotated {save_reachable}"
    )


def test_refusal_still_names_the_scheme_when_there_is_no_credential():
    # The guard must not be a blanket gag: with no userinfo there is nothing to
    # protect, and the scheme name is the whole diagnostic value of the message.
    with pytest.raises(ProxyUrlUnparseable, match="gopher"):
        engine_proxy_dict("gopher://1.2.3.4:1080")


# --------------------------------------------------------------- PS-217 parity
#
# The PS-206 harness used to hand-copy the launch path's proxy_dict and assert
# in its docstring that the copy was verbatim. PS-217 refactored the shipped
# function and the copy silently diverged — the harness then measured a browser
# we do not ship, which is PS-217's own finding 2 re-created in the harness.
#
# These two tests make the claim STRUCTURAL instead of asserted: it is now
# impossible for the harness and the shipped launch to disagree without a test
# going red, on either axis (the proxy dict, and the proxied pref set).


def _load_ps206():
    import importlib.util
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parent.parent
        / "scripts"
        / "ps206_second_tab.py"
    )
    spec = importlib.util.spec_from_file_location("ps206_second_tab", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "url",
    [
        "socks5://bob:secret@1.2.3.4:1080",
        "socks5h://bob:secret@1.2.3.4:1080",  # the scheme the old copy dropped
        "1.2.3.4:8080",
        "http://1.2.3.4:8080",
    ],
)
def test_ps206_harness_builds_the_same_proxy_dict_as_the_shipped_launch(url):
    # The harness must measure the product. It now IMPORTS engine_proxy_dict,
    # so this cannot drift — but it is pinned because the previous copy's
    # docstring predicted its own divergence and was right.
    assert _load_ps206().proxy_dict(url) == engine_proxy_dict(url)


def test_ps206_harness_prefs_match_the_shipped_proxied_prefs():
    # Same claim on the pref axis. network.proxy.failover_direct is the one
    # that matters: the harness/product asymmetry on it WAS finding 2, so a
    # harness pref set that stops matching the shipped one re-opens it.
    from src.services.browser.invisible_launch import _profile_prefs

    shipped = _profile_prefs(
        {"proxy_url": "socks5://bob:pw@1.2.3.4:1080", "search_engine": "duckduckgo"}
    )
    harness = _load_ps206().SHIPPED_PROXIED_PREFS
    mismatched = {k: (v, shipped.get(k)) for k, v in harness.items() if shipped.get(k) != v}
    assert not mismatched, f"harness prefs diverged from the shipped launch: {mismatched}"
    assert harness["network.proxy.failover_direct"] is False
