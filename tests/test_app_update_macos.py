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
    # the installed bundle now holds the new build, no backup left behind
    assert os.path.exists(os.path.join(str(installed), "Contents", "new"))
    assert not os.path.exists(str(installed) + ".bak")
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
    # the ORIGINAL bundle got the new build, no backup left behind
    assert os.path.exists(os.path.join(str(original), "Contents", "new"))
    assert not os.path.exists(os.path.join(str(original), "Contents", "old"))
    assert not os.path.exists(str(original) + ".bak")
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
