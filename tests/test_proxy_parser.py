import pytest

from src.utils.proxy_parser import (
    ProxyUrlUnparseable,
    build_proxy_url,
    engine_proxy_dict,
    parse_proxy,
    parse_proxy_server,
    split_proxy_url,
)


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
    "bad",
    [
        "ftp://bob:pw@1.2.3.4:1080",   # a scheme no engine path can take
        "1.2.3.4:1080",               # no scheme at all
        "socks5://bob:pw@1.2.3.4",    # no port
        "socks5://bob:pw@:1080",      # no host
        "socks5://host:notaport",     # unreadable port
    ],
)
def test_engine_proxy_dict_refuses_rather_than_dropping_credentials(bad):
    # THE CORE PS-217 PROPERTY: a URL this code cannot fully read must NOT
    # become an unauthenticated connection. The old code returned
    # {"server": <raw url>} for every one of these and the launch continued.
    with pytest.raises(ProxyUrlUnparseable):
        engine_proxy_dict(bad)


def test_engine_proxy_dict_refusal_never_echoes_the_credential():
    # The refusal message reaches logs and a pipe the UI renders. It carries
    # the credential, so it must never contain it — the same rule
    # verify/socks_fetch.parse_socks5h states for its own refusal.
    try:
        engine_proxy_dict("ftp://hunter2user:hunter2pass@1.2.3.4:1080")
    except ProxyUrlUnparseable as e:
        assert "hunter2pass" not in str(e)
        assert "hunter2user" not in str(e)
    else:  # pragma: no cover - the case above must raise
        raise AssertionError("expected a refusal")
