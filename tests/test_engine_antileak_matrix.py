"""The per-engine anti-leak matrix, stated as a matrix — including its gaps.

Every anti-leak vector is implemented TWICE (Chromium in ``process.py``,
Firefox in ``invisible_launch.py``) with no shared policy layer, so historically
a vector shipped for one engine and was rediscovered for the other releases
later — DoH/QUIC/dns-prefetch landed for Chromium in the initial commit and did
not exist in Firefox for releases afterwards. DoH/TRR was closed by #76,
dns-prefetch by #105, and QUIC/HTTP3 by #151 — the matrix has NO open cells.

#151 is the one closed WITHOUT adding a persona pref, and that difference is
load-bearing rather than a detail. Measured before writing any implementation:
the engine's own baseline (``invisible_core.prefs._BASELINE``) already sets BOTH
``network.http.http3.enable`` and ``.enabled`` to False, and
``translate_profile_to_prefs`` starts from ``dict(_BASELINE)`` — so Firefox's
HTTP/3 was already off for every profile persona launches, direct and proxied
alike. Adding a persona-level pref would have changed no value and bought
nothing. The cell below therefore asserts the ENGINE-layer coverage, which is
where the guard actually lives; asserting ``_profile_prefs`` would have pinned
an empty claim.

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
| QUIC / HTTP3         | --disable-quic + EnableQuic disabled      | engine _BASELINE http3 x2 (#151) |
"""

import pytest

from src.models.profile import Profile
from src.services.browser.invisible_launch import _profile_prefs, _proxy_dict
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


def _firefox_effective_proxied():
    """Every pref a proxied, HEADFUL Firefox profile actually launches with.

    `_firefox_proxied()` above returns only persona's own overlay. That is the
    right oracle for a cell persona pins itself, and the WRONG one for a cell
    the engine owns: a key absent from the overlay may still be set — the engine
    composes `_BASELINE` first and applies `extra_prefs` LAST, so the effective
    value is what the browser sees.

    `compose_session_prefs`, NOT `translate_profile_to_prefs`. The engine says
    why in its own source (`invisible_core/prefs.py:1466`): translate "is the
    fingerprint. It is never the whole prefs dict a session runs with" — a
    proxy, a cloak, a humanize toggle and two crash prefs sit on top of it, in
    an ordering the same file calls load-bearing. `compose_session_prefs` is
    the composer the product actually reaches (`invisible_launch.py:2956,3022`
    -> `launcher._build_prefs` -> `_session.build_prefs` -> here), so this is
    the layer the browser launches with rather than one below it.

    That distinction is the whole point of this helper. The cell it feeds is a
    regression sentinel against an engine that autobumps daily at 06:00 UTC,
    and today's value is identical at both layers — so reading the lower one
    would look correct indefinitely and then fail OPEN the moment a bump moved
    the guard up a layer, or a future `configure_proxy` touched HTTP/3 behind a
    UDP-blocking proxy (exactly the compatibility rationale at `prefs.py:566`).
    A sentinel that reads below the layer it guards is silently worthless.

    `proxy=` is passed for the same reason the helper is named `_proxied`: the
    proxy layer is one of the five above the fingerprint, and it is the one
    that mutates prefs. Built with the product's own `_proxy_dict` rather than
    a hand-written dict, so the shape cannot drift from what persona passes.

    HEADFUL, and the word is the honest bound rather than a decoration. The
    other three layer flags are derived from `headless` one level up
    (`invisible_playwright/_session.py:219-228`: `virtual_display` and `cloak`
    are `bool(headless and <platform>)`, `humanize` from the cursor engine),
    and persona launches `headless: False` (`invisible_launch.py:2956`), so
    all three are off on the path this cell describes. Measured rather than
    assumed: composing with them set explicitly differs from this helper's
    return in exactly ONE key, `stealthfox.humanize` (None vs False) — not an
    http3 key, so the cell's verdict is identical either way. This returns the
    headful composition, not every composition the engine can produce.

    Skips rather than fails when the engine is missing: it is a git dependency
    (`pyproject.toml:48`) and is genuinely absent from some containers, so an
    ImportError here would be an environment report, not a matrix regression.
    The same `importorskip` guard `test_invisible_launch.py:796` uses.
    """
    pytest.importorskip("invisible_core")
    from invisible_core import compose_session_prefs
    from invisible_core._fpforge import generate_profile

    return compose_session_prefs(
        generate_profile(1),
        extra_prefs=_firefox_proxied(),
        proxy=_proxy_dict(_FF_PROXIED_CFG["proxy_url"]),
    ).prefs


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


def test_matrix_quic_http3_both_engines(monkeypatch, tmp_path):
    # BOTH engines cover this one, as of #151. Gap closed: the known-gap
    # assertions that used to sit here are deleted, which IS the record.
    #
    # This cell is closed DIFFERENTLY from the other four, and the difference is
    # the finding rather than an implementation detail. #151 measured before
    # writing anything and found the guard already present one layer down: the
    # engine's `_BASELINE` sets both http3 spellings False, and
    # `translate_profile_to_prefs` starts from `dict(_BASELINE)`, so Firefox's
    # HTTP/3 is off for every profile persona launches. No persona-level pref
    # was added, because adding one would have changed no value.
    #
    # So the oracle here is the EFFECTIVE pref set a profile launches with, not
    # `_profile_prefs`'s return. Asserting on `_profile_prefs` would pin an
    # empty claim: the key is legitimately absent from it (see the direct/
    # proxied cell below), and absence there says nothing about the browser.
    #
    # NOT SOLD AS A LEAK. `process.py:741-748` gives Chromium's --disable-quic a
    # COMPATIBILITY motive (SOCKS5 tunnels only TCP, so HTTP/3 hangs behind a
    # UDP-blocking proxy) and a motive is not a measurement. Whether UDP would
    # egress on the real interface was NOT measured — the bundled Firefox is
    # absent from this container, so no live traffic run was possible. The
    # narrow, fully-evidenced claim is the one asserted: both engines disable
    # HTTP/3 for a proxied profile.
    args = _chromium_proxied(monkeypatch, tmp_path)
    assert "--disable-quic" in args
    assert "EnableQuic" in ",".join(_disable_features_values(args)).split(",")

    # Firefox: assert the ENGINE-layer coverage, on the composed pref set.
    prefs = _firefox_effective_proxied()
    assert prefs.get("network.http.http3.enable") is False
    assert prefs.get("network.http.http3.enabled") is False


def test_matrix_quic_http3_not_pinned_by_persona():
    # The companion to the cell above, and the reason it reads the EFFECTIVE set.
    #
    # This is NOT the direct-profile gate the trr.mode and dns-prefetch cells
    # use — those pin a persona pref to the `if cfg.get("proxy_url")` block, and
    # there is no persona pref here to gate. What it pins instead is WHERE the
    # HTTP/3 guard lives: entirely in the engine, for BOTH profile shapes.
    #
    # It earns its place by failing on a real regression: if someone later adds
    # a persona-level http3 pref (believing the cell above needs one), this goes
    # red and forces the question out loud — is the engine guard gone, or is the
    # new pref redundant? Either answer belongs in a commit message.
    for cfg in (_FF_PROXIED_CFG, {"search_engine": "duckduckgo"}):
        prefs = _profile_prefs(cfg)
        assert "network.http.http3.enable" not in prefs
        assert "network.http.http3.enabled" not in prefs
