"""The self-update must be unbrickable: it verifies the new AppImage actually
launches BEFORE replacing the live one, keeps a backup it restores on failure,
and never deletes the staged file when it bails. Regression for v2.1.3, which
replaced the running AppImage with one that wouldn't start ("open dir error")
and left the app unopenable."""

import hashlib
import os
import sys

import pytest

import src.services.app_update.updater as au

# AppImage self-update is Linux-only: it atomically replaces the running binary
# in place (os.replace over an open file), which POSIX allows but Windows
# refuses ("Bad file descriptor" — the file is locked while open). Windows/macOS
# get their own update mechanism; these regression tests cover the AppImage path.
pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="AppImage in-place self-update is Linux-only (macOS is POSIX too but "
    "can't apply an AppImage)",
)


def _stub_target(monkeypatch, tmp_path):
    target = tmp_path / "persona.AppImage"
    target.write_bytes(b"old")
    staged = tmp_path / "p.AppImage.part"
    staged.write_bytes(b"new")
    monkeypatch.setattr(au, "installed_appimage_path", lambda: str(target))
    # PS-6 made the missing-checksum policy fail-CLOSED, so an AppImage with no
    # published digest never reaches the swap. This staged name carries no
    # recoverable tag, so publish the REAL digest of the staged bytes — the way
    # CI publishes one for every released asset. These tests are about the
    # UNBRICKABLE SWAP (launch probe, backup, rollback, relaunch), all of which
    # sits behind that gate; stubbing verify_staged_installer out would take the
    # gate off the path they exercise, so the genuine verify runs and passes.
    monkeypatch.setattr(
        au, "fetch_expected_sha256",
        lambda tag, **k: hashlib.sha256(staged.read_bytes()).hexdigest(),
    )
    return target, staged


def test_aborts_and_keeps_working_version_when_new_build_wont_launch(
    monkeypatch, tmp_path
):
    target, staged = _stub_target(monkeypatch, tmp_path)
    # the new build fails the launch probe (the v2.1.3 brick scenario)
    monkeypatch.setattr(au, "verify_appimage_runs", lambda p, **k: False)
    # replace/execv must never be reached
    monkeypatch.setattr(
        au.os, "replace", lambda *a: (_ for _ in ()).throw(AssertionError("replaced!"))
    )
    msgs = []
    ok = au.apply_and_restart(str(staged), log=msgs.append)
    assert ok is False
    assert target.read_bytes() == b"old"  # untouched
    assert staged.exists()  # saved for retry
    assert any("didn't launch" in m.lower() for m in msgs)


def test_restores_backup_when_replace_fails(monkeypatch, tmp_path):
    target, staged = _stub_target(monkeypatch, tmp_path)
    monkeypatch.setattr(au, "verify_appimage_runs", lambda p, **k: True)
    real_replace = au.os.replace
    calls = {"n": 0}

    def replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("Text file busy")  # the staged->target swap fails
        return real_replace(src, dst)  # the backup->target restore succeeds

    monkeypatch.setattr(au.os, "replace", replace)
    msgs = []
    ok = au.apply_and_restart(str(staged), log=msgs.append)
    assert ok is False
    assert target.read_bytes() == b"old"  # restored from backup
    assert any("restoring backup" in m.lower() for m in msgs)


def test_logs_and_keeps_staged_when_not_appimage(monkeypatch, tmp_path):
    staged = tmp_path / "p.AppImage.part"
    staged.write_bytes(b"x")
    monkeypatch.setattr(au, "installed_appimage_path", lambda: None)
    msgs = []
    ok = au.apply_and_restart(str(staged), log=msgs.append)
    assert ok is False
    assert any("AppImage" in m for m in msgs)
    assert staged.exists()


def test_relaunch_failure_is_logged(monkeypatch, tmp_path):
    target, staged = _stub_target(monkeypatch, tmp_path)
    monkeypatch.setattr(au, "verify_appimage_runs", lambda p, **k: True)
    monkeypatch.setattr(
        au.os, "execv", lambda *a: (_ for _ in ()).throw(OSError("no fuse"))
    )
    msgs = []
    ok = au.apply_and_restart(str(staged), log=msgs.append)
    assert ok is False
    assert any("relaunch failed" in m.lower() for m in msgs)


def test_verify_appimage_runs_false_for_missing_file(tmp_path):
    assert au.verify_appimage_runs(str(tmp_path / "nope")) is False


# --- real-AppImage probe checks (skipped where no real AppImage is present,
#     e.g. CI; they run in dev-WS where a real type-2 AppImage exists) ---

import os  # noqa: E402

import pytest  # noqa: E402

_REAL = next(
    (
        p
        for p in (
            "/home/user/.persona/engine/fpchrome.AppImage",
            "/tmp/persona-flet.AppImage",
        )
        if os.path.isfile(p)
    ),
    None,
)


@pytest.mark.skipif(_REAL is None, reason="no real AppImage available")
def test_probe_accepts_a_real_launchable_appimage():
    assert au.verify_appimage_runs(_REAL) is True


@pytest.mark.skipif(_REAL is None, reason="no real AppImage available")
def test_probe_rejects_a_truncated_appimage(tmp_path):
    # the v2.1.3 brick class: the runtime starts but the squashfs payload is
    # unreadable (truncated/corrupt download) — the swap must never happen
    broken = tmp_path / "broken.AppImage"
    with open(_REAL, "rb") as f:
        broken.write_bytes(f.read(1 << 20))
    broken.chmod(0o755)
    assert au.verify_appimage_runs(str(broken)) is False
