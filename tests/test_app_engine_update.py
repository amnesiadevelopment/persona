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


# --- PS-49 round 3: a refusal has to STICK, not just be computed once ---
#
# Round 2 made the refusal reach the operator. It did not make it stay: the
# state the refusal left behind (version.txt unwritten, so current_version old
# while _engine_latest is new) still reads as "an update is available" to every
# consumer of _engine_update_available. These pin the four consequences.


def _refused_stub(**over):
    """An App-shaped stub sitting exactly where a refusal leaves one: the
    refusal has been recorded, and version.txt was never written."""
    stub = SimpleNamespace(
        _engine_busy=False,
        _engine_checking=False,
        _engine_latest="148.0",
        _engine_status="engine could not be verified",
        _engine_unverifiable_tag="148.0",
        # The refusal's own words, as _unverifiable_message produced them — the
        # click path REPLAYS this rather than paraphrasing, so the stub carries
        # the real sentence instead of a stand-in.
        _engine_unverifiable_msg=(
            "Engine 148.0 not installed: no sha256 digest was published for "
            "e.AppImage, so its contents cannot be verified. persona does not "
            "install an unverified browser engine. This is not a download "
            "failure and retrying will not change it."
        ),
        _engine_deferred_tag="",
        logs=[],
        bl=SimpleNamespace(running_profile_names=lambda: set()),
    )
    stub._log = stub.logs.append
    # The REAL predicate — it is the thing under test in every one of these.
    stub._engine_update_available = lambda: app_mod.App._engine_update_available(stub)
    stub._engine_tree_in_use = lambda: app_mod.App._engine_tree_in_use(stub)
    for k, v in over.items():
        setattr(stub, k, v)
    return stub


def test_the_row_shows_the_refusal_instead_of_offering_the_refused_build(monkeypatch):
    """The operator-facing half. _refresh_engine_text tests
    _engine_update_available BEFORE _engine_status, so while the predicate
    stayed True the refusal message was computed and then painted over with
    'update → 148.0' — the row went on advertising the build persona declined.

    Asserts what the row RENDERS, not merely that _engine_status was assigned:
    the assignment was already there and was inert, so an assertion on it
    passes against the defect.
    """
    monkeypatch.setattr(app_mod.engine, "current_version", lambda: "147.0")
    stub = _refused_stub()

    rendered = (
        f"update → {stub._engine_latest}"
        if stub._engine_update_available()
        else stub._engine_status
    )

    assert rendered == "engine could not be verified", (
        "the row must show the refusal, not offer the build that was refused"
    )


def test_the_hourly_tick_does_not_refetch_the_refused_build_forever(monkeypatch):
    """The half that actually costs something. _auto_update_engine gates ONLY
    on _engine_update_available, so every hourly tick re-ran the whole fetch and
    re-logged the four-sentence refusal — forever, because unlike a deferral
    (which resolves when profiles close) this resolves only if UPSTREAM
    publishes a digest.
    """
    monkeypatch.setattr(app_mod.engine, "is_installed", lambda: True)
    monkeypatch.setattr(app_mod.engine, "current_version", lambda: "147.0")

    fired = []
    stub = _refused_stub()
    stub._update_engine_async = lambda unattended=False: fired.append(unattended)

    for _ in range(5):
        app_mod.App._auto_update_engine(stub)

    assert fired == [], "a refused build must not be re-downloaded every hour"
    assert stub.logs == [], "nor re-announced on every tick"


def test_a_newer_build_supersedes_the_refusal_and_is_offered(monkeypatch):
    """The suppression is keyed by TAG, not a latch: upstream may well publish a
    digest for the next build, so a newer tag is a new fact and must be offered
    normally. A bool here would strand the operator on the last refused build
    forever."""
    monkeypatch.setattr(app_mod.engine, "current_version", lambda: "147.0")
    monkeypatch.setattr(app_mod.engine_policy, "is_installable", lambda tag: True)

    stub = _refused_stub()
    assert stub._engine_update_available() is False

    stub._engine_latest = "149.0"

    assert stub._engine_update_available() is True, (
        "a newer build than the refused one must not stay suppressed"
    )


def test_the_next_check_does_not_erase_the_refusal_from_the_row(monkeypatch):
    """_record_engine_check runs on every check path. A digest-less build is
    policy-OK ('ok', ''), so once the offer is suppressed this method falls
    past the verdict branch to its trailing `_engine_status = ''` — which would
    wipe the refusal off the row one tick after the operator was told, leaving
    the row reading as an ordinary up-to-date engine."""
    monkeypatch.setattr(app_mod.engine, "current_version", lambda: "147.0")
    stub = _refused_stub()

    line = app_mod.App._record_engine_check(stub, "148.0")

    assert stub._engine_status == "engine could not be verified", (
        "the refusal must survive the next version check"
    )
    assert line == "", "and must not re-log the refusal on every automatic check"


def test_clicking_a_refused_engine_does_not_claim_it_is_up_to_date(monkeypatch):
    """Suppressing the offer routes an explicit click into the CHECK branch,
    whose else-arm says 'Chromium engine is up to date (147.0)'. That is a plain
    falsehood for a build that is newer and was refused — and it is a
    consequence of the suppression, so this gate has to answer for it. An
    explicit gesture gets an explicit answer."""
    monkeypatch.setattr(app_mod.engine, "current_version", lambda: "147.0")
    monkeypatch.setattr(app_mod.engine, "fetch_latest", lambda: ("148.0", "http://a"))
    monkeypatch.setattr(app_mod.threading, "Thread", InlineThread)

    stub = _refused_stub()
    stub._refresh_engine_text = lambda *a: None
    stub._update_engine_async = lambda **kw: stub.logs.append("DOWNLOADED")
    # The REAL recorder: it is what decides the click falls through to the
    # else-arm at all, so stubbing it would test the test.
    stub._record_engine_check = lambda tag: app_mod.App._record_engine_check(stub, tag)

    app_mod.App._on_engine_click(stub)

    blob = " ".join(stub.logs)
    assert "up to date" not in blob, (
        "a refused build is newer than what is installed — saying 'up to date' "
        "to an operator who just asked is a lie"
    )
    assert "no sha256" in blob, "the click must be answered with the real reason"
    assert "DOWNLOADED" not in blob, "and must not start the refused download"
