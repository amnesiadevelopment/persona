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
