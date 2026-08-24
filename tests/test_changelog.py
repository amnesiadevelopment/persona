"""audit5 #5: the what's-new changelog never showed on a shipping release —
notes_for did an exact-key lookup, so notes_for("2.9.11") returned [] because
the newest CHANGELOG key was "2.8.1", and the dialog was skipped for the entire
2.9.x line. notes_for must fall back to the nearest version <= current, and
APP_VERSION must always be a CHANGELOG key (a release-cut guard)."""
from src.ui.changelog import CHANGELOG, notes_for


def test_exact_version_returns_its_notes():
    assert notes_for("2.8.1") == CHANGELOG["2.8.1"]


def test_unknown_newer_version_falls_back_to_nearest_older():
    # A user on a version with no exact entry (e.g. a patch release) still sees
    # the newest recorded notes at or below their version, not an empty dialog.
    #
    # Derive the probe FROM CHANGELOG rather than hardcoding one: the sentinel
    # has to be newer than every recorded entry for this assertion to mean
    # anything, and a literal silently stopped being that the first time a
    # major bump landed ("2.99.0" was above every 2.9.x entry, but not above
    # 3.0.0 — so the probe fell to 2.9.18 while `newest` moved to 3.0.0 and the
    # test failed on the release cut instead of on a real regression).
    newest = max(CHANGELOG, key=lambda v: tuple(int(x) for x in v.split(".")))
    probe = f"{int(newest.split('.')[0]) + 1}.0.0"
    notes = notes_for(probe)
    assert notes, "expected nearest-older notes, not []"
    # it must be the highest recorded version's notes
    assert notes == CHANGELOG[newest]


def test_version_below_all_entries_returns_empty():
    # Nothing recorded at or below → no dialog (fail safe, no crash).
    assert notes_for("0.0.1") == []


def test_app_version_is_a_changelog_key():
    # Release-cut guard: the shipping APP_VERSION must have its own changelog
    # entry, so the what's-new dialog is never silently dead again.
    from src.services.app_update import updater

    assert updater.APP_VERSION in CHANGELOG, (
        f"APP_VERSION {updater.APP_VERSION} has no CHANGELOG entry — add one"
    )


def test_current_shipping_line_has_entry():
    # The 2.9.x line that this audit found missing must now be present.
    assert any(v.startswith("2.9.") for v in CHANGELOG)
