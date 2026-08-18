"""The per-engine anti-leak matrix, stated as a matrix — including its gaps.

Every anti-leak vector is implemented TWICE (Chromium in ``process.py``,
Firefox in ``invisible_launch.py``) with no shared policy layer, so historically
a vector shipped for one engine and was rediscovered for the other releases
later — DoH/QUIC/dns-prefetch landed for Chromium in the initial commit and
still do not exist in Firefox.

The per-engine assertions that exist today live in two different files
(``test_process.py`` for Chromium argv, ``test_invisible_launch.py`` for Firefox
prefs) with nothing comparing them, so a one-engine addition is invisible.

This is a CHARACTERIZATION test: it pins the matrix AS IT IS TODAY, gaps
included, and is green on day one. It does not demand parity. Its job is to
make the shape of the matrix visible and to turn a one-engine change red:

* Add a vector to one engine only -> the other engine's known-gap cell flips
  and this file fails, forcing the parity question to be answered out loud.
* Close a known gap on purpose -> delete that cell's known-gap assertion in the
  same commit. That deletion IS the record that the gap was closed.

Closing a gap here would change existing profiles' fingerprints, which the
project's bit-stability invariant forbids — so gap-closing is deliberately
separate follow-up work, not part of this file.

Matrix pinned below, for ONE proxied profile:

| vector               | Chromium                                  | Firefox   |
|----------------------|-------------------------------------------|-----------|
| WebRTC non-proxied UDP | --force-webrtc-ip-handling-policy=...    | 5 ICE prefs |
| proxied DNS          | --dns-over-https-mode=off + DnsOverHttps  | socks_remote_dns |
| DoH / TRR            | present                                   | KNOWN GAP |
| dns-prefetch         | --dns-prefetch-disable                    | KNOWN GAP |
| QUIC / HTTP3         | --disable-quic + EnableQuic disabled      | KNOWN GAP |
"""

from src.models.profile import Profile
from src.services.browser.invisible_launch import _profile_prefs
from tests.test_process import (
    _StoreWithGeolessProxy,
    _disable_features_values,
    _spawn_chromium_args,
)

# The same single proxied profile drives both engines, so the two columns are
# genuinely comparable rather than two unrelated scenarios.
_FF_PROXIED_CFG = {
    "search_engine": "duckduckgo",
    "proxy_url": "socks5://user:pass@1.2.3.4:1080",
}


def _chromium_proxied(monkeypatch, tmp_path):
    """Chromium argv for a proxied profile, via the existing spawn harness."""
    captured = _spawn_chromium_args(
        monkeypatch,
        tmp_path,
        Profile(name="antileak-matrix", proxy="p1"),
        store=_StoreWithGeolessProxy,
    )
    return captured["args"]


def _firefox_proxied():
    """Firefox prefs for a proxied profile — _profile_prefs is a pure function."""
    return _profile_prefs(_FF_PROXIED_CFG)


def test_matrix_webrtc_non_proxied_udp_both_engines(monkeypatch, tmp_path):
    # BOTH engines cover this one. Chromium forbids non-proxied UDP outright;
    # Firefox reaches the same place with five ICE prefs.
    args = _chromium_proxied(monkeypatch, tmp_path)
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args

    prefs = _firefox_proxied()
    assert prefs.get("media.peerconnection.ice.relay_only") is True
    assert prefs.get("media.peerconnection.ice.no_host") is True
    assert prefs.get("media.peerconnection.ice.default_address_only") is True
    assert prefs.get("media.peerconnection.ice.proxy_only_if_behind_proxy") is True
    assert prefs.get("media.peerconnection.use_document_iceservers") is False


def test_matrix_proxied_dns_both_engines(monkeypatch, tmp_path):
    # BOTH engines cover this one: name lookups must go through the proxy, or
    # the DNS test shows a country unrelated to the exit IP.
    args = _chromium_proxied(monkeypatch, tmp_path)
    assert "--dns-over-https-mode=off" in args
    assert "DnsOverHttps" in ",".join(_disable_features_values(args)).split(",")

    prefs = _firefox_proxied()
    assert prefs.get("network.proxy.socks_remote_dns") is True


def test_matrix_doh_trr_chromium_only(monkeypatch, tmp_path):
    args = _chromium_proxied(monkeypatch, tmp_path)
    assert "--dns-over-https-mode=off" in args

    # KNOWN GAP — Firefox has no DoH/TRR guard. Chromium's DoH is switched off
    # for a proxied profile; Firefox's TRR is left at its default, so its
    # resolver can still bypass the SOCKS proxy. Asserted-as-absent on purpose:
    # a silent omission is what let this survive since the initial commit.
    # When the gap is closed, delete this assertion in the same commit.
    prefs = _firefox_proxied()
    assert "network.trr.mode" not in prefs
    assert "network.trr.uri" not in prefs


def test_matrix_dns_prefetch_chromium_only(monkeypatch, tmp_path):
    args = _chromium_proxied(monkeypatch, tmp_path)
    assert "--dns-prefetch-disable" in args

    # KNOWN GAP — Firefox has no dns-prefetch guard (see the note above).
    prefs = _firefox_proxied()
    assert "network.dns.disablePrefetch" not in prefs
    assert "network.dns.disablePrefetchFromHTTPS" not in prefs


def test_matrix_quic_http3_chromium_only(monkeypatch, tmp_path):
    args = _chromium_proxied(monkeypatch, tmp_path)
    assert "--disable-quic" in args
    assert "EnableQuic" in ",".join(_disable_features_values(args)).split(",")

    # KNOWN GAP — Firefox has no QUIC/HTTP3 guard. A SOCKS5 proxy tunnels only
    # TCP, so HTTP/3 over UDP never reaches the far side (see the note above).
    prefs = _firefox_proxied()
    assert "network.http.http3.enable" not in prefs
    assert "network.http.http3.enabled" not in prefs
