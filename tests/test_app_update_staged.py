import os

from src.services.app_update import updater as au


def test_staged_path_none_when_not_packaged(monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert au.staged_path() == ""


def test_staged_path_next_to_appimage(monkeypatch, tmp_path):
    app = tmp_path / "persona.AppImage"
    app.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(app))
    staged = au.staged_path()
    assert staged.startswith(str(tmp_path))
    assert staged.endswith(".part")


def test_find_ready_staged_returns_complete_file(monkeypatch, tmp_path):
    app = tmp_path / "persona.AppImage"
    app.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(app))
    staged = au.staged_path()
    with open(staged, "wb") as f:
        f.write(b"y" * 100)
    monkeypatch.setattr(au, "remote_size", lambda url, timeout=30: 100)
    assert au.find_ready_staged("http://x") == staged


def test_find_ready_staged_empty_when_incomplete(monkeypatch, tmp_path):
    app = tmp_path / "persona.AppImage"
    app.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(app))
    staged = au.staged_path()
    with open(staged, "wb") as f:
        f.write(b"y" * 50)  # short of the 100-byte remote
    monkeypatch.setattr(au, "remote_size", lambda url, timeout=30: 100)
    assert au.find_ready_staged("http://x") == ""


def test_find_ready_staged_empty_when_no_file(monkeypatch, tmp_path):
    app = tmp_path / "persona.AppImage"
    app.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(app))
    monkeypatch.setattr(au, "remote_size", lambda url, timeout=30: 100)
    assert au.find_ready_staged("http://x") == ""


def test_download_update_uses_curl_and_reports_progress(monkeypatch, tmp_path):
    app = tmp_path / "persona.AppImage"
    app.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(app))
    staged = au.staged_path()
    monkeypatch.setattr(au, "remote_size", lambda url, timeout=30: 10)

    calls = {}

    def fake_run(cmd, capture_output=False):
        calls["cmd"] = cmd
        # simulate curl writing the complete file
        with open(staged, "wb") as f:
            f.write(b"z" * 10)

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(au.subprocess, "run", fake_run)
    seen = []
    out = au.download_update("http://x", progress=lambda d, t: seen.append((d, t)))
    assert out == staged
    assert calls["cmd"][0] == "curl"
    assert "-C" in calls["cmd"]  # resumable
    # progress was reported at least once (the initial connecting tick)
    assert seen and seen[0] == (0, 10)


def test_download_update_retries_then_fails(monkeypatch, tmp_path):
    app = tmp_path / "persona.AppImage"
    app.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(app))
    monkeypatch.setattr(au, "remote_size", lambda url, timeout=30: 10)

    class R:
        returncode = 7  # curl connection failure

    monkeypatch.setattr(au, "_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(au.subprocess, "run", lambda cmd, capture_output=False: R())
    assert au.download_update("http://x") == ""
