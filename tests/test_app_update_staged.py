import os
import tempfile

from src.services.app_update import updater as au


def _force_linux(monkeypatch):
    monkeypatch.setattr(au._platform, "IS_WINDOWS", False)


def _force_windows(monkeypatch):
    monkeypatch.setattr(au._platform, "IS_WINDOWS", True)


def test_staged_path_empty_when_not_packaged(monkeypatch):
    _force_linux(monkeypatch)
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert au.staged_path("v9.9.9") == ""


def test_staged_path_next_to_appimage_keyed_by_tag(monkeypatch, tmp_path):
    _force_linux(monkeypatch)
    app = tmp_path / "persona.AppImage"
    app.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(app))
    staged = au.staged_path("v9.9.9")
    assert os.path.dirname(staged) == str(tmp_path)
    assert os.path.basename(staged) == ".persona-update-v9.9.9.AppImage.part"
    # per-tag naming: a different version never resumes onto this file
    assert au.staged_path("v8.0.0") != staged


def test_staged_path_windows_temp_keyed_by_tag(monkeypatch):
    _force_windows(monkeypatch)
    monkeypatch.delenv("APPIMAGE", raising=False)  # not needed on Windows
    staged = au.staged_path("v9.9.9")
    assert os.path.dirname(staged) == tempfile.gettempdir()
    assert os.path.basename(staged) == "persona-update-setup-v9.9.9.exe"
    assert au.staged_path("v8.0.0") != staged
    # tag is sanitized into a filesystem-safe slug
    assert os.path.basename(au.staged_path("v1/..\\evil")) == (
        "persona-update-setup-v1_.._evil.exe"
    )


def test_find_ready_staged_returns_complete_file(monkeypatch, tmp_path):
    _force_linux(monkeypatch)
    app = tmp_path / "persona.AppImage"
    app.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(app))
    staged = au.staged_path("v9.9.9")
    with open(staged, "wb") as f:
        f.write(b"y" * 100)
    monkeypatch.setattr(au, "remote_size", lambda url, timeout=30: 100)
    assert au.find_ready_staged("http://x", tag="v9.9.9") == staged


def test_find_ready_staged_ignores_other_versions_file(monkeypatch, tmp_path):
    _force_linux(monkeypatch)
    app = tmp_path / "persona.AppImage"
    app.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(app))
    with open(au.staged_path("v8.0.0"), "wb") as f:
        f.write(b"y" * 100)  # complete file, but for a DIFFERENT release
    monkeypatch.setattr(au, "remote_size", lambda url, timeout=30: 100)
    assert au.find_ready_staged("http://x", tag="v9.9.9") == ""


def test_find_ready_staged_empty_when_incomplete(monkeypatch, tmp_path):
    _force_linux(monkeypatch)
    app = tmp_path / "persona.AppImage"
    app.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(app))
    staged = au.staged_path("v9.9.9")
    with open(staged, "wb") as f:
        f.write(b"y" * 50)  # short of the 100-byte remote
    monkeypatch.setattr(au, "remote_size", lambda url, timeout=30: 100)
    assert au.find_ready_staged("http://x", tag="v9.9.9") == ""


def test_find_ready_staged_empty_when_no_file(monkeypatch, tmp_path):
    _force_linux(monkeypatch)
    app = tmp_path / "persona.AppImage"
    app.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(app))
    monkeypatch.setattr(au, "remote_size", lambda url, timeout=30: 100)
    assert au.find_ready_staged("http://x", tag="v9.9.9") == ""


def test_download_update_uses_curl_and_reports_progress(monkeypatch, tmp_path):
    _force_linux(monkeypatch)
    app = tmp_path / "persona.AppImage"
    app.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(app))
    staged = au.staged_path("v9.9.9")
    stale = au.staged_path("v8.0.0")
    with open(stale, "wb") as f:
        f.write(b"old-version leftover")

    calls = {}

    def fake_run(cmd, capture_output=False, **kwargs):
        calls["cmd"] = cmd
        # simulate curl writing the complete file
        with open(staged, "wb") as f:
            f.write(b"z" * 10)

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(au.subprocess, "run", fake_run)
    seen = []
    out = au.download_update(
        "http://x", progress=lambda d, t: seen.append((d, t)), size=10, tag="v9.9.9"
    )
    assert out == staged
    assert calls["cmd"][0] == "curl"
    assert "-C" in calls["cmd"]  # resumable
    assert staged in calls["cmd"]  # downloads to the per-tag file
    assert not os.path.exists(stale)  # other versions' leftovers cleared first
    # progress was reported at least once (the initial connecting tick)
    assert seen and seen[0] == (0, 10)


def test_download_update_retries_then_fails(monkeypatch, tmp_path):
    _force_linux(monkeypatch)
    app = tmp_path / "persona.AppImage"
    app.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(app))
    attempts = []

    class R:
        returncode = 7  # curl connection failure

    monkeypatch.setattr(au, "_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(
        au.subprocess,
        "run",
        lambda cmd, capture_output=False, **kw: attempts.append(cmd) or R(),
    )
    assert au.download_update("http://x", size=10, tag="v9.9.9") == ""
    assert len(attempts) == 3  # kept retrying up to the attempt cap
