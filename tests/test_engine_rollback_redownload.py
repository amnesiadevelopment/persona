"""PS-79: a SUCCESSFUL Chromium engine swap must be reversible — by
RE-DOWNLOADING the previous build, not by keeping a copy of it on disk.

Owner decision, 2026-08-23: keeping a second ~300-600MB engine tree on every
operator's disk forever, against an event that has never been observed, is not
worth it. A rollback re-downloads.

So THE BYTES WERE NEVER THE PROBLEM — THE NAME WAS. version.txt has exactly one
slot (updater.write_version), so the moment a swap succeeds the tag of the build
that was working is overwritten and exists nowhere on the machine. An operator
facing a bad unattended upgrade could not roll back, not because the old tree
was deleted, but because nothing recorded WHICH build to go back to.

WHAT THESE TESTS ASSERT, AND WHAT THEY DELIBERATELY DO NOT
----------------------------------------------------------
Every test here asserts OBSERVABLE STATE — what current_version() reports and
what bytes are in ENGINE_DIR after a real swap and a real revert — never that
some helper was called. A test that asserts "record_installed_build was called"
passes against an implementation that records the wrong tag, records it after
version.txt is already overwritten, or writes a record nothing can read back.

The Windows path is driven end to end over a real multi-file zip (as PS-38's
tests do) because _force_os + a real zipfile make that reachable in ANY
container. macOS is NOT faked: _install_macos shells out to hdiutil/ditto, which
are absent here, so the shared helpers are unit-tested instead and the PR states
plainly which paths ran end to end.

NOTE WHAT IS NOT TOUCHED. The success-path discard_aside calls are CORRECT and
unchanged: after a successful promotion ENGINE_DIR still holds the new build and
nothing else. PS-38's test_successful_windows_upgrade_leaves_no_backup_behind
passes UNMODIFIED, and test_the_swap_still_destroys_the_previous_tree below
re-asserts that from this ticket's side — the two are in agreement, not tension.
"""

import json
import os

import pytest

import src.core.platform as _platform
from src.services.engine import updater
from src.ui import app as _app_mod
from src.ui import progress_fmt as pf
from src.utils.httpdl import normalize_digest


def _target_is(tag: str, digest: str) -> bool:
    """rollback_target() equals (tag, digest), compared through the digest's
    CANONICAL form.

    The record stores the bare-hex form ("sha256:" stripped) and digest_ok
    normalizes both sides before comparing, so the two spellings verify
    identically. Pinning the literal string here would assert a storage detail
    instead of the property that matters — which build, and which digest.
    """
    got_tag, got_digest = updater.rollback_target()
    return got_tag == tag and normalize_digest(got_digest) == normalize_digest(
        digest
    )


def _force_os(monkeypatch, *, win=False, mac=False, linux=False):
    monkeypatch.setattr(_platform, "IS_WINDOWS", win)
    monkeypatch.setattr(_platform, "IS_MACOS", mac)
    monkeypatch.setattr(_platform, "IS_LINUX", linux)


def _make_windows_zip(path, members):
    import zipfile

    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def _build_zip(path, marker):
    """A whole, runnable-looking Windows engine tree whose bytes NAME the build,
    so a later assertion can tell WHICH build is installed by reading them."""
    _make_windows_zip(
        path,
        {
            "chrome-win/chrome.exe": b"MZ" + marker,
            "chrome-win/some.dll": b"DLL-" + marker,
            "chrome-win/locales/en.pak": b"PAK-" + marker,
        },
    )


class _Engine:
    """A repointed ENGINE_DIR with every module constant that hangs off it.

    ENGINE_DIR is read at import time into VERSION_FILE / MARKER_FILE /
    BUILDS_FILE / ENGINE_BINARY, so repointing the directory alone would leave
    four constants aimed at the operator's REAL engine dir.
    """

    def __init__(self, monkeypatch, tmp_path):
        self.dir = tmp_path / "engine"
        self.dir.mkdir()
        monkeypatch.setattr(updater, "ENGINE_DIR", str(self.dir))
        monkeypatch.setattr(updater, "ENGINE_BINARY", str(self.dir / "chrome.exe"))
        monkeypatch.setattr(updater, "VERSION_FILE", str(self.dir / "version.txt"))
        monkeypatch.setattr(updater, "BUILDS_FILE", str(self.dir / "builds.json"))
        monkeypatch.setattr(
            updater, "MARKER_FILE", str(self.dir / ".engine-complete")
        )

    def installed_marker(self) -> bytes:
        """WHICH build is actually on disk, read out of the engine's own bytes
        rather than out of any record that claims to describe it."""
        return (self.dir / "chrome.exe").read_bytes()[2:]

    def entries(self) -> set:
        return {p.name for p in self.dir.iterdir()}


class _FakeApp:
    """The narrowest stand-in for App that _auto_update_engine actually reads.

    ⚠️ `_engine_update_available` IS THE REAL METHOD, bound off the class — NOT a
    stub. That predicate is where the pin gate lives, and it is the single thing
    these tests exist to exercise: stubbing it would leave a test that drives
    _auto_update_engine, asserts nothing was re-installed, and would keep passing
    with the pin gate deleted. Everything else here is inert scaffolding.

    `_update_engine_async` RECORDS rather than performs, so a failure names
    itself ("the check tried to re-install") instead of silently reinstalling
    the build the operator just rolled back from.
    """

    _engine_busy = False
    _engine_checking = False
    _engine_latest = "149.0.1"
    _engine_unverifiable_tag = ""
    _engine_deferred_tag = ""
    _engine_status = ""

    # the real predicate, pin gate and all
    _engine_update_available = _app_mod.App._engine_update_available

    def __init__(self):
        self.updates_started = []

    def _log(self, *a, **k):
        pass

    def _engine_tree_in_use(self):
        return False

    def _update_engine_async(self, unattended=False):
        self.updates_started.append(unattended)


@pytest.fixture
def eng(monkeypatch, tmp_path):
    _force_os(monkeypatch, win=True)
    e = _Engine(monkeypatch, tmp_path)
    # No profile is running, so the in-use guard never defers. Wired explicitly
    # because _engine_in_use fails CLOSED on an unwired provider — an unwired
    # oracle would make every revert here defer and the tests would pass for
    # entirely the wrong reason.
    updater.set_in_use_provider(lambda: False)
    yield e
    updater.set_in_use_provider(None)


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    """Point the settings store at a temp file so a pin written by a test
    cannot touch the developer's real ~/.persona/settings.json."""
    from src.core import settings

    monkeypatch.setattr(settings, "_path", lambda: str(tmp_path / "settings.json"))
    yield


def _install(eng, monkeypatch, tag, marker, digest, tmp_path):
    """Perform a REAL install of `tag` through download_engine — a real zip, a
    real extract, a real _promote_staging into ENGINE_DIR — and record it the
    way the production callers do.

    The only thing faked is the network: _download_to drops the zip where the
    transfer would have. Everything downstream of the bytes arriving is real,
    which is the half that matters here.
    """
    zip_path = tmp_path / f"{tag}.zip"
    _build_zip(zip_path, marker)

    def fake_download_to(path, url, timeout, dg, progress, allow_missing=False):
        import shutil as _sh

        _sh.copyfile(zip_path, path)
        return True

    monkeypatch.setattr(updater, "_download_to", fake_download_to)
    ok = updater.download_engine(f"http://x/{tag}.zip", digest=digest, tag=tag)
    assert ok is True, f"the {tag} install itself failed"
    # The production ordering, asserted by being USED here: record BEFORE
    # write_version, because version.txt's single slot is what destroys the
    # outgoing identity.
    updater.record_installed_build(tag, digest)
    updater.write_version(tag)


# --- the defect this ticket exists to close ---------------------------------


def test_the_swap_still_destroys_the_previous_tree(eng, monkeypatch, tmp_path):
    """The tree really is gone after a successful swap — this ticket does NOT
    change that, and this test is here so nobody later "fixes" the rollback by
    quietly retaining a copy.

    This is the same fact PS-38's test_successful_windows_upgrade_leaves_no_
    backup_behind pins, asserted from this ticket's side. The two agree.
    """
    _install(eng, monkeypatch, "148.0.1", b"OLD", "sha256:" + "a" * 64, tmp_path)
    _install(eng, monkeypatch, "149.0.1", b"NEW", "sha256:" + "b" * 64, tmp_path)

    assert eng.installed_marker() == b"NEW"
    # not one byte of the old build survives anywhere under ENGINE_DIR
    assert not any(
        b"OLD" in p.read_bytes()
        for p in eng.dir.rglob("*")
        if p.is_file() and p.name != "builds.json"
    )
    assert updater.BACKUP_NAME not in eng.entries()


def test_the_identity_of_the_replaced_build_survives_the_swap(
    eng, monkeypatch, tmp_path
):
    """THE FIX, stated as the smallest true thing: the TREE is gone but the
    NAME is not. A few hundred bytes, not a second engine."""
    _install(eng, monkeypatch, "148.0.1", b"OLD", "sha256:" + "a" * 64, tmp_path)
    _install(eng, monkeypatch, "149.0.1", b"NEW", "sha256:" + "b" * 64, tmp_path)

    tag, digest = updater.rollback_target()
    assert tag == "148.0.1"
    assert normalize_digest(digest) == normalize_digest("sha256:" + "a" * 64)
    # and the record is genuinely small — this is the whole point of the design
    assert (eng.dir / "builds.json").stat().st_size < 4096


# --- the reversal itself, end to end ----------------------------------------


def _serve(monkeypatch, catalogue, tmp_path, seen=None):
    """Stand upstream up: `catalogue` maps tag -> (marker, digest), and anything
    absent from it is a YANKED release (fetch_release_full answers '','','')."""

    def fake_fetch_release_full(tag, timeout=20):
        if tag not in catalogue:
            return "", "", ""
        _marker, digest = catalogue[tag]
        return tag, f"http://x/{tag}.zip", digest

    def fake_download_to(path, url, timeout, dg, progress, allow_missing=False):
        import shutil as _sh

        tag = url.rsplit("/", 1)[-1][: -len(".zip")]
        if seen is not None:
            seen.append((tag, dg))
        marker, _digest = catalogue[tag]
        zip_path = tmp_path / f"serve-{tag}.zip"
        _build_zip(zip_path, marker)
        _sh.copyfile(zip_path, path)
        return True

    monkeypatch.setattr(updater, "fetch_release_full", fake_fetch_release_full)
    monkeypatch.setattr(updater, "_download_to", fake_download_to)


def test_revert_reinstalls_the_previous_build_after_a_successful_swap(
    eng, monkeypatch, tmp_path
):
    """THE CENTRAL CLAIM, on observable state alone.

    Swap 148 -> 149 for real, then revert, and assert the ENGINE ON DISK is 148
    again and current_version() says so. Nothing here asserts that a helper was
    called: the engine's own bytes are read back out of ENGINE_DIR.
    """
    old_digest = "sha256:" + "a" * 64
    new_digest = "sha256:" + "b" * 64
    _install(eng, monkeypatch, "148.0.1", b"OLD", old_digest, tmp_path)
    _install(eng, monkeypatch, "149.0.1", b"NEW", new_digest, tmp_path)

    # precondition: the swap really happened
    assert updater.current_version() == "149.0.1"
    assert eng.installed_marker() == b"NEW"

    _serve(
        monkeypatch,
        {"148.0.1": (b"OLD", old_digest), "149.0.1": (b"NEW", new_digest)},
        tmp_path,
    )
    ok, message = updater.revert_to_previous_build()

    assert ok is True, message
    # the ENGINE is the old one — read out of the tree, not out of a record
    assert eng.installed_marker() == b"OLD"
    assert (eng.dir / "some.dll").read_bytes() == b"DLL-OLD"
    assert (eng.dir / "locales" / "en.pak").read_bytes() == b"PAK-OLD"
    # ...and the machine SAYS so
    assert updater.current_version() == "148.0.1"
    # no debris from the reversal
    assert updater.BACKUP_NAME not in eng.entries()
    assert not any(n.startswith(".staging") for n in eng.entries())


def test_the_advertised_chromium_major_moves_with_the_revert(
    eng, monkeypatch, tmp_path
):
    """A profile must not advertise a major the engine underneath it is not.

    engine_version.installed_chromium_version() derives the advertised version
    from current_version(), so this is the masking consequence of the revert and
    it is asserted through the REAL derivation rather than restated.
    """
    from src.services.browser import engine_version

    old_digest = "sha256:" + "a" * 64
    new_digest = "sha256:" + "b" * 64
    _install(eng, monkeypatch, "148.0.7778.215", b"OLD", old_digest, tmp_path)
    _install(eng, monkeypatch, "149.0.8000.10", b"NEW", new_digest, tmp_path)

    # `major` is a STRING by design — it feeds the Client Hints brands list
    # verbatim (see ChromiumVersion.major) — so compare it as one.
    assert engine_version.installed_chromium_version().major == "149"

    _serve(
        monkeypatch,
        {
            "148.0.7778.215": (b"OLD", old_digest),
            "149.0.8000.10": (b"NEW", new_digest),
        },
        tmp_path,
    )
    assert updater.revert_to_previous_build()[0] is True

    assert engine_version.installed_chromium_version().major == "148"
    # the frozen user-agent form moves with it too: a profile must not advertise
    # a major the engine underneath it is not
    assert engine_version.installed_chromium_version().reduced == "148.0.0.0"


# --- FALSIFICATION (non-waivable) -------------------------------------------


def test_falsification_without_the_recording_the_revert_cannot_happen(
    eng, monkeypatch, tmp_path
):
    """WITH THE RECORDING REMOVED AND THE REST OF THE DIFF IN PLACE, THE REVERT
    MUST GO RED.

    This is the test that proves the other tests are not passing for some
    incidental reason. record_installed_build is neutered — everything else
    (fetch_release_full, revert_to_previous_build, the pin, the whole install
    path) is exactly as shipped — and the revert must then be IMPOSSIBLE, with
    the engine left untouched on the new build.

    That is the state of `main` today, reproduced deliberately: the tree is
    gone, and with no record of the previous build's identity there is nothing
    to re-download. Asserted on observable state — what is in ENGINE_DIR and
    what current_version() reports — not on a call count.
    """
    old_digest = "sha256:" + "a" * 64
    new_digest = "sha256:" + "b" * 64

    monkeypatch.setattr(updater, "record_installed_build", lambda tag, digest: None)

    _install(eng, monkeypatch, "148.0.1", b"OLD", old_digest, tmp_path)
    _install(eng, monkeypatch, "149.0.1", b"NEW", new_digest, tmp_path)

    # upstream is perfectly healthy and still serving 148 — the ONLY thing
    # missing is the record, so nothing else can be blamed for the failure
    _serve(
        monkeypatch,
        {"148.0.1": (b"OLD", old_digest), "149.0.1": (b"NEW", new_digest)},
        tmp_path,
    )

    ok, message = updater.revert_to_previous_build()

    assert ok is False, "a revert with no recorded identity must not succeed"
    assert "nothing to go back to" in message
    # and the engine is untouched: still the new build, byte for byte
    assert eng.installed_marker() == b"NEW"
    assert updater.current_version() == "149.0.1"


# --- the reversal must SURVIVE the unattended check --------------------------


def test_the_reversal_survives_the_next_unattended_check(eng, monkeypatch, tmp_path):
    """Let the hourly check ACTUALLY RUN, rather than asserting a pin was
    written.

    Without the pin the reversal lasts under an hour: the very next unattended
    check sees the build the operator just rejected as newer than what is now
    installed and re-installs it, with no operator present. So this drives the
    REAL _auto_update_engine path and then reads the engine back off disk.
    """
    from src.ui import app as app_mod

    old_digest = "sha256:" + "a" * 64
    new_digest = "sha256:" + "b" * 64
    _install(eng, monkeypatch, "148.0.1", b"OLD", old_digest, tmp_path)
    _install(eng, monkeypatch, "149.0.1", b"NEW", new_digest, tmp_path)
    _serve(
        monkeypatch,
        {"148.0.1": (b"OLD", old_digest), "149.0.1": (b"NEW", new_digest)},
        tmp_path,
    )
    assert updater.revert_to_previous_build()[0] is True
    assert eng.installed_marker() == b"OLD"

    # A bare object standing in for the App: _auto_update_engine reads these
    # attributes and calls _update_engine_async, which is where an unattended
    # re-install would happen. If the pin does not hold, this fires.
    fake = _FakeApp()
    # is_installed() is real and true here (marker + version.txt + binary), so
    # the guard inside _auto_update_engine is exercised rather than bypassed.
    assert updater.is_installed() is True

    app_mod.App._auto_update_engine(fake)

    assert fake.updates_started == [], (
        "the hourly unattended check re-installed the build the operator just "
        "rolled back from — the reversal did not survive"
    )
    # and the engine on disk is STILL the reverted-to build
    assert eng.installed_marker() == b"OLD"
    assert updater.current_version() == "148.0.1"


def test_resuming_updates_lets_the_engine_move_forward_again(
    eng, monkeypatch, tmp_path
):
    """The way OUT of the pinned state, from the same place the operator
    entered it — otherwise a revert is a one-way door."""
    from src.ui import app as app_mod

    old_digest = "sha256:" + "a" * 64
    new_digest = "sha256:" + "b" * 64
    _install(eng, monkeypatch, "148.0.1", b"OLD", old_digest, tmp_path)
    _install(eng, monkeypatch, "149.0.1", b"NEW", new_digest, tmp_path)
    _serve(
        monkeypatch,
        {"148.0.1": (b"OLD", old_digest), "149.0.1": (b"NEW", new_digest)},
        tmp_path,
    )
    assert updater.revert_to_previous_build()[0] is True
    assert updater.pinned_build() == "148.0.1"

    updater.resume_engine_updates()

    assert updater.pinned_build() == ""

    fake = _FakeApp()
    app_mod.App._auto_update_engine(fake)

    assert fake.updates_started == [True], (
        "after resuming, the unattended check must offer the newer build again"
    )


# --- the honest limit: a yanked release --------------------------------------


def test_a_yanked_release_is_refused_plainly_and_changes_nothing(
    eng, monkeypatch, tmp_path
):
    """THE STATED TRADE. persona keeps no copy, so if upstream stops hosting the
    release the target is unreachable and the operator is where they were.

    What must NOT happen is a rollback that silently installs something else —
    and "something else" would be the newest build, which is exactly the thing
    being rolled back FROM. So: refused, said plainly, engine untouched.
    """
    old_digest = "sha256:" + "a" * 64
    new_digest = "sha256:" + "b" * 64
    _install(eng, monkeypatch, "148.0.1", b"OLD", old_digest, tmp_path)
    _install(eng, monkeypatch, "149.0.1", b"NEW", new_digest, tmp_path)

    # 148 has been yanked; 149 is still served, so a sloppy implementation that
    # falls back to "latest" would succeed here and install the wrong thing.
    _serve(monkeypatch, {"149.0.1": (b"NEW", new_digest)}, tmp_path)

    ok, message = updater.revert_to_previous_build()

    assert ok is False
    assert "148.0.1" in message and "no longer available" in message
    # the engine is EXACTLY as it was — not reinstalled, not damaged
    assert eng.installed_marker() == b"NEW"
    assert updater.current_version() == "149.0.1"
    # and no pin was written: nothing was rolled back, so nothing must be held
    assert updater.pinned_build() == ""


def test_a_revert_verifies_against_the_recorded_digest_not_a_fresh_one(
    eng, monkeypatch, tmp_path
):
    """PS-49's check, applied to this path: the digest must come off the DISK
    RECORD, never off whatever upstream advertises for that tag today.

    Upstream is made to advertise a DIFFERENT digest for 148 than the one the
    build was verified against when it was installed. The revert must present
    the RECORDED digest to the transfer — that is the whole reason the pair is
    stored as one unit.
    """
    old_digest = "sha256:" + "a" * 64
    new_digest = "sha256:" + "b" * 64
    _install(eng, monkeypatch, "148.0.1", b"OLD", old_digest, tmp_path)
    _install(eng, monkeypatch, "149.0.1", b"NEW", new_digest, tmp_path)

    seen = []
    upstream_now_claims = "sha256:" + "f" * 64
    # upstream now claims a different digest for 148 than we recorded
    _serve(
        monkeypatch,
        {"148.0.1": (b"OLD", upstream_now_claims)},
        tmp_path,
        seen=seen,
    )

    assert updater.revert_to_previous_build()[0] is True

    assert len(seen) == 1 and seen[0][0] == "148.0.1"
    presented = seen[0][1]
    # THE CLAIM: what reached the transfer is the digest this build was ACTUALLY
    # verified against when it was installed, not the one upstream advertises
    # today. Compared through normalize_digest because the record stores the
    # canonical bare-hex form ("sha256:" prefix stripped) — digest_ok normalizes
    # both sides, so the two forms verify identically and pinning the literal
    # string would be asserting a storage detail rather than the security
    # property.
    from src.utils import httpdl

    assert httpdl.normalize_digest(presented) == httpdl.normalize_digest(old_digest)
    assert httpdl.normalize_digest(presented) != httpdl.normalize_digest(
        upstream_now_claims
    ), (
        "the revert trusted upstream's current digest instead of the one the "
        "build was actually verified against"
    )


def test_a_record_with_no_digest_is_not_a_rollback_target(eng, monkeypatch):
    """BOTH OR NEITHER. A tag without its digest would force the rollback to
    trust a fresh API response — so a half-record is no target at all, rather
    than a target to be fetched unverified."""
    (eng.dir / "builds.json").write_text(
        json.dumps(
            {
                "current": {"tag": "149.0.1", "digest": "sha256:" + "b" * 64},
                "previous": {"tag": "148.0.1", "digest": ""},
            }
        ),
        encoding="utf-8",
    )
    assert updater.rollback_target() == ("", "")

    ok, message = updater.revert_to_previous_build()
    assert ok is False
    assert "nothing to go back to" in message


def test_an_unreadable_record_degrades_to_no_rollback_offered(eng):
    """A corrupt record must mean "no rollback offered", never a crash on a
    path the UI row and the update check both read."""
    (eng.dir / "builds.json").write_text("{not json", encoding="utf-8")
    assert updater.rollback_target() == ("", "")
    assert updater.pinned_build() == ""


# --- depth 1 -----------------------------------------------------------------


def test_only_the_last_swap_is_reversible(eng, monkeypatch, tmp_path):
    """One previous identity is retained; a second successful swap replaces it.
    This matches Firefox's depth-1 policy and is NOT a version history."""
    d1 = "sha256:" + "a" * 64
    d2 = "sha256:" + "b" * 64
    d3 = "sha256:" + "c" * 64
    _install(eng, monkeypatch, "147.0.1", b"ONE", d1, tmp_path)
    _install(eng, monkeypatch, "148.0.1", b"TWO", d2, tmp_path)
    assert _target_is("147.0.1", d1)

    _install(eng, monkeypatch, "149.0.1", b"THR", d3, tmp_path)

    # the SECOND swap replaced the target; 147 is unreachable now
    assert _target_is("148.0.1", d2)
    rec = json.loads((eng.dir / "builds.json").read_text(encoding="utf-8"))
    assert set(rec) == {"current", "previous"}
    assert "147.0.1" not in json.dumps(rec)


def test_reinstalling_the_same_tag_does_not_demote_a_build_over_itself(
    eng, monkeypatch, tmp_path
):
    """A re-install of the SAME build is not a swap. Demoting here would make
    the rollback target the build you are already on, and quietly destroy the
    real one — turning a repair into a loss of the only recovery path."""
    d1 = "sha256:" + "a" * 64
    d2 = "sha256:" + "b" * 64
    _install(eng, monkeypatch, "148.0.1", b"OLD", d1, tmp_path)
    _install(eng, monkeypatch, "149.0.1", b"NEW", d2, tmp_path)
    assert _target_is("148.0.1", d1)

    # same tag again (a repair, a re-download after a crash)
    _install(eng, monkeypatch, "149.0.1", b"NEW", d2, tmp_path)

    assert _target_is("148.0.1", d1)


def test_the_first_install_records_a_starting_point_but_no_target(
    eng, monkeypatch, tmp_path
):
    """Nothing to go back to after ONE install — and the row must therefore not
    offer the gesture. The identity is still recorded so the NEXT swap has a
    target."""
    _install(eng, monkeypatch, "148.0.1", b"OLD", "sha256:" + "a" * 64, tmp_path)

    assert updater.rollback_target() == ("", "")
    rec = json.loads((eng.dir / "builds.json").read_text(encoding="utf-8"))
    assert rec["current"]["tag"] == "148.0.1"
    assert "previous" not in rec


# --- a running profile -------------------------------------------------------


def test_a_revert_will_not_replace_a_tree_a_profile_is_running_from(
    eng, monkeypatch, tmp_path
):
    """The install replaces ENGINE_DIR in place, so a revert obeys the same
    in-use guard an unattended update does, for the same reason."""
    old_digest = "sha256:" + "a" * 64
    new_digest = "sha256:" + "b" * 64
    _install(eng, monkeypatch, "148.0.1", b"OLD", old_digest, tmp_path)
    _install(eng, monkeypatch, "149.0.1", b"NEW", new_digest, tmp_path)
    _serve(
        monkeypatch,
        {"148.0.1": (b"OLD", old_digest), "149.0.1": (b"NEW", new_digest)},
        tmp_path,
    )

    updater.set_in_use_provider(lambda: True)
    ok, message = updater.revert_to_previous_build()

    assert ok is False
    assert "close your running profiles" in message
    # untouched
    assert eng.installed_marker() == b"NEW"
    assert updater.current_version() == "149.0.1"
    assert updater.pinned_build() == ""


# --- the two engines' pins must not be one pin -------------------------------


def test_the_chromium_pin_is_not_the_firefox_pin(eng, monkeypatch, tmp_path):
    """settings.py is ONE FLAT DICT — a shared key is a shared VALUE SPACE.

    engine_build_pin holds a "firefox-NN" build DIRECTORY NAME and is read in
    four live places (engine_install.active_build, its prune-immunity number,
    and two UI sites). This one holds an upstream Chromium TAG. Sharing the key
    would make a Chromium revert mute the FIREFOX update row, and Firefox's
    "resume updates" silently clear the Chromium revert.

    Asserted in both directions, because the coupling would be silent: no
    existing test writes a Chromium tag into the Firefox pin.
    """
    from src.core import settings

    old_digest = "sha256:" + "a" * 64
    new_digest = "sha256:" + "b" * 64
    _install(eng, monkeypatch, "148.0.1", b"OLD", old_digest, tmp_path)
    _install(eng, monkeypatch, "149.0.1", b"NEW", new_digest, tmp_path)
    _serve(
        monkeypatch,
        {"148.0.1": (b"OLD", old_digest), "149.0.1": (b"NEW", new_digest)},
        tmp_path,
    )

    settings.set_engine_build_pin("firefox-19")
    assert updater.revert_to_previous_build()[0] is True

    # the Chromium revert did NOT disturb Firefox's pin
    assert settings.engine_build_pin() == "firefox-19"
    assert updater.pinned_build() == "148.0.1"

    # ...and Firefox resuming does NOT clear the Chromium revert
    settings.set_engine_build_pin("")
    assert updater.pinned_build() == "148.0.1"

    # ...nor the other way round
    settings.set_engine_build_pin("firefox-19")
    updater.resume_engine_updates()
    assert updater.pinned_build() == ""
    assert settings.engine_build_pin() == "firefox-19"


# --- the by-tag fetch --------------------------------------------------------


def test_fetch_release_full_reads_the_by_tag_endpoint_for_this_os(monkeypatch):
    """The by-tag fetch is a VARIANT of the latest fetch, not a second
    mechanism: same egress authority, same document shape, same per-OS asset
    selection. Asserted by driving BOTH through one fake document."""
    _force_os(monkeypatch, win=True)
    seen = {}

    doc = {
        "tag_name": "personium-148.0.7778.215",
        "prerelease": True,
        "assets": [
            {
                "name": "personium-148.0.7778.215-macos-x86_64.dmg",
                "browser_download_url": "http://x/mac.dmg",
                "digest": "sha256:" + "c" * 64,
            },
            {
                "name": "personium-148.0.7778.215-windows-x86_64.zip",
                "browser_download_url": "http://x/win.zip",
                "digest": "sha256:" + "d" * 64,
            },
        ],
    }

    def fake_fetch_json(url, timeout=20, **k):
        seen["url"] = url
        return doc

    monkeypatch.setattr(updater.egress, "fetch_json", fake_fetch_json)

    tag, url, digest = updater.fetch_release_full("148.0.7778.215")

    # The BARE version off disk is prefixed back into the real published tag
    # for the URL (PS-305) — builds.json and version.txt hold bare versions.
    assert "releases/tags/personium-148.0.7778.215" in seen["url"]
    assert "amnesiadevelopment/persona" in seen["url"], (
        "engine releases come from OUR repository now, not the dead upstream"
    )
    assert tag == "148.0.7778.215"
    assert url == "http://x/win.zip"          # the WINDOWS asset, not the dmg
    assert digest == "sha256:" + "d" * 64

    # and it goes through persona's OWN egress authority, like every other
    # metadata poll — never a bare urlopen
    def refuse(url, timeout=20, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(updater.egress, "fetch_json", refuse)
    assert updater.fetch_release_full("148.0.7778.215") == ("", "", "")


def test_fetch_release_full_answers_empty_for_a_tag_upstream_does_not_serve(
    monkeypatch,
):
    """A yanked release must read as ('','',''), which is what the revert turns
    into its plain refusal. A 404 raises out of fetch_json."""
    _force_os(monkeypatch, win=True)

    def not_found(url, timeout=20, **k):
        raise OSError("HTTP Error 404: Not Found")

    monkeypatch.setattr(updater.egress, "fetch_json", not_found)
    assert updater.fetch_release_full("148.0.1") == ("", "", "")
    # and an empty tag never reaches the network at all
    assert updater.fetch_release_full("") == ("", "", "")


# --- the REVERT'S PROGRESS BAR ----------------------------------------------
#
# Everything above this line tests the service layer. These tests cover the UI
# path — _on_engine_rollback and the row it hangs off — which the first round of
# this ticket shipped with zero coverage, and which is where the audit found the
# defect.
#
# THE BUG THESE EXIST TO CATCH: _on_engine_rollback was the third download path
# in app.py and the only one that never called _engine_progress_start(). The
# other two arm the bar before starting; this one passed a progress callback
# into a state object still holding the PREVIOUS download's finished values.
# ProgressState is monotonic by design and resets only when `total` CHANGES
# (progress_fmt.ProgressState.update), and the builds either side of a revert
# are sibling Chromium releases of near-identical size — so the reset branch
# usually never fires and the row sits pinned at 100% for the whole re-download.
#
# THE ASSERTIONS ARE ON WHAT THE OPERATOR SEES — _engine_bar.value and
# _engine_detail.value — never on whether a helper was called. That is the
# standard this ticket set, and it is what makes these tests bite: an
# `assert progress_start_called` would pass against an implementation that armed
# the bar and then fed it the stale state anyway.
#
# EVERY TEST HERE SEEDS THE STALE STATE FIRST by running a COMPLETED download
# through the real _engine_progress_cb. Without that seeding they would pass for
# free against the broken code, because a fresh ProgressState looks exactly like
# an armed one.


class _InlineThreading:
    """Runs the rollback's worker body inline instead of on a daemon thread.

    Only _on_engine_rollback's `threading.Thread(...).start()` is affected — it
    reads the module-level name, and the methods that do a local `import
    threading` re-bind the real module. Making the thread inline is what lets a
    test assert on the state DURING the download (from inside a progress
    callback) rather than racing it.
    """

    class Thread:
        def __init__(self, target=None, daemon=None, **kwargs):
            self._target = target

        def start(self):
            self._target()


class _RollbackApp:
    """The narrowest stand-in for App that the rollback UI path actually reads.

    ⚠️ THE FIVE METHODS UNDER TEST ARE THE REAL ONES, bound off the class — not
    stubs. _engine_progress_start and _engine_progress_cb in particular MUST be
    real: the whole defect lives in whether the first is called before the
    second runs, so stubbing either would leave a test that cannot fail. Only
    the paint/sidebar plumbing below is inert scaffolding.
    """

    _engine_busy = False
    _engine_checking = False
    _engine_latest = "149.0.1"
    _engine_status = ""
    _sidebar_host = None
    page = None

    # the real methods, defect and all
    _on_engine_rollback = _app_mod.App._on_engine_rollback
    _on_engine_resume = _app_mod.App._on_engine_resume
    _engine_rollback_row = _app_mod.App._engine_rollback_row
    _engine_progress_start = _app_mod.App._engine_progress_start
    _engine_progress_cb = _app_mod.App._engine_progress_cb

    def __init__(self):
        import flet as ft

        self._engine_bar = ft.ProgressBar(value=None)
        self._engine_detail = ft.Text("")
        self.engine_text = ft.Text("")
        self._engine_pstate = pf.ProgressState()
        self._engine_throttle = pf.ProgressThrottle()
        self._engine_start_t = 0.0
        self.logs = []

    # --- inert plumbing ---
    def _log(self, message):
        self.logs.append(message)

    def _refresh_engine_text(self, status=""):
        pass

    def _refresh_sidebar(self):
        pass

    def _safe_update(self):
        pass

    # --- what the operator sees, as one value ---
    def seen(self):
        return (self._engine_bar.value, self._engine_detail.value)

    def seed_a_completed_download(self, size=189_000_000):
        """Run a FULL download through the real progress path, so the app is
        left in exactly the state a just-finished engine update leaves it in:
        bar at 100%, detail reading "189.0 MB of 189.0 MB".

        This is the precondition the bug needs, and seeding it through the real
        callback rather than by assigning the fields is deliberate — it proves
        the stale state is something the production path actually produces.
        """
        self._engine_progress_start()
        self._engine_progress_cb(size // 2, size)
        self._engine_progress_cb(size, size)
        assert self._engine_bar.value == 1.0, "seeding failed to reach 100%"
        assert "189.0 MB of 189.0 MB" in self._engine_detail.value
        return self.seen()


@pytest.fixture
def ui(monkeypatch):
    """A _RollbackApp whose rollback runs inline."""
    monkeypatch.setattr(_app_mod, "threading", _InlineThreading)
    return _RollbackApp()


def _revert_emitting(*samples, ok=True, message="", gap=0.0):
    """A fake revert_to_previous_build that emits `samples` as (done, total)
    progress callbacks and records what the row showed at each one.

    `gap` sleeps between samples. It is needed ONLY when the sizes are unknown
    (total=0): ProgressThrottle suppresses a repaint unless the whole percent
    advanced or min_interval (0.1s) has passed, and with total=0 the percent is
    pinned at 0 forever — so two samples fired microseconds apart legitimately
    paint once. That is the real production throttle doing its job, not the bug
    under test, so the test waits it out rather than stubbing it away.
    """
    import time as _time

    shown = []

    def fake_revert(progress=None, timeout=600, log=None):
        shown.append(("at click, before any byte moves", _CURRENT[0].seen()))
        for i, (done, total) in enumerate(samples):
            if gap and i:
                _time.sleep(gap)
            progress(done, total)
            shown.append((f"{done} of {total}", _CURRENT[0].seen()))
        return ok, message

    return fake_revert, shown


_CURRENT = [None]


def test_the_revert_bar_shows_this_download_not_the_previous_one(
    ui, monkeypatch
):
    """THE CENTRAL UI TEST. After an update finishes, going back must not render
    the finished update's 100% bar for the whole re-download.

    Both builds are sibling Chromium releases of near-identical size, so `total`
    is UNCHANGED across the two downloads and ProgressState's reset branch never
    fires. Nothing but arming the bar can clear it.
    """
    _CURRENT[0] = ui
    stale = ui.seed_a_completed_download()

    fake_revert, shown = _revert_emitting(
        (1_000_000, 189_000_000), (90_000_000, 189_000_000)
    )
    monkeypatch.setattr(updater, "revert_to_previous_build", fake_revert)

    ui._on_engine_rollback()

    at_click = dict(shown)["at click, before any byte moves"]
    one_mb_in = dict(shown)["1000000 of 189000000"]
    ninety_mb_in = dict(shown)["90000000 of 189000000"]

    # AT THE CLICK: _refresh_sidebar re-inserts the bar/detail controls, so
    # whatever they hold is on screen instantly. A full bar with a completed
    # byte count here is indistinguishable from "already done".
    assert at_click != stale, (
        "the row still showed the previous download's finished state at the "
        f"moment of the click: {at_click}"
    )
    assert at_click == (None, ""), (
        f"the bar was not armed before the download started: {at_click}"
    )

    # 1 MB IN: ~0.5%, not 100%.
    frac, detail = one_mb_in
    assert frac is not None and frac < 0.02, (
        f"1 MB into a 189 MB re-download the bar read {frac}"
    )
    assert "1.0 MB of 189.0 MB" in detail, detail
    assert "189.0 MB of 189.0 MB" not in detail, (
        f"the byte line was frozen at the previous build's size: {detail}"
    )

    # 90 MB IN: still moving, still not complete.
    frac, detail = ninety_mb_in
    assert 0.4 < frac < 0.6, f"90 MB of 189 MB read as {frac}"
    assert "90.0 MB of 189.0 MB" in detail, detail


def test_a_revert_with_no_content_length_is_not_frozen_at_the_old_size(
    ui, monkeypatch
):
    """The Tor case, and the worse half of the bug.

    download_engine reports total=0 when the server omits Content-Length — which
    app.py itself calls "common over Tor", and Tor is the operator this row's
    tooltip was written for. total=0 can NEVER satisfy ProgressState's
    `total > 0 and total != self.total` reset, so without arming, the line stays
    frozen at the previous build's size permanently rather than merely often.
    """
    _CURRENT[0] = ui
    ui.seed_a_completed_download()

    fake_revert, shown = _revert_emitting(
        (5_000_000, 0), (40_000_000, 0), gap=0.15
    )
    monkeypatch.setattr(updater, "revert_to_previous_build", fake_revert)

    ui._on_engine_rollback()

    _, five_mb_detail = dict(shown)["5000000 of 0"]
    _, forty_mb_detail = dict(shown)["40000000 of 0"]

    assert "5.0 MB" in five_mb_detail, five_mb_detail
    assert "189.0 MB" not in five_mb_detail, (
        f"frozen at the previous build's size with no Content-Length: "
        f"{five_mb_detail}"
    )
    # and it MOVES, which is the only signal an unknown-size download has
    assert "40.0 MB" in forty_mb_detail, forty_mb_detail


def test_a_refusal_after_bytes_moved_does_not_leave_a_stale_byte_count(
    ui, monkeypatch
):
    """The `finally` clear, isolated from the arming fix.

    Bytes move and THEN the install refuses (a profile started, or the transfer
    failed). Arming the bar cannot help here — it happened minutes ago — so this
    goes red on the missing `_engine_detail.value = ""` alone, and stays red if
    only BLOCKER 1 is applied.
    """
    _CURRENT[0] = ui
    ui.seed_a_completed_download()

    fake_revert, _shown = _revert_emitting(
        (90_000_000, 189_000_000),
        ok=False,
        message="Chromium engine: going back to 148.0.1 failed — download failed",
    )
    monkeypatch.setattr(updater, "revert_to_previous_build", fake_revert)

    ui._on_engine_rollback()

    assert ui._engine_detail.value == "", (
        "a revert that moved bytes and then refused left the byte count under "
        f"the row: {ui._engine_detail.value!r}"
    )
    assert ui._engine_status == "couldn't go back — see the log"
    assert ui._engine_busy is False


def test_an_instant_refusal_renders_the_reason_and_clears_the_row(
    ui, monkeypatch
):
    """The COMMON outcome — nothing to go back to / close your profiles / the
    yanked tag — all of which return in milliseconds having moved no bytes."""
    _CURRENT[0] = ui
    ui.seed_a_completed_download()

    def refuse(progress=None, timeout=600, log=None):
        return False, ""

    monkeypatch.setattr(updater, "revert_to_previous_build", refuse)

    ui._on_engine_rollback()

    assert ui._engine_detail.value == "", ui._engine_detail.value
    assert ui._engine_bar.value is None
    assert ui._engine_status == "nothing to go back to"
    assert ui._engine_busy is False


def test_a_raise_inside_the_revert_still_clears_the_row_and_the_busy_flag(
    ui, monkeypatch
):
    """_engine_busy wedged True dead-ends every later engine action this
    session, so the finally must survive a raise — and so must the clear."""
    _CURRENT[0] = ui
    ui.seed_a_completed_download()

    def boom(progress=None, timeout=600, log=None):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(updater, "revert_to_previous_build", boom)

    ui._on_engine_rollback()

    assert ui._engine_busy is False
    assert ui._engine_detail.value == ""
    assert ui._engine_status == "couldn't go back — see the log"
    assert any("upstream exploded" in m for m in ui.logs), ui.logs


def test_a_second_click_while_a_revert_is_running_is_ignored(ui, monkeypatch):
    """The busy guard, asserted through observable state: the second click must
    not re-arm the bar out from under the running download."""
    _CURRENT[0] = ui
    ui._engine_busy = True
    ui.seed_a_completed_download()
    before = ui.seen()

    def must_not_run(progress=None, timeout=600, log=None):
        raise AssertionError("a second revert started while one was running")

    monkeypatch.setattr(updater, "revert_to_previous_build", must_not_run)

    ui._on_engine_rollback()

    assert ui.seen() == before


def test_the_rollback_row_says_why_it_vanished_when_the_record_is_unreadable(
    ui, monkeypatch
):
    """The row must still render when settings/the record cannot be read — but
    it must not vanish SILENTLY.

    This handler also covers `settings` being unreadable, so a corrupt settings
    file takes the RESUME gesture away: an operator left pinned, with no way out
    of the pin from the place they entered it, and nothing on screen or in the
    log saying why the control disappeared.
    """
    def unreadable():
        raise OSError("settings.json is not valid JSON")

    monkeypatch.setattr(updater, "pinned_build", unreadable)

    row = ui._engine_rollback_row()

    assert row.height == 0, "the panel must still render"
    assert any("settings.json is not valid JSON" in m for m in ui.logs), (
        f"the reason was swallowed: {ui.logs}"
    )


def test_a_hand_edited_unusable_digest_refuses_and_changes_nothing(
    eng, monkeypatch, tmp_path
):
    """A hand-edited `"digest": "sha256:"` must never install anything.

    ⚠️ THIS TEST DOCUMENTS A CORRECTION. The audit suggested this value would
    land in revert_to_previous_build's EngineUnverifiable branch. Executed, it
    does not, and the reason is the httpdl distinction that branch is built on:

        digest_missing("sha256:") is False   -- a digest ARRIVED and is unusable
        digest_missing("")        is True    -- nothing was ever published

    Only the second raises EngineUnverifiable. So "sha256:" passes _entry's
    both-or-neither gate and DOES become a rollback target (asserted below,
    because that half of the audit's reasoning is right and is the dangerous
    half), but it then fails the ordinary verify gate inside the download and
    comes back as a plain refusal instead.

    That is the correct behaviour, not a gap: normalize_digest("sha256:") is ""
    and digest_ok refuses an unusable digest explicitly ("a digest arrived but
    is unusable — never accept it"), so the bytes are rejected either way. What
    matters, and what this asserts, is that NOTHING IS INSTALLED and nothing on
    disk moves.

    EngineUnverifiable is therefore genuinely unreachable from the revert path —
    _entry refuses every digest-less record before a URL is ever fetched. The
    branch is correct defensive code and is deliberately kept; it is simply not
    reachable by this mechanism, so no test here pretends to reach it.
    """
    _install(eng, monkeypatch, "148.0.1", b"OLD", "aa" * 32, tmp_path)
    _install(eng, monkeypatch, "149.0.1", b"NEW", "bb" * 32, tmp_path)

    rec = json.loads((eng.dir / "builds.json").read_text(encoding="utf-8"))
    rec["previous"]["digest"] = "sha256:"
    (eng.dir / "builds.json").write_text(json.dumps(rec), encoding="utf-8")

    # It really does survive _entry as a target — otherwise this tests nothing,
    # and this is precisely why the value is worth a test at all.
    assert updater.rollback_target() == ("148.0.1", "sha256:")

    monkeypatch.setattr(
        updater,
        "fetch_release_full",
        lambda tag, timeout=20: (tag, f"http://x/{tag}.zip", "bb" * 32),
    )

    # An HONEST transfer: it applies the same digest gate the real _download_to
    # does, so the refusal below is produced by the verification rule under test
    # rather than by a fake that declined for its own reasons.
    zip_path = tmp_path / "rollback.zip"
    _build_zip(zip_path, b"OLD")

    def verifying_download_to(path, url, timeout, dg, progress, allow_missing=False):
        import shutil as _sh
        from src.utils import httpdl as _h

        _sh.copyfile(zip_path, path)
        if not _h.verify_file(path, dg, allow_missing=allow_missing):
            os.remove(path)
            return False
        return True

    monkeypatch.setattr(updater, "_download_to", verifying_download_to)

    logged = []
    ok, message = updater.revert_to_previous_build(log=logged.append)

    assert ok is False
    # Refused, and named as a refusal of THIS tag rather than a generic error.
    assert "148.0.1" in message, message
    assert any("148.0.1" in m for m in logged), logged
    # THE POINT: nothing was installed. The engine on disk is still the new
    # build and version.txt still agrees with the bytes.
    assert eng.installed_marker() == b"NEW"
    assert updater.current_version() == "149.0.1"
    # and the record was not rewritten by a revert that did not happen
    assert updater.rollback_target() == ("148.0.1", "sha256:")
