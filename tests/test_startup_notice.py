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


def test_not_onboarded_but_version_recorded_still_onboards():
    # Defensive: onboarding was never completed but a version exists (a skipped
    # onboarding that didn't persist). Prefer finishing onboarding over a
    # changelog — the user hasn't seen the intro yet.
    assert decide_startup_notice(
        onboarding_done=False, last_version="2.5.1", current_version="2.5.2"
    ) == Notice.ONBOARDING


def test_downgrade_or_equal_recorded_newer_shows_nothing():
    # Recorded version >= current (a downgrade, or a dev running an older build):
    # nothing new to announce.
    assert decide_startup_notice(
        onboarding_done=True, last_version="2.6.0", current_version="2.5.2"
    ) == Notice.NONE
