"""PS-43: the Chromium engine fetches an acceptable build unattended.

Before this, every check path ended at _record_engine_check (which only records
a verdict) and the fetch routine had exactly ONE caller: the click handler. An
operator who never read the engine row kept an old engine forever — silently.

These tests pin the four promises that make the unattended trigger correct:
someone who never clicks ends up current; a build persona refused is still
skipped; a click and a background fetch cannot both run; and — the load-bearing
one — an unattended install never lands on the tree a running profile is
executing from.
"""

from types import SimpleNamespace

import pytest

import src.ui.app as app_mod


class InlineThread:
    """Run the worker on the calling thread so the test is deterministic."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


def _stub(**over):
    """An App-shaped stub whose _engine_update_available and
    _engine_tree_in_use are the REAL methods — the point of most of these
    tests is the decision those two make, so stubbing them out would test the
    test."""
    stub = SimpleNamespace(
        _engine_busy=False,
        _engine_checking=False,
        _engine_latest="",
        _engine_status="",
        # Tracks the build whose deferral has already been announced, so the
        # hourly retry doesn't repeat the same line forever.
        _engine_deferred_tag="",
        logs=[],
        updated=[],
        bl=SimpleNamespace(running_profile_names=lambda: set()),
    )
    stub._log = stub.logs.append
    # `unattended` is recorded separately from the tag: that flag is what arms
    # the in-lock deferral inside download_engine, so a trigger that dropped it
    # would install over a live session while every decision test still passed.
    def _update(unattended=False):
        stub.updated.append(stub._engine_latest)
        stub.unattended_flags.append(unattended)

    stub.unattended_flags = []
    stub._update_engine_async = _update
    stub._engine_update_available = lambda: app_mod.App._engine_update_available(stub)
    stub._engine_tree_in_use = lambda: app_mod.App._engine_tree_in_use(stub)
    for k, v in over.items():
        setattr(stub, k, v)
    return stub


@pytest.fixture
def installed_v100(monkeypatch):
    """An installed, up-and-running engine at v100 that nothing refuses."""
    monkeypatch.setattr(app_mod.engine, "is_installed", lambda: True)
    monkeypatch.setattr(app_mod.engine, "current_version", lambda: "100")
    monkeypatch.setattr(app_mod.engine_policy, "is_installable", lambda tag: True)


def test_an_operator_who_never_clicks_gets_the_newer_build(installed_v100):
    """The headline promise: no gesture anywhere, and the fetch still starts."""
    stub = _stub(_engine_latest="101")

    app_mod.App._auto_update_engine(stub)

    assert stub.updated == ["101"], (
        "an acceptable newer build must be fetched without being clicked"
    )


def test_an_up_to_date_engine_is_left_alone(installed_v100):
    stub = _stub(_engine_latest="100")

    app_mod.App._auto_update_engine(stub)

    assert stub.updated == []


def test_a_refused_build_is_never_fetched_unattended(monkeypatch):
    """Acceptability is decided upstream and consumed here unchanged: the
    unattended caller must not become a way to smuggle in a build the policy
    layer refuses (that is the whole reason the gate could be removed)."""
    monkeypatch.setattr(app_mod.engine, "is_installed", lambda: True)
    monkeypatch.setattr(app_mod.engine, "current_version", lambda: "100")
    monkeypatch.setattr(app_mod.engine_policy, "is_installable", lambda tag: False)

    stub = _stub(_engine_latest="999")

    app_mod.App._auto_update_engine(stub)

    assert stub.updated == [], "a build persona refused must not be auto-fetched"


def test_a_missing_engine_is_left_to_the_cold_start_path(monkeypatch):
    """Out of scope by design: an app with NO browser handles refusals
    differently on purpose, so this trigger must only ever upgrade."""
    monkeypatch.setattr(app_mod.engine, "is_installed", lambda: False)
    monkeypatch.setattr(app_mod.engine, "current_version", lambda: "")
    monkeypatch.setattr(app_mod.engine_policy, "is_installable", lambda tag: True)

    stub = _stub(_engine_latest="101")

    app_mod.App._auto_update_engine(stub)

    assert stub.updated == []


def test_a_fetch_already_in_flight_is_not_joined_by_a_second(installed_v100):
    """The busy flag is the same mechanism that stops a click and a background
    start colliding — _update_engine_async claims it synchronously."""
    stub = _stub(_engine_latest="101", _engine_busy=True)

    app_mod.App._auto_update_engine(stub)

    assert stub.updated == []


# --- the hard part: one un-versioned tree, replaced in place ---


def test_it_defers_while_a_profile_is_running(installed_v100):
    """Chromium runs ENGINE_DIR/<binary> directly and every install path
    replaces that same tree in place (os.replace) with no in-use check of its
    own. So the unattended fetch must WAIT rather than swap the binary under a
    live session."""
    stub = _stub(
        _engine_latest="101",
        bl=SimpleNamespace(running_profile_names=lambda: {"work"}),
    )

    app_mod.App._auto_update_engine(stub)

    assert stub.updated == [], (
        "must not replace the engine tree a running profile executes from"
    )
    assert any("waiting for running profiles" in m for m in stub.logs), (
        "the deferral must be visible, not a silent no-op"
    )


def test_the_deferred_build_is_fetched_once_the_profiles_close(installed_v100):
    """Deferral must be a WAIT, not a drop: the hourly check is the retry, so
    the same call with no profiles running now proceeds."""
    running = {"work"}
    stub = _stub(
        _engine_latest="101",
        bl=SimpleNamespace(running_profile_names=lambda: running),
    )

    app_mod.App._auto_update_engine(stub)
    assert stub.updated == []

    running.clear()
    app_mod.App._auto_update_engine(stub)

    assert stub.updated == ["101"], "a deferred update must land on a later check"


def test_an_unreadable_launcher_defers_rather_than_guessing_idle(installed_v100):
    """Fails CLOSED, unlike the prune guard's fail-open default: the cost of a
    false 'in use' is one hour of waiting; the cost of a false 'idle' is
    swapping a binary under a running browser."""
    def boom():
        raise RuntimeError("launcher unavailable")

    stub = _stub(
        _engine_latest="101",
        bl=SimpleNamespace(running_profile_names=boom),
    )

    assert app_mod.App._engine_tree_in_use(stub) is True
    app_mod.App._auto_update_engine(stub)
    assert stub.updated == []


# --- the wiring: the trigger is only worth anything if something calls it ---


def test_the_startup_check_ends_at_the_unattended_fetch(monkeypatch, installed_v100):
    """_check_engine_async is what runs at session-ready. Nothing else in the
    check path can tell a wired trigger from an unwired one — an unwired app
    records the verdict and stalls exactly as it did before."""
    monkeypatch.setattr(app_mod.engine, "fetch_latest", lambda: ("101", "http://a"))
    monkeypatch.setattr(app_mod.threading, "Thread", InlineThread)

    stub = _stub()
    stub._record_engine_check = lambda tag: (setattr(stub, "_engine_latest", tag) or "")
    stub._refresh_engine_text = lambda *a: None
    stub._download_engine_fresh = lambda: None
    stub._auto_update_engine = lambda: app_mod.App._auto_update_engine(stub)

    app_mod.App._check_engine_async(stub)

    assert stub.updated == ["101"], (
        "the startup check must trigger the unattended fetch"
    )


def test_the_hourly_poll_retries_the_unattended_fetch(monkeypatch, installed_v100):
    """The hourly tick is both discovery AND the retry that resolves a
    deferral, so it must reach the trigger too."""
    monkeypatch.setattr(app_mod.engine, "fetch_latest", lambda: ("101", "http://a"))

    calls = []
    stub = _stub()
    stub._record_engine_check = lambda tag: (setattr(stub, "_engine_latest", tag) or "")
    stub._refresh_sidebar = lambda: None
    stub._auto_update_engine = lambda: calls.append(stub._engine_latest)

    # Run the loop body exactly once: sleep raises to break out of `while True`
    # after the first pass, and the loop's own `except` must not swallow it.
    class Stop(BaseException):
        pass

    slept = []

    def fake_sleep(n):
        slept.append(n)
        if len(slept) > 1:
            raise Stop()

    monkeypatch.setattr(app_mod.threading, "Thread", InlineThread)
    import time as _time

    monkeypatch.setattr(_time, "sleep", fake_sleep)

    with pytest.raises(Stop):
        app_mod.App._check_engines_periodic(stub)

    assert calls == ["101"], "the hourly poll must retry the unattended fetch"


def test_the_unattended_trigger_arms_the_install_time_guard(installed_v100):
    """The decision-time check above is only an early exit — it answers minutes
    before the bytes are ready, and a profile can launch inside that window.
    What actually protects a live session is the re-check inside
    download_engine, and `unattended=True` is the ONLY thing that arms it.

    Worth a test of its own because dropping that flag is silent: every other
    test here stubs _update_engine_async, so an unarmed trigger would keep them
    all green while installing straight over a running profile."""
    stub = _stub(_engine_latest="101")

    app_mod.App._auto_update_engine(stub)

    assert stub.updated == ["101"]
    assert stub.unattended_flags == [True], (
        "the background fetch must arm the install-time in-use guard"
    )


def test_the_hourly_retry_does_not_repeat_the_deferral_every_hour(installed_v100):
    """The poll retries hourly, so an operator who keeps a profile open all day
    would otherwise get the identical 'waiting for running profiles' line eight
    times over. Announce the deferral once per build, not once per tick."""
    stub = _stub(
        _engine_latest="101",
        bl=SimpleNamespace(running_profile_names=lambda: {"work"}),
    )

    for _ in range(5):
        app_mod.App._auto_update_engine(stub)

    waits = [m for m in stub.logs if "waiting for running profiles" in m]
    assert len(waits) == 1, f"expected one deferral note, got {len(waits)}"

    # ...but a NEWER build is a new fact, and must speak up again.
    stub._engine_latest = "102"
    app_mod.App._auto_update_engine(stub)

    waits = [m for m in stub.logs if "waiting for running profiles" in m]
    assert len(waits) == 2, "a newer build's deferral must not be swallowed"
