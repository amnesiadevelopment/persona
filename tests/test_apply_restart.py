"""apply_and_restart must never fail silently: when the relaunch can't happen
it has to explain why (so the Activity Log shows it) and must leave the staged
file in place so the 'restart to update' button can retry. Regression for the
'download hit 100% but nothing restarted and there was no hint why' bug."""

import os

import src.services.app_update.updater as au


def test_logs_and_keeps_staged_when_not_appimage(monkeypatch, tmp_path):
    staged = tmp_path / "p.AppImage.part"
    staged.write_bytes(b"x")
    monkeypatch.setattr(au, "installed_appimage_path", lambda: None)
    msgs = []
    ok = au.apply_and_restart(str(staged), log=msgs.append)
    assert ok is False
    assert any("AppImage" in m for m in msgs)
    assert staged.exists()  # kept for a retry, not deleted


def test_logs_and_keeps_staged_when_replace_fails(monkeypatch, tmp_path):
    target = tmp_path / "persona.AppImage"
    target.write_bytes(b"old")
    staged = tmp_path / "p.AppImage.part"
    staged.write_bytes(b"new")
    monkeypatch.setattr(au, "installed_appimage_path", lambda: str(target))

    def boom(*a, **k):
        raise OSError("Text file busy")

    monkeypatch.setattr(au.os, "replace", boom)
    msgs = []
    ok = au.apply_and_restart(str(staged), log=msgs.append)
    assert ok is False
    assert any("failed" in m.lower() for m in msgs)
    assert staged.exists()  # NOT removed, so the user can retry the restart


def test_relaunch_failure_is_logged(monkeypatch, tmp_path):
    target = tmp_path / "persona.AppImage"
    target.write_bytes(b"old")
    staged = tmp_path / "p.AppImage.part"
    staged.write_bytes(b"new")
    monkeypatch.setattr(au, "installed_appimage_path", lambda: str(target))
    monkeypatch.setattr(au.os, "execv", lambda *a: (_ for _ in ()).throw(OSError("no fuse")))
    msgs = []
    ok = au.apply_and_restart(str(staged), log=msgs.append)
    assert ok is False
    assert any("relaunch failed" in m.lower() for m in msgs)
