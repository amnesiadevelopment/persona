"""The per-engine anti-leak matrix, stated as a matrix — including its gaps.

Every anti-leak vector is implemented TWICE (Chromium in ``process.py``,
Firefox in ``invisible_launch.py``) with no shared policy layer, so historically
a vector shipped for one engine and was rediscovered for the other releases
later — DoH/QUIC/dns-prefetch landed for Chromium in the initial commit and did
not exist in Firefox for releases afterwards. DoH/TRR was closed by #76 and
dns-prefetch by #105; QUIC/HTTP3 is still open.

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

Gap-closing was originally deferred here on the grounds that it "would change
existing profiles' fingerprints, which the project's bit-stability invariant
forbids". #76 measured that claim rather than inheriting it, and it does not
hold for these three prefs: no probe under ``src/services/verify/`` reads any
pref at all, so closing one moves no recorded reading. THAT is the
load-bearing evidence, and it is the one to re-derive when closing the QUIC
cell.

A byte-identical baseline recording before and after the #76 and #105 changes
is consistent with that but does NOT independently confirm it:
``baseline_profile()`` is ``proxy=None`` (``src/services/verify/baseline.py``
:136-145), so a proxy-gated pref never fires during that recording. The
comparison is therefore trivially true — for the ICE prefs, ``trr.mode`` and
``disablePrefetch`` alike — and would have come back byte-identical whether
such a pref were correct, wrong-valued, or absent entirely.

The remaining true part is narrower: a server can still observe that a client
does not use a TRR resolver. Each cell still needs its own measurement before
it is closed; the blanket deferral does not.

Matrix pinned below, for ONE proxied profile:

| vector               | Chromium                                  | Firefox   |
|----------------------|-------------------------------------------|-----------|
| WebRTC non-proxied UDP | --force-webrtc-ip-handling-policy=...    | 5 ICE prefs |
| proxied DNS          | --dns-over-https-mode=off + DnsOverHttps  | socks_remote_dns |
| DoH / TRR            | --dns-over-https-mode=off                 | trr.mode=5 (#76) |
| dns-prefetch         | --dns-prefetch-disable                    | disablePrefetch x2 (#105) |
| QUIC / HTTP3         | --disable-quic + EnableQuic disabled      | KNOWN GAP |
"""

from src.models.profile import Profile
from src.services.browser.invisible_launch import _profile_prefs
from tests.test_process import (
    _StoreWithCheckedProxy,
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
        store=_StoreWithCheckedProxy,
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


def test_matrix_doh_trr_both_engines(monkeypatch, tmp_path):
    # BOTH engines cover this one, as of #76. Gap closed: the known-gap
    # assertions that used to sit here are deleted, which IS the record.
    #
    # Chromium switches DoH off outright; Firefox pins TRR to 5 ("off by
    # explicit choice"). A TRR resolver talks straight to a DoH endpoint and
    # never asks SOCKS, so without this the DNS test shows a country unrelated
    # to the exit IP — a location disclosure, not a compatibility question.
    args = _chromium_proxied(monkeypatch, tmp_path)
    assert "--dns-over-https-mode=off" in args

    # 5, not 0: 0 would be "off by default" — a no-op an engine bump silently
    # overwrites. The pin only means anything if it is the explicit value.
    prefs = _firefox_proxied()
    assert prefs.get("network.trr.mode") == 5


def test_matrix_doh_trr_direct_profile_unpinned():
    # The parity above is scoped to a PROXIED profile, exactly like the ICE
    # guards: a direct profile has no tunnel to bypass, so pinning its resolver
    # would add a tell and buy nothing. Keeps the gate honest — a future edit
    # that hoists the pref out of the `if cfg.get("proxy_url")` block turns
    # this red rather than passing unnoticed.
    assert "network.trr.mode" not in _profile_prefs({"search_engine": "duckduckgo"})


def test_matrix_dns_prefetch_both_engines(monkeypatch, tmp_path):
    # BOTH engines cover this one, as of #105. Gap closed: the known-gap
    # assertions that used to sit here are deleted, which IS the record.
    #
    # Chromium disables prefetch outright; Firefox sets the two disablePrefetch
    # prefs. Measured on the bundled build (Firefox 151.0, watching the system
    # resolver via an LD_PRELOAD getaddrinfo shim and the tunnel via a logging
    # SOCKS5 server at once): with no guard a page's dns-prefetch hints resolve
    # on the SYSTEM RESOLVER and never through SOCKS, and the guard takes that
    # from three lookups to zero. On a proxied profile the lookups are already
    # silent today, so this is a PIN against an engine bump flipping that
    # default — the same shape as trr.mode above — not a fix for a live leak.
    args = _chromium_proxied(monkeypatch, tmp_path)
    assert "--dns-prefetch-disable" in args

    prefs = _firefox_proxied()
    assert prefs.get("network.dns.disablePrefetch") is True
    assert prefs.get("network.dns.disablePrefetchFromHTTPS") is True


def test_matrix_dns_prefetch_direct_profile_unpinned():
    # Scoped to a PROXIED profile, exactly like the ICE guards and trr.mode: a
    # direct profile has no tunnel to bypass, so suppressing its prefetch would
    # buy nothing. Keeps the gate honest — a future edit that hoists either pref
    # out of the `if cfg.get("proxy_url")` block turns this red rather than
    # passing unnoticed.
    prefs = _profile_prefs({"search_engine": "duckduckgo"})
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
