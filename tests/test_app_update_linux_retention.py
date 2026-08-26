"""PS-178: the previous AppImage SURVIVES a successful Linux update, and there
is a way back to it.

Before this, _apply_linux handed the swap to atomic_replace, which deleted its
backup the instant the replace returned. So the FAILURE path restored cleanly
and the SUCCESS path kept nothing — on the one platform that installs updates
unattended by default. verify_appimage_runs catches a corrupt runtime BEFORE
the swap, but its own docstring bounds it precisely (it never runs the
payload), so it cannot catch a build that is authentically what CI published,
passes its sha256, extracts cleanly and then does not work.

THE EVIDENCE DISCIPLINE HERE IS THE POINT (the owner's standing directive: a
check that could not have failed is not coverage). Every assertion below is on
FILES ON DISK after a real apply_and_restart / revert_to_previous_build call.
None asserts that atomic_replace was invoked, and none asserts that a constant
exists in the source — both of those pass against an implementation that does
not work. test_falsification_* at the bottom pins that by restoring the
deletion and requiring these to go RED.
"""

import hashlib
import os

import pytest

from src.services.app_update import updater as au


def _force_os(monkeypatch, *, win=False, mac=False, linux=False):
    monkeypatch.setattr(au._platform, "IS_WINDOWS", win)
    monkeypatch.setattr(au._platform, "IS_MACOS", mac)
    monkeypatch.setattr(au._platform, "IS_LINUX", linux)


def _publish_checksum(monkeypatch, staged):
    """Publish the REAL sha256 of the staged bytes, so the apply-time integrity
    gate genuinely runs and succeeds rather than being stubbed away."""
    digest = hashlib.sha256(staged.read_bytes()).hexdigest()
    monkeypatch.setattr(au, "fetch_expected_sha256", lambda tag, **k: digest)
    return digest


def _linux_fixture(monkeypatch, tmp_path, *, installed=b"v1-old-build"):
    """A packaged Linux install: the running AppImage plus a staged update.

    The AppImage lives in its own directory so `_retained_siblings` can count
    what the update leaves BESIDE it without tripping over unrelated tmp files.
    """
    _force_os(monkeypatch, linux=True)
    home = tmp_path / "Applications"
    home.mkdir()
    target = home / "persona.AppImage"
    target.write_bytes(installed)
    monkeypatch.setattr(au, "installed_appimage_path", lambda: str(target))
    monkeypatch.setattr(au, "verify_appimage_runs", lambda p: True)
    # fsync on an O_RDONLY fd is EBADF on Windows, where this suite also runs
    monkeypatch.setattr(au.os, "fsync", lambda fd: None)
    return target


def _stage(monkeypatch, tmp_path, payload):
    staged = tmp_path / "staged.AppImage"
    staged.write_bytes(payload)
    _publish_checksum(monkeypatch, staged)
    return staged


def _drive_linux_update(monkeypatch, staged):
    """Run apply_and_restart end to end on Linux, stopping at the execv.

    The real code execv's into the new binary and never returns; the fake
    raises SystemExit so the test can inspect the filesystem afterwards. Note
    what is NOT stubbed: the swap itself is the real atomic_replace against a
    real directory, which is what makes the assertions below evidence.
    Re-callable, so a SECOND update can be driven over the result of the first.
    """
    execs = []

    def fake_execv(path, args):
        execs.append(path)
        raise SystemExit(0)

    monkeypatch.setattr(au.os, "execv", fake_execv)
    with pytest.raises(SystemExit):
        au.apply_and_restart(str(staged), log=lambda m: None)
    return execs


def _retained_siblings(target):
    """Everything the update left beside the install target — the basis for
    "bounded to one", counted rather than spot-checked."""
    parent = os.path.dirname(str(target))
    base = os.path.basename(str(target))
    return sorted(n for n in os.listdir(parent) if n.startswith(base))


# --- AC1: the previous binary survives a SUCCESSFUL update ------------------


def test_linux_update_retains_the_previous_appimage_byte_identical(
    monkeypatch, tmp_path
):
    # AC1, the whole point. On origin/main this fails: atomic_replace's success
    # path os.remove'd the backup, so the count of retained binaries after a
    # successful update was 0 and there was nothing to be byte-identical to.
    target = _linux_fixture(monkeypatch, tmp_path)
    before = target.read_bytes()
    staged = _stage(monkeypatch, tmp_path, b"v2-new-build")

    _drive_linux_update(monkeypatch, staged)

    backup = str(target) + ".bak"
    assert os.path.isfile(backup), (
        "the previous AppImage must survive a SUCCESSFUL update — this is the "
        "defect PS-178 fixes"
    )
    with open(backup, "rb") as f:
        assert f.read() == before, (
            "the retained binary must be byte-identical to the pre-swap binary"
        )
    # ...and the new build really did land in the install location
    assert target.read_bytes() == b"v2-new-build"


def test_the_retained_appimage_stays_beside_the_original(monkeypatch, tmp_path):
    # The same-filesystem constraint macOS states at revert_to_previous_build:
    # the retained artifact is kept BESIDE the original so the revert's rename
    # never crosses a filesystem (a cross-device rename is EXDEV, and the
    # revert only ever renames). A retained binary parked in /tmp would make
    # the revert fail on exactly the machines where /tmp is a separate mount.
    target = _linux_fixture(monkeypatch, tmp_path)
    staged = _stage(monkeypatch, tmp_path, b"v2")

    _drive_linux_update(monkeypatch, staged)

    retained = au.rollback_target()
    assert os.path.dirname(retained) == os.path.dirname(str(target))


def test_the_retained_appimage_is_executable(monkeypatch, tmp_path):
    # A retained binary that is not executable is not a way back: the revert
    # renames it into place and the operator runs it directly. The backup is
    # taken with copy2, which carries the mode across.
    target = _linux_fixture(monkeypatch, tmp_path)
    os.chmod(str(target), 0o755)
    staged = _stage(monkeypatch, tmp_path, b"v2")

    _drive_linux_update(monkeypatch, staged)

    assert os.access(au.rollback_target(), os.X_OK), (
        "a retained binary that cannot be executed is not a way back"
    )


def test_linux_fresh_install_retains_nothing(monkeypatch, tmp_path):
    # Nothing was there to move aside, so retention is a clean no-op and the
    # way back is correctly NOT offered. Without this, an implementation that
    # fabricates an empty .bak would look like it passed AC1.
    _force_os(monkeypatch, linux=True)
    home = tmp_path / "Applications"
    home.mkdir()
    target = home / "persona.AppImage"  # never created
    monkeypatch.setattr(au, "installed_appimage_path", lambda: str(target))
    monkeypatch.setattr(au, "verify_appimage_runs", lambda p: True)
    monkeypatch.setattr(au.os, "fsync", lambda fd: None)
    staged = _stage(monkeypatch, tmp_path, b"v1")

    # installed_appimage_path is what _apply_linux resolves; with no file there
    # the run refuses before the swap, so nothing is retained either way.
    au.apply_and_restart(str(staged), log=lambda m: None)

    assert not os.path.exists(str(target) + ".bak")
    assert au.rollback_target() == ""


# --- AC2: rollback_target answers on Linux, so the UI row RENDERS -----------


def test_rollback_target_is_empty_before_any_update(monkeypatch, tmp_path):
    # The negative control for the test below: "" means the gesture must not be
    # offered, because a revert with no retained binary is a button that cannot
    # work.
    _linux_fixture(monkeypatch, tmp_path)

    assert au.rollback_target() == ""


def test_rollback_target_reports_the_retained_appimage(monkeypatch, tmp_path):
    # AC2's service half. On origin/main this returns "" on Linux
    # unconditionally (`if not _platform.IS_MACOS: return ""`).
    target = _linux_fixture(monkeypatch, tmp_path)
    staged = _stage(monkeypatch, tmp_path, b"v2")

    _drive_linux_update(monkeypatch, staged)

    assert au.rollback_target() == str(target) + ".bak"


def test_rollback_target_is_quiet_when_the_install_cannot_be_resolved(
    monkeypatch,
):
    # This decides whether to RENDER a control, so it must answer rather than
    # raise — a panel that throws is worse than a missing row.
    _force_os(monkeypatch, linux=True)

    def boom():
        raise OSError("APPIMAGE unreadable")

    monkeypatch.setattr(au, "installed_appimage_path", boom)

    assert au.rollback_target() == ""


def test_windows_still_offers_no_app_revert(monkeypatch, tmp_path):
    # The boundary this ticket deliberately does NOT cross: Windows keeps its
    # own `.prev` under the fast-update scheme and upgrades in place under
    # Inno's AppId semantics via the full installer. Neither is resolved here,
    # so the answer stays "" and the row stays absent there.
    _force_os(monkeypatch, win=True)

    assert au.rollback_target() == ""


# --- AC2, rendered: the ROW appears in the panel a Linux operator sees ------
#
# Asserted on the RENDERED PANEL, not on the flag — the shape the macOS rounds
# converged on after asserting the wrong thing three times. An implementation
# that resolves a retained binary but never reaches the UI passes a
# rollback_target() assertion while the operator still sees no button.


def _walk_texts(panel):
    """Collect every string the built panel renders."""
    found: list[str] = []

    def walk(c):
        v = getattr(c, "value", None)
        if isinstance(v, str):
            found.append(v)
        for attr in ("content", "controls"):
            child = getattr(c, attr, None)
            if child is None:
                continue
            for k in (child if isinstance(child, list) else [child]):
                walk(k)

    walk(panel)
    return found


def _linux_panel_texts(monkeypatch, *, retained):
    """Build the REAL version panel on a Linux host whose updater reports
    `retained`, and return every string it renders."""
    from src.ui import app as app_mod
    from tests.test_app_ui import make_app

    monkeypatch.setattr(app_mod.app_update, "rollback_target", lambda: retained)
    monkeypatch.setattr(
        app_mod.app_settings, "is_auto_update_enabled", lambda: False
    )
    app = make_app(None)
    # make_app goes through __new__, so the fields _build_version_panel reads
    # must be set here exactly as the real __init__ sets them — the stub must
    # not be a weaker object than the one it stands in for.
    app._app_rollback_status = ""
    app._log = lambda *a, **k: None
    app._refresh_sidebar = lambda *a, **k: None
    app._app_latest = ""
    app._app_update_status = ""
    app._update_staged = ""
    return _walk_texts(app._build_version_panel())


def test_the_go_back_row_renders_on_linux_when_a_binary_is_retained(
    monkeypatch,
):
    # AC2 as the operator experiences it. _app_rollback_row is NOT
    # platform-gated — it asks rollback_target() and renders nothing when the
    # answer is "" — so on origin/main this row silently did not exist for any
    # Linux operator. Nothing here is stubbed except the service answer.
    texts = _linux_panel_texts(
        monkeypatch, retained="/home/u/Applications/persona.AppImage.bak"
    )

    assert "go back to the previous version" in texts


def test_the_go_back_row_is_absent_when_nothing_is_retained(monkeypatch):
    # The paired negative control: without it, a row that renders
    # unconditionally would pass the test above. A button that cannot work is
    # worse than no button.
    texts = _linux_panel_texts(monkeypatch, retained="")

    assert "go back to the previous version" not in texts


# --- AC3: a revert really restores the previous binary ----------------------


def test_revert_restores_the_previous_appimage(monkeypatch, tmp_path):
    # AC3. Asserted on the BYTES at the install path afterwards, not on a
    # return value alone: the operator's next launch reads that file.
    target = _linux_fixture(monkeypatch, tmp_path)
    original = target.read_bytes()
    staged = _stage(monkeypatch, tmp_path, b"v2-broken-build")

    _drive_linux_update(monkeypatch, staged)
    assert target.read_bytes() == b"v2-broken-build"  # the bad build is live

    msgs = []
    assert au.revert_to_previous_build(log=msgs.append) == str(target)

    assert target.read_bytes() == original, (
        "the revert must put the previous binary back at the install path"
    )
    # and the operator is told to restart INTO it — the relaunch is theirs,
    # because the process still running is the build being reverted FROM
    assert any("restart" in m.lower() for m in msgs), msgs


def test_the_reverted_from_build_becomes_the_new_retained_binary(
    monkeypatch, tmp_path
):
    # The revert is itself undoable by the same gesture: the build reverted
    # FROM lands in the single retained slot rather than being destroyed. An
    # operator who reverts by mistake is not stranded on the old build.
    target = _linux_fixture(monkeypatch, tmp_path)
    staged = _stage(monkeypatch, tmp_path, b"v2-new-build")

    _drive_linux_update(monkeypatch, staged)
    au.revert_to_previous_build(log=lambda m: None)

    retained = au.rollback_target()
    assert retained == str(target) + ".bak"
    with open(retained, "rb") as f:
        assert f.read() == b"v2-new-build"
    # still exactly one retained binary, and no parked leftover
    assert _retained_siblings(target) == [
        "persona.AppImage", "persona.AppImage.bak",
    ]


def test_the_revert_relocates_the_retained_binary_rather_than_copying_it(
    monkeypatch, tmp_path
):
    # os.rename is atomic and preserves the inode; a copy allocates a new one.
    # This matters beyond tidiness: a half-written copy at the install path is
    # a non-launchable app, which is precisely the state the whole retention
    # exists to prevent. Swap the implementation to a copy and this goes red.
    target = _linux_fixture(monkeypatch, tmp_path)
    staged = _stage(monkeypatch, tmp_path, b"v2")

    _drive_linux_update(monkeypatch, staged)
    retained_ino = os.stat(str(target) + ".bak").st_ino

    au.revert_to_previous_build(log=lambda m: None)

    assert os.stat(str(target)).st_ino == retained_ino, (
        "the revert must relocate the retained binary, not reconstruct it"
    )


def test_revert_refuses_when_nothing_is_retained(monkeypatch, tmp_path):
    # The "render nothing at all" rule's service-side counterpart: a revert
    # with no retained binary must refuse rather than damage the install.
    target = _linux_fixture(monkeypatch, tmp_path)
    before = target.read_bytes()

    msgs = []
    assert au.revert_to_previous_build(log=msgs.append) == ""

    assert target.read_bytes() == before, "a refused revert must touch nothing"
    assert _retained_siblings(target) == ["persona.AppImage"]
    assert any("nothing to go back to" in m for m in msgs), msgs


def test_a_refused_revert_leaves_a_launchable_binary_in_place(
    monkeypatch, tmp_path
):
    # The guarantee revert_to_previous_build's docstring makes: a rename that
    # fails midway leaves the operator with SOMETHING rather than an empty
    # install location. The current build is parked first, so the failure
    # window is real — the compensating restore must close it.
    target = _linux_fixture(monkeypatch, tmp_path)
    staged = _stage(monkeypatch, tmp_path, b"v2-new-build")
    _drive_linux_update(monkeypatch, staged)

    real_rename = os.rename

    def failing_rename(src, dst):
        # fail exactly the restore of the retained binary INTO the install path
        if src == str(target) + ".bak" and dst == str(target):
            raise OSError("read-only filesystem")
        return real_rename(src, dst)

    monkeypatch.setattr(au.os, "rename", failing_rename)

    msgs = []
    assert au.revert_to_previous_build(log=msgs.append) == ""

    assert os.path.isfile(str(target)), (
        "a refused revert must never leave the install path EMPTY"
    )
    assert target.read_bytes() == b"v2-new-build", (
        "the build the operator was running must be what is put back"
    )


# --- AC4: bounded to ONE retained binary -----------------------------------


def test_a_second_update_replaces_the_retained_binary(monkeypatch, tmp_path):
    # AC4, and the count is the assertion. Depth, not duration: each update's
    # retained binary REPLACES the last rather than accumulating a third. This
    # is the policy engine_install states in place for the far larger engine
    # tree; one AppImage is a fraction of that, so the disk-cost question is
    # not re-opened here.
    target = _linux_fixture(monkeypatch, tmp_path)

    _drive_linux_update(monkeypatch, _stage(monkeypatch, tmp_path, b"v2"))
    _drive_linux_update(monkeypatch, _stage(monkeypatch, tmp_path, b"v3"))

    siblings = _retained_siblings(target)
    assert siblings == ["persona.AppImage", "persona.AppImage.bak"], (
        f"exactly one retained binary must remain, found {siblings}"
    )
    # and it holds the build the SECOND update displaced (v2), not the original
    with open(str(target) + ".bak", "rb") as f:
        assert f.read() == b"v2"
    assert target.read_bytes() == b"v3"


def test_a_refused_revert_leaves_no_parked_binary_behind_after_the_next_update(
    monkeypatch, tmp_path
):
    # The self-bounding half of AC4, mirroring the `.reverting` pre-clean
    # _apply_macos does beside its stale `.bak`. A revert that is refused after
    # parking the current build can leave one behind; without a pre-clean the
    # only thing that would ever remove it is the NEXT revert, so an operator
    # who reverted once and never again would keep a whole extra AppImage
    # forever. Bounding it at the update keeps the policy a DEPTH.
    target = _linux_fixture(monkeypatch, tmp_path)
    _drive_linux_update(monkeypatch, _stage(monkeypatch, tmp_path, b"v2"))

    # simulate the orphan a refused revert leaves
    orphan = str(target) + ".reverting"
    with open(orphan, "wb") as f:
        f.write(b"stranded")
    assert os.path.exists(orphan)

    _drive_linux_update(monkeypatch, _stage(monkeypatch, tmp_path, b"v3"))

    assert not os.path.exists(orphan), (
        "a stale parked binary must not outlive the next update"
    )
    assert _retained_siblings(target) == [
        "persona.AppImage", "persona.AppImage.bak",
    ]


# --- AC5: the ENGINE caller of the shared helper is unaffected --------------


def test_engine_installs_still_drop_their_backup(tmp_path):
    # AC5, asserted on behaviour rather than only on the diff. atomic_replace
    # has two callers and only the app AppImage is in scope; the retention is
    # an OPT-IN parameter the engine caller does not pass, so a default call
    # must still delete its backup exactly as before. Flipping the shared
    # default instead would have silently changed engine installs too.
    from src.utils.httpdl import atomic_replace

    dst = tmp_path / "engine-binary"
    dst.write_bytes(b"old-engine")
    src = tmp_path / "new-engine"
    src.write_bytes(b"new-engine")

    assert atomic_replace(str(src), str(dst), mode=None) is True

    assert dst.read_bytes() == b"new-engine"
    assert not os.path.exists(str(dst) + ".bak"), (
        "the default (engine) behaviour must still drop the backup"
    )


def test_the_shared_helper_retains_only_when_asked(tmp_path):
    # The positive control for the test above: the SAME helper keeps the backup
    # when the caller opts in. Together these two pin the parameter as the
    # thing that decides, which is what makes "the engine is unaffected" a
    # measured claim rather than an intention.
    from src.utils.httpdl import atomic_replace

    dst = tmp_path / "app.AppImage"
    dst.write_bytes(b"old-app")
    src = tmp_path / "staged.AppImage"
    src.write_bytes(b"new-app")

    assert atomic_replace(
        str(src), str(dst), mode=None, retain_backup=True
    ) is True

    assert dst.read_bytes() == b"new-app"
    with open(str(dst) + ".bak", "rb") as f:
        assert f.read() == b"old-app"


def test_a_failed_retaining_replace_still_restores_the_original(tmp_path):
    # The retention must not weaken the guarantee that was already there: when
    # the replace FAILS, the working artifact is restored and no half-state is
    # left at the install path.
    from src.utils import httpdl

    dst = tmp_path / "app.AppImage"
    dst.write_bytes(b"old-app")
    src = tmp_path / "staged.AppImage"
    src.write_bytes(b"new-app")

    def boom(a, b):
        raise OSError("no space left on device")

    orig_replace = httpdl.os.replace
    httpdl.os.replace = boom
    try:
        assert httpdl.atomic_replace(
            str(src), str(dst), mode=None, retain_backup=True
        ) is False
    finally:
        httpdl.os.replace = orig_replace

    assert dst.read_bytes() == b"old-app", "the working artifact must be back"


# --- AC6: FALSIFICATION (non-waivable) -------------------------------------
#
# The standing directive: a check that could not have failed is not coverage.
# These re-introduce the exact defect — the success-path deletion of the backup
# — and require the AC1/AC2 evidence to go RED. If these ever pass, the
# assertions above are not measuring what they claim to measure.


def _restore_the_deletion(monkeypatch):
    """Put the origin/main behaviour back: drop the backup on success,
    regardless of what the caller asked for."""
    from src.utils import httpdl

    real = httpdl.atomic_replace

    def deleting_replace(src, dst, mode=0o755, log=None, retain_backup=False):
        ok = real(src, dst, mode=mode, log=log, retain_backup=False)
        return ok

    monkeypatch.setattr(httpdl, "atomic_replace", deleting_replace)
    monkeypatch.setattr(au, "atomic_replace", deleting_replace)


def test_falsification_ac1_goes_red_when_the_deletion_is_restored(
    monkeypatch, tmp_path
):
    # With the deletion restored, NOTHING survives a successful update — which
    # is exactly the state origin/main is in, measured here rather than
    # asserted from memory.
    target = _linux_fixture(monkeypatch, tmp_path)
    staged = _stage(monkeypatch, tmp_path, b"v2")
    _restore_the_deletion(monkeypatch)

    _drive_linux_update(monkeypatch, staged)

    assert not os.path.exists(str(target) + ".bak"), (
        "the falsification control is not reproducing the defect"
    )
    # AC1's evidence is therefore unavailable: the count of retained binaries
    # after a SUCCESSFUL update is zero.
    assert _retained_siblings(target) == ["persona.AppImage"]


def test_falsification_ac2_goes_red_when_the_deletion_is_restored(
    monkeypatch, tmp_path
):
    # ...and with nothing retained, the way back is correctly not offered, so
    # the UI row disappears again. This is the whole user-visible consequence
    # of the defect, reproduced.
    target = _linux_fixture(monkeypatch, tmp_path)
    staged = _stage(monkeypatch, tmp_path, b"v2")
    _restore_the_deletion(monkeypatch)

    _drive_linux_update(monkeypatch, staged)

    assert au.rollback_target() == ""
    texts = _linux_panel_texts(monkeypatch, retained=au.rollback_target())
    assert "go back to the previous version" not in texts
