from types import SimpleNamespace

import src.ui.app as app_mod


class InlineThread:
    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


def test_manual_engine_update_downloads_with_digest(monkeypatch):
    calls = {}

    monkeypatch.setattr(
        app_mod.engine,
        "fetch_latest_full",
        lambda timeout=20: ("v9.9", "http://example/asset", "sha256:abc123"),
    )

    def fake_download(url, timeout=600, digest=None, progress=None):
        calls["url"] = url
        calls["digest"] = digest
        return True

    monkeypatch.setattr(app_mod.engine, "download_engine", fake_download)
    written = []
    monkeypatch.setattr(app_mod.engine, "write_version", written.append)
    monkeypatch.setattr(app_mod.threading, "Thread", InlineThread)

    stub = SimpleNamespace(
        _engine_busy=False,
        _engine_latest="v9.9",
        _engine_detail=SimpleNamespace(value=""),
        _engine_progress_start=lambda: None,
        _refresh_engine_text=lambda *a: None,
        _log=lambda m: None,
        _engine_progress_cb=lambda d, t: None,
    )
    app_mod.App._update_engine_async(stub)

    assert calls["url"] == "http://example/asset"
    assert calls["digest"] == "sha256:abc123"
    assert written == ["v9.9"]
    assert stub._engine_busy is False
