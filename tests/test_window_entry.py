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


def _legacy_body(profile_name: str, icon: str = "chromium") -> str:
    """A legacy (pre-PS-209) entry exactly as the old writer produced it.

    Byte-for-byte the merge-base `write_window_entry` body — crucially it
    carries `StartupWMClass=app_id_for(name)`, which the old writer has ALWAYS
    written. That token is what proves ownership of an ambiguous legacy
    filename, so a fixture that omits it is not a legacy entry the product
    could ever have written, and testing against one would prove nothing.
    """
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={profile_name}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        f"StartupWMClass={app_id_for(profile_name)}\n"
    )


def test_write_converges_off_the_legacy_filename(tmp_path, monkeypatch):
    # PS-209 migration (a): an entry already on disk under the old digest-less
    # name would otherwise be orphaned forever, since remove_window_entry no
    # longer computes that path. The next launch heals it.
    monkeypatch.setenv("HOME", str(tmp_path))
    d = pathlib.Path(tmp_path) / ".local/share/applications"
    d.mkdir(parents=True, exist_ok=True)
    legacy = d / "persona-acct.desktop"
    legacy.write_text(_legacy_body("acct"), encoding="utf-8")

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
    legacy.write_text(_legacy_body("secret acct"), encoding="utf-8")

    remove_window_entry("secret acct")

    assert _entries(tmp_path) == []


# --- PS-209 round 2: the migration helper must not re-open the collision -----
#
# `_legacy_safe_filename` is ambiguous BY CONSTRUCTION, and the new scheme's
# own output is a well-formed legacy name for a DIFFERENT profile, because the
# `-<8 hex>` suffix uses only characters the sanitiser passes through:
#
#     _safe_filename("a_b") == _legacy_safe_filename("a_b-28cb39ea")
#
# Both names pass validate_profile_name, so the pair is creatable. Unlinking a
# legacy path by NAME therefore deleted another live profile's CURRENT entry —
# the very collision this ticket closes, reintroduced through the migration.
# Ownership is confirmed from the entry's StartupWMClass instead.

_VICTIM = "a_b"
# _safe_filename(_VICTIM) == _legacy_safe_filename(_ATTACKER)
_ATTACKER = "a_b-28cb39ea"


def test_launching_a_profile_keeps_a_colliding_profiles_current_entry(
    tmp_path, monkeypatch
):
    # The WRITE path. Merely launching _ATTACKER ran the legacy cleanup for its
    # own name, which resolved to _VICTIM's current file and unlinked it.
    monkeypatch.setenv("HOME", str(tmp_path))
    victim = pathlib.Path(write_window_entry(_VICTIM))
    attacker = pathlib.Path(write_window_entry(_ATTACKER))

    assert victim.exists(), "a live profile's entry was deleted by another's launch"
    assert attacker.exists()
    assert sorted(_entries(tmp_path)) == sorted([victim.name, attacker.name])
    # the survivor still matches its OWN window identity
    assert f"StartupWMClass={app_id_for(_VICTIM)}\n" in victim.read_text(
        encoding="utf-8"
    )


def test_deleting_a_profile_keeps_a_colliding_profiles_current_entry(
    tmp_path, monkeypatch
):
    # The REMOVE path. Deleting _ATTACKER unlinked its own entry AND, via the
    # legacy path, _VICTIM's — leaving the live victim with no entry at all and
    # its window on the generic taskbar label this module exists to prevent.
    monkeypatch.setenv("HOME", str(tmp_path))
    victim = pathlib.Path(write_window_entry(_VICTIM))
    write_window_entry(_ATTACKER)

    remove_window_entry(_ATTACKER)

    assert victim.exists(), "deleting one profile removed a live profile's entry"
    assert _entries(tmp_path) == [victim.name]
    body = victim.read_text(encoding="utf-8")
    assert f"StartupWMClass={app_id_for(_VICTIM)}\n" in body
    assert f"Name={_VICTIM}\n" in body


# --- PS-209 round 3: the ownership token has TWO eras ----------------------
#
# `app_id_for` gained its digest at 8c38017 (v2.1.8); before that it returned
# `persona-{raw name}`. `_safe_filename` was byte-identical from v1.0.0 through
# the merge-base, so a pre-v2.1.8 entry sits at EXACTLY the path the legacy
# helper computes — it is FOUND, and a guard that knows only the modern token
# then refuses it. That turns residue the pre-PS-209 code cleaned up (it
# unlinked by name, unguarded) into residue nothing can ever reach: the write
# path cannot heal it and the delete path cannot remove it, so it would survive
# a panic wipe with the profile's cleartext Name= on the host.
#
# The fixture body below is the OLD writer's, copied from 7ad7422 — NOT
# `_legacy_body`, which uses today's `app_id_for` and so only covers v2.1.8+.


def _pre_v218_body(profile_name: str, icon: str = "chromium") -> str:
    """A v2.1.7-era entry, byte-for-byte as the writer at 7ad7422 produced it.

    The only difference from `_legacy_body` is the StartupWMClass token, and
    that difference is the whole point: this is the shape a guard pinned to the
    modern token silently fails to recognise.
    """
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={profile_name}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        f"StartupWMClass=persona-{profile_name}\n"
    )


def test_remove_unlinks_a_pre_v218_legacy_entry(tmp_path, monkeypatch):
    # The DELETE path, and the one that matters most: remove_window_entry is
    # reached from wipe_all_profiles, whose "nothing survives it" is only true
    # if this file goes. An interior space is a valid profile name, so the
    # legacy filename is persona-client_acct.desktop.
    monkeypatch.setenv("HOME", str(tmp_path))
    d = pathlib.Path(tmp_path) / ".local/share/applications"
    d.mkdir(parents=True, exist_ok=True)
    legacy = d / "persona-client_acct.desktop"
    legacy.write_text(_pre_v218_body("client acct"), encoding="utf-8")
    # the era check itself: this entry does NOT carry today's token
    assert f"StartupWMClass={app_id_for('client acct')}" not in legacy.read_text(
        encoding="utf-8"
    )

    remove_window_entry("client acct")

    assert not legacy.exists(), "pre-v2.1.8 residue survived a delete/panic wipe"
    assert _entries(tmp_path) == []


def test_write_converges_off_a_pre_v218_legacy_entry(tmp_path, monkeypatch):
    # The WRITE path: the next launch heals it, rather than leaving the orphan
    # sitting beside the new file forever.
    monkeypatch.setenv("HOME", str(tmp_path))
    d = pathlib.Path(tmp_path) / ".local/share/applications"
    d.mkdir(parents=True, exist_ok=True)
    legacy = d / "persona-client_acct.desktop"
    legacy.write_text(_pre_v218_body("client acct"), encoding="utf-8")

    new_path = pathlib.Path(write_window_entry("client acct"))

    assert not legacy.exists(), "pre-v2.1.8 entry left beside the new one"
    assert _entries(tmp_path) == [new_path.name]
    assert f"StartupWMClass={app_id_for('client acct')}" in new_path.read_text(
        encoding="utf-8"
    )


def test_pre_v218_token_does_not_reopen_the_collision(tmp_path, monkeypatch):
    # Accepting the superseded token must not re-open round 2's collision. It
    # cannot: the old token embeds the raw name verbatim behind a fixed prefix
    # and the match is line-anchored, and the two token spaces differ at
    # character 8 (`persona_` vs `persona-`), so an old-token match can never
    # be satisfied by a new-scheme file. Asserted here on files on disk.
    monkeypatch.setenv("HOME", str(tmp_path))
    victim = pathlib.Path(write_window_entry(_VICTIM))
    attacker = pathlib.Path(write_window_entry(_ATTACKER))

    assert victim.exists(), "the widened guard deleted a live profile's entry"
    assert sorted(_entries(tmp_path)) == sorted([victim.name, attacker.name])

    remove_window_entry(_ATTACKER)

    assert victim.exists(), "the widened guard deleted a live profile's entry"
    assert _entries(tmp_path) == [victim.name]
    assert f"StartupWMClass={app_id_for(_VICTIM)}\n" in victim.read_text(
        encoding="utf-8"
    )


def test_legacy_cleanup_leaves_an_entry_it_cannot_prove_it_owns(tmp_path, monkeypatch):
    # The stated bound of the content guard, pinned rather than left implicit.
    # Ownership is proven from StartupWMClass; a file at the legacy path that
    # does NOT carry this profile's token is left alone. In exchange for never
    # deleting a live profile's entry, a hand-edited or truncated legacy file
    # survives as residue — the safe direction of the trade, and the reason the
    # guard is content-based rather than name-based.
    monkeypatch.setenv("HOME", str(tmp_path))
    d = pathlib.Path(tmp_path) / ".local/share/applications"
    d.mkdir(parents=True, exist_ok=True)
    foreign = d / "persona-acct.desktop"
    foreign.write_text("[Desktop Entry]\nName=something else\n", encoding="utf-8")

    write_window_entry("acct")

    assert foreign.exists(), "unlinked a file it could not prove it owned"
    assert foreign.read_text(encoding="utf-8") == (
        "[Desktop Entry]\nName=something else\n"
    )
