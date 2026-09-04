"""A profile records WHICH ENGINE BUILD it was last launched under.

Without this, "this profile's identity has not moved" is unanswerable: a
difference between what a profile exposes now and what it exposed before is
only interpretable if you know which build produced each reading. Otherwise an
engine update and a genuine masking regression look identical.

These tests pin the three properties that make the record trustworthy rather
than merely present:

1. **It is a PAIR, recorded verbatim.** The engines report different shapes
   (``firefox-NN`` vs a dotted Chromium version). Normalising them would lose
   which engine produced the string, and a build that cannot say which engine
   it came from is not provenance.
2. **Absence means "not known", never a guess.** No backfill, no default
   constant, no substituting the currently-installed build. A wrong stamp is
   worse than no stamp, because the comparison it enables returns a confident
   false answer.
3. **Recording never fails a launch.** Writing it is a launch-path change, so
   the failure mode to avoid is a launch that dies because the record could not
   be written.

Red-first: every assertion below fails on main today (neither field exists, and
no resolver or launch-path consumer existed).
"""
import json
import subprocess

import pytest

from src.models.profile import Profile
from src.services.browser.launcher import BrowserLauncher
from src.services.profile.manager import ProfileManager


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    pf = tmp_path / "profiles.json"
    dd = tmp_path / "data"
    monkeypatch.setenv("PERSONA_PROFILES_FILE", str(pf))
    monkeypatch.setenv("PERSONA_DATA_DIR", str(dd))
    import src.core.config as cfg
    import src.services.profile.manager as mod

    monkeypatch.setattr(cfg, "PROFILES_FILE", str(pf))
    monkeypatch.setattr(cfg, "DATA_DIR", str(dd))
    monkeypatch.setattr(mod, "PROFILES_FILE", str(pf))
    monkeypatch.setattr(mod, "DATA_DIR", str(dd))
    return ProfileManager()


# --------------------------------------------------------------------------
# The field exists, and its absence is honest.
# --------------------------------------------------------------------------

def test_profile_defaults_to_no_recorded_launch_build():
    # None is the field's "not known". Every profile that predates the field
    # reads this, and so does every profile that has not launched since.
    p = Profile(name="a")
    assert p.last_launch_engine is None
    assert p.last_launch_build is None


def test_a_profile_that_never_launched_is_not_given_a_guessed_build(mgr):
    # THE NO-BACKFILL PROPERTY, at the creation door. Creating a profile does
    # not launch it, so there is no build to record — and the installed build
    # is emphatically NOT the answer, because this profile never ran under it.
    mgr.add_profile("fresh", "", "windows")
    assert mgr.profiles["fresh"].last_launch_engine is None
    assert mgr.profiles["fresh"].last_launch_build is None


def test_an_existing_profile_record_without_the_field_loads_as_not_known(
    mgr, tmp_path
):
    # A profiles.json written before this field existed. It must load, and it
    # must read as "not known" — NOT as the currently-installed build, which
    # would be inventing provenance for a launch nobody observed.
    (tmp_path / "profiles.json").write_text(
        json.dumps({"old": {"name": "old", "engine": "chromium"}}),
        encoding="utf-8",
    )
    # Constructing a manager loads from disk (_load_profiles runs in __init__).
    loaded = ProfileManager()
    assert loaded.profiles["old"].last_launch_engine is None
    assert loaded.profiles["old"].last_launch_build is None


# --------------------------------------------------------------------------
# Persistence: it survives a save/load cycle.
# --------------------------------------------------------------------------

def test_to_dict_roundtrips_the_pair():
    p = Profile(
        name="a", last_launch_engine="firefox", last_launch_build="firefox-18"
    )
    d = p.to_dict()
    assert d["last_launch_engine"] == "firefox"
    assert d["last_launch_build"] == "firefox-18"
    assert Profile(**d).last_launch_build == "firefox-18"


def test_the_recorded_build_survives_a_restart(mgr, tmp_path):
    # THE REGRESSION THIS TEST EXISTS FOR. ProfileManager's load path used to
    # be a HAND-ENUMERATED allow-list, so a field missing from it was written
    # by to_dict() and then silently DROPPED on the next load — the record
    # looked correct in memory and evaporated on restart, which is exactly the
    # bug cookie_import_status hit. PS-269 replaced that list with a
    # derivation from dataclasses.fields(Profile), so the omission this test
    # was written to catch is no longer expressible. The RESTART assertion
    # still is: an in-memory-only assertion cannot see the persistence
    # boundary at all, and the derived loader still carries two explicit
    # migration post-steps that only a reload exercises. So reload from disk.
    mgr.add_profile("p1", "", "windows")
    mgr.set_last_launch_build("p1", "chromium", "151.0.8000.10")

    raw = json.loads((tmp_path / "profiles.json").read_text(encoding="utf-8"))
    assert raw["p1"]["last_launch_build"] == "151.0.8000.10"

    # Constructing a manager loads from disk (_load_profiles runs in __init__),
    # so this is a genuine restart, not a re-read of the in-memory dict.
    reloaded = ProfileManager()
    assert reloaded.profiles["p1"].last_launch_engine == "chromium"
    assert reloaded.profiles["p1"].last_launch_build == "151.0.8000.10"


def test_setting_the_build_reports_false_for_an_unknown_profile(mgr):
    # A profile deleted mid-launch. False, not an exception — the caller treats
    # it as a non-event, because recording must never fail a launch.
    assert mgr.set_last_launch_build("nope", "chromium", "151.0.1.2") is False


def test_a_build_that_could_not_be_read_is_recorded_as_not_known(mgr):
    # The resolver's None reaches the record as None rather than being replaced
    # by a fallback. The ENGINE is still worth recording without it.
    mgr.add_profile("p1", "", "windows")
    assert mgr.set_last_launch_build("p1", "chromium", None) is True
    assert mgr.profiles["p1"].last_launch_engine == "chromium"
    assert mgr.profiles["p1"].last_launch_build is None


# --------------------------------------------------------------------------
# The resolver: each engine's OWN shape, recorded verbatim.
# --------------------------------------------------------------------------

def test_firefox_build_is_recorded_in_firefoxs_own_shape(monkeypatch):
    # The two engines' identifiers are NOT normalised into one format: the tag
    # is stored exactly as firefox reports it, suffix and all.
    import src.services.engine.firefox as ff
    from src.services.browser import launch_provenance as lp

    monkeypatch.setattr(
        ff, "current_version", lambda: "firefox-18_151.0_20260724001829"
    )
    assert lp.engine_build_for("firefox") == "firefox-18_151.0_20260724001829"


def test_chromium_build_is_recorded_in_chromiums_own_shape(monkeypatch):
    import src.services.engine.updater as up
    from src.services.browser import launch_provenance as lp

    monkeypatch.setattr(up, "current_version", lambda: "151.0.8000.10")
    assert lp.engine_build_for("chromium") == "151.0.8000.10"


def test_the_two_engines_are_read_from_their_own_reporters(monkeypatch):
    # THE NON-NORMALISATION PROPERTY, stated as the difference it protects. The
    # same launch machinery asked about each engine must return that engine's
    # OWN identifier — not one shape for both, and never the other engine's
    # value. `firefox-18` and `151.0.8000.10` are not points on one scale, so a
    # single normalised format would have to discard one of them.
    import src.services.engine.firefox as ff
    import src.services.engine.updater as up
    from src.services.browser import launch_provenance as lp

    monkeypatch.setattr(ff, "current_version", lambda: "firefox-18")
    monkeypatch.setattr(up, "current_version", lambda: "151.0.8000.10")

    assert lp.engine_build_for("firefox") == "firefox-18"
    assert lp.engine_build_for("chromium") == "151.0.8000.10"


def test_an_uninstalled_engine_yields_not_known_rather_than_an_empty_build(
    monkeypatch
):
    # Both reporters answer '' for "not installed". An empty string is the
    # ABSENCE of a build, not a build named "" — it must not be stored as if a
    # real identifier had been read.
    import src.services.engine.updater as up
    from src.services.browser import launch_provenance as lp

    monkeypatch.setattr(up, "current_version", lambda: "")
    assert lp.engine_build_for("chromium") is None


def test_an_unreadable_build_yields_not_known_rather_than_a_guess(monkeypatch):
    # THE CORE HONESTY PROPERTY. When the build cannot be read, the answer is
    # None — never a fallback constant, never the last known value. A stamp
    # that says the wrong build makes a later comparison return a confident
    # false answer, whereas None makes it correctly decline.
    import src.services.engine.updater as up
    from src.services.browser import launch_provenance as lp

    def boom():
        raise OSError("version.txt unreadable")

    monkeypatch.setattr(up, "current_version", boom)
    assert lp.engine_build_for("chromium") is None


def test_the_resolver_records_the_engine_that_actually_launched(monkeypatch):
    # A mobile profile STORED as firefox actually launches chromium
    # (process.effective_engine). Attributing a chromium build to "firefox"
    # would be the one way this pair can actively mislead rather than merely be
    # absent — so the engine recorded is the EFFECTIVE one, and the stored
    # `engine` field is not the answer.
    import src.services.engine.updater as up
    from src.services.browser import launch_provenance as lp
    from src.services.browser.process import effective_engine

    monkeypatch.setattr(up, "current_version", lambda: "151.0.8000.10")
    mobile = Profile(
        name="m", engine="firefox", os_type="android", device_type="mobile"
    )
    # Premise: this profile really does resolve to a different engine than the
    # one stored on it. If that stops being true the test below proves nothing.
    assert mobile.engine == "firefox"
    assert effective_engine(mobile) == "chromium"

    engine, build = lp.resolve(mobile)
    assert engine == "chromium"
    assert build == "151.0.8000.10"


def test_the_resolver_reads_the_updater_rather_than_keeping_its_own_record(
    monkeypatch
):
    # NOT A SECOND SOURCE OF TRUTH. The updater owns "what is installed"; this
    # module must READ it, so a change there is reflected here with nothing to
    # keep in sync. Moving the updater's answer moves the resolver's answer.
    import src.services.engine.updater as up
    from src.services.browser import launch_provenance as lp

    monkeypatch.setattr(up, "current_version", lambda: "151.0.8000.10")
    assert lp.engine_build_for("chromium") == "151.0.8000.10"
    monkeypatch.setattr(up, "current_version", lambda: "152.0.9000.1")
    assert lp.engine_build_for("chromium") == "152.0.9000.1"


# --------------------------------------------------------------------------
# The launch path: it records, and it NEVER fails the launch.
# --------------------------------------------------------------------------

class _FakeProc:
    """A spawned process that stays 'running' and produces no output."""

    def __init__(self):
        self.stdout = None
        self.pid = 4242
        self._terminated = False

    def poll(self):
        return None

    def terminate(self):
        self._terminated = True

    def kill(self):
        self._terminated = True

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def launcher(monkeypatch):
    """A BrowserLauncher whose spawn is faked, so a launch can be exercised
    without a real engine binary."""
    import src.services.browser.launcher as mod

    monkeypatch.setattr(mod, "spawn_browser", lambda profile: _FakeProc())
    monkeypatch.setattr(mod, "terminate", lambda *a, **k: None)
    monkeypatch.setattr(mod, "wait_for_exit", lambda *a, **k: None)
    bl = BrowserLauncher()
    yield bl
    bl._active_sessions.clear()


def test_launching_records_the_build_on_the_profile(launcher):
    recorded = []
    launcher.set_launch_record_hook(
        lambda p: recorded.append(("chromium", "151.0.8000.10", p.name))
    )
    launcher.start_thread(Profile(name="p1"), log_callback=lambda _m: None)
    assert recorded == [("chromium", "151.0.8000.10", "p1")]


def test_a_launch_still_opens_when_the_record_cannot_be_written(launcher):
    # THE CONSTRAINT THIS FEATURE MUST NOT VIOLATE. Writing the stamp is a
    # launch-path change, so the failure mode to avoid is a launch that dies
    # because the record could not be written. A profile that cannot record its
    # build must still OPEN.
    #
    # This asserts the browser is actually RUNNING afterwards, not merely that
    # start_thread returned without raising: the hook runs inside the outer
    # "Error starting process" handler, which swallows the exception and calls
    # on_stop — so an unguarded raise would return quietly while reporting a
    # successful launch as a failure and leaving the session untracked.
    def explode(_profile):
        raise RuntimeError("disk full")

    launcher.set_launch_record_hook(explode)

    stopped = []
    launcher.start_thread(
        Profile(name="p1"),
        log_callback=lambda _m: None,
        on_stop=lambda: stopped.append(True),
    )

    assert launcher.is_running("p1") is True, (
        "the browser must still be running after a failed provenance write"
    )
    assert stopped == [], (
        "a failed provenance write must not report the launch as stopped"
    )


def test_a_launcher_with_no_hook_launches_exactly_as_before(launcher):
    # The hook is optional by construction, so nothing that builds a launcher
    # without wiring one (tests, headless) changes behaviour.
    launcher.start_thread(Profile(name="p1"), log_callback=lambda _m: None)
    assert launcher.is_running("p1") is True


def test_the_record_hook_receives_the_profile_being_launched(launcher):
    # The hook resolves the engine from the profile, so it must be handed the
    # profile that was actually launched rather than only its name.
    seen = []
    launcher.set_launch_record_hook(lambda p: seen.append(p))
    prof = Profile(name="p1", engine="firefox", os_type="windows")
    launcher.start_thread(prof, log_callback=lambda _m: None)
    assert len(seen) == 1
    assert seen[0] is prof


# --------------------------------------------------------------------------
# All three launch lanes are covered, not just the UI one.
# --------------------------------------------------------------------------

def test_every_launch_lane_records_the_build_because_the_hook_is_wired_centrally():
    # There are THREE launch lanes (UI, REST, MCP) and they all resolve the
    # launcher from the container. Wiring the hook there is what makes an
    # absent stamp mean "never launched" rather than "launched through a lane
    # nobody wired" — an ambiguity that would make the field untrustworthy.
    #
    # Asserted on the container's own wiring rather than by driving three
    # lanes: the container is the single place that decides, and each lane's
    # use of it is already covered by that lane's own tests.
    from src.core.container import Container

    bl = Container().browser_launcher
    assert bl._launch_record_hook is not None, (
        "the container must wire the launch-record hook, or API/MCP launches "
        "would silently record nothing"
    )


# --------------------------------------------------------------------------
# Untrusted import.
# --------------------------------------------------------------------------

def test_an_imported_archive_cannot_smuggle_a_non_string_build(tmp_path):
    # Profile sharing is a feature and the archive is UNTRUSTED input, which
    # round-trips fields automatically. A malformed value must be dropped to
    # None (the honest "not known") rather than stored verbatim to blow up the
    # first surface that formats it.
    import zipfile

    from src.services.profile.transfer import import_from_zip

    zip_path = tmp_path / "p.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "profile.json",
            json.dumps({
                "name": "imported",
                "last_launch_engine": {"not": "a string"},
                "last_launch_build": 12345,
            }),
        )

    ok, profile = import_from_zip(str(zip_path), str(tmp_path / "data"))
    assert ok is True, profile
    assert isinstance(profile, Profile)
    assert profile.last_launch_engine is None
    assert profile.last_launch_build is None


def test_an_imported_archive_keeps_a_well_formed_build(tmp_path):
    # The exported data dir travels with the archive, so the imported profile
    # continues the SAME identity — the build that produced it is genuine
    # provenance and dropping it would discard a true fact. Only malformed
    # values are dropped.
    import zipfile

    from src.services.profile.transfer import import_from_zip

    zip_path = tmp_path / "p.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "profile.json",
            json.dumps({
                "name": "imported",
                "last_launch_engine": "firefox",
                "last_launch_build": "firefox-18",
            }),
        )

    ok, profile = import_from_zip(str(zip_path), str(tmp_path / "data"))
    assert ok is True, profile
    assert profile.last_launch_engine == "firefox"
    assert profile.last_launch_build == "firefox-18"


# --------------------------------------------------------------------------
# PS-221: the LIVE session build — a separate record from the persisted stamp
# above, because the persisted one cannot answer a liveness question.
# --------------------------------------------------------------------------

def test_a_registered_session_knows_which_build_it_is_running(launcher, monkeypatch):
    # The whole mechanism: a session that is registered has its build recorded
    # in the SAME locked block, so "running" and "what it is running" are one
    # atomic fact rather than two reads with a window between them.
    import src.services.browser.launch_provenance as lp

    monkeypatch.setattr(lp, "engine_build_for", lambda engine: "firefox-14")
    launcher.start_thread(
        Profile(name="p1", engine="firefox"), log_callback=lambda _m: None
    )

    assert launcher.running_session_builds() == {"p1": ("firefox", "firefox-14")}


def test_the_session_build_is_recorded_before_the_stamp_hook_runs(
    launcher, monkeypatch
):
    # THE WINDOW THE PERSISTED STAMP CANNOT COVER, pinned as a property rather
    # than argued in a comment.
    #
    # The record hook fires AFTER the session is registered, so at the moment
    # the hook runs the profile is ALREADY reported as running. If the prune's
    # narrowing read the persisted stamp, that window would be a live deletion
    # on the ordinary launch path — no failure required. The live map must
    # already be correct by then.
    import src.services.browser.launch_provenance as lp

    monkeypatch.setattr(lp, "engine_build_for", lambda engine: "firefox-14")

    seen_at_hook_time = {}

    def _hook(profile):
        seen_at_hook_time.update(launcher.running_session_builds())

    launcher.set_launch_record_hook(_hook)
    launcher.start_thread(
        Profile(name="p1", engine="firefox"), log_callback=lambda _m: None
    )

    assert seen_at_hook_time == {"p1": ("firefox", "firefox-14")}, (
        "the session's build must already be known by the time the persistence "
        "hook runs — otherwise there is a window in which the profile reads as "
        "running while nothing knows which build it is on"
    )


def test_a_launch_whose_build_cannot_be_read_is_running_with_an_unknown_build(
    launcher, monkeypatch
):
    # None is honest — "running, build not known" — and every consumer must
    # treat it as UNKNOWN. A guess here would be worse than nothing, because it
    # is an affirmative claim a prune acts on.
    import src.services.browser.launch_provenance as lp

    def unreadable(engine):
        raise OSError("version file unreadable")

    monkeypatch.setattr(lp, "engine_build_for", unreadable)
    launcher.start_thread(
        Profile(name="p1", engine="firefox"), log_callback=lambda _m: None
    )

    assert launcher.running_session_builds() == {"p1": ("firefox", None)}
    assert launcher.is_running("p1") is True, (
        "an unreadable build must not fail the launch"
    )


def test_an_in_flight_launch_has_no_session_build_at_all(launcher):
    # A name in _starting is reported as running (so the UI shows it busy and a
    # second launch is refused) but has NOT been registered, so there is no
    # build for it. That is UNKNOWN by construction — there is no value to read,
    # correct or otherwise — which is what the persisted stamp could not offer:
    # a profile that has launched before carries the PREVIOUS launch's build,
    # which is a positive value about the wrong launch.
    launcher._starting.add("spawning")
    assert "spawning" in launcher.running_profile_names()
    assert launcher.running_session_builds() == {"spawning": None}


def test_the_session_build_dies_with_the_session(launcher, monkeypatch):
    # Left behind, it would tell the prune to SPARE a build nothing is running
    # from — a disk leak rather than a deletion, so it fails safe, but "fails
    # safe" is not the same as correct. _forget_session_facts is the file's own
    # mechanism for exactly this and the new dict must be in it.
    import src.services.browser.launch_provenance as lp

    monkeypatch.setattr(lp, "engine_build_for", lambda engine: "firefox-14")
    launcher.start_thread(
        Profile(name="p1", engine="firefox"), log_callback=lambda _m: None
    )
    assert launcher.running_session_builds() == {"p1": ("firefox", "firefox-14")}

    launcher.stop_profile("p1")
    assert launcher.running_session_builds() == {}
    assert launcher._session_build == {}


def test_shutdown_all_clears_every_session_build(launcher, monkeypatch):
    # The bulk counterpart, which the file's own comment names as the site most
    # easily forgotten when a per-session dict is introduced.
    import src.services.browser.launch_provenance as lp

    monkeypatch.setattr(lp, "engine_build_for", lambda engine: "firefox-14")
    for name in ("p1", "p2"):
        launcher.start_thread(
            Profile(name=name, engine="firefox"), log_callback=lambda _m: None
        )
    assert len(launcher._session_build) == 2

    launcher.shutdown_all()
    assert launcher._session_build == {}


def test_the_names_and_the_builds_are_taken_in_one_lock_acquisition(launcher):
    # Asking for the names and then asking for the builds would let a session
    # start or stop between the two reads, and a consumer that spares "the
    # builds in use" would be acting on a name set that no longer matches. The
    # KEY SET must be exactly running_profile_names(), from one acquisition.
    class _Proc:
        def poll(self):
            return None

    launcher._active_sessions["running"] = _Proc()
    launcher._session_build["running"] = ("firefox", "firefox-14")
    launcher._starting.add("spawning")
    # A leftover build for a name that is NOT running must not leak into the
    # answer — the map is keyed off the live name set, not iterated directly.
    launcher._session_build["gone"] = ("firefox", "firefox-9")

    builds = launcher.running_session_builds()
    assert set(builds) == launcher.running_profile_names() == {"running", "spawning"}
    assert builds["running"] == ("firefox", "firefox-14")
    assert builds["spawning"] is None
    launcher._active_sessions.clear()


def test_a_failed_stamp_write_clears_the_build_rather_than_leaving_a_stale_one(
    monkeypatch
):
    # THE MODULE'S OWN RULE APPLIED AT THE ONE SITE THAT VIOLATED IT: "a stamp
    # that says the wrong build is worse than no stamp at all". The launcher
    # swallows a raise from this hook so the browser still opens — correct, and
    # unchanged — but leaving the PREVIOUS launch's build standing is an
    # affirmative claim about a build this session is not on. Clearing it makes
    # the record's absence honest.
    #
    # This is hygiene on the persisted record, NOT the prune's guard: the prune
    # reads the launcher's live map and is unaffected either way.
    from src.core.container import Container

    c = Container()
    calls = []

    class _PM:
        def set_last_launch_build(self, name, engine, build):
            calls.append((name, engine, build))
            if len(calls) == 1:
                raise OSError("profiles.json is unwritable")
            return True

    monkeypatch.setattr(type(c), "profile_manager", property(lambda self: _PM()))
    monkeypatch.setattr(
        "src.services.browser.launch_provenance.resolve",
        lambda profile: ("firefox", "firefox-14"),
    )

    hook = c.browser_launcher._launch_record_hook
    with pytest.raises(OSError):
        # Re-raised on purpose: the launcher's own except is what keeps the
        # browser open, and swallowing here would hide the first failure.
        hook(Profile(name="p1", engine="firefox"))

    assert calls == [
        ("p1", "firefox", "firefox-14"),
        ("p1", "firefox", None),
    ], (
        "a failed write must be followed by an explicit CLEAR, so the record "
        "reads 'not known' rather than naming the previous launch's build"
    )


def test_the_session_build_is_known_the_instant_the_session_is_registered(
    launcher, monkeypatch
):
    # THE INVARIANT THIS WHOLE GUARD RESTS ON: "registered" and "we know which
    # build it is running" must be ONE atomic fact, not two writes with a gap.
    #
    # The sibling test above probes at the record HOOK, which is the LAST thing
    # start_thread does — so it passes even if the build is recorded moments
    # earlier but still after registration. That leaves the exact defect this
    # ticket was re-opened for uncovered: any window between the session being
    # visible as running and its build being known is a window in which the
    # prune sees a running profile it cannot account for. Today that direction
    # merely loses a reclaim, but the invariant is what stops it from being
    # resolved by a STALE value instead, which is a live deletion.
    #
    # Probed at the registry write, which runs BETWEEN registration and the
    # hook and is the earliest observable point after the session goes live.
    import src.services.browser.launch_provenance as lp

    monkeypatch.setattr(lp, "engine_build_for", lambda engine: "firefox-14")

    observed = {}

    class _Spy:
        def record(self, rec):
            # The session is registered by now — is its build known too?
            observed["running"] = launcher.running_profile_names()
            observed["builds"] = launcher.running_session_builds()

        def forget(self, name):
            pass

        def forget_all(self):
            pass

    launcher._registry = _Spy()
    launcher.start_thread(
        Profile(name="p1", engine="firefox"), log_callback=lambda _m: None
    )

    assert observed["running"] == {"p1"}, (
        "precondition: the session is already reported as running here"
    )
    assert observed["builds"] == {"p1": ("firefox", "firefox-14")}, (
        "a session that is visible as RUNNING must already carry the build it "
        "is executing from — recording it later opens a window in which the "
        "prune sees a running profile it cannot account for, which is exactly "
        "where the persisted-stamp implementation resolved a STALE build and "
        "deleted the live one"
    )
