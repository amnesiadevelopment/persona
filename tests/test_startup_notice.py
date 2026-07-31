"""What to show at startup: full onboarding (first install), a what's-new
changelog (just updated), or nothing (same version as last time).
"""
from src.ui.startup_notice import Notice, decide_startup_notice


def test_first_install_shows_onboarding():
    # Never onboarded, no version ever recorded → a genuine first run.
    assert decide_startup_notice(
        onboarding_done=False, last_version="", current_version="2.5.2"
    ) == Notice.ONBOARDING


def test_updated_shows_changelog():
    # Onboarded already, and the recorded version is older than now → the user
    # just auto-updated: show what changed, not the full welcome (#215).
    assert decide_startup_notice(
        onboarding_done=True, last_version="2.5.1", current_version="2.5.2"
    ) == Notice.CHANGELOG


def test_same_version_shows_nothing():
    assert decide_startup_notice(
        onboarding_done=True, last_version="2.5.2", current_version="2.5.2"
    ) == Notice.NONE


def test_onboarded_but_no_version_recorded_is_an_update_not_onboarding():
    # An existing install from BEFORE version tracking existed: it's onboarded
    # but has no last_version. That's an upgrade into the changelog era, not a
    # first run — show the changelog once, never re-onboard (#214/#215).
    assert decide_startup_notice(
        onboarding_done=True, last_version="", current_version="2.5.2"
    ) == Notice.CHANGELOG


def test_not_onboarded_but_version_recorded_is_an_update_not_onboarding():
    # A recorded version proves a full prior session ran (the record is written
    # at the end of startup, after onboarding). If onboarding_done reads False
    # here it's a transient settings-read failure after an update, NOT a genuine
    # unfinished onboarding — re-running the welcome on every update was the bug
    # (#226). Proof-of-prior-run overrides the racy flag: treat as an update.
    assert decide_startup_notice(
        onboarding_done=False, last_version="2.5.1", current_version="2.5.2"
    ) == Notice.CHANGELOG


def test_not_onboarded_version_recorded_same_version_shows_nothing():
    # Same as above but the recorded version equals current (a plain restart, no
    # update): proof-of-prior-run still overrides the racy flag → nothing, and
    # definitely not onboarding.
    assert decide_startup_notice(
        onboarding_done=False, last_version="2.5.2", current_version="2.5.2"
    ) == Notice.NONE


def test_downgrade_or_equal_recorded_newer_shows_nothing():
    # Recorded version >= current (a downgrade, or a dev running an older build):
    # nothing new to announce.
    assert decide_startup_notice(
        onboarding_done=True, last_version="2.6.0", current_version="2.5.2"
    ) == Notice.NONE


def test_existing_profiles_never_re_onboard_despite_lost_settings():
    # The strongest proof of a prior run is the user's own data. If settings.json
    # reads empty (both flags racy after a self-update relaunch) but the user
    # already has profiles, this is NOT a first install — it's an update. Never
    # pop the full welcome over an existing setup (the self-update-relaunch bug
    # Mars hit: onboarding shown on top of 3 loaded profiles).
    assert decide_startup_notice(
        onboarding_done=False, last_version="", current_version="2.8.1",
        has_profiles=True,
    ) == Notice.CHANGELOG


def test_no_profiles_and_never_onboarded_is_still_first_run():
    # A genuine first install: no data, no flag, no version → the real onboarding.
    assert decide_startup_notice(
        onboarding_done=False, last_version="", current_version="2.8.1",
        has_profiles=False,
    ) == Notice.ONBOARDING


def test_has_profiles_defaults_false_keeps_old_behaviour():
    # The parameter is optional so existing callers/tests are unaffected.
    assert decide_startup_notice(
        onboarding_done=False, last_version="", current_version="2.8.1"
    ) == Notice.ONBOARDING
