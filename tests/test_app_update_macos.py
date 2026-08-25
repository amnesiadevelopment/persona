import os
import sys
import tempfile

import pytest

from src.services.app_update import updater as au


def _force_os(monkeypatch, *, win=False, mac=False, linux=False):
    monkeypatch.setattr(au._platform, "IS_WINDOWS", win)
    monkeypatch.setattr(au._platform, "IS_MACOS", mac)
    monkeypatch.setattr(au._platform, "IS_LINUX", linux)


# --- the root of "update download failed" on macOS: staged_path had no macOS
# --- branch, fell into the AppImage one, and returned '' (no $APPIMAGE on a
# --- mac), so download_update bailed before a single byte moved.


def test_staged_path_macos_temp_dmg_keyed_by_tag(monkeypatch):
    _force_os(monkeypatch, mac=True)
    monkeypatch.delenv("APPIMAGE", raising=False)
    staged = au.staged_path("v9.9.9")
    assert staged, "macOS must have somewhere to download the update"
    assert os.path.dirname(staged) == tempfile.gettempdir()
    assert os.path.basename(staged) == "persona-update-v9.9.9.dmg"
    assert au.staged_path("v8.0.0") != staged


def test_download_update_works_on_macos(monkeypatch, tmp_path):
    _force_os(monkeypatch, mac=True)
    monkeypatch.delenv("APPIMAGE", raising=False)
    staged = au.staged_path("v9.9.9")
    stale = au.staged_path("v8.0.0")
    with open(stale, "wb") as f:
        f.write(b"leftover")

    def fake_run(cmd, capture_output=False, **kwargs):
        with open(staged, "wb") as f:
            f.write(b"z" * 10)

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(au.subprocess, "run", fake_run)
    try:
        out = au.download_update("http://x", size=10, tag="v9.9.9")
        assert out == staged
        assert not os.path.exists(stale)
    finally:
        for p in (staged, stale):
            try:
                os.remove(p)
            except OSError:
                pass


def test_tag_recovered_from_macos_staged_name(monkeypatch):
    _force_os(monkeypatch, mac=True)
    assert au._tag_from_staged(au.staged_path("v9.9.9")) == "v9.9.9"


# --- packaged detection per OS (what gates the automatic download)


def test_can_self_update_macos_only_from_app_bundle(monkeypatch):
    _force_os(monkeypatch, mac=True)
    monkeypatch.setattr(
        sys, "executable", "/Applications/persona.app/Contents/MacOS/persona"
    )
    assert au.installed_macos_app() == "/Applications/persona.app"
    assert au.can_self_update() is True
    monkeypatch.setattr(sys, "executable", "/usr/local/bin/python3")
    assert au.installed_macos_app() == ""
    assert au.can_self_update() is False


def test_can_self_update_windows_only_from_installed_exe(monkeypatch):
    _force_os(monkeypatch, win=True)
    # Forward slashes so os.path.basename splits the same under posixpath (this
    # test host on Linux/CI) and ntpath (a real Windows run) — the test forces
    # IS_WINDOWS but runs on whatever OS the suite runs on.
    monkeypatch.setattr(sys, "executable", "C:/Program Files/persona/persona.exe")
    assert au.can_self_update() is True
    monkeypatch.setattr(sys, "executable", "C:/Python312/python.exe")
    assert au.can_self_update() is False


def test_can_self_update_linux_requires_appimage(monkeypatch, tmp_path):
    _force_os(monkeypatch, linux=True)
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert au.can_self_update() is False
    app = tmp_path / "persona.AppImage"
    app.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(app))
    assert au.can_self_update() is True


# --- the macOS apply path: verify -> mount -> swap the .app -> relaunch


def _mac_apply_fixture(monkeypatch, tmp_path):
    _force_os(monkeypatch, mac=True)
    staged = tmp_path / "persona-update-v9.9.9.dmg"
    staged.write_bytes(b"dmg")
    installed = tmp_path / "Applications" / "persona.app"
    (installed / "Contents").mkdir(parents=True)
    (installed / "Contents" / "old").write_text("old")
    monkeypatch.setattr(au, "installed_macos_app", lambda: str(installed))
    monkeypatch.setattr(au, "verify_staged_installer", lambda s, tag="", log=None: True)
    return staged, installed


def test_apply_and_restart_macos_swaps_app_and_relaunches(monkeypatch, tmp_path):
    staged, installed = _mac_apply_fixture(monkeypatch, tmp_path)
    mounts = {}
    runs = []

    def fake_run(cmd, **kw):
        runs.append(cmd)

        class R:
            returncode = 0

        if cmd[0] == "hdiutil" and cmd[1] == "attach":
            mount = cmd[cmd.index("-mountpoint") + 1]
            new_app = os.path.join(mount, "persona.app", "Contents")
            os.makedirs(new_app, exist_ok=True)
            with open(os.path.join(new_app, "new"), "w") as f:
                f.write("new")
            mounts["point"] = mount
        if cmd[0] == "ditto":
            import shutil

            shutil.copytree(cmd[1], cmd[2])
        return R()

    popens = []

    def fake_popen(args, **kw):
        popens.append(args)

        class P:
            pid = 1

        return P()

    monkeypatch.setattr(au.subprocess, "run", fake_run)
    monkeypatch.setattr(au.subprocess, "Popen", fake_popen)

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(au.os, "_exit", fake_exit)
    msgs = []
    with pytest.raises(SystemExit):
        au.apply_and_restart(str(staged), log=msgs.append)
    # mounted read-only without a Finder window, then detached
    attach = next(c for c in runs if c[:2] == ["hdiutil", "attach"])
    assert "-nobrowse" in attach and str(staged) in attach
    assert any(c[:2] == ["hdiutil", "detach"] for c in runs)
    # the installed bundle now holds the new build
    assert os.path.exists(os.path.join(str(installed), "Contents", "new"))
    # ...and the PREVIOUS one is retained rather than destroyed. This assertion
    # was inverted (`assert not ... ".bak"`): it pinned the defect PS-152 fixes,
    # where the success path rmtree'd the aside-renamed bundle the instant ditto
    # returned 0, leaving nothing to go back to. Corrected, not deleted — the
    # surviving invariants above and below are still the point of this test.
    assert os.path.isdir(str(installed) + ".bak")
    assert os.path.exists(
        os.path.join(str(installed) + ".bak", "Contents", "old")
    )
    # a helper waits for this pid to die, then reopens the app
    sh = next(p for p in popens if p[0] == "/bin/sh")
    assert f"kill -0 {os.getpid()}" in sh[2]
    assert f'open "{installed}"' in sh[2]


def test_apply_and_restart_macos_restores_backup_on_copy_failure(
    monkeypatch, tmp_path
):
    staged, installed = _mac_apply_fixture(monkeypatch, tmp_path)

    def fake_run(cmd, **kw):
        class R:
            returncode = 0

        if cmd[0] == "hdiutil" and cmd[1] == "attach":
            mount = cmd[cmd.index("-mountpoint") + 1]
            os.makedirs(os.path.join(mount, "persona.app"), exist_ok=True)
        if cmd[0] == "ditto":
            R.returncode = 1  # copy failed mid-way
        return R()

    monkeypatch.setattr(au.subprocess, "run", fake_run)
    msgs = []
    assert au.apply_and_restart(str(staged), log=msgs.append) is False
    # the working install is back in place and the backup is gone
    assert os.path.exists(os.path.join(str(installed), "Contents", "old"))
    assert not os.path.exists(str(installed) + ".bak")


def test_apply_and_restart_macos_refuses_corrupt_dmg(monkeypatch, tmp_path):
    _force_os(monkeypatch, mac=True)
    staged = tmp_path / "persona-update-v9.9.9.dmg"
    staged.write_bytes(b"junk")
    monkeypatch.setattr(
        au, "verify_staged_installer", lambda s, tag="", log=None: False
    )
    msgs = []
    assert au.apply_and_restart(str(staged), log=msgs.append) is False
    # quarantined so find_ready_staged can't match it again
    assert not staged.exists()


# --- #172: App Translocation. Gatekeeper runs an unsigned, quarantined app
# --- from a READ-ONLY mirror under /private/var/folders/.../AppTranslocation/,
# --- so moving persona.app aside there fails with Errno 30 and the update
# --- aborted. The update must resolve and replace the ORIGINAL bundle instead.

_TRANSLOCATED = (
    "/private/var/folders/ls/abc123/T/AppTranslocation/f00-hash/d/persona.app"
)


def test_install_target_is_the_running_bundle_when_not_translocated(tmp_path):
    app = str(tmp_path / "Applications" / "persona.app")
    msgs = []
    assert au._macos_install_target(app, msgs.append) == app
    assert msgs == []


def test_install_target_resolves_the_translocation_original(monkeypatch, tmp_path):
    original = tmp_path / "Downloads" / "persona.app"
    (original / "Contents").mkdir(parents=True)
    monkeypatch.setattr(
        au, "_translocated_original_path", lambda p: str(original)
    )
    msgs = []
    assert au._macos_install_target(_TRANSLOCATED, msgs.append) == str(original)
    assert any("translocated" in m for m in msgs)


def test_install_target_falls_back_to_applications(monkeypatch):
    # the Security API couldn't resolve the original: install to /Applications
    monkeypatch.setattr(au, "_translocated_original_path", lambda p: "")
    monkeypatch.setattr(au.os, "access", lambda p, m: p == "/Applications")
    msgs = []
    assert au._macos_install_target(_TRANSLOCATED, msgs.append) == (
        "/Applications/persona.app"
    )
    assert any("/Applications" in m for m in msgs)


def test_install_target_falls_back_when_original_dir_unwritable(
    monkeypatch, tmp_path
):
    # the original resolved but sits somewhere genuinely unwritable (e.g. a
    # mounted dmg): fall back to /Applications instead of failing the rename
    original = tmp_path / "ro-volume" / "persona.app"
    (original / "Contents").mkdir(parents=True)
    monkeypatch.setattr(
        au, "_translocated_original_path", lambda p: str(original)
    )
    monkeypatch.setattr(au.os, "access", lambda p, m: p == "/Applications")
    msgs = []
    assert au._macos_install_target(_TRANSLOCATED, msgs.append) == (
        "/Applications/persona.app"
    )


def test_install_target_empty_when_nowhere_writable(monkeypatch):
    monkeypatch.setattr(au, "_translocated_original_path", lambda p: "")
    monkeypatch.setattr(au.os, "access", lambda p, m: False)
    msgs = []
    assert au._macos_install_target(_TRANSLOCATED, msgs.append) == ""
    assert any("aborting" in m.lower() for m in msgs)


class _FakeCFunc:
    """Stands in for a ctypes foreign function: callable, and accepts the
    .restype/.argtypes assignments the resolver makes."""

    def __init__(self, fn):
        self.fn = fn
        self.restype = None
        self.argtypes = None

    def __call__(self, *args):
        return self.fn(*args)


def test_translocated_original_path_reads_the_security_api(monkeypatch):
    import ctypes

    released = []
    URL, ORIG = 111, 222

    def get_fs_rep(url, resolve, buf, size):
        assert url == ORIG
        buf.value = b"/Users/mars/Downloads/persona.app"
        return True

    class FakeCF:
        CFURLCreateFromFileSystemRepresentation = _FakeCFunc(
            lambda alloc, raw, ln, isdir: URL
        )
        CFURLGetFileSystemRepresentation = _FakeCFunc(get_fs_rep)
        CFRelease = _FakeCFunc(released.append)

    class FakeSec:
        SecTranslocateCreateOriginalPathForURL = _FakeCFunc(
            lambda url, err: ORIG if url == URL else 0
        )

    def fake_cdll(path):
        return FakeSec() if "Security" in path else FakeCF()

    monkeypatch.setattr(ctypes, "CDLL", fake_cdll)
    assert au._translocated_original_path(_TRANSLOCATED) == (
        "/Users/mars/Downloads/persona.app"
    )
    assert sorted(released) == [URL, ORIG]  # no CF handles leaked


def test_translocated_original_path_empty_when_api_unavailable(monkeypatch):
    import ctypes

    def no_dylib(path):
        raise OSError("dlopen failed")

    monkeypatch.setattr(ctypes, "CDLL", no_dylib)
    assert au._translocated_original_path(_TRANSLOCATED) == ""


def test_translocated_original_path_empty_when_api_returns_null(monkeypatch):
    import ctypes

    class FakeCF:
        CFURLCreateFromFileSystemRepresentation = _FakeCFunc(
            lambda alloc, raw, ln, isdir: 111
        )
        CFURLGetFileSystemRepresentation = _FakeCFunc(lambda *a: True)
        CFRelease = _FakeCFunc(lambda h: None)

    class FakeSec:
        # NULL: the path wasn't actually translocated / resolution failed
        SecTranslocateCreateOriginalPathForURL = _FakeCFunc(lambda url, err: 0)

    monkeypatch.setattr(
        ctypes, "CDLL", lambda p: FakeSec() if "Security" in p else FakeCF()
    )
    assert au._translocated_original_path(_TRANSLOCATED) == ""


def test_apply_and_restart_translocated_updates_the_original(
    monkeypatch, tmp_path
):
    # THE #172 scenario: persona runs from the read-only translocated mirror;
    # the update must replace the real bundle the user has and relaunch it —
    # not abort with Errno 30 on the mirror.
    _force_os(monkeypatch, mac=True)
    staged = tmp_path / "persona-update-v9.9.9.dmg"
    staged.write_bytes(b"dmg")
    original = tmp_path / "Downloads" / "persona.app"
    (original / "Contents").mkdir(parents=True)
    (original / "Contents" / "old").write_text("old")
    monkeypatch.setattr(au, "installed_macos_app", lambda: _TRANSLOCATED)
    monkeypatch.setattr(
        au, "_translocated_original_path", lambda p: str(original)
    )
    monkeypatch.setattr(
        au, "verify_staged_installer", lambda s, tag="", log=None: True
    )

    def fake_run(cmd, **kw):
        class R:
            returncode = 0

        if cmd[0] == "hdiutil" and cmd[1] == "attach":
            mount = cmd[cmd.index("-mountpoint") + 1]
            new_app = os.path.join(mount, "persona.app", "Contents")
            os.makedirs(new_app, exist_ok=True)
            with open(os.path.join(new_app, "new"), "w") as f:
                f.write("new")
        if cmd[0] == "ditto":
            import shutil

            shutil.copytree(cmd[1], cmd[2])
        return R()

    popens = []

    def fake_popen(args, **kw):
        popens.append(args)

        class P:
            pid = 1

        return P()

    monkeypatch.setattr(au.subprocess, "run", fake_run)
    monkeypatch.setattr(au.subprocess, "Popen", fake_popen)

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(au.os, "_exit", fake_exit)
    msgs = []
    with pytest.raises(SystemExit):
        au.apply_and_restart(str(staged), log=msgs.append)
    # the ORIGINAL bundle got the new build
    assert os.path.exists(os.path.join(str(original), "Contents", "new"))
    assert not os.path.exists(os.path.join(str(original), "Contents", "old"))
    # ...and the previous one is retained beside it. Same inverted assertion as
    # in test_apply_and_restart_macos_swaps_app_and_relaunches: it was pinning
    # the PS-152 defect (success-path rmtree of the aside-renamed bundle).
    # Retention applies on the translocated path too — the bundle that got moved
    # aside is the ORIGINAL, which is what was replaced, so that is what a
    # revert must be able to restore. Corrected, not deleted.
    assert os.path.isdir(str(original) + ".bak")
    assert os.path.exists(
        os.path.join(str(original) + ".bak", "Contents", "old")
    )
    # and the relaunch opens the original, not the dead translocated mirror
    sh = next(p for p in popens if p[0] == "/bin/sh")
    assert f'open "{original}"' in sh[2]


def test_apply_and_restart_installs_fresh_when_target_missing(
    monkeypatch, tmp_path
):
    # translocated with no resolvable original: the /Applications fallback has
    # no persona.app yet — no move-aside, straight copy, relaunch from there.
    _force_os(monkeypatch, mac=True)
    staged = tmp_path / "persona-update-v9.9.9.dmg"
    staged.write_bytes(b"dmg")
    target = tmp_path / "Applications" / "persona.app"
    (tmp_path / "Applications").mkdir()
    monkeypatch.setattr(au, "installed_macos_app", lambda: _TRANSLOCATED)
    monkeypatch.setattr(
        au, "_macos_install_target", lambda app, say: str(target)
    )
    monkeypatch.setattr(
        au, "verify_staged_installer", lambda s, tag="", log=None: True
    )

    def fake_run(cmd, **kw):
        class R:
            returncode = 0

        if cmd[0] == "hdiutil" and cmd[1] == "attach":
            mount = cmd[cmd.index("-mountpoint") + 1]
            os.makedirs(os.path.join(mount, "persona.app"), exist_ok=True)
        if cmd[0] == "ditto":
            import shutil

            shutil.copytree(cmd[1], cmd[2])
        return R()

    popens = []

    def fake_popen(args, **kw):
        popens.append(args)

        class P:
            pid = 1

        return P()

    monkeypatch.setattr(au.subprocess, "run", fake_run)
    monkeypatch.setattr(au.subprocess, "Popen", fake_popen)

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(au.os, "_exit", fake_exit)
    with pytest.raises(SystemExit):
        au.apply_and_restart(str(staged), log=lambda m: None)
    assert os.path.isdir(str(target))
    sh = next(p for p in popens if p[0] == "/bin/sh")
    assert f'open "{target}"' in sh[2]


def test_apply_and_restart_aborts_when_no_writable_target(monkeypatch, tmp_path):
    _force_os(monkeypatch, mac=True)
    staged = tmp_path / "persona-update-v9.9.9.dmg"
    staged.write_bytes(b"dmg")
    monkeypatch.setattr(au, "installed_macos_app", lambda: _TRANSLOCATED)
    monkeypatch.setattr(au, "_macos_install_target", lambda app, say: "")
    monkeypatch.setattr(
        au, "verify_staged_installer", lambda s, tag="", log=None: True
    )
    assert au.apply_and_restart(str(staged), log=lambda m: None) is False


def test_apply_and_restart_macos_from_source_hands_over_the_dmg(
    monkeypatch, tmp_path
):
    # running from a source checkout: nothing to swap, but the verified dmg is
    # opened for the user instead of a bare "download failed".
    _force_os(monkeypatch, mac=True)
    staged = tmp_path / "persona-update-v9.9.9.dmg"
    staged.write_bytes(b"dmg")
    monkeypatch.setattr(au, "installed_macos_app", lambda: "")
    monkeypatch.setattr(au, "verify_staged_installer", lambda s, tag="", log=None: True)
    popens = []

    def fake_popen(args, **kw):
        popens.append(args)

        class P:
            pid = 1

        return P()

    monkeypatch.setattr(au.subprocess, "Popen", fake_popen)
    msgs = []
    assert au.apply_and_restart(str(staged), log=msgs.append) is False
    assert popens and popens[0] == ["open", str(staged)]
    assert staged.exists()


# --- PS-152: the previous bundle SURVIVES a successful update, and there is a
# --- way back to it.
#
# Before this, the success path rmtree'd the aside-renamed bundle the instant
# ditto returned 0. A release that is authentically what upstream published,
# passes its sha256, installs perfectly and then does not launch left the
# operator with nothing to go back to — while the FAILED-ditto arm restored
# cleanly. The arm where everything succeeded was the one keeping nothing.
#
# Every assertion below is on FILES ON DISK after a real apply_and_restart /
# revert_to_previous_build call. None of them asserts that a helper was called.


def _tree_manifest(root):
    """Every file under `root` as {relative path: bytes} — the whole bundle's
    content, so "byte-identical to before the swap" is a real comparison rather
    than a spot check on one file."""
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            with open(full, "rb") as f:
                out[os.path.relpath(full, root)] = f.read()
    return out


def _drive_mac_update(monkeypatch, staged, payload="new"):
    """Run apply_and_restart end to end, faking hdiutil/ditto exactly as the
    swap tests above do (attach materialises a bundle under the mountpoint,
    ditto is a copytree). Re-callable, so a SECOND update can be driven over the
    result of the first."""
    popens = []

    def fake_run(cmd, **kw):
        class R:
            returncode = 0

        if cmd[0] == "hdiutil" and cmd[1] == "attach":
            mount = cmd[cmd.index("-mountpoint") + 1]
            new_app = os.path.join(mount, "persona.app", "Contents")
            os.makedirs(new_app, exist_ok=True)
            with open(os.path.join(new_app, payload), "w") as f:
                f.write(payload)
        if cmd[0] == "ditto":
            import shutil

            shutil.copytree(cmd[1], cmd[2])
        return R()

    def fake_popen(args, **kw):
        popens.append(args)

        class P:
            pid = 1

        return P()

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(au.subprocess, "run", fake_run)
    monkeypatch.setattr(au.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(au.os, "_exit", fake_exit)
    with pytest.raises(SystemExit):
        au.apply_and_restart(str(staged), log=lambda m: None)
    return popens


def _retained_siblings(installed):
    """Everything the update left beside the install target — the basis for
    "bounded to one", counted rather than spot-checked."""
    parent = os.path.dirname(str(installed))
    base = os.path.basename(str(installed))
    return sorted(n for n in os.listdir(parent) if n.startswith(base))


def test_macos_update_retains_the_previous_bundle_byte_identical(
    monkeypatch, tmp_path
):
    # AC1. The whole point: after a SUCCESSFUL update the previous bundle is
    # still on disk and unchanged. On origin/main this fails — nothing survives.
    staged, installed = _mac_apply_fixture(monkeypatch, tmp_path)
    (installed / "Contents" / "Info.plist").write_bytes(b"<plist>v1</plist>")
    before = _tree_manifest(str(installed))

    _drive_mac_update(monkeypatch, staged)

    backup = str(installed) + ".bak"
    assert os.path.isdir(backup), "the previous bundle must survive a success"
    assert _tree_manifest(backup) == before, (
        "the retained bundle must be byte-identical to what was installed "
        "before the swap"
    )
    # and the new build really did land in the install location
    assert os.path.exists(os.path.join(str(installed), "Contents", "new"))


def test_macos_retained_bundle_is_relocated_not_reconstructed(
    monkeypatch, tmp_path
):
    # AC4. The retained bundle must be the ORIGINAL DIRECTORY, moved — not a
    # reconstruction of it. os.rename preserves the directory's inode; no
    # copytree variant does (plain, copy_function=os.link and symlinks=True were
    # all measured to allocate a new inode), so inode identity is what tells
    # rename from copy here.
    #
    # HONEST BOUND, and it is the reason this assertion exists in this shape:
    # the real consequence is that `ditto`/rename preserve the code signature
    # and resource forks a python copy destroys, so Gatekeeper still accepts the
    # restored bundle. That CANNOT be observed in this container — the harness
    # fakes ditto into a shutil.copytree, so no fixture bundle carries a
    # signature or a resource fork at all. This asserts that the bundle was
    # RELOCATED rather than RECONSTRUCTED, which is the mechanism the signature
    # property rides on; Gatekeeper accepting it is the consequence and is out
    # of reach here. Swap the implementation to copytree and this goes red.
    staged, installed = _mac_apply_fixture(monkeypatch, tmp_path)
    ino_before = os.stat(str(installed)).st_ino

    _drive_mac_update(monkeypatch, staged)

    backup = str(installed) + ".bak"
    assert os.stat(backup).st_ino == ino_before, (
        "the retained bundle must be the original directory relocated, not a "
        "copy of it"
    )


def test_second_macos_update_replaces_the_retained_bundle(
    monkeypatch, tmp_path
):
    # AC3. Depth, not duration: at most ONE retained bundle. The second update
    # must REPLACE the first's retained copy rather than accumulate a third.
    staged, installed = _mac_apply_fixture(monkeypatch, tmp_path)
    _drive_mac_update(monkeypatch, staged, payload="v2")

    # the dmg is consumed on success — stage another one for the second update
    staged.write_bytes(b"dmg")
    _drive_mac_update(monkeypatch, staged, payload="v3")

    siblings = _retained_siblings(installed)
    assert siblings == ["persona.app", "persona.app.bak"], (
        f"exactly one retained bundle must remain, found {siblings}"
    )
    # and it is the build the SECOND update displaced (v2), not the original
    backup = str(installed) + ".bak"
    assert os.path.exists(os.path.join(backup, "Contents", "v2"))
    assert not os.path.exists(os.path.join(backup, "Contents", "old"))
    assert os.path.exists(os.path.join(str(installed), "Contents", "v3"))


def test_macos_fresh_install_retains_nothing(monkeypatch, tmp_path):
    # AC5. Nothing was there to move aside, so retention is a clean no-op and
    # the way back is correctly not offered.
    _force_os(monkeypatch, mac=True)
    staged = tmp_path / "persona-update-v9.9.9.dmg"
    staged.write_bytes(b"dmg")
    target = tmp_path / "Applications" / "persona.app"
    (tmp_path / "Applications").mkdir()
    monkeypatch.setattr(au, "installed_macos_app", lambda: str(target))
    monkeypatch.setattr(
        au, "verify_staged_installer", lambda s, tag="", log=None: True
    )

    _drive_mac_update(monkeypatch, staged)

    assert os.path.isdir(str(target))
    assert not os.path.exists(str(target) + ".bak")
    assert au.rollback_target() == ""


def test_rollback_target_reports_the_retained_bundle(monkeypatch, tmp_path):
    staged, installed = _mac_apply_fixture(monkeypatch, tmp_path)
    # nothing retained yet: the gesture must not be offered
    assert au.rollback_target() == ""

    _drive_mac_update(monkeypatch, staged)

    assert au.rollback_target() == str(installed) + ".bak"


def test_revert_restores_the_retained_bundle_by_relocating_it(
    monkeypatch, tmp_path
):
    # AC4's second half: the reversal restores the retained bundle, and does it
    # by relocating that directory rather than reconstructing it — same inode
    # assertion, same honest bound as above (signature preservation is the real
    # consequence and is unobservable in this container).
    staged, installed = _mac_apply_fixture(monkeypatch, tmp_path)
    original = _tree_manifest(str(installed))

    _drive_mac_update(monkeypatch, staged)

    backup = str(installed) + ".bak"
    retained_ino = os.stat(backup).st_ino

    msgs = []
    assert au.revert_to_previous_build(log=msgs.append) == str(installed)

    # the previous version is installed again, whole
    assert _tree_manifest(str(installed)) == original
    assert os.stat(str(installed)).st_ino == retained_ino, (
        "the revert must relocate the retained bundle, not copy it"
    )
    # still exactly one retained bundle — now holding the build we reverted
    # FROM, so the revert is itself reversible
    assert _retained_siblings(installed) == ["persona.app", "persona.app.bak"]
    assert os.path.exists(os.path.join(backup, "Contents", "new"))


def test_revert_refuses_when_nothing_is_retained(monkeypatch, tmp_path):
    # The "render nothing at all" rule's service-side counterpart: a revert with
    # no retained bundle must refuse rather than damage the install.
    staged, installed = _mac_apply_fixture(monkeypatch, tmp_path)
    before = _tree_manifest(str(installed))

    msgs = []
    assert au.revert_to_previous_build(log=msgs.append) == ""

    assert _tree_manifest(str(installed)) == before
    assert _retained_siblings(installed) == ["persona.app"]


# --- PS-164: the CORRELATED double rename failure -------------------------
#
# revert_to_previous_build parks the current build, then renames the retained
# .bak into place. When BOTH renames fail the install location was left EMPTY,
# while the function's own docstring guaranteed the opposite ("a rename that
# fails midway leaves the operator with something rather than an empty install
# location").
#
# WHERE THE CORRELATION ACTUALLY LIVES — this was measured, not assumed, and it
# is NOT where the ticket said. A pre-existing non-writable /Applications
# (EACCES) cannot produce the empty-install state at all: the FIRST rename
# (app -> .reverting) is not inside its own except, so it fails first, hits the
# outer handler, and nothing has moved yet — the app survives untouched. The
# real correlation is that every restore attempt shares ONE destination
# (`app`), so a condition attached to THAT DESTINATION defeats the retained
# bundle and the parked bundle identically, with the same errno from both
# sources. The tests below drive that shape.
#
# Every assertion is on FILES ON DISK after a real revert_to_previous_build
# call, and each one first proves its own precondition: that an app WAS
# installed, and that BOTH restore attempts were genuinely made and failed. An
# assertion that would pass against an inert implementation is not coverage.


def _dest_failing_rename(monkeypatch, app, *, fail_first):
    """Instrument ONLY os.rename so that renames landing at `app` fail, for the
    first `fail_first` such attempts (use a large number for "always").

    Keyed on the DESTINATION rather than on a source or a call index, because
    that is the actual correlated shape: one condition on `app`, every source
    defeated identically. Returns the list of (src, dst) basenames attempted,
    so a test can prove both restore attempts really happened."""
    import errno

    real_rename = au.os.rename
    attempts = []
    failed = []

    def failing_rename(src, dst):
        attempts.append((os.path.basename(src), os.path.basename(dst)))
        if dst == app and len(failed) < fail_first:
            failed.append((os.path.basename(src), os.path.basename(dst)))
            raise OSError(errno.EACCES, "Permission denied")
        return real_rename(src, dst)

    monkeypatch.setattr(au.os, "rename", failing_rename)
    return attempts, failed


def test_revert_double_rename_failure_still_leaves_a_launchable_bundle(
    monkeypatch, tmp_path
):
    # AC1 + AC2. The load-bearing one: drive the correlated double failure
    # through the REAL revert_to_previous_build and assert on files on disk.
    staged, installed = _mac_apply_fixture(monkeypatch, tmp_path)
    previous = _tree_manifest(str(installed))
    _drive_mac_update(monkeypatch, staged)
    app = str(installed)

    # PRECONDITION, proved rather than assumed: there IS an app installed and a
    # retained bundle beside it before anything is induced to fail.
    assert os.path.isdir(app), "precondition: an app is installed"
    assert _retained_siblings(installed) == ["persona.app", "persona.app.bak"]

    # fail the revert's own restore AND the compensating one — the two that
    # share the destination
    attempts, failed = _dest_failing_rename(monkeypatch, app, fail_first=2)
    msgs = []
    went = au.revert_to_previous_build(log=msgs.append)

    # PRECONDITION, part two: the induced failure was actually observable —
    # both restore attempts were made, from DIFFERENT sources, and both failed.
    assert len(failed) == 2, f"both renames must be attempted and fail: {attempts}"
    assert {src for src, _dst in failed} == {
        "persona.app.bak",
        "persona.app.reverting",
    }, f"the two failures must be the two different sources: {failed}"

    # THE CONSEQUENCE: something launchable is at the install location.
    assert os.path.isdir(app), (
        "the double rename failure left NO bundle at the install location, "
        "which is exactly what the docstring guarantees cannot happen"
    )
    assert _tree_manifest(app) == previous, (
        "what is installed must be a whole, real bundle — the retained "
        "previous build, relocated rather than reconstructed"
    )
    # and the revert stayed reversible: still exactly one retained sibling
    assert _retained_siblings(installed) == ["persona.app", "persona.app.bak"]

    # THE CONSEQUENCE THE UI ACTS ON. Reaching the goal the hard way is still
    # reaching it: the previous version IS installed above, so this arm must
    # report success. src/ui/app.py:553 branches on this exact return value,
    # and a falsy one there tells the operator "couldn't go back — see the
    # log" while they sit in front of a successfully reverted app, with no
    # prompt to restart into it. Asserting the disk state alone leaves that
    # one-token regression green.
    assert went == app, (
        "the revert reached its goal the hard way — the previous version IS "
        "installed, so the UI must render 'restart to run the previous "
        "version', not 'couldn't go back' (src/ui/app.py:553 branches on "
        "this exact value)"
    )
    assert "now installed" in msgs[0] and "restart" in msgs[0], (
        "the recovered arm is the third of Work #3's three situations and "
        "the only one carrying an action the operator must take; it must "
        "read as neither refusal"
    )


def test_revert_recovers_when_the_install_location_is_occupied(
    monkeypatch, tmp_path
):
    # AC2 against the correlated cause with NO injected failure at all: a real
    # ENOTEMPTY from the OS. Something occupies `app` in the window after the
    # current build is parked, which defeats BOTH restore renames identically.
    staged, installed = _mac_apply_fixture(monkeypatch, tmp_path)
    _drive_mac_update(monkeypatch, staged)
    app = str(installed)
    current = _tree_manifest(app)
    assert os.path.isdir(app), "precondition: an app is installed"

    real_rename = au.os.rename
    squatted = []

    def rename_then_squat(src, dst):
        result = real_rename(src, dst)
        if dst.endswith(".reverting"):
            # the install location was just vacated — something else takes it
            os.makedirs(os.path.join(app, "Contents"), exist_ok=True)
            with open(os.path.join(app, "Contents", "squatter"), "w") as f:
                f.write("not ours")
            squatted.append(dst)
        return result

    monkeypatch.setattr(au.os, "rename", rename_then_squat)
    msgs = []
    au.revert_to_previous_build(log=msgs.append)

    assert squatted, "precondition: the install location was actually occupied"
    assert os.path.isdir(app), "an occupied destination must not end as no app"
    manifest = _tree_manifest(app)
    assert "Contents/squatter" not in manifest, (
        "the obstruction must be cleared, not left as the installed app"
    )
    assert manifest == current, (
        "the build the operator was running must be back at the install "
        "location, whole"
    )
    # the retained bundle is untouched, so a second attempt can still work
    assert _retained_siblings(installed) == ["persona.app", "persona.app.bak"]


def test_revert_reports_the_double_failure_distinctly(monkeypatch, tmp_path):
    # Work #3. In the single-failure case the operator's app is fine; in the
    # unrecoverable double-failure case there is no app. The same sentence for
    # both tells the operator nothing about which situation they are in.
    staged, installed = _mac_apply_fixture(monkeypatch, tmp_path)
    _drive_mac_update(monkeypatch, staged)
    app = str(installed)

    single = []
    _dest_failing_rename(monkeypatch, app, fail_first=1)
    au.revert_to_previous_build(log=single.append)
    assert os.path.isdir(app), "single failure: the app is fine"
    assert _tree_manifest(app), "single failure: and it is a real bundle"

    # rebuild the same starting state for the unrecoverable arm
    second = tmp_path / "second"
    second.mkdir()
    staged2, installed2 = _mac_apply_fixture(monkeypatch, second)
    _drive_mac_update(monkeypatch, staged2)
    app2 = str(installed2)
    double = []
    _, failed = _dest_failing_rename(monkeypatch, app2, fail_first=99)
    au.revert_to_previous_build(log=double.append)
    assert len(failed) >= 2, "precondition: this arm really did fail twice"
    assert not os.path.isdir(app2), (
        "precondition for the message assertion: this is the arm where "
        "nothing could be put back"
    )

    # THE THIRD SITUATION. Work #3 is a three-way distinction, not a two-way
    # one: between "your app is fine" and "you have no app" sits the arm where
    # the restore failed but the revert's GOAL was reached anyway — the
    # previous version is installed. That is the only one of the three
    # carrying an instruction the operator must act on, so it is the one that
    # must least be allowed to read as either refusal.
    third = tmp_path / "third"
    third.mkdir()
    staged3, installed3 = _mac_apply_fixture(monkeypatch, third)
    _drive_mac_update(monkeypatch, staged3)
    app3 = str(installed3)
    recovered = []
    _, failed3 = _dest_failing_rename(monkeypatch, app3, fail_first=2)
    went3 = au.revert_to_previous_build(log=recovered.append)
    assert len(failed3) == 2, "precondition: this arm really did fail twice"
    assert os.path.isdir(app3), (
        "precondition for the message assertion: this is the arm where the "
        "previous version DID land at the install location"
    )
    assert went3 == app3, (
        "precondition: and the function reports that it got there — the "
        "sentence and the return value must agree about which arm this is"
    )

    assert single and double and recovered
    # all THREE mutually distinct, not two of three: a message that is unique
    # against one arm but shared with the other still leaves the operator
    # unable to tell which situation they are in.
    assert len({single[0], double[0], recovered[0]}) == 3, (
        "the three situations differ radically — a working app, no app, and "
        "a completed revert — so no two may report the same sentence: "
        f"{single[0]!r} / {double[0]!r} / {recovered[0]!r}"
    )
    assert app2 in double[0] and "could not be put back" in double[0], (
        "the unrecoverable arm must say plainly that there is no app at the "
        "install location, and where it is"
    )
    assert "now installed" in recovered[0] and "restart" in recovered[0], (
        "the recovered arm must carry its action — the operator has to "
        "restart to actually run the version they asked to go back to"
    )
    assert "could not be put back" not in recovered[0], (
        "and it must not borrow the unrecoverable arm's language: there IS "
        "an app at the install location on this arm"
    )
    # narrowed honestly: BOTH bundles are safe beside the install location, so
    # the situation is recoverable by the operator
    assert _retained_siblings(installed2) == [
        "persona.app.bak",
        "persona.app.reverting",
    ]


def test_reverting_orphan_does_not_survive_the_next_update(
    monkeypatch, tmp_path
):
    # AC3, the same depth-not-duration bound PS-152 established for `.bak`:
    # counted, not spot-checked. Before this, the orphan was cleaned only by
    # the NEXT REVERT's own pre-clean — so an operator who reverted once and
    # never again kept a full bundle forever.
    staged, installed = _mac_apply_fixture(monkeypatch, tmp_path)
    _drive_mac_update(monkeypatch, staged)
    app = str(installed)

    _, failed = _dest_failing_rename(monkeypatch, app, fail_first=99)
    au.revert_to_previous_build(log=lambda m: None)

    # PRECONDITION: an orphan really was produced by a real failed revert
    assert len(failed) >= 2
    assert "persona.app.reverting" in _retained_siblings(installed), (
        "precondition: the failed revert left a parked bundle behind"
    )

    # a later successful update must bound it, exactly as it bounds a stale .bak
    staged.write_bytes(b"dmg")
    _drive_mac_update(monkeypatch, staged, payload="newer")

    assert _retained_siblings(installed) == ["persona.app"], (
        "the .reverting orphan must not outlive the next update — a full "
        "bundle kept forever is the leak this bound exists to prevent"
    )
