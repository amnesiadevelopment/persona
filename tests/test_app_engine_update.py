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

    def fake_download(url, timeout=600, digest=None, progress=None, **kw):
        calls["url"] = url
        calls["digest"] = digest
        # PS-43: the click path must never arm the deferral — the operator
        # asked for this install, so it happens even while a profile runs.
        calls["defer"] = kw.get("defer_if_in_use", False)
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
    assert calls["defer"] is False, (
        "the operator's click must install even while a profile is running"
    )
    assert written == ["v9.9"]
    assert stub._engine_busy is False


def test_engine_update_refusal_is_not_reported_as_a_download_failure(monkeypatch):
    """PS-49 round 2 — THE UPDATE PATH, which is the one an existing operator
    actually hits.

    The digest refusal was first written in `ensure_engine`, the FIRST-INSTALL
    path. This function never calls it: it calls `download_engine` directly. So
    on a digest-less release the operator was told "Engine update failed" — the
    network blamed for a decision persona made, about a condition retrying
    cannot change, which is precisely the confusion the refuse/failed vocabulary
    exists to prevent. An operator who reads "download failed" retries forever.

    Asserts the OPERATOR-FACING WORDS, not just that nothing installed: the
    security half (failing closed) was already correct before this fix, so a
    test that only checked `write_version` was not called would have passed
    against the very defect this pins.
    """
    monkeypatch.setattr(
        app_mod.engine,
        "fetch_latest_checked",
        lambda timeout=20: (
            "148.0",
            "http://example/e.AppImage",
            "",  # upstream published no digest
            app_mod.engine_policy.OK,  # NOT a policy refusal — that path already worked
            "",
        ),
    )

    # the real refusal, raised where both entry points share it
    def refusing_download(url, timeout=600, digest=None, **kw):
        raise app_mod.engine.EngineUnverifiable(
            "Engine 148.0 not installed: no sha256 digest was published for "
            "e.AppImage, so its contents cannot be verified. persona does not "
            "install an unverified browser engine. This is not a download "
            "failure and retrying will not change it."
        )

    monkeypatch.setattr(app_mod.engine, "download_engine", refusing_download)
    written = []
    monkeypatch.setattr(app_mod.engine, "write_version", written.append)
    monkeypatch.setattr(app_mod.threading, "Thread", InlineThread)

    logged = []
    stub = SimpleNamespace(
        _engine_busy=False,
        _engine_latest="147.0",
        _engine_status="",
        _engine_deferred_tag="",
        _engine_detail=SimpleNamespace(value=""),
        _engine_progress_start=lambda: None,
        _refresh_engine_text=lambda *a: None,
        _refresh_sidebar=lambda *a: None,
        _log=logged.append,
        _engine_progress_cb=lambda d, t: None,
    )
    app_mod.App._update_engine_async(stub)

    assert written == [], "an unverifiable engine must not be recorded as installed"
    blob = " ".join(logged)
    assert "no sha256 digest" in blob, "the refusal must reach the operator"
    assert "e.AppImage" in blob, "and must name what could not be verified"
    # THE REGRESSION THIS PINS: the old code fell through to the generic else.
    assert "Engine update failed" not in blob, (
        "a refusal must not be reported as a transfer failure — an operator "
        "told the download failed retries forever"
    )
    assert stub._engine_busy is False, "the busy flag must not wedge on a refusal"


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


def test_app_construction_wires_the_chromium_engine_in_use_guard(monkeypatch):
    """PS-43: the same argument as the prune guard above, for the more
    dangerous of the two moments.

    Pruning must not DELETE a build a session runs from; an unattended install
    must not REPLACE one — and Chromium keeps a single un-versioned tree, so
    its install path overwrites the very binary a live profile is executing.
    The updater sits below the launcher and cannot import it, so the oracle is
    injected, and an un-wired app is INVISIBLE from the install path itself.

    It must be wired to the real launcher, not merely non-None: a provider that
    always answered "idle" would defer nothing and read as correctly wired.
    """
    from src.core.container import Container
    from src.services.engine import updater as chromium

    monkeypatch.setattr(chromium, "_in_use_provider", None)

    app = app_mod.App(Container())

    assert chromium._in_use_provider is not None, (
        "App construction must wire the Chromium engine in-use guard"
    )

    running: list[str] = []
    monkeypatch.setattr(app.bl, "running_profile_names", lambda: running)
    assert chromium._engine_in_use() is False
    running.append("some-profile")
    assert chromium._engine_in_use() is True, (
        "the wired provider must report a running profile from the launcher"
    )
