import src.services.browser.invisible_launch as il
import src.services.browser.process as process
from src.models.profile import Profile


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
    monkeypatch.setattr(il, "spawn", lambda cfg: captured.append(cfg) or object())
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
    monkeypatch.setattr(il, "spawn", lambda cfg: captured.append(cfg) or object())
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
