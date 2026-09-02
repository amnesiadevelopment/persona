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
