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


def test_app_construction_wires_the_engine_prune_in_use_guard(monkeypatch):
    """PS-14: the guard is only worth anything if production actually wires it.

    Engine pruning defers to running profiles via an injected provider
    (engine_install sits below the launcher and cannot import it). Nothing in
    the prune path can tell a correctly-wired app from an un-wired one — an
    un-wired app prunes exactly as it did before the fix, silently. So pin the
    wiring itself: after ordinary construction the provider must be set, and it
    must answer from the launcher's running_profile_names().
    """
    import src.services.browser.engine_install as eng
    from src.core.container import Container

    # setattr (not just a save/restore) so pytest reverts the module global
    # afterwards — App() writes it, and a leak would change how every later
    # test in the session prunes.
    monkeypatch.setattr(eng, "_in_use_provider", None)

    app = app_mod.App(Container())

    assert eng._in_use_provider is not None, (
        "App construction must wire the engine-prune in-use guard"
    )

    # And it must be the REAL oracle, not a stub that always says "idle":
    # drive the launcher both ways and check the provider tracks it.
    running: list[str] = []
    monkeypatch.setattr(app.bl, "running_profile_names", lambda: running)
    assert eng._engine_in_use() is False
    running.append("some-profile")
    assert eng._engine_in_use() is True, (
        "the wired provider must report a running profile from the launcher"
    )
