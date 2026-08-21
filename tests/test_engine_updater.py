import os

import pytest

import src.core.platform as _platform
from src.services.engine import updater
from src.services.engine.updater import (
    appimage_url_for,
    is_newer,
    parse_version,
)

WIN_ASSET = "ungoogled-chromium_148.0.7778.215-1.1_windows_x64.zip"
MAC_ASSET = "ungoogled-chromium_148.0.7778.215-1.1_macos.dmg"
LINUX_ASSET = "ungoogled-chromium-148.0.7778.215-1-x86_64.AppImage"
LINUX_TARXZ = "ungoogled-chromium-148.0.7778.215-1-x86_64_linux.tar.xz"
WIN_INSTALLER = "ungoogled-chromium_148.0.7778.215-1.1_installer_x64.exe"


def _force_os(monkeypatch, *, win=False, mac=False, linux=False):
    monkeypatch.setattr(_platform, "IS_WINDOWS", win)
    monkeypatch.setattr(_platform, "IS_MACOS", mac)
    monkeypatch.setattr(_platform, "IS_LINUX", linux)


def test_parse_version():
    assert parse_version("144.0.7559.132") == (144, 0, 7559, 132)
    assert parse_version("") == ()
    assert parse_version("v143.0.1") == (143, 0, 1)


def test_is_newer():
    assert is_newer("144.0.7559.132", "143.0.7000.10") is True
    assert is_newer("144.0.7559.132", "144.0.7559.132") is False
    assert is_newer("144.0.7559.100", "144.0.7559.132") is False


def test_is_newer_edges():
    assert is_newer("144.0.0.1", "") is True       # nothing installed
    assert is_newer("", "144.0.0.1") is False       # no latest info


def test_appimage_url():
    url = appimage_url_for("144.0.7559.132")
    assert url.endswith("/144.0.7559.132/ungoogled-chromium-144.0.7559.132-1-x86_64.AppImage")
    assert url.startswith("https://github.com/adryfish/fingerprint-chromium/")


def test_asset_matches_linux_picks_appimage(monkeypatch):
    _force_os(monkeypatch, linux=True)
    assert updater._asset_matches(LINUX_ASSET) is True
    assert updater._asset_matches(WIN_ASSET) is False
    assert updater._asset_matches(MAC_ASSET) is False
    assert updater._asset_matches(LINUX_TARXZ) is False  # not the AppImage


def test_asset_matches_windows_picks_zip(monkeypatch):
    _force_os(monkeypatch, win=True)
    assert updater._asset_matches(WIN_ASSET) is True
    assert updater._asset_matches(LINUX_ASSET) is False
    assert updater._asset_matches(MAC_ASSET) is False
    assert updater._asset_matches(WIN_INSTALLER) is False  # zip, not the .exe installer


def test_asset_matches_macos_picks_dmg(monkeypatch):
    _force_os(monkeypatch, mac=True)
    assert updater._asset_matches(MAC_ASSET) is True
    assert updater._asset_matches(WIN_ASSET) is False
    assert updater._asset_matches(LINUX_ASSET) is False


def test_fetch_latest_full_selects_per_os_asset(monkeypatch):
    release = {
        "tag_name": "148.0.7778.215",
        "assets": [
            {"name": LINUX_ASSET, "browser_download_url": "http://x/linux", "digest": "sha256:aa"},
            {"name": WIN_ASSET, "browser_download_url": "http://x/win", "digest": "sha256:bb"},
            {"name": MAC_ASSET, "browser_download_url": "http://x/mac", "digest": "sha256:cc"},
        ],
    }

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return __import__("json").dumps(release).encode()

    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **k: FakeResp())

    _force_os(monkeypatch, win=True)
    tag, url, digest = updater.fetch_latest_full()
    assert (tag, url, digest) == ("148.0.7778.215", "http://x/win", "sha256:bb")

    _force_os(monkeypatch, mac=True)
    _, url, _ = updater.fetch_latest_full()
    assert url == "http://x/mac"

    _force_os(monkeypatch, linux=True)
    _, url, _ = updater.fetch_latest_full()
    assert url == "http://x/linux"


def test_download_engine_forwards_digest_verification(monkeypatch, tmp_path):
    # download_engine must pass the digest down; an un-digested asset is refused
    # (allow_missing stays False by default), a digested one verifies as normal.
    calls = []

    def fake_download_to(path, url, timeout, digest, progress, allow_missing=False):
        calls.append((digest, allow_missing))
        return bool(digest) or allow_missing

    monkeypatch.setattr(updater, "_download_to", fake_download_to)
    monkeypatch.setattr(updater, "_install_linux", lambda p: True)
    monkeypatch.setattr(updater, "_install_windows", lambda p: True)
    _force_os(monkeypatch, linux=True)
    monkeypatch.setattr(updater, "ENGINE_DIR", str(tmp_path))

    # digested asset installs
    assert updater.download_engine("http://x/linux", digest="sha256:aa") is True
    # un-digested asset is refused by default (fail-closed)
    assert updater.download_engine("http://x/linux", digest="") is False
    assert calls[-1] == ("", False)
    # ...unless the caller explicitly allows it (Linux predictable-URL fallback)
    assert (
        updater.download_engine("http://x/linux", digest="", allow_unverified=True)
        is True
    )
    assert calls[-1] == ("", True)


def test_ensure_engine_allows_unverified_only_for_linux_fallback(monkeypatch):
    # When fetch_latest_full returns a url but NO digest, that only happens on
    # the Linux predictable-URL fallback — ensure_engine must allow it there.
    _force_os(monkeypatch, linux=True)
    monkeypatch.setattr(updater, "is_installed", lambda: False)
    monkeypatch.setattr(
        updater, "fetch_latest_full", lambda *a, **k: ("148.0", "http://x/linux", "")
    )
    monkeypatch.setattr(updater, "write_version", lambda tag: None)
    seen = {}

    def fake_download(url, timeout=600, digest=None, progress=None, allow_unverified=False):
        seen["allow_unverified"] = allow_unverified
        return True

    monkeypatch.setattr(updater, "download_engine", fake_download)
    ok, msg = updater.ensure_engine(attempts=1)
    assert ok is True
    assert seen["allow_unverified"] is True


def _make_windows_zip(path, members):
    """Write a zip at `path` containing {name: bytes}."""
    import zipfile

    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def test_install_windows_atomic_via_staging(monkeypatch, tmp_path):
    # The Windows zip must not be extracted straight into ENGINE_DIR (chrome.exe
    # could appear before its DLLs). Extract into a staging dir, then move the
    # whole tree into ENGINE_DIR at once.
    _force_os(monkeypatch, win=True)
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    monkeypatch.setattr(updater, "ENGINE_DIR", str(engine_dir))
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(engine_dir / "chrome.exe"))

    zip_path = tmp_path / "win.zip"
    _make_windows_zip(
        zip_path,
        {
            "chrome-win/chrome.exe": b"MZ" + b"\x00" * 100,
            "chrome-win/some.dll": b"\x00" * 50,
            "chrome-win/locales/en.pak": b"pak",
        },
    )

    assert updater._install_windows(str(zip_path)) is True
    assert (engine_dir / "chrome.exe").read_bytes().startswith(b"MZ")
    assert (engine_dir / "some.dll").exists()
    assert (engine_dir / "locales" / "en.pak").read_bytes() == b"pak"
    # staging must be cleaned up, not left beside the engine
    assert not any(p.name.startswith(".staging") for p in engine_dir.iterdir())


def test_download_engine_writes_marker_last(monkeypatch, tmp_path):
    # The completion marker must be written only AFTER a successful install, so
    # is_installed() can gate on it. If install fails, no marker.
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    monkeypatch.setattr(updater, "ENGINE_DIR", str(engine_dir))
    monkeypatch.setattr(updater, "MARKER_FILE", str(engine_dir / ".engine-complete"))
    _force_os(monkeypatch, linux=True)
    monkeypatch.setattr(
        updater, "_download_to", lambda *a, **k: True
    )

    monkeypatch.setattr(updater, "_install_linux", lambda p: True)
    assert updater.download_engine("http://x/e", digest="sha256:aa") is True
    assert (engine_dir / ".engine-complete").exists()

    # failed install → no marker
    (engine_dir / ".engine-complete").unlink()
    monkeypatch.setattr(updater, "_install_linux", lambda p: False)
    assert updater.download_engine("http://x/e", digest="sha256:aa") is False
    assert not (engine_dir / ".engine-complete").exists()


def test_download_engine_serialized_by_lock(monkeypatch, tmp_path):
    # Two concurrent installs (UI update thread + ensure_engine) must not run
    # their extract/move at the same time into the shared ENGINE_DIR.
    import threading

    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    monkeypatch.setattr(updater, "ENGINE_DIR", str(engine_dir))
    monkeypatch.setattr(updater, "MARKER_FILE", str(engine_dir / ".engine-complete"))
    _force_os(monkeypatch, linux=True)
    monkeypatch.setattr(updater, "_download_to", lambda *a, **k: True)

    overlap = {"max": 0, "cur": 0}
    lock = threading.Lock()

    def slow_install(p):
        with lock:
            overlap["cur"] += 1
            overlap["max"] = max(overlap["max"], overlap["cur"])
        # busy a moment so a second thread would overlap if unserialized
        for _ in range(100000):
            pass
        with lock:
            overlap["cur"] -= 1
        return True

    monkeypatch.setattr(updater, "_install_linux", slow_install)

    threads = [
        threading.Thread(
            target=lambda: updater.download_engine("http://x/e", digest="sha256:aa")
        )
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    # install never ran concurrently
    assert overlap["max"] == 1


def test_ensure_engine_refuses_unverified_off_linux(monkeypatch):
    # On Windows/macOS a missing digest is NOT expected (assets always carry one)
    # -> ensure_engine must NOT allow unverified there.
    _force_os(monkeypatch, win=True)
    monkeypatch.setattr(updater, "is_installed", lambda: False)
    monkeypatch.setattr(
        updater, "fetch_latest_full", lambda *a, **k: ("148.0", "http://x/win", "")
    )
    monkeypatch.setattr(updater, "write_version", lambda tag: None)
    seen = {}

    def fake_download(url, timeout=600, digest=None, progress=None, allow_unverified=False):
        seen["allow_unverified"] = allow_unverified
        return allow_unverified  # would only succeed if (wrongly) allowed

    monkeypatch.setattr(updater, "download_engine", fake_download)
    ok, _msg = updater.ensure_engine(attempts=1)
    assert seen["allow_unverified"] is False
    assert ok is False


# --- PS-38: a FAILED Chromium upgrade must leave the working build behind -----
#
# PS-32 made a failed upgrade DETECTABLE (the sentinel keeps is_installed()
# False over a half-promoted tree). These pin the other half: RECOVERABLE. The
# Windows path is driven end to end here, over a real multi-file zip, because
# _force_os + _make_windows_zip make that reachable in any container.


def _populate_engine(engine_dir):
    """An ENGINE_DIR holding a previous, WORKING build."""
    (engine_dir / "chrome.exe").write_bytes(b"OLD-ENGINE-EXE")
    (engine_dir / "some.dll").write_bytes(b"OLD-DLL")
    (engine_dir / "locales").mkdir()
    (engine_dir / "locales" / "en.pak").write_bytes(b"OLD-PAK")
    # a file only the OLD build has: on main this one is destroyed outright
    (engine_dir / "old_only.dat").write_bytes(b"OLD-ONLY")


def _new_build_zip(path):
    _make_windows_zip(
        path,
        {
            "chrome-win/chrome.exe": b"MZ" + b"\x00" * 100,
            "chrome-win/some.dll": b"\x00" * 50,
            "chrome-win/locales/en.pak": b"pak",
        },
    )


def test_failed_windows_promotion_restores_the_previous_build(monkeypatch, tmp_path):
    # AC2. RED ON MAIN: _promote_staging used to rmtree/os.remove each old entry
    # before moving the new one on top, so a promotion that died partway left a
    # tree that was part old build, part new — and old_only.dat gone entirely,
    # with nothing anywhere to go back to. The previous build must survive
    # BYTE-IDENTICAL when the promotion raises.
    _force_os(monkeypatch, win=True)
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    _populate_engine(engine_dir)
    monkeypatch.setattr(updater, "ENGINE_DIR", str(engine_dir))
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(engine_dir / "chrome.exe"))

    zip_path = tmp_path / "win.zip"
    _new_build_zip(zip_path)

    # fail the promotion PARTWAY: let some entries move, then blow up. Which
    # entry trips it doesn't matter — os.listdir order is arbitrary — only that
    # the failure lands mid-loop, with some old entries already moved aside.
    real_move = updater.shutil.move
    calls = {"n": 0}

    def failing_move(src, dst):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("No space left on device")
        return real_move(src, dst)

    monkeypatch.setattr(updater.shutil, "move", failing_move)

    assert updater._install_windows(str(zip_path)) is False

    # every byte of the previous build is back, exactly as it was
    assert (engine_dir / "chrome.exe").read_bytes() == b"OLD-ENGINE-EXE"
    assert (engine_dir / "some.dll").read_bytes() == b"OLD-DLL"
    assert (engine_dir / "locales" / "en.pak").read_bytes() == b"OLD-PAK"
    assert (engine_dir / "old_only.dat").read_bytes() == b"OLD-ONLY"
    # and no backup/staging debris is left beside the engine
    assert not (engine_dir / updater.BACKUP_NAME).exists()
    assert not any(p.name.startswith(".staging") for p in engine_dir.iterdir())


def test_successful_windows_upgrade_leaves_no_backup_behind(monkeypatch, tmp_path):
    # AC4. The rollback must not become a disk leak: on the SUCCESS path the
    # backup of the previous build is dropped, so ENGINE_DIR holds the new build
    # and nothing else. (Upgrading over a populated dir, unlike the pinning test
    # next door, which installs into an empty one.)
    _force_os(monkeypatch, win=True)
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    _populate_engine(engine_dir)
    monkeypatch.setattr(updater, "ENGINE_DIR", str(engine_dir))
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(engine_dir / "chrome.exe"))

    zip_path = tmp_path / "win.zip"
    _new_build_zip(zip_path)

    assert updater._install_windows(str(zip_path)) is True
    # the NEW build is live
    assert (engine_dir / "chrome.exe").read_bytes().startswith(b"MZ")
    assert (engine_dir / "locales" / "en.pak").read_bytes() == b"pak"
    # no backup, no staging debris
    assert not (engine_dir / updater.BACKUP_NAME).exists()
    assert not any(p.name.startswith(".staging") for p in engine_dir.iterdir())
    assert not any(p.name.startswith(".engine-backup") for p in engine_dir.iterdir())


def test_promotion_backup_moves_the_old_build_it_never_copies_it(monkeypatch, tmp_path):
    # AC7. A Chromium tree is ~300-600MB; copying it would double peak disk on
    # the very path whose failure mode is a disk-full, and a copy would drop the
    # macOS signature/resource forks. The backup must be a RENAME. Proven two
    # ways: the backup shares the ORIGINAL's inode, and no recursive copy is
    # called during promotion.
    _force_os(monkeypatch, win=True)
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    _populate_engine(engine_dir)
    monkeypatch.setattr(updater, "ENGINE_DIR", str(engine_dir))
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(engine_dir / "chrome.exe"))

    old_inode = (engine_dir / "chrome.exe").stat().st_ino
    seen_inode = {}

    def no_recursive_copy(*a, **k):
        raise AssertionError("copytree: the old build must be MOVED, not copied")

    monkeypatch.setattr(updater.shutil, "copytree", no_recursive_copy)

    staging = engine_dir / ".staging"
    staging.mkdir()
    (staging / "chrome.exe").write_bytes(b"NEW-EXE")

    real_move = updater.shutil.move

    def peeking_move(src, dst):
        # while the promotion is mid-flight, the backup already holds the old
        # build — capture its inode before the backup is dropped on success
        backup = engine_dir / updater.BACKUP_NAME / "chrome.exe"
        if backup.exists():
            seen_inode["backup"] = backup.stat().st_ino
        return real_move(src, dst)

    monkeypatch.setattr(updater.shutil, "move", peeking_move)
    updater._promote_staging(str(staging))

    # same inode => renamed, not copied: O(1), no extra disk, bytes untouched
    assert seen_inode.get("backup") == old_inode
    assert (engine_dir / "chrome.exe").read_bytes() == b"NEW-EXE"


def test_a_failed_rollback_keeps_the_backup_instead_of_deleting_it(
    monkeypatch, tmp_path
):
    import os

    import pytest

    # The second-order failure. The rollback itself can fail — on Windows an
    # antivirus scanning a freshly-written .exe raises PermissionError(32) on
    # the restore's rename. If the promotion then dropped the backup anyway, the
    # ONLY surviving copy of the working build would be deleted behind it: a
    # failed upgrade would become no engine at all, which is strictly worse than
    # the mixed tree this slice set out to fix. The backup must SURVIVE a
    # rollback that could not be completed.
    _force_os(monkeypatch, win=True)
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    _populate_engine(engine_dir)
    monkeypatch.setattr(updater, "ENGINE_DIR", str(engine_dir))
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(engine_dir / "chrome.exe"))

    staging = engine_dir / ".staging"
    staging.mkdir()
    (staging / "chrome.exe").write_bytes(b"NEW-EXE")
    (staging / "some.dll").write_bytes(b"NEW-DLL")

    # fail the promotion mid-loop...
    real_move = updater.shutil.move
    calls = {"n": 0}

    def failing_move(src, dst):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("No space left on device")
        return real_move(src, dst)

    # ...and then fail the ROLLBACK's rename too
    backup_root = engine_dir / updater.BACKUP_NAME
    real_replace = os.replace

    def locked_replace(src, dst):
        if str(src).startswith(str(backup_root)):
            raise PermissionError(32, "The process cannot access the file")
        return real_replace(src, dst)

    monkeypatch.setattr(updater.shutil, "move", failing_move)
    monkeypatch.setattr(os, "replace", locked_replace)

    with pytest.raises(OSError):
        updater._promote_staging(str(staging))

    monkeypatch.undo()

    # The working build is still on disk, recoverable by hand, because the
    # backup was NOT deleted after a rollback that did not complete.
    assert backup_root.exists()
    recovered = list(backup_root.iterdir())
    assert recovered, "the backup dir survived but is empty — nothing to recover"
    assert (backup_root / "chrome.exe").read_bytes() == b"OLD-ENGINE-EXE"


def test_a_rolled_back_promotion_removes_files_only_the_new_build_had(
    monkeypatch, tmp_path
):
    import os

    import pytest

    # Restoring the old entries is not the whole rollback. An entry the NEW
    # build introduces has no previous counterpart, so it is moved in with no
    # backup — and if it is left behind, the rolled-back tree is STILL part old
    # and part new, which is the shape this slice exists to prevent. Chromium
    # upgrades routinely add files, so this is the ordinary case, not an edge.
    _force_os(monkeypatch, win=True)
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    _populate_engine(engine_dir)
    monkeypatch.setattr(updater, "ENGINE_DIR", str(engine_dir))
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(engine_dir / "chrome.exe"))

    staging = engine_dir / ".staging"
    staging.mkdir()
    (staging / "newlib.dll").write_bytes(b"NEW-ONLY-LIB")  # no old counterpart
    (staging / "chrome.exe").write_bytes(b"NEW-EXE")
    (staging / "some.dll").write_bytes(b"NEW-DLL")

    # Force the new-build-only entry to be promoted FIRST, so it is definitely
    # in place when the failure lands (os.listdir order is otherwise arbitrary).
    real_listdir = updater.os.listdir

    def newlib_first(path):
        names = real_listdir(path)
        if os.path.abspath(path) == os.path.abspath(str(staging)):
            return sorted(names, key=lambda n: n != "newlib.dll")
        return names

    real_move = updater.shutil.move
    calls = {"n": 0}

    def failing_move(src, dst):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("No space left on device")
        return real_move(src, dst)

    monkeypatch.setattr(updater.os, "listdir", newlib_first)
    monkeypatch.setattr(updater.shutil, "move", failing_move)

    with pytest.raises(OSError):
        updater._promote_staging(str(staging))

    # the previous build is back, byte-identical...
    assert (engine_dir / "chrome.exe").read_bytes() == b"OLD-ENGINE-EXE"
    assert (engine_dir / "old_only.dat").read_bytes() == b"OLD-ONLY"
    # ...and the new build's own file did NOT survive the rollback
    assert not (engine_dir / "newlib.dll").exists()
    assert not (engine_dir / updater.BACKUP_NAME).exists()


# --- PS-43: an unattended install must not replace a tree in use -------------
#
# These drive the REAL download_engine. The app-level check in
# _auto_update_engine is only an early exit — it answers minutes before the
# bytes are ready — so the guard that actually protects a live session has to
# be tested HERE, at the point of replacement, with nothing stubbed between the
# oracle and the os.replace it is guarding.


def _digest_of(data: bytes) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(data).hexdigest()


def _engine_at(monkeypatch, tmp_path, old: bytes = b"OLD-ENGINE"):
    """An installed Linux engine whose binary holds `old`, ready to be upgraded."""
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    binary = engine_dir / "chrome"
    binary.write_bytes(old)
    monkeypatch.setattr(updater, "ENGINE_DIR", str(engine_dir))
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(binary))
    monkeypatch.setattr(updater, "MARKER_FILE", str(engine_dir / ".engine-complete"))
    monkeypatch.setattr(updater, "VERSION_FILE", str(engine_dir / "version.txt"))
    (engine_dir / ".engine-complete").write_text("ok")
    _force_os(monkeypatch, linux=True)
    return engine_dir, binary


def test_a_profile_launched_during_the_download_stops_the_install(monkeypatch, tmp_path):
    """THE regression this guard exists for, and the one a decision-time check
    cannot catch: idle when the fetch is decided, running by the time the bytes
    land. A Chromium asset takes minutes to download, so this is not exotic.

    The oracle flips DURING the download — exactly what a profile launching
    mid-fetch looks like — so a guard consulted only beforehand would sail
    straight into replacing the tree that session is executing from."""
    engine_dir, binary = _engine_at(monkeypatch, tmp_path)
    new = b"NEW-ENGINE"

    running = {"yes": False}

    def download_and_launch_a_profile(path, url, timeout, digest, progress,
                                      allow_missing=False):
        # the bytes arrive...
        with open(path, "wb") as f:
            f.write(new)
        # ...and while they were arriving, the operator launched a profile.
        running["yes"] = True
        return True

    monkeypatch.setattr(updater, "_download_to", download_and_launch_a_profile)
    updater.set_in_use_provider(lambda: running["yes"])
    monkeypatch.setattr(updater, "_in_use_provider", lambda: running["yes"])

    with pytest.raises(updater.InstallDeferred):
        updater.download_engine(
            "http://x/e", digest=_digest_of(new), defer_if_in_use=True
        )

    # The live session's binary is untouched — this is the whole point.
    assert binary.read_bytes() == b"OLD-ENGINE"


def test_a_deferred_install_leaves_the_engine_launchable(monkeypatch, tmp_path):
    """A deferral must be INERT. The marker/sentinel are what make
    is_installed() answer, and launch_or_stop gates on it — so writing them and
    then bailing would report a perfectly good engine as missing and block the
    very launches the deferral is protecting."""
    engine_dir, binary = _engine_at(monkeypatch, tmp_path)
    new = b"NEW-ENGINE"
    monkeypatch.setattr(
        updater, "_download_to",
        lambda path, *a, **k: (open(path, "wb").write(new), True)[1],
    )
    monkeypatch.setattr(updater, "_in_use_provider", lambda: True)

    with pytest.raises(updater.InstallDeferred):
        updater.download_engine(
            "http://x/e", digest=_digest_of(new), defer_if_in_use=True
        )

    assert updater.is_installed() is True
    assert not os.path.exists(updater._installing_file()), (
        "a deferral must not leave the in-progress sentinel behind"
    )


def test_the_deferred_bytes_are_reused_not_downloaded_again(monkeypatch, tmp_path):
    """Deferring has to be CHEAP or it is the wrong answer: the hourly poll
    retries, and re-fetching a ~300MB asset every hour while a profile stays
    open would be worse than the stall this ticket removes. The verified asset
    stays on disk and the retry installs from it."""
    engine_dir, binary = _engine_at(monkeypatch, tmp_path)
    new = b"NEW-ENGINE"
    digest = _digest_of(new)
    downloads = []

    def counted_download(path, *a, **k):
        downloads.append(1)
        with open(path, "wb") as f:
            f.write(new)
        return True

    monkeypatch.setattr(updater, "_download_to", counted_download)

    # First pass: a profile is running, so the install defers.
    monkeypatch.setattr(updater, "_in_use_provider", lambda: True)
    with pytest.raises(updater.InstallDeferred):
        updater.download_engine("http://x/e", digest=digest, defer_if_in_use=True)
    assert downloads == [1]

    # Later: profiles closed. The retry installs WITHOUT downloading again.
    monkeypatch.setattr(updater, "_in_use_provider", lambda: False)
    assert updater.download_engine(
        "http://x/e", digest=digest, defer_if_in_use=True
    ) is True

    assert downloads == [1], "the deferred asset must be reused, not re-fetched"
    assert binary.read_bytes() == new
    assert updater.is_installed() is True


def test_a_corrupt_leftover_asset_is_re_downloaded_not_installed(monkeypatch, tmp_path):
    """The reuse path re-verifies rather than trusting the file's presence: a
    leftover that was truncated or tampered with on disk must not be promoted
    into the engine tree just because it has the right name."""
    engine_dir, binary = _engine_at(monkeypatch, tmp_path)
    new = b"NEW-ENGINE"
    (engine_dir / "e").write_bytes(b"TAMPERED")  # right name, wrong bytes

    downloads = []

    def counted_download(path, *a, **k):
        downloads.append(1)
        with open(path, "wb") as f:
            f.write(new)
        return True

    monkeypatch.setattr(updater, "_download_to", counted_download)
    monkeypatch.setattr(updater, "_in_use_provider", lambda: False)

    assert updater.download_engine(
        "http://x/e", digest=_digest_of(new), defer_if_in_use=True
    ) is True

    assert downloads == [1], "an unverifiable leftover must be re-downloaded"
    assert binary.read_bytes() == new


def test_the_operators_click_installs_even_while_a_profile_runs(monkeypatch, tmp_path):
    """Only the UNATTENDED path defers. The operator asked for this one, and a
    silent no-op would look exactly like the stall this ticket exists to
    remove — so defer_if_in_use is off by default and the click keeps today's
    behaviour."""
    engine_dir, binary = _engine_at(monkeypatch, tmp_path)
    new = b"NEW-ENGINE"
    monkeypatch.setattr(
        updater, "_download_to",
        lambda path, *a, **k: (open(path, "wb").write(new), True)[1],
    )
    monkeypatch.setattr(updater, "_in_use_provider", lambda: True)

    assert updater.download_engine("http://x/e", digest=_digest_of(new)) is True
    assert binary.read_bytes() == new


def test_an_unwired_or_broken_oracle_defers(monkeypatch, tmp_path):
    """Fails CLOSED, opposite of the prune guard's fail-open default: the cost
    of a false 'idle' here is a binary swapped under a running browser, whereas
    the cost of a false 'in use' is waiting for the next check."""
    engine_dir, binary = _engine_at(monkeypatch, tmp_path)
    new = b"NEW-ENGINE"
    monkeypatch.setattr(
        updater, "_download_to",
        lambda path, *a, **k: (open(path, "wb").write(new), True)[1],
    )

    def boom():
        raise RuntimeError("launcher unavailable")

    for provider in (None, boom):
        monkeypatch.setattr(updater, "_in_use_provider", provider)
        with pytest.raises(updater.InstallDeferred):
            updater.download_engine(
                "http://x/e", digest=_digest_of(new), defer_if_in_use=True
            )
        assert binary.read_bytes() == b"OLD-ENGINE"
