"""The download progress total must come from the GitHub API asset size, not a
HEAD request, so the progress bar has a real total over Tor (where HEAD is
flaky). Regression for the 'stuck/indeterminate bar, no total, no ETA' bug."""

import src.services.app_update.updater as au


def test_pick_asset_returns_url_and_size():
    assets = [
        {"name": "persona-x86_64.AppImage", "browser_download_url": "u", "size": 4096},
    ]
    assert au.pick_asset(assets) == ("u", 4096)


def test_check_for_update_propagates_size(monkeypatch):
    body = (
        '{"tag_name": "v9.9.9", "assets": '
        '[{"name": "persona-x86_64.AppImage", "browser_download_url": "u", '
        '"size": 12345}]}'
    )
    monkeypatch.setattr(au, "_curl_get", lambda *a, **k: body)
    monkeypatch.setattr(au, "update_available", lambda tag: True)
    assert au.check_for_update() == ("v9.9.9", "u", 12345)


def test_download_uses_api_size_not_head(monkeypatch, tmp_path):
    # if download trusts the API size, it must never call remote_size (HEAD)
    staged = tmp_path / "p.part"
    monkeypatch.setattr(au, "staged_path", lambda: str(staged))

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
