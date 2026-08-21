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
            getattr(profile, "resolution", "auto"), seed
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
    before = pick_preset(mgr.profiles["acme-bank-jdoe"].fingerprint_seed, "ios")

    mgr.update_profile(
        "acme-bank-jdoe",
        "acme-bank-jdoe-2",
        "",
        "ios",
        new_device_type="mobile",
        new_engine="chromium",
    )

    after = pick_preset(
        mgr.profiles["acme-bank-jdoe-2"].fingerprint_seed, "ios"
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
    assert resolve_resolution("auto", legacy.fingerprint_seed) == (1440, 900)
    assert pick_preset(legacy.fingerprint_seed, "ios").key == "iphone-14"


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
    assert resolve_resolution("auto", loaded.fingerprint_seed) == (1440, 900)


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
