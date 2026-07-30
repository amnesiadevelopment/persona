import src.services.browser.invisible_launch as il
import src.services.browser.process as process
from src.models.profile import Profile


class _Spawned:
    """A stand-in for the spawned handle that accepts attribute assignment
    (the real Popen does; a bare object() doesn't)."""


class _Store:
    def resolve(self, name):
        return ""

    def get(self, name):
        return None


class _Bookmarks:
    def resolve_selection(self, pool, names):
        return []


def _launch_cfg(monkeypatch, tmp_path, calls):
    def _ensure(*a, **k):
        calls["ensure"] += 1
        return True

    def _is_installed():
        calls["is"] += 1
        return True

    captured = []
    monkeypatch.setattr(il, "ensure_invisible_installed", _ensure)
    monkeypatch.setattr(il, "is_invisible_installed", _is_installed)
    monkeypatch.setattr(il, "spawn", lambda cfg: captured.append(cfg) or _Spawned())
    monkeypatch.setattr(process, "ProxyStore", _Store)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    profile = Profile(name="seed-check", engine="firefox")
    process._spawn_invisible(profile, str(tmp_path))
    return profile, captured[0]


def test_cfg_carries_stable_fingerprint_seed(monkeypatch, tmp_path):
    calls = {"ensure": 0, "is": 0}
    profile, cfg = _launch_cfg(monkeypatch, tmp_path, calls)
    assert cfg["seed"] == profile.fingerprint_seed
    _, cfg2 = _launch_cfg(monkeypatch, tmp_path, calls)
    assert cfg2["seed"] == cfg["seed"]


def test_cfg_no_mtls_ca_when_unassigned(monkeypatch, tmp_path):
    calls = {"ensure": 0, "is": 0}
    _, cfg = _launch_cfg(monkeypatch, tmp_path, calls)
    assert cfg["mtls_ca_path"] is None


def test_ff_cert_starts_terminator_sets_proxy_and_ca(monkeypatch, tmp_path):
    # A firefox profile with a cert: the launch starts a terminator, points the
    # engine's proxy at it, and passes the terminator CA for cert9.db trust.
    from src.services.cert.store import Certificate

    class _CertStore:
        def get(self, name):
            return Certificate(
                name="admin", p12_path="/vault/admin.p12", password="pw",
                url="https://admin.example.com/",
            ) if name == "admin" else None

    class _Session:
        proxy_url = "http://127.0.0.1:44444"
        ca_path = "/work/term_ca.crt"
        spki_b64 = "SPKI=="

        def stop(self):
            pass

    seen = {}

    def fake_start(cert, upstream, work, verify_upstream=True):
        seen["cert"] = cert
        seen["upstream"] = upstream
        return _Session()

    captured = []
    monkeypatch.setattr(il, "is_invisible_installed", lambda: True)
    monkeypatch.setattr(il, "spawn", lambda cfg: captured.append(cfg) or _Spawned())
    monkeypatch.setattr(process, "ProxyStore", _Store)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "CertStore", _CertStore)
    import src.services.cert.manager as cm
    monkeypatch.setattr(cm, "start_cert_session", fake_start)

    profile = Profile(name="p", engine="firefox", certificate="admin")
    process._spawn_invisible(profile, str(tmp_path))
    cfg = captured[0]
    assert cfg["proxy_url"] == "http://127.0.0.1:44444"
    assert cfg["mtls_ca_path"] == "/work/term_ca.crt"
    assert seen["cert"].name == "admin"


def test_needs_fetch_never_triggers_download(monkeypatch, tmp_path):
    calls = {"ensure": 0, "is": 0}
    _, cfg = _launch_cfg(monkeypatch, tmp_path, calls)
    assert calls["ensure"] == 0
    assert calls["is"] >= 1
    assert cfg["_needs_fetch"] is False


class _GeolessProxy:
    timezone = ""
    country_code = ""
    lat = None
    lon = None


class _StoreWithGeolessProxy:
    def resolve(self, name):
        return "socks5://1.2.3.4:1080"

    def get(self, name):
        return _GeolessProxy()


def test_firefox_unchecked_proxy_ships_host_zone_not_utc(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(il, "is_invisible_installed", lambda: True)
    monkeypatch.setattr(il, "spawn", lambda cfg: captured.append(cfg) or _Spawned())
    monkeypatch.setattr(process, "ProxyStore", _StoreWithGeolessProxy)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "_host_timezone", lambda: "Europe/Kyiv")
    profile = Profile(name="tz-firefox", engine="firefox", proxy="p1")
    process._spawn_invisible(profile, str(tmp_path))
    assert captured[0]["timezone"] == "Europe/Kyiv"


def test_chromium_unchecked_proxy_ships_host_zone_not_utc(monkeypatch, tmp_path):
    captured = {}

    class _FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args

    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", _StoreWithGeolessProxy)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "_host_timezone", lambda: "Europe/Kyiv")
    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    profile = Profile(name="tz-chromium", proxy="p1")
    process.spawn_browser(profile)
    assert "--timezone=Europe/Kyiv" in captured["args"]
    assert "--timezone=UTC" not in captured["args"]


def test_chromium_keeps_window_visible_so_overlays_render(monkeypatch, tmp_path):
    # #233: a non-foreground/occluded persona window reported
    # visibilityState=hidden, so chromium throttled requestAnimationFrame to 0fps
    # and Google Sheets' rAF-driven overlays (the date-cell calendar, the
    # custom-currency dialog) never painted — on every OS. These flags keep the
    # page visible and rAF running regardless of foreground/occlusion.
    captured = {}

    class _FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args

    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", _Store)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    process.spawn_browser(Profile(name="vis-chromium"))
    args = captured["args"]
    assert "--disable-backgrounding-occluded-windows" in args
    assert "--disable-renderer-backgrounding" in args
    assert "--disable-background-timer-throttling" in args
    disabled = next(
        (a for a in args if a.startswith("--disable-features=")), ""
    )
    assert "CalculateNativeWinOcclusion" in disabled


def test_linux_chromium_env_keeps_system_fontconfig(monkeypatch, tmp_path):
    # A per-profile FONTCONFIG_FILE flooded live sessions with "Cannot load
    # default config file" errors from chromium child processes and rendered
    # pages with the bundled clone fonts instead of the system set. The
    # browser must inherit the system fontconfig.
    captured = {}

    class _FakePopen:
        def __init__(self, args, **kwargs):
            captured["env"] = kwargs.get("env")

    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", _Store)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "write_window_entry", lambda name: None)
    monkeypatch.setattr(process._platform, "IS_LINUX", True)
    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    monkeypatch.delenv("FONTCONFIG_FILE", raising=False)
    monkeypatch.delenv("FONTCONFIG_PATH", raising=False)
    profile = Profile(name="fontenv")
    process.spawn_browser(profile)
    assert "FONTCONFIG_FILE" not in captured["env"]
    assert "FONTCONFIG_PATH" not in captured["env"]
    assert not (tmp_path / "fontenv" / "fonts.conf").exists()


def _spawn_chromium_args(monkeypatch, tmp_path, profile, linux=False, store=_Store):
    captured = {}

    class _FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            captured["env"] = kwargs.get("env")

    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", store)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "write_window_entry", lambda name: None)
    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    # Pin IS_LINUX to the case under test regardless of the host running the
    # suite — a non-linux case left the real platform in place, so the VA-API
    # assertions failed on a Linux CI/dev box while passing on Windows.
    monkeypatch.setattr(process._platform, "IS_LINUX", bool(linux))
    process.spawn_browser(profile)
    return captured


def test_linux_chromium_strips_inherited_fontconfig_env(monkeypatch, tmp_path):
    # The app's own runtime can export FONTCONFIG_FILE/PATH into os.environ
    # (AppImage bundle mount); inheriting them re-creates the "Cannot load
    # default config file" flood. The browser env must be scrubbed even when
    # the parent process is polluted.
    monkeypatch.setenv("FONTCONFIG_FILE", "/tmp/.mount_gone/etc/fonts/fonts.conf")
    monkeypatch.setenv("FONTCONFIG_PATH", "/tmp/.mount_gone/etc/fonts")
    monkeypatch.setenv("FONTCONFIG_SYSROOT", "/tmp/.mount_gone")
    captured = _spawn_chromium_args(
        monkeypatch, tmp_path, Profile(name="fontenv2"), linux=True
    )
    assert "FONTCONFIG_FILE" not in captured["env"]
    assert "FONTCONFIG_PATH" not in captured["env"]
    assert "FONTCONFIG_SYSROOT" not in captured["env"]


def test_linux_chromium_tames_software_compositor_animations(monkeypatch, tmp_path):
    captured = _spawn_chromium_args(
        monkeypatch, tmp_path, Profile(name="composite"), linux=True
    )
    assert "--disable-threaded-animation" in captured["args"]
    assert "--animation-duration-scale=0" in captured["args"]
    assert "--wm-window-animations-disabled" in captured["args"]


def _disable_features_values(args):
    return [a.split("=", 1)[1] for a in args if a.startswith("--disable-features=")]


def test_linux_chromium_suppresses_vaapi_probe(monkeypatch, tmp_path):
    captured = _spawn_chromium_args(
        monkeypatch, tmp_path, Profile(name="vaapi"), linux=True
    )
    values = _disable_features_values(captured["args"])
    assert len(values) == 1
    features = values[0].split(",")
    assert "VaapiVideoDecoder" in features
    assert "VaapiVideoEncoder" in features


def test_non_linux_chromium_gets_no_vaapi_flags(monkeypatch, tmp_path):
    captured = _spawn_chromium_args(monkeypatch, tmp_path, Profile(name="vaapi-win"))
    assert not any("Vaapi" in a for a in captured["args"])


def test_proxied_linux_chromium_keeps_vaapi_suppression(monkeypatch, tmp_path):
    # Chromium honors only the LAST --disable-features switch, so the proxy's
    # DnsOverHttps entry must merge with the VA-API suppression, not replace it.
    captured = _spawn_chromium_args(
        monkeypatch,
        tmp_path,
        Profile(name="vaapi-proxy", proxy="p1"),
        linux=True,
        store=_StoreWithGeolessProxy,
    )
    values = _disable_features_values(captured["args"])
    assert len(values) == 1
    features = values[0].split(",")
    assert "VaapiVideoDecoder" in features
    assert "VaapiVideoEncoder" in features
    assert "DnsOverHttps" in features


def test_proxied_non_linux_chromium_disables_doh_and_quic(monkeypatch, tmp_path):
    captured = _spawn_chromium_args(
        monkeypatch,
        tmp_path,
        Profile(name="doh-win", proxy="p1"),
        store=_StoreWithGeolessProxy,
    )
    values = _disable_features_values(captured["args"])
    assert len(values) == 1
    features = values[0].split(",")
    assert "DnsOverHttps" in features
    assert "EnableQuic" in features


def test_proxied_chromium_disables_quic(monkeypatch, tmp_path):
    # #184: SOCKS5 tunnels only TCP, but Google apps prefer QUIC (UDP/HTTP3) for
    # their realtime collab channel. Behind the proxy that UDP can't traverse, so
    # Sheets' channel never connects and the "Working" throbber sticks (and the
    # calendar / currency overlays that load over it never paint). Disable QUIC so
    # everything falls back to HTTP/2 over TCP, which the proxy carries. Both the
    # switch and the feature-disable, per current Chromium.
    captured = _spawn_chromium_args(
        monkeypatch,
        tmp_path,
        Profile(name="quic-proxy", proxy="p1"),
        store=_StoreWithGeolessProxy,
    )
    assert "--disable-quic" in captured["args"]
    assert "EnableQuic" in _disable_features_values(captured["args"])[0].split(",")


def test_unproxied_chromium_keeps_quic(monkeypatch, tmp_path):
    # No proxy means QUIC's UDP flows freely and is the fingerprint-correct
    # behaviour of a real Chrome — only kill it when a TCP-only proxy forces the
    # fallback, so a direct profile isn't left without HTTP/3.
    captured = _spawn_chromium_args(monkeypatch, tmp_path, Profile(name="quic-direct"))
    assert "--disable-quic" not in captured["args"]
    assert not any("EnableQuic" in v for v in _disable_features_values(captured["args"]))


def _proxy_server_value(args):
    for a in args:
        if a.startswith("--proxy-server="):
            return a.split("=", 1)[1]
    return None


def test_proxied_chromium_uses_socks5_not_socks5h(monkeypatch, tmp_path):
    # Chromium already does remote DNS for socks5 and REJECTS socks5h (a curl-ism)
    # with ERR_NO_SUPPORTED_PROXIES, which would kill all proxied traffic. Keep
    # the scheme socks5 for the no-auth path.
    captured = _spawn_chromium_args(
        monkeypatch,
        tmp_path,
        Profile(name="dns-noauth", proxy="p1"),
        store=_StoreWithGeolessProxy,  # socks5://1.2.3.4:1080 (no creds)
    )
    assert _proxy_server_value(captured["args"]) == "socks5://1.2.3.4:1080"


def test_proxied_chromium_bridge_uses_socks5(monkeypatch, tmp_path):
    # A credentialed upstream goes through the local ProxyBridge; Chromium is
    # pointed at it with socks5 (socks5h would be rejected).
    class _StoreWithAuthProxy:
        def resolve(self, name):
            return "socks5://user:pass@1.2.3.4:1080"

        def get(self, name):
            return _GeolessProxy()

    class _FakeBridge:
        def __init__(self, url):
            pass

        def start(self):
            return 40555

        def stop(self):
            pass

    monkeypatch.setattr(process, "ProxyBridge", _FakeBridge)
    captured = _spawn_chromium_args(
        monkeypatch,
        tmp_path,
        Profile(name="dns-auth", proxy="p1"),
        store=_StoreWithAuthProxy,
    )
    assert _proxy_server_value(captured["args"]) == "socks5://127.0.0.1:40555"


def test_failed_launch_stops_bridge_no_orphan(monkeypatch, tmp_path):
    # If the browser Popen raises AFTER the bridge started, the bridge must be
    # stopped — otherwise a SOCKS listener orphans on 127.0.0.1 and repeated
    # failures exhaust ports.
    stopped = []

    class _StoreWithAuthProxy:
        def resolve(self, name):
            return "socks5://user:pass@1.2.3.4:1080"

        def get(self, name):
            return _GeolessProxy()

    class _FakeBridge:
        def __init__(self, url):
            pass

        def start(self):
            return 40555

        def stop(self):
            stopped.append(True)

    def _boom(*a, **k):
        raise OSError("launch failed")

    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", _StoreWithAuthProxy)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "write_window_entry", lambda name: None)
    monkeypatch.setattr(process, "ProxyBridge", _FakeBridge)
    monkeypatch.setattr(process.subprocess, "Popen", _boom)
    monkeypatch.setattr(process._platform, "IS_LINUX", False)

    import pytest

    with pytest.raises(OSError):
        process.spawn_browser(Profile(name="fail-launch", proxy="p1"))
    assert stopped == [True]


def test_hidpi_host_gets_render_scale_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(process, "_host_display_scale", lambda: 1.5)
    captured = _spawn_chromium_args(
        monkeypatch, tmp_path, Profile(name="hidpi", resolution="2560x1440")
    )
    assert "--force-device-scale-factor=1.5" in captured["args"]


def test_scale_100_host_gets_no_render_scale_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(process, "_host_display_scale", lambda: 1.0)
    captured = _spawn_chromium_args(
        monkeypatch, tmp_path, Profile(name="lodpi", resolution="2560x1440")
    )
    assert not any(
        a.startswith("--force-device-scale-factor") for a in captured["args"]
    )


def test_render_scale_flag_leaves_fingerprint_args_alone(monkeypatch, tmp_path):
    monkeypatch.setattr(process, "_host_display_scale", lambda: 2.0)
    profile = Profile(name="fp-intact", resolution="2560x1440")
    captured = _spawn_chromium_args(monkeypatch, tmp_path, profile)
    assert f"--fingerprint={profile.fingerprint_seed}" in captured["args"]
    assert "--force-device-scale-factor=2" in captured["args"]


def test_effective_engine_mobile_forces_chromium():
    p = Profile(name="m", engine="firefox", os_type="android")
    assert process.effective_engine(p) == "chromium"


def test_effective_engine_desktop_firefox_stays_firefox():
    p = Profile(name="d", engine="firefox", os_type="windows")
    assert process.effective_engine(p) == "firefox"


def test_effective_engine_camoufox_maps_to_firefox():
    p = Profile(name="c", engine="camoufox", os_type="windows")
    assert process.effective_engine(p) == "firefox"


def test_effective_engine_default_is_chromium():
    p = Profile(name="x")
    assert process.effective_engine(p) == "chromium"


def test_chromium_no_proxy_timezone_matches_en_us_language(monkeypatch, tmp_path):
    # Anti-detect consistency: with no proxy the language is forced en-US (never
    # leak the host locale), so the timezone must AGREE with en-US — a US zone,
    # NOT the host zone. CreepJS on a Kyiv host flagged en-US paired with
    # Europe/Kyiv (language⊥timezone). Assert a direct chromium profile pins a US
    # timezone so the identity is a coherent US-English user.
    captured = {}

    class _FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args

    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", _Store)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "_host_timezone", lambda: "Europe/Kyiv")
    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    process.spawn_browser(Profile(name="direct-chromium"))
    tz = next(
        (a for a in captured["args"] if a.startswith("--timezone=")), ""
    )
    assert tz == "--timezone=America/New_York", (
        f"no-proxy chromium must pin a US tz to match en-US, got {tz!r}"
    )
    assert "Europe/Kyiv" not in tz, "host timezone must not leak on a direct profile"
