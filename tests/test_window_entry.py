import pathlib
import re

from src.services.browser.window_entry import (
    app_id_for,
    remove_window_entry,
    write_window_entry,
)


def test_app_id_is_dbus_valid_and_unique():
    # The app_id doubles as the Firefox DBus remoting name, so it must be a
    # valid DBus path segment: only [A-Za-z0-9_]. A name with a dash/space
    # would be rejected by Firefox and collapse to the shared default, which
    # makes a second profile fail to launch.
    app_id = app_id_for("My Profile-8")
    assert re.fullmatch(r"persona_[A-Za-z0-9_]+", app_id)
    assert "-" not in app_id and " " not in app_id


def test_app_id_is_deterministic():
    # Same name → same id across launches, so the .desktop StartupWMClass keeps
    # matching the window and the icon stays stable.
    assert app_id_for("acc") == app_id_for("acc")


def test_app_id_distinguishes_names_that_sanitize_alike():
    # "a-b" and "a b" both sanitise to "a_b"; the crc suffix keeps them distinct
    # so two such profiles get different remoting names.
    assert app_id_for("a-b") != app_id_for("a b")


def test_write_uses_app_id_as_wmclass(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = write_window_entry("test8")
    assert pathlib.Path(path).exists()
    text = pathlib.Path(path).read_text(encoding="utf-8")
    assert f"StartupWMClass={app_id_for('test8')}" in text
    assert "Name=test8" in text
    assert "Icon=chromium" in text


def test_write_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    p1 = write_window_entry("acc")
    p2 = write_window_entry("acc")
    assert p1 == p2


def test_filename_sanitized(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = write_window_entry("my acc/01")
    # no path separators leak into the filename
    assert "/" not in pathlib.Path(path).name
    text = pathlib.Path(path).read_text(encoding="utf-8")
    # the human-facing Name keeps the original profile name
    assert "Name=my acc/01" in text


def test_remove_window_entry_deletes_the_file(tmp_path, monkeypatch):
    # audit6 LOW c: the desktop entry embeds the profile name in cleartext;
    # delete/wipe must remove it so no forensic trace survives.
    monkeypatch.setenv("HOME", str(tmp_path))
    path = write_window_entry("secret-acct")
    assert pathlib.Path(path).exists()
    remove_window_entry("secret-acct")
    assert not pathlib.Path(path).exists()


def test_remove_window_entry_absent_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # no exception when there's nothing to remove
    remove_window_entry("never-existed")


# --- PS-209: the filename must disambiguate exactly as the app_id does ------
#
# `app_id_for` appends a crc of the ORIGINAL name so profiles whose names
# collapse to one sanitised form stay distinct; `_safe_filename` performed the
# same collapse with no digest, so "a_b" and "a b" — both creatable, an
# interior space is a valid profile name — shared ONE host .desktop file.
# These assert on FILES ON DISK, never that a helper was called.


def _entries(tmp_path):
    d = pathlib.Path(tmp_path) / ".local/share/applications"
    return sorted(p.name for p in d.glob("*.desktop")) if d.exists() else []


def test_colliding_names_get_two_distinct_files_on_disk(tmp_path, monkeypatch):
    # AC1. Before the digest these two names produced persona-a_b.desktop
    # twice: the second write overwrote the first, so ONE file on disk claimed
    # to be both profiles.
    monkeypatch.setenv("HOME", str(tmp_path))
    p1 = write_window_entry("a_b")
    p2 = write_window_entry("a b")

    assert p1 != p2
    assert len(_entries(tmp_path)) == 2, _entries(tmp_path)
    assert pathlib.Path(p1).exists() and pathlib.Path(p2).exists()

    # each file labels its OWN profile — the taskbar match this module exists for
    assert f"StartupWMClass={app_id_for('a_b')}" in pathlib.Path(p1).read_text(
        encoding="utf-8"
    )
    assert f"StartupWMClass={app_id_for('a b')}" in pathlib.Path(p2).read_text(
        encoding="utf-8"
    )


def test_deleting_one_colliding_profile_keeps_the_others_entry(tmp_path, monkeypatch):
    # AC3. The delete path unlinks BY FILENAME, so with a shared name removing
    # profile "a_b" deleted the entry belonging to the still-LIVE profile "a b",
    # which then fell back to the generic taskbar label.
    monkeypatch.setenv("HOME", str(tmp_path))
    write_window_entry("a_b")
    survivor = pathlib.Path(write_window_entry("a b"))

    remove_window_entry("a_b")

    assert _entries(tmp_path) == [survivor.name]
    body = survivor.read_text(encoding="utf-8")
    # the survivor is intact AND still matches its own window identity
    assert f"StartupWMClass={app_id_for('a b')}" in body
    assert "Name=a b" in body
    # and the deleted profile's cleartext name is gone from the host
    assert "Name=a_b" not in body


def test_write_converges_off_the_legacy_filename(tmp_path, monkeypatch):
    # PS-209 migration (a): an entry already on disk under the old digest-less
    # name would otherwise be orphaned forever, since remove_window_entry no
    # longer computes that path. The next launch heals it.
    monkeypatch.setenv("HOME", str(tmp_path))
    d = pathlib.Path(tmp_path) / ".local/share/applications"
    d.mkdir(parents=True, exist_ok=True)
    legacy = d / "persona-acct.desktop"
    legacy.write_text("[Desktop Entry]\nName=acct\n", encoding="utf-8")

    new_path = pathlib.Path(write_window_entry("acct"))

    assert not legacy.exists(), "legacy entry orphaned by the scheme change"
    assert _entries(tmp_path) == [new_path.name]


def test_remove_also_unlinks_the_legacy_filename(tmp_path, monkeypatch):
    # A delete/wipe is the last chance to reach that residue: after it the
    # profile is gone, so no later launch would ever converge the old file away
    # and the cleartext Name= would survive the delete (the PS-15/PS-16 class).
    monkeypatch.setenv("HOME", str(tmp_path))
    d = pathlib.Path(tmp_path) / ".local/share/applications"
    write_window_entry("secret acct")
    # planted AFTER the write, or the write's own convergence would remove it
    # and this would silently stop exercising the removal path. The space
    # sanitises to "_", so the legacy name is persona-secret_acct.desktop.
    legacy = d / "persona-secret_acct.desktop"
    legacy.write_text("[Desktop Entry]\nName=secret acct\n", encoding="utf-8")

    remove_window_entry("secret acct")

    assert _entries(tmp_path) == []
