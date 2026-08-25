"""PS-148 — a NEW profile's fingerprint seed is per-install secret.

THE DEFECT THIS PINS. `mint_fingerprint_seed()` was `zlib.crc32(name)`: a pure,
public function of a string the operator typed. Same name, same integer, on
every install and every machine. The seed is not internal bookkeeping — it
deterministically derives the PRESENTED machine (resolution, device preset,
touch points, `--fingerprint=`) — so an adversary who guessed a naming scheme
('acme-bank', 'shop1', 'client-alpha') computed that profile's presented
hardware OFFLINE, with no access to the install at all.

EVERY ASSERTION HERE BINDS TO A RETURNED SEED VALUE, never to "a secret file
exists" (AC8). That distinction is the whole point: a test asserting the secret
is present passes cheerfully against an implementation that still returns
crc32(name) and ignores the secret entirely. Reverting only the salting must
turn AC1 and AC5 RED, and it does — see PS-148's falsification run.

THE LOAD-BEARING BOUND, pinned in AC3 below. `mint_fingerprint_seed()` is on
NEITHER read path: `Profile.fingerprint_seed` returns `fingerprint_seed_value`
when set, else computes `crc32(name)` INLINE. So salting the mint moves NO
existing profile — every stored seed keeps its value, every pre-field profile
keeps its crc32(name) fallback. That is what makes this shippable without the
seed-migration that would re-roll fingerprints across the installed base.
"""

import json as _json
import os as _os
import subprocess as _subprocess
import sys as _sys
import textwrap as _textwrap
import zlib as _zlib

import pytest

from src.services.profile.manager import ProfileManager

_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))


def _crc32(s: str) -> int:
    """crc32(name) — what the mint USED to return, and what the LEGACY READ
    FALLBACK still returns. Computed independently here so a test can assert
    both that the mint has left it and that the fallback has not."""
    return _zlib.crc32(s.encode("utf-8"))


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Never touch the developer's real ~/.persona.

    The install secret resolves PERSONA_HOME AT CALL TIME, so pointing the env
    var at tmp_path is enough to keep the secret file inside the test — but the
    in-process cache is keyed by resolved path and would otherwise survive
    between tests, so it is dropped here too.
    """
    import src.core.install_secret as isec

    monkeypatch.setenv("PERSONA_HOME", str(tmp_path / "home"))
    isec.reset_cache_for_tests()
    yield
    isec.reset_cache_for_tests()


def _mint_under_install(home, name, taken=None):
    """Mint `name` as if on a SEPARATE INSTALL rooted at `home`.

    A second install is genuinely a second secret file under a second
    PERSONA_HOME, which is exactly what this sets up — the cache is dropped so
    the new home's secret is really read rather than the previous home's being
    reused. This is the only honest way to assert the cross-install property;
    comparing two mints under ONE secret would assert nothing.
    """
    import src.core.install_secret as isec
    from src.models.profile import mint_fingerprint_seed

    old = __import__("os").environ.get("PERSONA_HOME")
    __import__("os").environ["PERSONA_HOME"] = str(home)
    isec.reset_cache_for_tests()
    try:
        return mint_fingerprint_seed(name, taken)
    finally:
        if old is not None:
            __import__("os").environ["PERSONA_HOME"] = old
        isec.reset_cache_for_tests()


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    """A manager isolated onto tmp_path, trash included.

    Trash is redirected because `_reserved_seeds()` reads it, and AC4 is
    precisely a claim about what the trash reserves.
    """
    import src.core.config as cfg
    import src.services.profile.manager as mod
    from src.services.trash.store import TrashStore

    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    for m in (cfg, mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))
    m = ProfileManager()
    m.set_trash(TrashStore())
    return m


# --- AC1 + AC2: the property the slice exists for, and its premise ---------


@pytest.mark.parametrize(
    "name", ["acme-bank", "shop1", "client-alpha", "jdoe"]
)
def test_two_installs_mint_different_seeds_for_the_same_name(tmp_path, name):
    # AC1. THE property. The names are the ticket's own worked examples — a
    # guessable naming scheme is the attack, so the test uses guessable names.
    a = _mint_under_install(tmp_path / "install-a", name)
    b = _mint_under_install(tmp_path / "install-b", name)

    assert a != b, (
        f"{name!r} minted the same seed ({a}) on two independent installs, so "
        "the presented machine is still computable by anyone who guesses the "
        "name"
    )


def test_the_premise_the_mint_is_no_longer_a_pure_function_of_the_name(
    tmp_path,
):
    # AC2, the premise inversion, stated as an executable assertion rather
    # than as prose. On the pre-fix code EVERY line below was false: the mint
    # WAS crc32(name), so both installs returned crc32(name) and both equalled
    # it. This is the test that would have failed on `ba39a03`/`c0afeb0`.
    a = _mint_under_install(tmp_path / "a", "acme-bank")
    b = _mint_under_install(tmp_path / "b", "acme-bank")
    plain = _crc32("acme-bank")

    assert a != plain
    assert b != plain
    assert a != b


def test_the_seed_is_not_recoverable_from_the_name_across_many_installs(
    tmp_path,
):
    # The same claim at a scale where coincidence is not a plausible reading:
    # ten independent installs, one name, ten distinct seeds. On the pre-fix
    # code this collapsed to a single value.
    seeds = {
        _mint_under_install(tmp_path / f"install-{i}", "acme-bank")
        for i in range(10)
    }

    assert len(seeds) == 10, f"expected 10 distinct seeds, got {sorted(seeds)}"
    assert _crc32("acme-bank") not in seeds


# --- AC3: THE LOAD-BEARING BOUND — no existing profile moves ---------------


def test_a_legacy_profile_without_the_field_still_reads_crc32_of_its_name(
    tmp_path, monkeypatch
):
    # AC3, the half that matters most: a profile that predates the field must
    # keep presenting EXACTLY what it presented on origin/main. The fallback is
    # deliberately the whole migration, so on upgrade every profile on disk is
    # a derived-seed profile — if this moved, every fingerprint on every
    # install would move with it.
    #
    # The fixture OMITS the field (rather than setting it to None in memory),
    # so the load allow-list is exercised on the way through.
    import src.core.config as cfg
    import src.services.profile.manager as mod

    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    pf.write_text(
        _json.dumps({"legacy-acct": {"name": "legacy-acct", "os_type": "windows"}}),
        encoding="utf-8",
    )
    for m in (cfg, mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)

    loaded = ProfileManager().profiles["legacy-acct"]

    assert loaded.fingerprint_seed_value is None  # genuinely pre-field
    assert loaded.fingerprint_seed == _crc32("legacy-acct")


def test_a_stored_seed_is_returned_unchanged_whatever_the_install_secret_is(
    tmp_path,
):
    # AC3, the other half: a profile that already froze a seed returns THAT
    # value, and the salted mint has no say in it. Asserted across two
    # different install secrets to show the read path never consults one.
    from src.models.profile import Profile

    p = Profile(name="acme-bank", fingerprint_seed_value=12345)

    assert _mint_under_install(tmp_path / "a", "x") != _mint_under_install(
        tmp_path / "b", "x"
    )  # the secrets really do differ
    assert p.fingerprint_seed == 12345  # ...and the stored seed ignores both


def test_the_read_path_is_byte_identical_to_main_for_pre_field_profiles():
    # AC3/AC9 as arithmetic, independent of any fixture: the fallback formula
    # is unchanged, so these are the exact integers origin/main served. Written
    # as literals rather than as crc32() calls so that re-pointing the fallback
    # at the salted derivation would fail here even if crc32 were also changed.
    from src.models.profile import Profile

    assert Profile(name="acme-bank").fingerprint_seed == 2170035726
    assert Profile(name="shop1").fingerprint_seed == 3148367679
    assert Profile(name="persona-fingerprint-baseline").fingerprint_seed == 1042768975


# --- AC4: the trap — _reserved_seeds() must still reserve the fallback -----


def test_a_pre_field_trashed_record_still_reserves_the_seed_a_restore_lands_on(
    mgr, tmp_path
):
    # AC4, THE TRAP, driven through the REAL MANAGER rather than by inspecting
    # the call site.
    #
    # WHY THIS IS THE MOST DANGEROUS PART OF THE SLICE. `_reserved_seeds()`
    # used to model the pre-field restore value by CALLING the mint — correct
    # only while the mint equalled crc32(name). Salt the mint and that line
    # silently stops modelling the fallback: it reserves a value nothing
    # restores onto, leaves the real crc32(name) free, and a live profile is
    # then handed the very seed the trashed record will restore onto. Two live
    # profiles, one presented machine — the exact isolation failure the
    # reservation exists to prevent, REINTRODUCED BY THE SECRECY FIX.
    from src.services.trash.store import KIND_PROFILE

    # a pre-field trashed record: a payload with a name and NO seed field,
    # exactly as a build predating the field would have written it
    mgr._trash().add(KIND_PROFILE, "ghost", {"name": "ghost", "os_type": "windows"})

    # the value such a record restores onto is the LEGACY FALLBACK, and it must
    # be held aside now — it cannot be re-minted later, because restore rebuilds
    # the record verbatim
    assert _crc32("ghost") in mgr._reserved_seeds()

    # ...and the consequence, which is what actually matters: a live profile
    # created afterwards must not be sitting on it
    mgr.add_profile("ghost", "", "windows")
    assert mgr.profiles["ghost"].fingerprint_seed != _crc32("ghost")


def test_a_trashed_legacy_profile_and_a_live_one_do_not_share_a_machine(mgr):
    # AC4 as the operator-visible event, through the real doors end to end:
    # trash a legacy-shaped record, create a live profile of the same name,
    # and assert the two do not present one machine. This is the sequence the
    # codebase's own documented remedy ("free the name and restore again")
    # walks into.
    from src.services.trash.store import KIND_PROFILE

    mgr._trash().add(KIND_PROFILE, "alpha", {"name": "alpha", "os_type": "windows"})
    mgr.add_profile("alpha", "", "windows")

    live_seed = mgr.profiles["alpha"].fingerprint_seed
    restored_seed = _crc32("alpha")  # what the trashed record comes back on

    assert live_seed != restored_seed


# --- AC5: the collision walk is salted too --------------------------------


def test_the_collision_walk_is_salted_across_installs(tmp_path):
    # AC5. A salted mint whose WALK fell back to plain crc32(f"{name}:{n}")
    # would leak the scheme straight back — and it would leak it on exactly the
    # profiles an operator creates most deliberately, since the walk is the
    # branch the "archive last quarter's account, reuse the label" workflow
    # takes.
    #
    # The `taken` set is the SAME on both installs and is built so the walk is
    # genuinely entered on each: each install's own first-choice value is
    # taken, plus the unsalted values, so a walk that ignored the secret would
    # land on crc32(name:n) and be caught below.
    name = "acme-bank"
    a_home, b_home = tmp_path / "a", tmp_path / "b"

    a_first = _mint_under_install(a_home, name)
    b_first = _mint_under_install(b_home, name)
    taken = {a_first, b_first, _crc32(name), _crc32(f"{name}:1")}

    a_walk = _mint_under_install(a_home, name, taken)
    b_walk = _mint_under_install(b_home, name, taken)

    # the walk was really entered, and really avoided the taken values
    assert a_walk not in taken
    assert b_walk not in taken
    # AC5 proper: same name, same taken set, DIFFERENT installs -> different walk
    assert a_walk != b_walk
    # and it is not the unsalted walk value either
    assert a_walk != _crc32(f"{name}:1")
    assert a_walk != _crc32(f"{name}:2")


def test_the_walk_keeps_finding_a_free_seed_as_the_taken_set_grows(tmp_path):
    # The walk's own invariant, unchanged by salting: it terminates on a value
    # nothing holds, however many are held. Salting must not have turned the
    # walk into a loop that revisits the same value.
    from src.models.profile import mint_fingerprint_seed

    taken: set[int] = set()
    for _ in range(25):
        seed = mint_fingerprint_seed("acme-bank", taken)
        assert seed not in taken
        taken.add(seed)

    assert len(taken) == 25


# --- AC6: determinism within one install ----------------------------------


def test_the_mint_is_deterministic_within_one_install():
    # AC6. No RNG at call time — the entropy was spent ONCE, when the install
    # secret file was created. This is what keeps a profile's presented machine
    # stable, and it is why the secret is persisted rather than generated per
    # process.
    from src.models.profile import mint_fingerprint_seed

    assert mint_fingerprint_seed("acme-bank") == mint_fingerprint_seed("acme-bank")

    taken = {mint_fingerprint_seed("acme-bank")}
    walked = mint_fingerprint_seed("acme-bank", taken)
    assert mint_fingerprint_seed("acme-bank", taken) == walked
    assert mint_fingerprint_seed("acme-bank", set(taken)) == walked


def test_the_seed_survives_a_process_restart_because_the_secret_is_persisted(
    tmp_path,
):
    # AC6's real-world shape: the same install, a LATER process. A secret
    # generated per-process instead of persisted would silently re-roll the
    # mint on every restart — invisible to the assertion above, which never
    # leaves one process.
    import src.core.install_secret as isec
    from src.models.profile import mint_fingerprint_seed

    home = tmp_path / "one-install"
    first = _mint_under_install(home, "acme-bank")

    # model a restart: the in-process cache is gone, the FILE is not
    import os

    os.environ["PERSONA_HOME"] = str(home)
    isec.reset_cache_for_tests()
    second = mint_fingerprint_seed("acme-bank")

    assert second == first


# --- AC7: the secret never leaves the machine -----------------------------


def test_the_install_secret_is_absent_from_the_serialized_profile(mgr, tmp_path):
    # AC7. The secret is identity material: a wrong home would trade a
    # guessable seed for a readable one. It is not a Profile field, so it
    # cannot reach to_dict() — this asserts that structurally rather than
    # trusting it.
    import src.core.install_secret as isec

    mgr.add_profile("acme-bank", "", "windows")
    secret = isec.install_secret()

    payload = mgr.profiles["acme-bank"].to_dict()
    blob = _json.dumps(payload)

    assert secret.hex() not in blob
    assert repr(secret) not in blob
    assert not any("secret" in k.lower() for k in payload)
    # the on-disk store, too
    on_disk = (tmp_path / "profiles.json").read_text(encoding="utf-8")
    assert secret.hex() not in on_disk


def test_the_install_secret_is_absent_from_an_exported_archive(mgr, tmp_path):
    # AC7 for the export door — the one place a profile's bytes deliberately
    # LEAVE the machine, which is exactly why it is checked rather than
    # assumed.
    import zipfile

    import src.core.install_secret as isec

    mgr.add_profile("acme-bank", "", "windows")
    secret = isec.install_secret()

    outdir = tmp_path / "out"
    outdir.mkdir()
    ok, archive = mgr.export_profile("acme-bank", str(outdir), include_data=False)
    assert ok is True, archive

    with zipfile.ZipFile(archive) as zf:
        for entry in zf.namelist():
            raw = zf.read(entry)
            assert secret not in raw, f"the install secret leaked into {entry}"
            assert secret.hex().encode() not in raw


def test_the_install_secret_is_absent_from_the_logs(mgr, caplog, tmp_path):
    # AC7 for the log door. The failure paths in install_secret name the PATH
    # and never the bytes; this pins that, plus the create+mint path generally.
    import logging

    import src.core.install_secret as isec

    with caplog.at_level(logging.DEBUG):
        secret = isec.install_secret()
        mgr.add_profile("acme-bank", "", "windows")
        mgr.delete_profile("acme-bank")

    text = caplog.text
    assert secret.hex() not in text
    assert str(secret) not in text


@pytest.mark.skipif(_sys.platform == "win32", reason="POSIX permission bits")
def test_the_secret_file_is_not_world_readable(tmp_path):
    # AC7's on-disk half: a secret at mode 0644 is a secret an unprivileged
    # local process reads, which would trade a guessable seed for a readable
    # one — the ticket's own words.
    #
    # POSIX-ONLY, and the skip is a real bound rather than a tidy-up. The
    # `os.chmod(tmp, 0o600)` this asserts is a NO-OP on Windows, which has no
    # POSIX mode bits — the mode reads back 0o666 and the assertion fails on a
    # correct implementation. Confining the secret on Windows means an ACL
    # (icacls / pywin32), which this slice does not attempt, so the honest
    # statement is: the 0600 guarantee is POSIX-only and the Windows file sits
    # at whatever the parent directory's ACL grants. Skipping matches the
    # repo's standing convention for mode-bit assertions (test_cert_store.py,
    # test_ssh_store.py, test_trash_store.py and five more use this exact
    # marker); it does NOT weaken the POSIX assertion, which still runs on the
    # two platforms where the bits exist.
    import os
    import stat

    import src.core.install_secret as isec

    isec.install_secret()
    mode = stat.S_IMODE(os.stat(isec._path()).st_mode)

    assert mode & stat.S_IRWXO == 0, f"world bits set: {oct(mode)}"
    assert mode & stat.S_IRWXG == 0, f"group bits set: {oct(mode)}"


# --- AC10: the import path -------------------------------------------------


def _archive_with_seed(tmp_path, name, seed):
    """A hand-authored profile archive — i.e. untrusted input, which is what a
    shared profile is."""
    import zipfile

    payload = {
        "name": name,
        "os_type": "windows",
        "engine": "chromium",
        "device_type": "desktop",
        "resolution": "auto",
    }
    if seed is not None:
        payload["fingerprint_seed_value"] = seed
    path = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("profile.json", _json.dumps(payload))
    return str(path)


def test_a_non_colliding_import_keeps_its_carried_seed_untouched(mgr, tmp_path):
    # AC10, the AC3 BOUNDARY — and the more important half. "Move my profile to
    # another machine" is the whole point of exporting one, so an import whose
    # seed is free must carry its identity across UNCHANGED. Re-minting here
    # would move a fingerprint the operator never asked to move, on the very
    # path that exists to preserve it.
    archive = _archive_with_seed(tmp_path, "carried", 4242)
    ok, name = mgr.import_profile(archive)
    assert ok is True, name

    assert mgr.profiles["carried"].fingerprint_seed_value == 4242
    assert mgr.profiles["carried"].fingerprint_seed == 4242


def test_a_colliding_import_is_re_minted_onto_a_salted_value(mgr, tmp_path):
    # AC10 proper. A carried seed already held by a live profile is re-minted —
    # and since that IS a genuine mint (a new value being born, not a legacy
    # value being modelled) it must be salted like the create path.
    mgr.add_profile("victim", "", "windows")
    victim_seed = mgr.profiles["victim"].fingerprint_seed

    archive = _archive_with_seed(tmp_path, "attacker", victim_seed)
    ok, name = mgr.import_profile(archive)
    assert ok is True, name

    attacker_seed = mgr.profiles["attacker"].fingerprint_seed
    # it did not land on the victim's machine
    assert attacker_seed != victim_seed
    # the victim did not move
    assert mgr.profiles["victim"].fingerprint_seed == victim_seed
    # and the re-mint is salted, not the guessable crc32 of the name
    assert attacker_seed != _crc32("attacker")
    assert attacker_seed != _crc32("attacker:1")


def test_a_colliding_import_re_mints_differently_on_two_installs(
    tmp_path, monkeypatch
):
    # AC10 x AC1: the re-mint really is per-install, asserted on the RETURNED
    # SEED. Two installs are given identical inputs — same archive bytes, same
    # colliding name — and must produce different values.
    import src.core.config as cfg
    import src.core.install_secret as isec
    import src.services.profile.manager as mod
    from src.services.trash.store import TrashStore

    def _import_on(home):
        pf, dd = home / "profiles.json", home / "data"
        home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("PERSONA_HOME", str(home))
        monkeypatch.setenv("PERSONA_TRASH_FILE", str(home / "trash.json"))
        for m in (cfg, mod):
            monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
            monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
        isec.reset_cache_for_tests()
        m = ProfileManager()
        m.set_trash(TrashStore())
        m.add_profile("victim", "", "windows")
        seed = m.profiles["victim"].fingerprint_seed
        archive = _archive_with_seed(home, "attacker", seed)
        ok, name = m.import_profile(archive)
        assert ok is True, name
        return m.profiles["attacker"].fingerprint_seed

    a = _import_on(tmp_path / "install-a")
    b = _import_on(tmp_path / "install-b")

    assert a != b


# --- PS-167: the secret follows config's fallback like every other data file --
#
# THE DEFECT THESE PIN. `config._ensure_home` falls back to ~/.persona when the
# configured PERSONA_HOME cannot be created, and states the contract: "Returns
# the directory actually in use, so every path below is derived from the home
# that really exists." `install_secret._path()` was the ONE runtime data path
# that was not — it re-read PERSONA_HOME from the environment and joined the
# REQUESTED home, the one `_ensure_home` had just proved could not be made. So
# `_create` raised, the handler returned a process-lifetime secret, and that
# repeated EVERY LAUNCH, permanently, while all the install's real data
# persisted happily in the fallback home.
#
# These assert on BYTES READ BACK IN A SECOND PROCESS, never that a helper was
# called — a call-shape assertion passes against an inert implementation.


def _isec_filename():
    import src.core.install_secret as isec

    return isec._SECRET_FILENAME


def _blocked_home(tmp_path):
    """A PERSONA_HOME that genuinely cannot be created: its PARENT IS A FILE, so
    `os.makedirs` raises NotADirectoryError rather than us mocking a failure.
    Returns (unmakeable_home, sandbox_HOME) — the caller must redirect HOME to
    the latter, or the fallback writes into the developer's real ~/.persona."""
    sandbox_home = tmp_path / "real-home"
    sandbox_home.mkdir(parents=True, exist_ok=True)
    parent_that_is_a_file = tmp_path / "iamafile"
    parent_that_is_a_file.write_text("a file, not a directory")
    return parent_that_is_a_file / "persona", sandbox_home


def test_an_uncreatable_home_persists_the_secret_where_config_fell_back(
    tmp_path, monkeypatch
):
    # AC1. The secret lands in the home that REALLY EXISTS, and a second
    # process reads THE SAME BYTES. On origin/main this is RED: nothing is
    # persisted at all and the second read is fresh entropy.
    import src.core.install_secret as isec

    blocked, sandbox_home = _blocked_home(tmp_path)
    monkeypatch.setenv("HOME", str(sandbox_home))
    monkeypatch.setenv("USERPROFILE", str(sandbox_home))  # expanduser on Windows
    monkeypatch.setenv("PERSONA_HOME", str(blocked))
    monkeypatch.delenv("PERSONA_INSTALL_SECRET_FILE", raising=False)

    isec.reset_cache_for_tests()
    first = isec.install_secret()

    # It is ON DISK, in the fallback home, and it is the bytes we were handed.
    landed = sandbox_home / ".persona" / _isec_filename()
    assert landed.exists(), "the secret was never persisted anywhere"
    assert landed.read_bytes() == first

    # ...and it did NOT land under the home that could not be made.
    assert not (blocked / _isec_filename()).exists()

    # A SECOND PROCESS: caches gone, the FILE is not. This is the assertion the
    # ticket is about — bytes read back, not a helper call.
    isec.reset_cache_for_tests()
    assert isec.install_secret() == first


def test_two_creatable_homes_keep_separate_secret_files(tmp_path, monkeypatch):
    # AC3 at the layer this ticket actually changed: the same isolation stated
    # about the SECRET rather than the seed derived from it, so a regression in
    # `_path` is caught here even if the mint were to stop consuming it.
    import src.core.install_secret as isec

    monkeypatch.delenv("PERSONA_INSTALL_SECRET_FILE", raising=False)
    secrets_by_home = {}
    for leaf in ("install-a", "install-b"):
        home = tmp_path / leaf
        home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("PERSONA_HOME", str(home))
        isec.reset_cache_for_tests()
        secrets_by_home[leaf] = isec.install_secret()
        assert (home / isec._SECRET_FILENAME).exists()  # its OWN file

    assert secrets_by_home["install-a"] != secrets_by_home["install-b"]


def test_the_explicit_secret_file_override_still_wins_over_the_fallback(
    tmp_path, monkeypatch
):
    # AC4. The override is the operator's most explicit instruction, so an
    # unmakeable PERSONA_HOME must not redirect it into ~/.persona.
    import src.core.install_secret as isec

    blocked, sandbox_home = _blocked_home(tmp_path)
    chosen = tmp_path / "elsewhere" / "my_secret"
    monkeypatch.setenv("HOME", str(sandbox_home))
    monkeypatch.setenv("USERPROFILE", str(sandbox_home))
    monkeypatch.setenv("PERSONA_HOME", str(blocked))
    monkeypatch.setenv("PERSONA_INSTALL_SECRET_FILE", str(chosen))

    isec.reset_cache_for_tests()
    assert isec._path() == str(chosen)

    secret = isec.install_secret()
    assert chosen.read_bytes() == secret
    assert not (sandbox_home / ".persona" / isec._SECRET_FILENAME).exists()


def test_the_could_not_persist_error_stops_firing_under_an_uncreatable_home(
    tmp_path, monkeypatch, caplog
):
    # AC7 of PS-167, and the OBSERVABLE proof that the persistence guarantee
    # now holds: the operator's symptom was an install_secret error on every
    # single launch. It is stronger evidence than any assertion about internals
    # — if this line is absent, the secret really was written.
    #
    # The CONFIG line is deliberately NOT asserted absent: PERSONA_HOME still
    # cannot be created and the operator should still be told so, exactly once.
    import logging

    import src.core.install_secret as isec

    blocked, sandbox_home = _blocked_home(tmp_path)
    monkeypatch.setenv("HOME", str(sandbox_home))
    monkeypatch.setenv("USERPROFILE", str(sandbox_home))
    monkeypatch.setenv("PERSONA_HOME", str(blocked))
    monkeypatch.delenv("PERSONA_INSTALL_SECRET_FILE", raising=False)

    isec.reset_cache_for_tests()
    with caplog.at_level(logging.ERROR, logger="install_secret"):
        isec.install_secret()
        isec.reset_cache_for_tests()
        isec.install_secret()  # a second launch: still no complaint

    offending = [r for r in caplog.records if r.name == "install_secret"]
    assert offending == [], (
        f"still failing to persist: {[r.getMessage() for r in offending]}"
    )


def _launch_under_unmakeable_home(tmp_path, body):
    """ONE REAL LAUNCH: a fresh interpreter whose PERSONA_HOME cannot be created,
    with `HOME` redirected into the sandbox so the fallback cannot touch the
    developer's real ~/.persona. Returns the CompletedProcess.

    WHY A SUBPROCESS IS NOT OPTIONAL HERE — this is the whole point of the
    harness, so it is stated rather than left to be rediscovered. The cadence
    fix has two branches in `install_secret._existing_home`, and WHICH ONE FIRES
    IS DECIDED BY WHETHER `core.config` WAS IMPORTED BEFORE OR AFTER
    `PERSONA_HOME` WAS SET:

      * A REAL LAUNCH sets the env first, so `config` binds `_REQUESTED_HOME` to
        the unmakeable home and `_existing_home` takes the REUSE branch —
        reusing config's already-resolved answer and emitting NO second line.
      * ANY IN-PROCESS TEST is the reverse. `core.config` is imported at
        collection with the developer's real HOME, so `_REQUESTED_HOME` is
        `~/.persona` and never equals a `tmp_path` home. The reuse branch is
        therefore STRUCTURALLY UNREACHABLE from in-process pytest, whatever the
        monkeypatching, and the `else` (resolve-it-here) branch always fires.

    So an in-process caplog test cannot observe the reuse branch AT ALL: delete
    it outright and the in-process suite stays entirely green while a real
    launch starts logging the operator's error TWICE (measured 1 -> 2). That is
    exactly the PS-11 shape — an assertion whose name claims more than its
    mechanism can observe — and it is why this test pays for a subprocess.
    """
    sandbox_home = tmp_path / "real-home"
    sandbox_home.mkdir(parents=True, exist_ok=True)
    parent_that_is_a_file = tmp_path / "iamafile"
    parent_that_is_a_file.write_text("a file, not a directory")

    code = "import src.core.install_secret as isec\n" + _textwrap.dedent(body).strip()
    env = dict(
        _os.environ,
        HOME=str(sandbox_home),
        USERPROFILE=str(sandbox_home),
        PERSONA_HOME=str(parent_that_is_a_file / "persona"),
        PYTHONPATH=_REPO_ROOT,
    )
    env.pop("PERSONA_INSTALL_SECRET_FILE", None)
    proc = _subprocess.run(
        [_sys.executable, "-c", code],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return proc, sandbox_home


def test_a_real_launch_reports_the_unmakeable_home_exactly_once(tmp_path):
    # THE CADENCE GUARD, asserted where it is actually observable (PS-167
    # consequence #1: "do not trade a silent defect for log spam").
    #
    # This is the test that defends the cross-module coupling — `config`
    # exposing `_REQUESTED_HOME` so `install_secret` can reuse its resolved
    # answer instead of re-deriving it. Without that branch a real launch emits
    # the operator's "could not be created" error TWICE: once from config's
    # import, once more from `_path()` re-resolving the same home. Measured
    # 1 -> 2 by deleting the branch, which the in-process suite calls GREEN.
    #
    # It asserts on LINES ON STDERR FROM A REAL LAUNCH, never that a helper was
    # called — a call-shape assertion passes against an inert implementation.
    proc, _ = _launch_under_unmakeable_home(
        tmp_path,
        "isec.install_secret()\nisec.reset_cache_for_tests()\nisec.install_secret()",
    )

    complaints = [
        line for line in proc.stderr.splitlines() if "could not be created" in line
    ]
    assert len(complaints) == 1, (
        f"a launch under an unmakeable home must tell the operator ONCE, not "
        f"{len(complaints)} times:\n" + "\n".join(complaints)
    )


def test_a_real_launch_does_not_re_log_however_many_secrets_it_mints(tmp_path):
    # The same guard against the OTHER regression: the memo in `_existing_home`.
    # `_path()` runs on every mint, so an unmemoised routing through the
    # effectful `_ensure_home` makes the operator's log volume scale with how
    # many profiles they create. 25 mints, still one line.
    proc, _ = _launch_under_unmakeable_home(
        tmp_path,
        "\n".join(
            [
                "for _ in range(25):",
                "    isec.reset_cache_for_tests()",
                "    isec.install_secret()",
            ]
        ),
    )

    complaints = [
        line for line in proc.stderr.splitlines() if "could not be created" in line
    ]
    assert len(complaints) == 1, (
        f"{len(complaints)} error lines for 25 mints — the operator's log volume "
        "must not scale with profile count"
    )


def test_repeated_mints_reuse_one_resolved_home_within_a_process(
    tmp_path, monkeypatch, caplog
):
    # NARROWED AND RENAMED (was test_repeated_mints_do_not_re_log_the_unmakeable
    # _home, which claimed the launch-wide cadence this CANNOT observe — see
    # `_launch_under_unmakeable_home` for why the reuse branch is unreachable
    # in-process). What it pins is real and worth keeping: the `_home_cache`
    # memo, so 25 `_path()` calls resolve the home ONCE rather than 25 times.
    # The launch-wide cadence is pinned by the two subprocess tests above.
    import logging

    import src.core.config as cfg
    import src.core.install_secret as isec

    blocked, sandbox_home = _blocked_home(tmp_path)
    monkeypatch.setenv("HOME", str(sandbox_home))
    monkeypatch.setenv("USERPROFILE", str(sandbox_home))
    monkeypatch.setenv("PERSONA_HOME", str(blocked))
    monkeypatch.delenv("PERSONA_INSTALL_SECRET_FILE", raising=False)

    resolutions = []
    real_ensure_home = cfg._ensure_home
    monkeypatch.setattr(
        cfg,
        "_ensure_home",
        lambda p: (resolutions.append(p), real_ensure_home(p))[1],
    )

    isec.reset_cache_for_tests()
    with caplog.at_level(logging.ERROR, logger="config"):
        for _ in range(25):
            isec._path()

    # The memo held: one resolution, and therefore one error line, for 25 calls.
    assert len(resolutions) == 1, f"{len(resolutions)} resolutions for 25 calls"
    complaints = [r for r in caplog.records if "could not be created" in r.getMessage()]
    assert len(complaints) == 1, f"{len(complaints)} error lines for 25 calls"


def test_two_installs_that_both_fail_to_make_their_homes_share_one_secret(
    tmp_path, monkeypatch
):
    # THE DELIBERATE BEHAVIOUR CHANGE, pinned so it is a decision rather than a
    # discovery (PS-167 consequence #3). Two installs whose configured homes
    # BOTH cannot be created now resolve to the same ~/.persona and therefore
    # share one secret.
    #
    # This is believed CORRECT, and the reason is the control in the ticket's
    # transcript: those two installs are already sharing ~/.persona for
    # profiles.json and every other data file, because that is where config put
    # them. Sharing the secret matches where their data actually lives. The
    # alternative — inventing a third, per-request home just for the secret —
    # would put the secret somewhere none of their other data is.
    #
    # NOTE THE BOUND: this is emphatically NOT the isolation property. Two
    # CREATABLE homes still mint different secrets — pinned directly above in
    # test_two_creatable_homes_keep_separate_secret_files, which is the test
    # that would catch a fix that over-reached.
    import src.core.install_secret as isec

    sandbox_home = tmp_path / "real-home"
    sandbox_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(sandbox_home))
    monkeypatch.setenv("USERPROFILE", str(sandbox_home))
    monkeypatch.delenv("PERSONA_INSTALL_SECRET_FILE", raising=False)

    secrets_seen = []
    for leaf in ("blocked-a", "blocked-b"):
        parent_that_is_a_file = tmp_path / leaf
        parent_that_is_a_file.write_text("a file, not a directory")
        monkeypatch.setenv("PERSONA_HOME", str(parent_that_is_a_file / "persona"))
        isec.reset_cache_for_tests()
        secrets_seen.append(isec.install_secret())

    # Same bytes, because both genuinely run out of ~/.persona.
    assert secrets_seen[0] == secrets_seen[1]
    assert (sandbox_home / ".persona" / isec._SECRET_FILENAME).read_bytes() == (
        secrets_seen[0]
    )
