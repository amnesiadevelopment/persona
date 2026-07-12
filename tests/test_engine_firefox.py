import json
import sys
from pathlib import Path
from types import SimpleNamespace

import src.services.browser.invisible_launch as inv
from src.services.engine import firefox as ff

WIN_ASSET = "firefox-150.0.1-stealth-win-x86_64.zip"
FULL_ASSETS = [
    {"name": "checksums.txt"},
    {"name": "firefox-150.0.1-stealth-linux-arm64.tar.gz"},
    {"name": "firefox-150.0.1-stealth-linux-x86_64.tar.gz"},
    {"name": "firefox-150.0.1-stealth-macos-arm64.tar.gz"},
    {"name": "firefox-150.0.1-stealth-macos-x86_64.tar.gz"},
    {"name": WIN_ASSET},
]


def test_build_number():
    assert ff.build_number("firefox-15") == 15
    assert ff.build_number("firefox-8") == 8
    assert ff.build_number("usage-counter") == -1
    assert ff.build_number("firefox-15-beta") == -1
    assert ff.build_number("") == -1


def test_is_newer():
    assert ff.is_newer("firefox-16", "firefox-15") is True
    assert ff.is_newer("firefox-15", "firefox-15") is False
    assert ff.is_newer("firefox-14", "firefox-15") is False
    assert ff.is_newer("firefox-16", "") is True  # nothing installed
    assert ff.is_newer("", "firefox-15") is False  # no latest info
    assert ff.is_newer("usage-counter", "firefox-15") is False


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _serve(monkeypatch, payload):
    monkeypatch.setattr(ff, "_expected_asset", lambda: WIN_ASSET)
    monkeypatch.setattr(
        ff.urllib.request, "urlopen", lambda req, timeout=20: _Resp(payload)
    )


def test_fetch_latest_picks_highest_firefox_tag(monkeypatch):
    _serve(
        monkeypatch,
        [
            {"tag_name": "usage-counter", "assets": [{"name": "launch.txt"}]},
            {"tag_name": "firefox-15", "assets": FULL_ASSETS},
            {"tag_name": "firefox-16", "assets": FULL_ASSETS},
        ],
    )
    assert ff.fetch_latest() == ("firefox-16", True)


def test_fetch_latest_skips_broken_versions(monkeypatch):
    # firefox-8 is in the package's real BROKEN_VERSIONS (shipped without the
    # juggler layer) — it must never be picked even when it's the highest.
    _serve(
        monkeypatch,
        [
            {"tag_name": "firefox-8", "assets": FULL_ASSETS},
            {"tag_name": "firefox-7", "assets": FULL_ASSETS},
        ],
    )
    assert ff.fetch_latest() == ("firefox-7", True)


def test_fetch_latest_skips_drafts_and_prereleases(monkeypatch):
    _serve(
        monkeypatch,
        [
            {"tag_name": "firefox-17", "draft": True, "assets": FULL_ASSETS},
            {"tag_name": "firefox-16", "prerelease": True, "assets": FULL_ASSETS},
            {"tag_name": "firefox-15", "assets": FULL_ASSETS},
        ],
    )
    assert ff.fetch_latest() == ("firefox-15", True)


def test_fetch_latest_incompatible_when_expected_asset_missing(monkeypatch):
    # An upstream-Firefox bump renames the assets → the installed package's
    # ARCHIVE_NAME no longer matches → needs a persona update, not a download.
    _serve(
        monkeypatch,
        [
            {
                "tag_name": "firefox-16",
                "assets": [
                    {"name": "checksums.txt"},
                    {"name": "firefox-151.0-stealth-win-x86_64.zip"},
                ],
            },
            {"tag_name": "firefox-15", "assets": FULL_ASSETS},
        ],
    )
    assert ff.fetch_latest() == ("firefox-16", False)


def test_fetch_latest_network_failure(monkeypatch):
    monkeypatch.setattr(ff, "_expected_asset", lambda: WIN_ASSET)

    def boom(req, timeout=20):
        raise OSError("no network")

    monkeypatch.setattr(ff.urllib.request, "urlopen", boom)
    assert ff.fetch_latest() == ("", False)


# --- active-build selection (invisible_launch) ---


def _fake_cache(monkeypatch, tmp_path, builds, binary_version="firefox-15"):
    """Create firefox-NN dirs under a fake cache root. `builds` is a list of
    (tag, has_binary, has_marker)."""
    import invisible_core.constants as consts
    import invisible_core.download as dl

    from invisible_playwright.constants import BINARY_ENTRY_REL

    monkeypatch.setattr(dl, "cache_root", lambda: tmp_path)
    monkeypatch.setattr(consts, "BINARY_VERSION", binary_version)
    entry_rel = BINARY_ENTRY_REL[sys.platform]
    for tag, has_binary, has_marker in builds:
        d = tmp_path / tag
        if has_binary:
            p = d / Path(entry_rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
        else:
            d.mkdir(parents=True, exist_ok=True)
        if has_marker:
            (d / inv._INSTALL_MARKER).touch()


def test_active_build_pinned_only(monkeypatch, tmp_path):
    # the package-pinned build counts without a marker (ensure_binary installs
    # it markerless)
    _fake_cache(monkeypatch, tmp_path, [("firefox-15", True, False)])
    assert inv.installed_builds() == ["firefox-15"]
    assert inv.active_build() == "firefox-15"
    assert inv._binary_path_override() is None
    assert inv.is_invisible_installed() is True


def test_active_build_prefers_newer_complete_build(monkeypatch, tmp_path):
    _fake_cache(
        monkeypatch,
        tmp_path,
        [("firefox-15", True, False), ("firefox-16", True, True)],
    )
    assert inv.installed_builds() == ["firefox-15", "firefox-16"]
    assert inv.active_build() == "firefox-16"
    override = inv._binary_path_override()
    assert override is not None
    assert "firefox-16" in override


def test_active_build_ignores_unmarked_build(monkeypatch, tmp_path):
    # binary present but no completion marker = crashed mid-extract → the
    # half build must never become active
    _fake_cache(
        monkeypatch,
        tmp_path,
        [("firefox-15", True, False), ("firefox-16", True, False)],
    )
    assert inv.installed_builds() == ["firefox-15"]
    assert inv.active_build() == "firefox-15"
    assert inv._binary_path_override() is None


def test_active_build_ignores_broken_versions(monkeypatch, tmp_path):
    import invisible_core.constants as consts

    _fake_cache(
        monkeypatch,
        tmp_path,
        [("firefox-15", True, False), ("firefox-16", True, True)],
    )
    monkeypatch.setattr(consts, "BROKEN_VERSIONS", frozenset({"firefox-16"}))
    assert inv.installed_builds() == ["firefox-15"]
    assert inv.active_build() == "firefox-15"


def test_current_version_empty_when_not_installed(monkeypatch, tmp_path):
    _fake_cache(monkeypatch, tmp_path, [])
    assert inv.is_invisible_installed() is False
    assert ff.current_version() == ""


def test_install_engine_build_marks_completion(monkeypatch, tmp_path):
    from invisible_playwright.constants import BINARY_ENTRY_REL

    _fake_cache(monkeypatch, tmp_path, [])
    entry_rel = BINARY_ENTRY_REL[sys.platform]

    def fake_dl(url, path, progress=None, **kw):
        Path(path).write_bytes(b"data")
        return True

    def fake_extract(archive, dst, asset):
        p = Path(dst) / Path(entry_rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()

    monkeypatch.setattr(inv, "_resumable_download", fake_dl)
    monkeypatch.setattr(inv, "_extract_as", fake_extract)

    assert inv.install_engine_build("firefox-16") is True
    assert (tmp_path / "firefox-16" / inv._INSTALL_MARKER).exists()
    assert inv.active_build() == "firefox-16"


def test_install_engine_build_no_marker_when_extract_incomplete(
    monkeypatch, tmp_path
):
    _fake_cache(monkeypatch, tmp_path, [("firefox-15", True, False)])

    def fake_dl(url, path, progress=None, **kw):
        Path(path).write_bytes(b"data")
        return True

    monkeypatch.setattr(inv, "_resumable_download", fake_dl)
    # extraction produced no binary (bad archive) → not installed, no marker,
    # the previous build stays active
    monkeypatch.setattr(inv, "_extract_as", lambda a, d, n: None)

    assert inv.install_engine_build("firefox-16") is False
    assert not (tmp_path / "firefox-16" / inv._INSTALL_MARKER).exists()
    assert inv.active_build() == "firefox-15"


# --- app wiring ---


class InlineThread:
    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


def test_engine2_update_available_gating(monkeypatch):
    import src.ui.app as app_mod

    monkeypatch.setattr(inv, "is_invisible_installed", lambda: True)
    monkeypatch.setattr(ff, "current_version", lambda: "firefox-15")

    stub = SimpleNamespace(_engine2_latest="firefox-16", _engine2_compatible=True)
    assert app_mod.App._engine2_update_available(stub) is True

    stub._engine2_compatible = False
    assert app_mod.App._engine2_update_available(stub) is False

    stub._engine2_compatible = True
    stub._engine2_latest = "firefox-15"
    assert app_mod.App._engine2_update_available(stub) is False

    stub._engine2_latest = "firefox-16"
    monkeypatch.setattr(inv, "is_invisible_installed", lambda: False)
    assert app_mod.App._engine2_update_available(stub) is False


def test_update_engine2_downloads_tag(monkeypatch):
    import src.ui.app as app_mod

    calls = {}

    def fake_download(tag, progress=None, log=None):
        calls["tag"] = tag
        return True

    monkeypatch.setattr(ff, "download_engine", fake_download)
    monkeypatch.setattr(app_mod.threading, "Thread", InlineThread)

    stub = SimpleNamespace(
        _engine2_busy=False,
        _engine2_status="",
        _engine2_latest="firefox-16",
        _engine2_start_t=0.0,
        _engine2_throttle=None,
        _engine2_pstate=None,
        _engine2_bar=SimpleNamespace(value=None),
        _engine2_detail=SimpleNamespace(value=""),
        _engine2_progress_cb=lambda d, t: None,
        _refresh_sidebar=lambda: None,
        _log=lambda m: None,
    )
    app_mod.App._update_engine2_async(stub)

    assert calls["tag"] == "firefox-16"
    assert stub._engine2_busy is False
    assert stub._engine2_status == ""


def test_check_engine2_incompatible_says_update_persona(monkeypatch):
    import src.ui.app as app_mod

    monkeypatch.setattr(ff, "fetch_latest", lambda timeout=20: ("firefox-16", False))
    monkeypatch.setattr(ff, "current_version", lambda: "firefox-15")
    monkeypatch.setattr(app_mod.threading, "Thread", InlineThread)

    logs = []
    stub = SimpleNamespace(
        _engine2_checking=False,
        _engine2_latest="",
        _engine2_compatible=True,
        _engine2_status="",
        _engine2_update_available=lambda: False,
        _refresh_engine_text=lambda *a: None,
        _log=logs.append,
    )
    app_mod.App._check_engine2_async(stub)

    assert stub._engine2_latest == "firefox-16"
    assert stub._engine2_compatible is False
    assert stub._engine2_checking is False
    assert stub._engine2_status == "update persona for the newest engine"
    assert any("newer persona" in m for m in logs)


def test_prune_removes_old_marked_builds_keeps_new_and_pinned(monkeypatch, tmp_path):
    # After firefox-16 is installed, prune the old firefox-14 (marked, ours);
    # keep firefox-16 (the new active), firefox-15 (the package-pinned
    # BINARY_VERSION, markerless) and anything >= keep.
    _fake_cache(
        monkeypatch,
        tmp_path,
        [
            ("firefox-14", True, True),   # old, ours → pruned
            ("firefox-15", True, False),  # pinned BINARY_VERSION → kept
            ("firefox-16", True, True),   # new active → kept
        ],
        binary_version="firefox-15",
    )
    logs = []
    inv._prune_old_engine_builds(keep="firefox-16", log=logs.append)
    assert not (tmp_path / "firefox-14").exists()
    assert (tmp_path / "firefox-15").exists()
    assert (tmp_path / "firefox-16").exists()
    assert any("firefox-14" in m for m in logs)


def test_prune_leaves_unmarked_half_downloads(monkeypatch, tmp_path):
    # A crashed mid-extract build (binary, no marker) is not ours to delete —
    # leave it (a later download resumes/overwrites it).
    _fake_cache(
        monkeypatch,
        tmp_path,
        [("firefox-14", True, False), ("firefox-16", True, True)],
        binary_version="firefox-15",
    )
    inv._prune_old_engine_builds(keep="firefox-16")
    assert (tmp_path / "firefox-14").exists()
