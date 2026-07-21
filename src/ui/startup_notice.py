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
    onboarding_done: bool, last_version: str, current_version: str
) -> Notice:
    """What the just-started app should surface once.

    - Not onboarded yet → ONBOARDING (a real first run, or an intro the user
      never finished). Takes priority over any changelog.
    - Onboarded, and this build is newer than the last one we recorded (or we
      never recorded one — an install from before version tracking) → CHANGELOG:
      the user just updated, show what changed, NOT the full welcome again (#215;
      the after-update re-onboarding was #214).
    - Otherwise (same version, or a downgrade) → NONE.
    """
    if not onboarding_done:
        return Notice.ONBOARDING
    if not last_version or is_newer(current_version, last_version):
        return Notice.CHANGELOG
    return Notice.NONE
