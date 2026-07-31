"""Decide what to show on startup: full onboarding, a what's-new changelog, or
nothing — from the onboarding flag and the last-seen vs current app version.

Pure logic, no flet, so the branching is unit-tested in isolation (#214/#215).
"""
import enum

from ..services.app_update.updater import is_newer


class Notice(enum.Enum):
    NONE = "none"
    ONBOARDING = "onboarding"
    CHANGELOG = "changelog"


def decide_startup_notice(
    onboarding_done: bool,
    last_version: str,
    current_version: str,
    has_profiles: bool = False,
) -> Notice:
    """What the just-started app should surface once.

    - A version was recorded before → the app already completed a full prior
      session (that record is written at the end of startup, after onboarding),
      so this is NEVER a first run — show the CHANGELOG if the build moved
      forward, else NONE. A recorded version overrides a False onboarding flag:
      the flag can read False from a transient settings-read failure right after
      a Windows update, and re-running the whole welcome on every update was the
      bug (#214/#226). Proof-of-prior-run beats a flag that can race.
    - The user already has PROFILES → also proof of a prior run, and a stronger
      one than settings.json (their data can't race the way the flag can). A
      self-update relaunch that momentarily read settings.json empty popped the
      full welcome on top of an existing setup (Mars, 2026-07-31); never onboard
      when there's real data — treat it as an update (CHANGELOG if the build
      moved, else NONE).
    - No version recorded, no profiles, AND not onboarded → a genuine first
      install → ONBOARDING.
    - No version recorded but onboarded (an install from before version
      tracking) → CHANGELOG once.
    """
    if last_version or has_profiles:
        return Notice.CHANGELOG if is_newer(current_version, last_version) else Notice.NONE
    if not onboarding_done:
        return Notice.ONBOARDING
    return Notice.CHANGELOG
