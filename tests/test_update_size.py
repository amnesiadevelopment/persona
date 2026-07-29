"""The download progress total must come from the GitHub API asset size, not a
HEAD request, so the progress bar has a real total over Tor (where HEAD is
flaky). Regression for the 'stuck/indeterminate bar, no total, no ETA' bug."""

import src.services.app_update.updater as au


def _force_linux(monkeypatch):
    monkeypatch.setattr(au._platform, "IS_WINDOWS", False)
    monkeypatch.setattr(au._platform, "IS_MACOS", False)
    monkeypatch.setattr(au._platform, "IS_LINUX", True)


def test_pick_asset_returns_url_and_size(monkeypatch):
    _force_linux(monkeypatch)
    assets = [
        {"name": "persona-x86_64.AppImage", "browser_download_url": "u", "size": 4096},
    ]
    assert au.pick_asset(assets) == ("u", 4096)


def test_check_for_update_propagates_size(monkeypatch):
    _force_linux(monkeypatch)
    # The tag comes from the rate-limit-free releases/latest redirect, the asset
    # URL is deterministic, and the size from a HEAD on it.
    monkeypatch.setattr(au, "APP_REPO", "amnesiadevelopment/persona")
    monkeypatch.setattr(au, "latest_tag", lambda timeout=30: "v9.9.9")
    monkeypatch.setattr(au, "update_available", lambda tag: True)
    monkeypatch.setattr(au, "remote_size", lambda url, timeout=30: 12345)
    tag, url, size = au.check_for_update()
    assert tag == "v9.9.9"
    assert url.endswith("/releases/download/v9.9.9/persona-x86_64.AppImage")
    assert size == 12345


def test_latest_tag_parses_redirect_location():
    # The releases/latest page 302s to /releases/tag/<tag>; the tag is read from
    # the Location header (no API, no rate limit).
    headers = (
        "HTTP/2 302\r\n"
        "location: https://github.com/amnesiadevelopment/persona/releases/tag/v2.7.2\r\n"
        "content-length: 0\r\n"
    )
    assert au._tag_from_location(headers) == "v2.7.2"


def test_latest_tag_empty_when_no_location():
    assert au._tag_from_location("HTTP/2 200\r\ncontent-type: text/html\r\n") == ""


def test_download_uses_api_size_not_head(monkeypatch, tmp_path):
    # if download trusts the API size, it must never call remote_size (HEAD)
    staged = tmp_path / "p.part"
    monkeypatch.setattr(au, "staged_path", lambda tag="": str(staged))
    monkeypatch.setattr(au, "_clear_stale_staged", lambda keep: None)

    def boom(*a, **k):
        raise AssertionError("remote_size (HEAD) must not be called when size given")

    monkeypatch.setattr(au, "remote_size", boom)

    def fake_run(cmd, **k):
        staged.write_bytes(b"x" * 100)

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(au.subprocess, "run", fake_run)
    seen = []
    out = au.download_update(
        "http://x", progress=lambda d, t: seen.append((d, t)), size=100
    )
    assert out == str(staged)
    # the total reported to the UI is the API size
    assert any(t == 100 for _, t in seen)
