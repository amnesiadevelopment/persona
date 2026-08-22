import pytest

from src.services.browser.window_entry import _entry_dir, write_window_entry
from src.services.profile.manager import ProfileManager


@pytest.fixture(autouse=True)
def _isolate_entry_dir(tmp_path, monkeypatch):
    """Redirect the desktop-entry dir away from the real host.

    `_entry_dir()` resolves `~/.local/share/applications` at call time and is
    NOT under PROFILES_FILE/DATA_DIR, so the `mgr` fixture below does not cover
    it. update_profile now removes an entry, so without this every test in this
    file would unlink against the developer's real applications dir. Autouse so
    no test can opt out by forgetting.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _entries():
    """Every persona desktop entry currently on disk, as (filename, body)."""
    d = _entry_dir()
    if not d.exists():
        return []
    return [(p.name, p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.desktop"))]


def _mentions(name):
    """Entries whose FILENAME or BODY mentions `name` — the cleartext residue."""
    return [(fn, body) for fn, body in _entries() if name in fn or name in body]


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    import src.core.config as cfg
    import src.services.profile.manager as mod

    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    for m in (cfg, mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
    return ProfileManager()


def _names(mgr):
    return [p.name for p in mgr.list_profiles()]


def test_rename_keeps_list_position(mgr):
    mgr.add_profile("a", "", "windows")
    mgr.add_profile("b", "", "windows")
    mgr.add_profile("c", "", "windows")

    mgr.update_profile("b", "b2", "", "windows")

    assert _names(mgr) == ["a", "b2", "c"]


def test_rename_first_keeps_position(mgr):
    mgr.add_profile("a", "", "windows")
    mgr.add_profile("b", "", "windows")

    mgr.update_profile("a", "a2", "", "windows")

    assert _names(mgr) == ["a2", "b"]


def test_update_without_rename_keeps_position(mgr):
    mgr.add_profile("a", "", "windows")
    mgr.add_profile("b", "", "windows")
    mgr.add_profile("c", "", "windows")

    mgr.update_profile("b", "b", "1.2.3.4:8080", "linux")

    assert _names(mgr) == ["a", "b", "c"]


def test_failed_dir_rename_leaves_profile_unchanged(mgr, monkeypatch):
    # #15: a data-dir rename can fail (Windows lock while the browser runs). The
    # fields must be mutated AFTER the rename, so a failure leaves the profile
    # fully intact — no half-applied name/proxy/os and no memory/disk divergence.
    import pathlib

    mgr.add_profile("a", "1.1.1.1:1", "windows")

    def boom(self, target):
        raise OSError("dir locked")

    monkeypatch.setattr(pathlib.Path, "rename", boom)
    ok = mgr.update_profile("a", "a2", "2.2.2.2:2", "linux")

    assert ok is False
    # nothing changed: still keyed by "a", original name/proxy/os intact
    assert _names(mgr) == ["a"]
    p = mgr.profiles.get("a")
    assert p.name == "a"
    assert p.os_type == "windows"
    assert (p.proxy or "") == "1.1.1.1:1"


# --- PS-16: a rename must not strand the old name's desktop entry ----------
#
# The entry lives in ~/.local/share/applications (host-global, OUTSIDE
# PERSONA_HOME), is keyed by profile NAME, and carries `Name=<name>` in
# cleartext. delete_profile/wipe_all_profiles only ever remove the CURRENT
# name, so before this fix a rename left the OLD name on the host permanently —
# unreachable by both delete and the panic wipe. Invariant #0: nothing about a
# deleted profile may survive it.
#
# The launcher writes the entry on every launch (process.py); these tests call
# write_window_entry directly to stand in for that launch.


def test_rename_then_delete_leaves_no_trace_of_the_old_name(mgr):
    mgr.add_profile("alpha", "", "windows")
    write_window_entry("alpha")  # as a launch would
    assert _mentions("alpha")

    mgr.update_profile("alpha", "bravo", "", "windows")
    write_window_entry("bravo")  # next launch re-creates it under the new name
    mgr.delete_profile("bravo")

    # the whole point: the operator deleted the profile, so the name they were
    # hiding must be gone from the host — not just the name it ended up with.
    assert _mentions("alpha") == []
    assert _mentions("bravo") == []


def test_rename_chain_leaves_no_entry_for_any_previous_name(mgr):
    mgr.add_profile("alpha", "", "windows")
    write_window_entry("alpha")
    mgr.update_profile("alpha", "bravo", "", "windows")
    write_window_entry("bravo")
    mgr.update_profile("bravo", "charlie", "", "windows")
    write_window_entry("charlie")

    mgr.delete_profile("charlie")

    # every link in the chain, not just the last one
    for name in ("alpha", "bravo", "charlie"):
        assert _mentions(name) == [], f"{name} survived the delete"
    assert _entries() == []


def test_wipe_leaves_no_entry_for_a_renamed_profile(mgr):
    mgr.add_profile("keeper", "", "windows")
    mgr.add_profile("alpha", "", "windows")
    write_window_entry("keeper")
    write_window_entry("alpha")
    mgr.update_profile("alpha", "bravo", "", "windows")
    write_window_entry("bravo")

    mgr.wipe_all_profiles()

    # a panic wipe is the strongest promise the app makes; a pre-rename name
    # surviving it is exactly the residue that made this a leak.
    for name in ("alpha", "bravo", "keeper"):
        assert _mentions(name) == [], f"{name} survived the panic wipe"
    assert _entries() == []


def test_rename_removes_only_the_old_name_not_a_bystander(mgr):
    mgr.add_profile("bystander", "", "windows")
    mgr.add_profile("alpha", "", "windows")
    write_window_entry("bystander")
    write_window_entry("alpha")

    mgr.update_profile("alpha", "bravo", "", "windows")

    assert _mentions("alpha") == []
    # an unrelated profile's entry is untouched — the rename is not a sweep
    assert [fn for fn, _ in _mentions("bystander")] == ["persona-bystander.desktop"]


def test_update_without_rename_keeps_the_entry(mgr):
    mgr.add_profile("alpha", "", "windows")
    write_window_entry("alpha")

    mgr.update_profile("alpha", "alpha", "1.2.3.4:8080", "linux")

    # nothing was renamed, so nothing is stale: removing here would delete the
    # live profile's own entry and blank its taskbar label until the next launch
    assert [fn for fn, _ in _mentions("alpha")] == ["persona-alpha.desktop"]


def test_failed_dir_rename_keeps_the_old_entry(mgr, monkeypatch):
    # Companion to test_failed_dir_rename_leaves_profile_unchanged: a failed
    # rename returns early leaving the profile fully intact, so it must ALSO
    # leave the entry intact — the profile is still called "alpha" and still
    # needs its taskbar entry. This is why the removal sits AFTER the rename.
    import pathlib as _pathlib

    mgr.add_profile("alpha", "", "windows")
    write_window_entry("alpha")

    def boom(self, target):
        raise OSError("dir locked")

    monkeypatch.setattr(_pathlib.Path, "rename", boom)
    ok = mgr.update_profile("alpha", "bravo", "", "linux")

    assert ok is False
    assert [fn for fn, _ in _mentions("alpha")] == ["persona-alpha.desktop"]


# --- PS-45: a rename must not re-roll the profile's presented machine -------
#
# A profile's whole presented machine (auto resolution, mobile device preset,
# touch points, --fingerprint=) derives from Profile.fingerprint_seed, which
# used to be crc32(name) computed on every read. update_profile renames the
# DATA DIR — the cookie jar, storage and sessions — and then assigns the new
# name, so before this fix every rename handed a site the SAME cookies under a
# DIFFERENT machine. That is the exact linkage event restore_profile refuses in
# writing ("restoring under a different name would hand back the cookie jar
# attached to a DIFFERENT fingerprint"): stated as a rule on one path, and
# performed on every rename one method away.
#
# The seed is now frozen at creation. A record with no seed (every profile that
# existed before this change) still falls back to crc32(name), so nothing that
# exists today moves by a single bit.
#
# These tests bind to the SEED and the DERIVED IDENTITY, never to "the
# dataclass has a field" — an implementation that carries the attribute but
# re-derives from the name on load would pass the latter and fail the point.

import json as _json
import zlib as _zlib

from src.models.hardware_generation import CURRENT_HARDWARE_GENERATION
from src.models.profile import Profile
from src.services.browser.device_presets import pick_preset
from src.services.browser.resolution import resolve_resolution

# Validated rename pairs (validate_profile_name accepts every one), each of
# which moves the auto resolution on the pre-fix code.
RENAME_PAIRS = [
    ("acme-bank-jdoe", "acme-bank-jdoe-2"),
    ("work", "work1"),
    ("acct-a", "acct-b"),
    ("client-alpha", "client-alpha-old"),
    ("shop1", "shop2"),
    ("jdoe", "jdoe-2"),
]


def _crc32(s):
    """crc32(name) — what the seed USED to be, computed independently here so a
    test can assert the seed is no longer following the name."""
    return _zlib.crc32(s.encode("utf-8"))


def _identity(profile):
    """The presented machine, as the values a page actually observes.

    Deliberately not the seed alone: the seed is the input, and asserting only
    the input would not catch a consumer reading the name directly. Deliberately
    not the resolution alone either — two seeds can collide onto the same pool
    entry, so a resolution that happens not to move is not evidence of a stable
    identity. Seed AND derived values, together.
    """
    seed = profile.fingerprint_seed
    return {
        "seed": seed,
        "resolution": resolve_resolution(
            getattr(profile, "resolution", "auto"), seed,
            profile.hardware_generation,
        ),
        "touch_points": (5, 10)[seed % 2],
        "fingerprint_arg": f"--fingerprint={seed}",
    }


@pytest.mark.parametrize("old,new", RENAME_PAIRS)
def test_rename_preserves_the_presented_machine(mgr, old, new):
    # AC1: same cookie jar, same machine. All six pairs move the resolution on
    # the pre-fix code, so this is the defect stated as an assertion.
    mgr.add_profile(old, "", "windows")
    before = _identity(mgr.profiles[old])

    assert mgr.update_profile(old, new, "", "windows") is True

    after = _identity(mgr.profiles[new])
    assert after == before, (
        f"renaming {old!r} -> {new!r} moved the presented machine: "
        f"{before} -> {after}"
    )


@pytest.mark.parametrize("old,new", RENAME_PAIRS)
def test_rename_keeps_the_seed_though_the_names_differ(mgr, old, new):
    # AC2, the premise inversion: crc32(old) != crc32(new) is precisely WHY the
    # identity moved before. That inequality is still true of the names; what
    # must no longer be true is that the seed follows it.
    assert _crc32(old) != _crc32(new)

    mgr.add_profile(old, "", "windows")
    seed_before = mgr.profiles[old].fingerprint_seed
    mgr.update_profile(old, new, "", "windows")

    renamed = mgr.profiles[new]
    assert renamed.name == new  # the label really did change
    assert renamed.fingerprint_seed == seed_before
    # ...and it is emphatically NOT the new name's crc32, which is the value
    # the pre-fix code served here.
    assert renamed.fingerprint_seed != _crc32(new)


def test_rename_preserves_the_mobile_device_preset(mgr):
    # AC1, mobile arm: on a mobile profile the seed picks a real device preset,
    # so the pre-fix rename swapped the handset out from under the session.
    # 'acme-bank-jdoe' -> 'acme-bank-jdoe-2' moves iPhone 14 -> iPhone 15.
    mgr.add_profile(
        "acme-bank-jdoe", "", "ios", device_type="mobile", engine="chromium"
    )
    before = pick_preset(
        mgr.profiles["acme-bank-jdoe"].fingerprint_seed, "ios",
        mgr.profiles["acme-bank-jdoe"].hardware_generation,
    )

    mgr.update_profile(
        "acme-bank-jdoe",
        "acme-bank-jdoe-2",
        "",
        "ios",
        new_device_type="mobile",
        new_engine="chromium",
    )

    after = pick_preset(
        mgr.profiles["acme-bank-jdoe-2"].fingerprint_seed, "ios",
        mgr.profiles["acme-bank-jdoe-2"].hardware_generation,
    )
    assert after.key == before.key
    assert (after.width, after.height) == (before.width, before.height)


def test_a_profile_saved_without_a_seed_still_derives_it_from_its_name():
    # AC3, the blast-radius bound: this is today's profiles.json — a record with
    # NO seed key at all. It must keep presenting exactly what it always has.
    # The expected values are pinned literals computed against origin/main, not
    # recomputed from the implementation under test, so this cannot pass by
    # agreeing with a bug.
    legacy = Profile(**{"name": "acme-bank-jdoe", "os_type": "windows"})

    assert legacy.fingerprint_seed_value is None  # nothing to fall back FROM
    assert legacy.fingerprint_seed == 1951056451
    assert resolve_resolution(
        "auto", legacy.fingerprint_seed, legacy.hardware_generation
    ) == (1440, 900)
    assert pick_preset(
        legacy.fingerprint_seed, "ios", legacy.hardware_generation
    ).key == "iphone-14"


def test_a_pre_seed_record_on_disk_loads_with_the_name_derived_seed(
    mgr, tmp_path, monkeypatch
):
    # AC3 through the LOAD path specifically: the allow-list must map an absent
    # key to None (the fallback) and must not helpfully default it to
    # crc32(name) — freezing a derived value at load time would make a later
    # rename of an OLD profile behave differently depending on whether it had
    # been reloaded since, which is a worse bug than the one being fixed.
    import src.services.profile.manager as mod

    pathlib_file = tmp_path / "profiles.json"
    pathlib_file.write_text(
        _json.dumps(
            {"legacy-profile": {"name": "legacy-profile", "os_type": "windows"}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "PROFILES_FILE", str(pathlib_file), raising=False)

    fresh = ProfileManager()
    loaded = fresh.profiles["legacy-profile"]

    assert loaded.fingerprint_seed_value is None
    assert loaded.fingerprint_seed == 2974223098  # crc32('legacy-profile')
    assert resolve_resolution(
        "auto", loaded.fingerprint_seed, loaded.hardware_generation
    ) == (1440, 900)


def test_the_seed_survives_a_save_load_round_trip_after_a_rename(mgr, tmp_path):
    # AC4 — the criterion most likely to fail, and the one an in-memory
    # assertion cannot stand in for. The load allow-list is hand-enumerated, so
    # a field the dataclass has and to_dict() saves is still silently DROPPED on
    # reload unless it is listed there too (cookie_import_status was the last
    # field to hit this). If that happened here the reloaded profile would fall
    # back to crc32(NEW name) and the freeze would evaporate at the next restart
    # while every test above still passed.
    mgr.add_profile("work", "", "windows")
    seed_before = mgr.profiles["work"].fingerprint_seed
    mgr.update_profile("work", "work1", "", "windows")

    # a brand-new manager reading the same file back off disk
    fresh = ProfileManager()
    reloaded = fresh.profiles["work1"]

    assert reloaded.fingerprint_seed == seed_before
    # the load did not silently drop the field and re-derive from the name
    assert reloaded.fingerprint_seed_value == seed_before
    assert reloaded.fingerprint_seed != _crc32("work1")
    # and the file itself really carries it
    on_disk = _json.loads(
        (tmp_path / "profiles.json").read_text(encoding="utf-8")
    )
    assert on_disk["work1"]["fingerprint_seed_value"] == seed_before


def test_the_hardware_generation_survives_a_save_load_round_trip(mgr, tmp_path):
    # PS-54, and the same AC4 trap as the seed above: the load allow-list is
    # hand-enumerated, so a field the dataclass has and to_dict() SAVES is still
    # silently DROPPED on reload unless it is listed there too. If that happened
    # here, every restart would quietly re-read each profile as generation 0 —
    # invisible today, then a mass re-roll the first time a hardware list grew,
    # which is precisely the event this field exists to prevent.
    #
    # WRITTEN WITH A NON-ZERO GENERATION ON PURPOSE. CURRENT_HARDWARE_GENERATION
    # is 0 today, so a profile created now stores 0 — and a dropped field also
    # reads as 0 (None -> 0 is the migration fallback). At generation 0 the two
    # outcomes are INDISTINGUISHABLE, so a test using the freshly-minted value
    # would pass just as happily against an allow-list with this field missing.
    # Setting 3 is what makes the assertion able to fail.
    mgr.add_profile("work", "", "windows")
    mgr.profiles["work"].hardware_generation_value = 3
    mgr.save_profiles()

    # a brand-new manager reading the same file back off disk
    fresh = ProfileManager()
    reloaded = fresh.profiles["work"]

    assert reloaded.hardware_generation_value == 3
    assert reloaded.hardware_generation == 3
    # and the file itself really carries it
    on_disk = _json.loads(
        (tmp_path / "profiles.json").read_text(encoding="utf-8")
    )
    assert on_disk["work"]["hardware_generation_value"] == 3


def test_a_new_profile_is_minted_into_the_current_generation(mgr):
    # The stamp is written at CREATE, like the seed. Without it a new profile
    # would read as generation 0 forever and could never be picked onto hardware
    # added later — the lists would be unmaintainable in the other direction.
    mgr.add_profile("fresh", "", "windows")
    assert (
        mgr.profiles["fresh"].hardware_generation_value
        == CURRENT_HARDWARE_GENERATION
    )


def test_a_rename_does_not_move_a_profiles_hardware_generation(mgr):
    # The generation is frozen at creation exactly like the seed: an edit path
    # that rewrote it would move the profile onto a newer pool and re-roll the
    # very hardware the freeze exists to pin. Same reasoning as
    # test_rename_preserves_the_presented_machine, for the mapping rather than
    # for the index into it.
    mgr.add_profile("acct", "", "windows")
    mgr.profiles["acct"].hardware_generation_value = 2

    assert mgr.update_profile("acct", "acct-renamed", "", "windows") is True

    assert mgr.profiles["acct-renamed"].hardware_generation_value == 2


def test_the_cookie_jar_and_the_identity_travel_together(mgr, tmp_path):
    # AC5: the data dir still moves — the fix is not "stop renaming the dir".
    # The point is that the jar and the machine now arrive at the new name
    # TOGETHER, instead of the jar moving while the machine was re-rolled.
    mgr.add_profile("acct-a", "", "windows")
    data_root = tmp_path / "data"
    (data_root / "acct-a" / "Default").mkdir(parents=True, exist_ok=True)
    (data_root / "acct-a" / "Default" / "Cookies").write_text(
        "session-cookie", encoding="utf-8"
    )
    seed_before = mgr.profiles["acct-a"].fingerprint_seed

    mgr.update_profile("acct-a", "acct-b", "", "windows")

    # the jar moved...
    assert not (data_root / "acct-a").exists()
    assert (
        data_root / "acct-b" / "Default" / "Cookies"
    ).read_text(encoding="utf-8") == "session-cookie"
    # ...and the machine that jar is attached to did not change underneath it
    assert mgr.profiles["acct-b"].fingerprint_seed == seed_before


def test_failed_dir_rename_does_not_mint_or_move_a_seed(mgr, monkeypatch):
    # AC7: the failed-rename path returns False leaving everything untouched,
    # and that must include the seed — a seed minted or mutated on a path that
    # returned False would be a fingerprint change the operator never asked for
    # and the return value denies. Companion to the two existing failed-rename
    # tests, which this must not disturb.
    import pathlib

    mgr.add_profile("alpha", "", "windows")
    before = _identity(mgr.profiles["alpha"])

    def boom(self, target):
        raise OSError("dir locked")

    monkeypatch.setattr(pathlib.Path, "rename", boom)
    assert mgr.update_profile("alpha", "bravo", "", "linux") is False

    assert _identity(mgr.profiles["alpha"]) == before


def test_a_new_profiles_seed_is_the_one_it_would_have_derived(mgr):
    # The freeze must not RE-ROLL anything: a profile created today presents
    # exactly what it would have presented before the seed was persisted. This
    # is what keeps the change a freeze rather than the seed-secrecy change
    # (which would move fingerprints and is deliberately out of scope).
    mgr.add_profile("shop1", "", "windows")

    assert mgr.profiles["shop1"].fingerprint_seed == _crc32("shop1")
    assert mgr.profiles["shop1"].fingerprint_seed_value == _crc32("shop1")


# --- the OTHER half of the seed invariant: distinct live profiles, distinct
# --- machines. Freezing the seed is what put this at risk: a name is REUSABLE,
# --- so crc32(name) alone would hand a recreated name the seed its renamed
# --- predecessor is still holding. Every test below asserts on the DERIVED
# --- identity, not on "a field exists", so it fails against an implementation
# --- that merely stores a seed without keeping it unique.


@pytest.fixture
def trash_mgr(tmp_path, monkeypatch):
    """A manager whose TRASH is redirected into tmp_path as well.

    The plain `mgr` fixture above isolates PROFILES_FILE and DATA_DIR but NOT
    the trash store, which resolves PERSONA_TRASH_FILE / PERSONA_HOME at call
    time — so a test that trashes a profile through `mgr` writes into the
    developer's real ~/.persona/trash.json. Same pattern as
    tests/test_trash_profiles.py.
    """
    import src.core.config as cfg
    import src.services.profile.manager as mod
    from src.services.trash.store import TrashStore

    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    for m in (cfg, mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))
    monkeypatch.setenv("PERSONA_HOME", str(tmp_path))
    m = ProfileManager()
    m.set_trash(TrashStore())
    return m


def _seeds_are_unique(mgr):
    seeds = [p.fingerprint_seed for p in mgr.profiles.values()]
    return len(seeds) == len(set(seeds))


def test_recreating_a_freed_name_does_not_reuse_the_renamed_profiles_machine(mgr):
    # COLLISION PATH A — the most ordinary sequence this feature creates:
    # "archive last quarter's account, start a fresh one under the same label".
    # Before the uniqueness rule both profiles landed on crc32('acme-bank').
    mgr.add_profile("acme-bank", "", "windows")
    mgr.update_profile("acme-bank", "acme-bank-old", "", "windows")
    mgr.add_profile("acme-bank", "", "windows")

    old = _identity(mgr.profiles["acme-bank-old"])
    new = _identity(mgr.profiles["acme-bank"])

    # the renamed profile kept its machine (the stability half, still true)
    assert old["seed"] == _crc32("acme-bank")
    # and the recreated name did NOT inherit it (the isolation half)
    assert new["seed"] != old["seed"]
    assert new != old
    assert _seeds_are_unique(mgr)


def test_a_restored_profile_and_a_recreated_name_present_different_machines(
    trash_mgr,
):
    # COLLISION PATH C — following the codebase's OWN documented remedy.
    # restore_profile refuses a rename-on-restore and tells the operator to
    # "Free the name and restore again"; doing exactly that used to manufacture
    # a collision. The restored profile must come back on its ORIGINAL seed
    # (re-rolling it is the very linkage restore refuses), so the seed has to be
    # reserved while it sits in the trash.
    mgr = trash_mgr
    mgr.add_profile("alpha", "", "windows")
    original = _identity(mgr.profiles["alpha"])

    assert mgr.delete_profile("alpha") is True
    entry = mgr._trash().list("profile")[0]

    # free the name, exactly as the refusal message instructs
    mgr.add_profile("alpha", "", "windows")
    mgr.update_profile("alpha", "alpha-2", "", "windows")

    ok, msg = mgr.restore_profile(entry)
    assert ok is True, msg

    # the restored profile is byte-identical to what it was before trashing
    assert _identity(mgr.profiles["alpha"]) == original
    # and the profile that borrowed its name in the meantime is a DIFFERENT
    # machine, not a second copy of it
    assert _identity(mgr.profiles["alpha-2"]) != original
    assert _seeds_are_unique(mgr)


def test_reimporting_an_export_of_a_since_renamed_profile_does_not_collide(
    mgr, tmp_path
):
    # COLLISION PATH B — export 'client-alpha', rename it away, re-import the
    # archive. Both records claim crc32('client-alpha'); the import must land on
    # a machine of its own rather than a duplicate of the renamed original's.
    mgr.add_profile("client-alpha", "", "windows")
    original = _identity(mgr.profiles["client-alpha"])
    outdir = tmp_path / "out"
    outdir.mkdir()
    ok, archive = mgr.export_profile("client-alpha", str(outdir))
    assert ok is True, archive

    mgr.update_profile("client-alpha", "client-alpha-old", "", "windows")
    ok, name = mgr.import_profile(archive)
    assert ok is True, name

    assert _identity(mgr.profiles["client-alpha-old"]) == original
    assert _identity(mgr.profiles["client-alpha"]) != original
    assert _seeds_are_unique(mgr)


def test_an_import_onto_a_machine_with_no_conflict_keeps_its_own_seed(
    mgr, tmp_path, monkeypatch
):
    # The uniqueness rule must fire ONLY on a real clash. Moving a profile to
    # another machine is the whole point of exporting one, and it has to arrive
    # presenting the same machine — otherwise the fix would re-roll identities
    # on every import, which is the failure mode this ticket exists to prevent.
    import src.core.config as cfg
    import src.services.profile.manager as mod

    mgr.add_profile("client-alpha", "", "windows")
    original = _identity(mgr.profiles["client-alpha"])
    outdir = tmp_path / "out"
    outdir.mkdir()
    ok, archive = mgr.export_profile("client-alpha", str(outdir))
    assert ok is True, archive

    # a second, empty "machine"
    other = tmp_path / "machine2"
    other.mkdir()
    for m in (cfg, mod):
        monkeypatch.setattr(
            m, "PROFILES_FILE", str(other / "profiles.json"), raising=False
        )
        monkeypatch.setattr(m, "DATA_DIR", str(other / "data"), raising=False)
    fresh = ProfileManager()
    ok, name = fresh.import_profile(archive)
    assert ok is True, name

    assert _identity(fresh.profiles["client-alpha"]) == original


def _archive_with_seed(tmp_path, name, seed, filename=None):
    """A hand-authored profile archive — i.e. UNTRUSTED input, which is what a
    shared profile is. Written by hand rather than via export_profile precisely
    because the point is a value no honest export would produce."""
    import zipfile

    payload = {
        "name": name,
        "os_type": "windows",
        "engine": "chromium",
        "device_type": "desktop",
        "resolution": "auto",
    }
    if seed is not _NO_SEED:
        payload["fingerprint_seed_value"] = seed
    path = tmp_path / (filename or f"{name}.zip")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("profile.json", _json.dumps(payload))
    return str(path)


_NO_SEED = object()


def test_an_archive_cannot_pin_an_import_onto_an_existing_profiles_machine(
    mgr, tmp_path
):
    # A shared archive is untrusted input (the name is already validated against
    # exactly this class of hostile edit). A hand-set seed matching a LOCAL
    # profile's would make the imported profile present that profile's exact
    # machine — deliberate cross-profile linkage, chosen by whoever authored the
    # archive rather than by the operator importing it.
    mgr.add_profile("victim", "", "windows")
    victim = _identity(mgr.profiles["victim"])

    archive = _archive_with_seed(tmp_path, "attacker", victim["seed"])
    ok, name = mgr.import_profile(archive)
    assert ok is True, name

    assert _identity(mgr.profiles["attacker"]) != victim
    assert mgr.profiles["attacker"].fingerprint_seed != victim["seed"]
    assert _identity(mgr.profiles["victim"]) == victim  # victim unmoved
    assert _seeds_are_unique(mgr)


@pytest.mark.parametrize(
    "bogus",
    [
        "not-an-int",       # stored verbatim, then crashed the launch path
        True,               # bool is an int subclass — would become seed 1
        -1,                 # outside crc32 range
        2**32,              # outside crc32 range
        1.5,
        [123],
        {"a": 1},
    ],
)
def test_an_archive_with_a_non_crc32_seed_is_dropped_not_stored(
    mgr, tmp_path, bogus
):
    # The seed is arithmetic input to every consumer (touch_points does
    # `seed % 2`, --fingerprint= formats it). A non-integer sailed in and
    # hard-crashed the launch path with "not all arguments converted during
    # string formatting" — a shared archive that bricks a profile. Drop the bad
    # value rather than refuse the archive: the profile lands on the crc32(name)
    # fallback, and an operator recovering an old export is not punished for a
    # field their build never wrote.
    archive = _archive_with_seed(tmp_path, "imported", bogus)
    ok, name = mgr.import_profile(archive)
    assert ok is True, name

    imported = mgr.profiles["imported"]
    assert imported.fingerprint_seed_value is None
    assert imported.fingerprint_seed == _crc32("imported")
    # and the derived identity computes rather than raising
    assert _identity(imported)["touch_points"] in (5, 10)


def test_an_archive_without_a_seed_still_imports_on_the_name_fallback(
    mgr, tmp_path
):
    # An archive exported by a build that predates the field. It must import,
    # and it must land on the same fallback an old local record does.
    archive = _archive_with_seed(tmp_path, "old-export", _NO_SEED)
    ok, name = mgr.import_profile(archive)
    assert ok is True, name

    assert mgr.profiles["old-export"].fingerprint_seed_value is None
    assert mgr.profiles["old-export"].fingerprint_seed == _crc32("old-export")


def test_reimporting_over_a_profile_does_not_re_roll_its_own_seed(
    mgr, tmp_path
):
    # overwrite=True re-imports over the profile's OWN record, so the archive's
    # seed "collides" with the very profile it is replacing. That is not a
    # clash, and treating it as one would re-roll the machine of a profile the
    # operator was merely refreshing.
    mgr.add_profile("client-alpha", "", "windows")
    original = _identity(mgr.profiles["client-alpha"])
    outdir = tmp_path / "out"
    outdir.mkdir()
    ok, archive = mgr.export_profile("client-alpha", str(outdir))
    assert ok is True, archive

    ok, name = mgr.import_profile(archive, overwrite=True)
    assert ok is True, name

    assert _identity(mgr.profiles["client-alpha"]) == original


def test_the_mint_walks_to_the_next_free_seed_deterministically():
    # The mint is pure and deterministic — no RNG — so a given (name, taken)
    # always produces the same seed. It returns crc32(name) whenever that is
    # free, which is what keeps every non-colliding create presenting exactly
    # what it always would have.
    from src.models.profile import mint_fingerprint_seed

    plain = _crc32("acme-bank")
    assert mint_fingerprint_seed("acme-bank") == plain
    assert mint_fingerprint_seed("acme-bank", set()) == plain

    first = mint_fingerprint_seed("acme-bank", {plain})
    assert first == _crc32("acme-bank:1")
    assert first != plain
    assert mint_fingerprint_seed("acme-bank", {plain}) == first  # deterministic

    second = mint_fingerprint_seed("acme-bank", {plain, first})
    assert second == _crc32("acme-bank:2")
    assert second not in (plain, first)


def test_a_trashed_profiles_seed_is_not_handed_to_a_new_profile(trash_mgr):
    # The reservation must cover the trash, not just the live list. A trashed
    # profile can come BACK on its stored seed, so handing that value out now
    # schedules a collision for whenever the operator restores.
    mgr = trash_mgr
    mgr.add_profile("alpha", "", "windows")
    trashed_seed = mgr.profiles["alpha"].fingerprint_seed
    assert mgr.delete_profile("alpha") is True

    mgr.add_profile("alpha", "", "windows")

    assert mgr.profiles["alpha"].fingerprint_seed != trashed_seed


# --- the rename path is the THIRD place a seed can change, and the one the
# --- uniqueness rule did not reach. add_profile and import_profile MINT a seed
# --- and consult the reserved set; update_profile does not mint at all, but a
# --- PRE-FIELD profile derives its seed from its name on every read, so
# --- renaming it moves its seed onto whatever the new name hashes to. Every
# --- test below asserts on the DERIVED identity, never on "a field exists".


@pytest.fixture
def legacy_trash_mgr(tmp_path, monkeypatch):
    """A manager whose profiles.json holds ONE PRE-FIELD record.

    That is not a synthetic case: the absent-field fallback is deliberately the
    whole migration, so on upgrade EVERY profile on disk is a derived-seed
    profile and stays one until it is recreated. The record is written to disk
    and loaded through the normal path rather than having the field poked to
    None in memory, so the load allow-list is exercised too.

    Trash is redirected as well (same reason as `trash_mgr`): the rename path
    has to be checked against reserved TRASHED seeds, and `_reserved_seeds()`
    reaches the real ~/.persona/trash.json otherwise.
    """
    import src.core.config as cfg
    import src.services.profile.manager as mod
    from src.services.trash.store import TrashStore

    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    pf.write_text(
        _json.dumps(
            {"legacy-acct": {"name": "legacy-acct", "os_type": "windows"}}
        ),
        encoding="utf-8",
    )
    for m in (cfg, mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))
    monkeypatch.setenv("PERSONA_HOME", str(tmp_path))
    # The legacy profile gets a real data dir, because a profile installed on a
    # real machine has one — that is the cookie jar this whole ticket is about
    # keeping married to its fingerprint. It also makes the dir rename actually
    # HAPPEN: update_profile skips the rename when the dir is absent, so a
    # fixture without one would let the failed-rename test below pass VACUOUSLY
    # (no rename attempted, so nothing for the patched OSError to fire on)
    # rather than exercising the path it names.
    (dd / "legacy-acct").mkdir(parents=True)
    m = ProfileManager()
    m.set_trash(TrashStore())
    return m


def test_renaming_a_legacy_profile_does_not_land_on_a_live_profiles_machine(
    legacy_trash_mgr,
):
    # COLLISION PATH D — the rename door, reached with a pre-field profile.
    # 'acme-bank' is created and renamed away, FREEZING crc32('acme-bank') and
    # freeing the name; the legacy profile is then renamed INTO that freed name
    # and its still-derived seed lands straight on the frozen one. Two live
    # profiles, one presented machine — the same invariant-#0 failure as the
    # create door, which the uniqueness rule did not reach because
    # update_profile mints nothing and consults no reserved set.
    mgr = legacy_trash_mgr
    assert mgr.profiles["legacy-acct"].fingerprint_seed_value is None

    mgr.add_profile("acme-bank", "", "windows")
    assert mgr.update_profile("acme-bank", "acme-bank-old", "", "windows") is True
    frozen = _identity(mgr.profiles["acme-bank-old"])
    # the name is genuinely free and its crc32 is genuinely the frozen value —
    # otherwise this test would pass without ever setting up the collision
    assert "acme-bank" not in mgr.profiles
    assert frozen["seed"] == _crc32("acme-bank")

    assert mgr.update_profile("legacy-acct", "acme-bank", "", "windows") is True

    assert _identity(mgr.profiles["acme-bank"]) != frozen
    assert mgr.profiles["acme-bank"].fingerprint_seed != _crc32("acme-bank")
    assert _seeds_are_unique(mgr)


def test_renaming_a_legacy_profile_does_not_land_on_a_trashed_profiles_seed(
    legacy_trash_mgr,
):
    # The nastier half: the reservation added so a restore can stay verbatim is
    # bypassed entirely by the rename door. A trashed seed cannot be re-minted
    # later — restore_profile rebuilds the record verbatim — so a live profile
    # renamed onto it schedules a collision for whenever the operator restores.
    mgr = legacy_trash_mgr
    mgr.add_profile("zeta", "", "windows")
    trashed_seed = mgr.profiles["zeta"].fingerprint_seed
    assert mgr.delete_profile("zeta") is True
    assert trashed_seed in mgr._reserved_seeds()  # reserved, and must stay so

    assert mgr.update_profile("legacy-acct", "zeta", "", "windows") is True

    assert mgr.profiles["zeta"].fingerprint_seed != trashed_seed
    # the reservation still holds afterwards — the point is that the rename
    # stopped walking onto the reserved value, NOT that the value got released
    assert trashed_seed in mgr._reserved_seeds()
    assert _seeds_are_unique(mgr)


def test_renaming_a_legacy_profile_keeps_the_machine_it_already_presented(
    legacy_trash_mgr,
):
    # The stability half, now extended to legacy profiles. On HEAD a pre-field
    # profile still re-rolls its whole machine on every rename — AC1 only ever
    # held for profiles created since the field existed. Freezing to the value
    # it is ALREADY presenting is what makes the collision fix safe (the set of
    # presented seeds is unchanged by the write), and this is that property
    # asserted directly: same seed, same resolution, same touch points, same
    # --fingerprint=, across a rename.
    mgr = legacy_trash_mgr
    before = _identity(mgr.profiles["legacy-acct"])
    assert before["seed"] == _crc32("legacy-acct")  # genuinely the fallback

    assert mgr.update_profile("legacy-acct", "legacy-acct-2", "", "windows") is True

    after = _identity(mgr.profiles["legacy-acct-2"])
    assert after == before, (
        f"renaming a pre-field profile moved its machine: {before} -> {after}"
    )
    assert after["seed"] != _crc32("legacy-acct-2")


def test_a_legacy_profiles_frozen_seed_survives_a_save_load_round_trip(
    legacy_trash_mgr, tmp_path
):
    # AC4 for the rename path. The freeze is written on a field the load
    # allow-list must carry; if it were dropped there the reloaded profile would
    # fall back to crc32(NEW name) — re-rolling the machine at the next restart
    # and re-opening the collision — while every in-memory assertion above
    # still passed.
    mgr = legacy_trash_mgr
    before = _identity(mgr.profiles["legacy-acct"])

    assert mgr.update_profile("legacy-acct", "legacy-acct-2", "", "windows") is True

    fresh = ProfileManager()
    reloaded = fresh.profiles["legacy-acct-2"]

    assert _identity(reloaded) == before
    assert reloaded.fingerprint_seed_value == before["seed"]
    assert reloaded.fingerprint_seed != _crc32("legacy-acct-2")
    on_disk = _json.loads((tmp_path / "profiles.json").read_text(encoding="utf-8"))
    assert on_disk["legacy-acct-2"]["fingerprint_seed_value"] == before["seed"]


def test_a_failed_rename_does_not_freeze_a_legacy_profiles_seed(
    legacy_trash_mgr, monkeypatch
):
    # AC7 for the new write. The freeze sits after the dir-rename success check,
    # so a rename that returned False must leave the profile exactly as it was —
    # still deriving from its name, with NO value written. A freeze on the False
    # path would pin the profile to a seed the operator never asked for and the
    # return value denies.
    import pathlib

    mgr = legacy_trash_mgr
    before = _identity(mgr.profiles["legacy-acct"])

    def boom(self, target):
        raise OSError("dir locked")

    monkeypatch.setattr(pathlib.Path, "rename", boom)
    assert mgr.update_profile("legacy-acct", "legacy-acct-2", "", "windows") is False

    assert mgr.profiles["legacy-acct"].fingerprint_seed_value is None
    assert _identity(mgr.profiles["legacy-acct"]) == before


def test_an_edit_that_is_not_a_rename_does_not_freeze_a_legacy_profiles_seed(
    legacy_trash_mgr,
):
    # The freeze is scoped to an actual rename. Editing a note or a proxy must
    # not quietly pin a legacy profile's seed: that would make the profile's
    # behaviour depend on whether an unrelated edit had happened since, which is
    # the same class of hidden state the load path deliberately avoids.
    mgr = legacy_trash_mgr
    before = _identity(mgr.profiles["legacy-acct"])

    assert mgr.update_profile(
        "legacy-acct", "legacy-acct", "", "windows", new_notes="edited"
    ) is True

    assert mgr.profiles["legacy-acct"].fingerprint_seed_value is None
    assert _identity(mgr.profiles["legacy-acct"]) == before
