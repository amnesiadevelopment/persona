import os

import pytest

import src.core.platform as _platform
from src.services.engine import updater
from src.services.engine.updater import (
    engine_tag,
    is_engine_tag,
    is_newer,
    parse_version,
    version_from_tag,
)

# The PERSONIUM asset names, per RELEASING.md's table. Engine assets carry the
# `personium-` prefix AND an explicit OS marker — see _asset_matches for why
# both anchors exist and why neither may be dropped.
WIN_ASSET = "personium-148.0.7778.215-windows-x86_64.zip"
MAC_ASSET = "personium-148.0.7778.215-macos-x86_64.dmg"
LINUX_ASSET = "personium-148.0.7778.215-linux-x86_64.AppImage"
LINUX_TARXZ = "personium-148.0.7778.215-linux-x86_64.tar.xz"
WIN_INSTALLER = "personium-148.0.7778.215-windows-x86_64-installer.exe"
ENGINE_TAG = "personium-148.0.7778.215"
ENGINE_VERSION = "148.0.7778.215"


def _force_os(monkeypatch, *, win=False, mac=False, linux=False):
    monkeypatch.setattr(_platform, "IS_WINDOWS", win)
    monkeypatch.setattr(_platform, "IS_MACOS", mac)
    monkeypatch.setattr(_platform, "IS_LINUX", linux)


def _copy_zip_to(src, dst):
    """Stand in for a real download: drop a prepared zip where _download_to
    would have written the asset. Returns None; callers discard it."""
    import shutil as _sh

    return _sh.copyfile(str(src), str(dst))


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


def test_engine_tag_and_version_round_trip():
    """The `personium-` prefix lives on the PUBLISHED TAG and nowhere else.

    Re-points what test_appimage_url used to assert. That test pinned the
    Linux predictable-URL fallback's hardcoded adryfish URL; the fallback is
    gone (PS-305 — see the note where it used to live), so the assertion is
    replaced by the mapping that now stands between a published tag and every
    on-disk record, rather than deleted.

    The bare-version half is LOAD-BEARING: version.txt is the sole source of the
    Chromium version an Android profile advertises, so a prefixed string
    recorded there would leak into what a page can read."""
    assert is_engine_tag(ENGINE_TAG) is True
    assert is_engine_tag("v3.0.2") is False, "an APPLICATION release is not an engine one"
    assert is_engine_tag("") is False

    assert version_from_tag(ENGINE_TAG) == ENGINE_VERSION
    assert version_from_tag(ENGINE_VERSION) == ENGINE_VERSION  # already bare
    assert engine_tag(ENGINE_VERSION) == ENGINE_TAG
    assert engine_tag(ENGINE_TAG) == ENGINE_TAG, "must not double-prefix"
    assert engine_tag("") == ""

    # And the recorded value stays something parse_version handles.
    assert parse_version(version_from_tag(ENGINE_TAG)) == (148, 0, 7778, 215)


def test_appimage_url_fallback_is_gone():
    """The Linux predictable-URL fallback was REMOVED, not re-pointed.

    Pinned so it cannot come back by accident: it built a download URL by
    string-formatting a tag, it carried no digest (so PS-49 refuses whatever it
    produced anyway), and against our own releases a missing per-OS asset is a
    broken release that must be refused visibly rather than guessed at."""
    assert not hasattr(updater, "appimage_url_for")


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
        "tag_name": ENGINE_TAG,
        "prerelease": True,
        "assets": [
            {"name": LINUX_ASSET, "browser_download_url": "http://x/linux", "digest": "sha256:aa"},
            {"name": WIN_ASSET, "browser_download_url": "http://x/win", "digest": "sha256:bb"},
            {"name": MAC_ASSET, "browser_download_url": "http://x/mac", "digest": "sha256:cc"},
        ],
    }
    # PS-305 discovery is TWO requests: the tag refs (server-filtered by our
    # prefix), then that tag's release document. So the fake answers BY URL.
    refs = [{"ref": f"refs/tags/{ENGINE_TAG}"}]

    class FakeResp:
        def __init__(self, payload): self._payload = payload
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return __import__("json").dumps(self._payload).encode()

    def fake_urlopen(req, *a, **k):
        url = getattr(req, "full_url", req)
        return FakeResp(refs if "matching-refs" in url else release)

    monkeypatch.setattr(updater.urllib.request, "urlopen", fake_urlopen)

    _force_os(monkeypatch, win=True)
    tag, url, digest = updater.fetch_latest_full()
    assert (tag, url, digest) == (ENGINE_VERSION, "http://x/win", "sha256:bb")

    _force_os(monkeypatch, mac=True)
    _, url, _ = updater.fetch_latest_full()
    assert url == "http://x/mac"

    _force_os(monkeypatch, linux=True)
    _, url, _ = updater.fetch_latest_full()
    assert url == "http://x/linux"


def test_download_engine_refuses_an_undigested_asset_with_no_way_to_opt_out(
    monkeypatch, tmp_path
):
    """download_engine passes the digest down, and an un-digested asset is
    REFUSED AT THE TRANSFER ITSELF with no caller-supplied escape (PS-49).

    The refusal lives here, not at a caller, and that is the point of this test
    (PS-49 round 2): download_engine is the one thing BOTH entry points reach —
    ensure_engine on first install, _update_engine_async on the sidebar update.
    A refusal written at either caller covers one of them, which is exactly how
    the update path kept saying "Engine update failed" for a condition retrying
    cannot change.

    Asserts the raise happens BEFORE any bytes move: the transfer is never
    entered at all, so this is not merely a download that fails closed.

    The TypeError assertion pins the seam where the permission used to enter —
    `allow_unverified` is gone from the signature, so a caller cannot re-open
    the hole by passing it."""
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
    assert calls[-1] == ("sha256:aa", False)

    # un-digested asset is REFUSED — on Linux, the OS that used to be exempt —
    # and refused by RAISING, not by returning False. False is the transfer-
    # failed answer and both callers render it as "download failed"; this is a
    # refusal, and must be distinguishable from a network problem.
    before = len(calls)
    with pytest.raises(updater.EngineUnverifiable) as excinfo:
        updater.download_engine("http://x/e.AppImage", digest="", tag="148.0")
    assert len(calls) == before, "no bytes may move for an asset we would refuse"

    msg = str(excinfo.value)
    assert "no sha256 digest" in msg
    assert "e.AppImage" in msg, "the refusal must name what could not be verified"
    assert "148.0" in msg
    assert "download failure" in msg, "must say it is NOT a transfer failure"

    # a digest that ARRIVED and is unusable is NOT this refusal — it is an
    # ordinary mismatch for the verify gate to reject. Collapsing the two would
    # describe a malformed digest to the operator as an upstream omission.
    # Asserted as "reaches the transfer instead of raising": _download_to is
    # stubbed here, so this test cannot (and must not claim to) show what the
    # real verify gate does with "   " — only that this refusal declines to own
    # it. test_engine_ensure.py covers the gate's own answer.
    before = len(calls)
    updater.download_engine("http://x/linux", digest="   ")
    assert calls[before:] == [("   ", False)], (
        "an unusable-but-present digest belongs to the verify gate, not to the "
        "no-digest-was-published refusal"
    )

    # the opt-in no longer exists to be passed
    with pytest.raises(TypeError):
        updater.download_engine("http://x/linux", digest="", allow_unverified=True)


def test_ensure_engine_refuses_an_undigested_asset_on_linux_too(monkeypatch):
    """The carve-out this ticket removes (PS-49): Linux used to install an
    un-digested asset that Windows and macOS refused.

    Measured against upstream on 2026-08-21, the exemption's premise was false
    twice over — every asset persona matches carries a sha256, and the
    predictable-URL fallback the exemption existed for 404s on every release
    where it actually fires. So Linux now refuses like everyone else.

    The refusal is RAISED BY download_engine (round 2), not owned here, so this
    asserts ensure_engine translates it into its (ok, message) contract and gets
    the reason to the operator — the onboarding caller reads only `ok` and
    discards the message, so logging is the only way it is ever seen.

    Asserts the REFUSAL REACHED THE OPERATOR IN ITS OWN WORDS, not just that
    ok is False: an unverifiable asset is a fourth situation beside known-bad,
    above-ceiling and a failed transfer, and 'download failed' would send an
    operator retrying forever against something retrying cannot change."""
    _force_os(monkeypatch, linux=True)
    monkeypatch.setattr(updater, "is_installed", lambda: False)
    monkeypatch.setattr(
        updater, "fetch_latest_full", lambda *a, **k: ("148.0", "http://x/e.AppImage", "")
    )
    monkeypatch.setattr(updater, "write_version", lambda tag: None)

    # NOT stubbed: the real download_engine must be the thing that refuses, so
    # this test fails if the guard is ever moved back up to the caller. The
    # transfer beneath it IS stubbed, and asserts it is never reached.
    moved = []
    monkeypatch.setattr(
        updater, "_download_to", lambda *a, **k: moved.append(1) or True
    )
    logged = []
    ok, msg = updater.ensure_engine(attempts=1, log=logged.append)

    assert ok is False
    assert moved == [], "an unverified asset must not be downloaded at all"
    # the fourth message, distinguishable from a transfer failure
    assert "download failed" not in msg.lower()
    assert "no sha256 digest" in msg
    assert "e.AppImage" in msg, "the refusal must name what could not be verified"
    assert logged and logged[-1] == msg, "the reason must reach the operator"


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


def test_ensure_engine_refuses_an_undigested_asset_off_linux_as_it_always_did(
    monkeypatch,
):
    """The half that was already correct, kept as a regression: Windows/macOS
    refuse an un-digested asset. After PS-49 this is no longer a per-OS rule but
    the same single rule, so this test and its Linux sibling above now assert
    identical behaviour on purpose — that identity IS the fix."""
    _force_os(monkeypatch, win=True)
    monkeypatch.setattr(updater, "is_installed", lambda: False)
    monkeypatch.setattr(
        updater, "fetch_latest_full", lambda *a, **k: ("148.0", "http://x/e.zip", "")
    )
    monkeypatch.setattr(updater, "write_version", lambda tag: None)

    # Stubbed at the TRANSFER, not at download_engine: the refusal lives inside
    # download_engine now (round 2), so stubbing that out would stub out the
    # very behaviour under test and pass against a restored carve-out.
    moved = []
    monkeypatch.setattr(
        updater, "_download_to", lambda *a, **k: moved.append(1) or True
    )
    ok, msg = updater.ensure_engine(attempts=1)

    assert ok is False
    assert moved == [], "an unverified asset must not be downloaded at all"
    assert "no sha256 digest" in msg


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
    (engine_dir / ".engine-complete").write_text("ok", encoding="utf-8")
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


def test_an_unverifiable_leftover_is_refused_outright_not_reused(monkeypatch, tmp_path):
    """The case with NO digest to check against — where reuse is not merely
    unverified but unverifiABLE.

    Distinct from the corrupt-leftover test above, which supplies a real digest
    and so exercises the hash comparison. Here there is nothing to compare to:
    verify_file short-circuits on a missing digest and answers `allow_missing`
    WITHOUT READING THE FILE, so a reuse gate that forwarded such a permission
    would accept any bytes holding this name and promote them into the engine
    tree unread.

    THE ANSWER GOT STRICTER IN ROUND 2, and this test now pins the stricter one.
    It used to assert the leftover was discarded and RE-DOWNLOADED — true while
    a missing digest only disqualified the leftover. Now a missing digest is
    refused at the top of download_engine, so there is no re-fetch either: bytes
    we could never verify are not worth spending a download on, and installing
    the result would be the very hole PS-49 closed.

    The two properties that matter are unchanged and asserted below: the
    unverifiable leftover is NOT installed, and it is not silently accepted."""
    engine_dir, binary = _engine_at(monkeypatch, tmp_path)
    (engine_dir / "e").write_bytes(b"TRUNCATED-GARBAGE")  # right name, unverifiable

    downloads = []

    def counted_download(path, *a, **k):
        downloads.append(1)
        with open(path, "wb") as f:
            f.write(b"NEW-ENGINE")
        return True

    monkeypatch.setattr(updater, "_download_to", counted_download)
    monkeypatch.setattr(updater, "_in_use_provider", lambda: False)

    with pytest.raises(updater.EngineUnverifiable):
        updater.download_engine("http://x/e", digest=None)

    assert downloads == [], "nothing to verify against means nothing worth fetching"
    assert binary.read_bytes() == b"OLD-ENGINE", (
        "the unverifiable leftover must not be promoted into the engine tree"
    )


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


# --- PS-280: an undecodable version.txt ------------------------------------
#
# `current_version()` is the SOLE source of the Chromium version an Android
# profile advertises, and its `except OSError` arm did not cover
# `UnicodeDecodeError` — which inherits from `ValueError`, not `OSError`. So an
# undecodable version.txt escaped the guard and reached the caller as a raw
# traceback, instead of the "" that `engine_version.parse` turns into the named
# `EngineVersionUnreadableError` refusal `browser/process.py` catches BY TYPE.
#
# The files here are written as RAW BYTES against the real VERSION_FILE and
# driven through the real function — a mocked `open` would prove nothing about
# which exception a real decoding read raises, which is the entire defect.

_UNDECODABLE_VERSIONS = {
    # a lone 0xff mid-string: never a valid UTF-8 start byte
    "raw-0xff": b"148.0.7559.13\xff2",
    # a real encoding persona does not read: the BOM alone is undecodable
    "utf-16": "148.0.7559.132".encode("utf-16"),
}


@pytest.mark.parametrize("label", sorted(_UNDECODABLE_VERSIONS))
def test_an_undecodable_version_file_reads_as_absent_not_as_a_crash(
    monkeypatch, tmp_path, label
):
    """The documented answer for an unusable version.txt is "" — the same
    answer a missing one gets. Asserted on the RETURN VALUE of the real
    function reading real bytes off disk: an escaping `UnicodeDecodeError`
    fails this as an ERROR, which is precisely the regression."""
    version_file = tmp_path / "version.txt"
    version_file.write_bytes(_UNDECODABLE_VERSIONS[label])
    monkeypatch.setattr(updater, "VERSION_FILE", str(version_file))

    assert updater.current_version() == ""

    # And the bytes are DECLINED, never clobbered or re-encoded: a file persona
    # cannot decode is not one persona wrote.
    assert version_file.read_bytes() == _UNDECODABLE_VERSIONS[label]


def test_a_decodable_version_file_still_reads_its_version(monkeypatch, tmp_path):
    """THE CONTROL, and it is the load-bearing half: the widened arm must not
    have turned every read into "". A normal version.txt still reads."""
    version_file = tmp_path / "version.txt"
    version_file.write_text("148.0.7559.132\n", encoding="utf-8")
    monkeypatch.setattr(updater, "VERSION_FILE", str(version_file))

    assert updater.current_version() == "148.0.7559.132"


def test_a_decodable_but_garbage_version_file_is_returned_verbatim(
    monkeypatch, tmp_path
):
    """THE SECOND CONTROL. Deciding whether a version is READABLE is not this
    function's job — it hands the string on and `engine_version.parse` refuses
    it by name. A garbage-but-decodable file must therefore come back verbatim,
    not as "": that is what keeps the two refusal causes distinguishable."""
    version_file = tmp_path / "version.txt"
    version_file.write_text("not-a-version\n", encoding="utf-8")
    monkeypatch.setattr(updater, "VERSION_FILE", str(version_file))

    assert updater.current_version() == "not-a-version"


# --- PS-245: _promote_staging reports the rollback verdict it already computes -
#
# The function BUILT `fully_restored`, used it once to decide whether to drop the
# backup, and threw it away. That is the exact fact download_engine needs to tell
# a tree restored to the working build from one left part old and part new. AC3
# is the criterion that keeps this from inverting into a leak: the arm where the
# restore could NOT be completed must stay silent, so the sentinel is kept.


def test_a_fully_restored_promotion_reports_restored(monkeypatch, tmp_path):
    # The rollback completed: every entry is back and the backup was dropped as
    # provably redundant. That is the ONLY state that may clear the sentinel.
    import os

    import pytest

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

    # fail the promotion mid-loop; the rollback's renames are left working
    real_move = updater.shutil.move
    calls = {"n": 0}

    def failing_move(src, dst):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("No space left on device")
        return real_move(src, dst)

    monkeypatch.setattr(updater.shutil, "move", failing_move)
    updater._reset_install_outcome()
    with pytest.raises(OSError):
        updater._promote_staging(str(staging))
    monkeypatch.undo()

    # the working build is genuinely back, byte-identical...
    assert (engine_dir / "chrome.exe").read_bytes() == b"OLD-ENGINE-EXE"
    assert not (engine_dir / updater.BACKUP_NAME).exists()
    # ...and the verdict says so, instead of being discarded
    assert updater._previous_build_restored() is True
    assert os.path.isdir(str(engine_dir))


def test_a_partially_restored_promotion_reports_nothing(monkeypatch, tmp_path):
    """⭐ AC3 — the anti-regression criterion, and the one that matters most.

    When the rollback itself could not be completed, the tree is part old and
    part new with the only good copy sitting in the backup dir. Clearing the
    sentinel there would launch precisely the mixed engine PS-24/PS-32 built the
    mechanism to prevent. The verdict must stay UNKNOWN.
    """
    import os

    import pytest

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

    real_move = updater.shutil.move
    calls = {"n": 0}

    def failing_move(src, dst):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("No space left on device")
        return real_move(src, dst)

    # ...and fail the ROLLBACK's rename too (the antivirus-on-Windows case)
    backup_root = engine_dir / updater.BACKUP_NAME
    real_replace = os.replace

    def locked_replace(src, dst):
        if str(src).startswith(str(backup_root)):
            raise PermissionError(32, "The process cannot access the file")
        return real_replace(src, dst)

    monkeypatch.setattr(updater.shutil, "move", failing_move)
    # Named at the seam that actually makes the call: the rollback rename is
    # httpdl.restore_aside's, so it goes through httpdl's own `os` import. (It
    # is the same module object as `updater.os` — patching either mutates the
    # shared stdlib module — but naming the caller is what makes the fake
    # reviewable, and it matches the `updater.os.replace` idiom the sibling
    # tests in test_engine_ensure.py use for the Linux arm.)
    monkeypatch.setattr(updater.httpdl.os, "replace", locked_replace)
    updater._reset_install_outcome()
    with pytest.raises(OSError):
        updater._promote_staging(str(staging))
    monkeypatch.undo()

    # the backup survived — it is the last copy of the working build (PS-38)
    assert (backup_root / "chrome.exe").read_bytes() == b"OLD-ENGINE-EXE"
    # ...and CRUCIALLY the verdict is unknown, so the sentinel is kept
    assert updater._previous_build_restored() is False


def test_a_partial_rollback_leaves_the_engine_unlaunchable_end_to_end(
    monkeypatch, tmp_path
):
    """AC3 stated where it is actually consumed: over the REAL download_engine,
    with the REAL _install_windows and _promote_staging, a promotion whose
    rollback could not complete must keep the sentinel and read
    is_installed() == False."""
    import os

    _force_os(monkeypatch, win=True)
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    _populate_engine(engine_dir)
    (engine_dir / ".engine-complete").touch()
    (engine_dir / "version.txt").write_text("148.0.7778.215", encoding="utf-8")
    monkeypatch.setattr(updater, "ENGINE_DIR", str(engine_dir))
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(engine_dir / "chrome.exe"))
    monkeypatch.setattr(
        updater, "MARKER_FILE", str(engine_dir / ".engine-complete")
    )
    monkeypatch.setattr(updater, "VERSION_FILE", str(engine_dir / "version.txt"))
    assert updater.is_installed() is True  # precondition

    zip_path = tmp_path / "engine.zip"
    _new_build_zip(zip_path)
    monkeypatch.setattr(updater, "_download_to", lambda *a, **k: True)

    real_move = updater.shutil.move
    calls = {"n": 0}

    def failing_move(src, dst):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("No space left on device")
        return real_move(src, dst)

    backup_root = engine_dir / updater.BACKUP_NAME
    real_replace = os.replace

    def locked_replace(src, dst):
        if str(src).startswith(str(backup_root)):
            raise PermissionError(32, "The process cannot access the file")
        return real_replace(src, dst)

    monkeypatch.setattr(updater.shutil, "move", failing_move)
    # Same seam as the unit-level sibling above: restore_aside's own os.replace.
    monkeypatch.setattr(updater.httpdl.os, "replace", locked_replace)
    monkeypatch.setattr(
        updater, "_download_to",
        lambda path, *a, **k: (_copy_zip_to(zip_path, path), True)[1],
    )
    assert updater.download_engine("http://x/engine.zip", digest="sha256:aa") is False

    # ⚠️ BOTH ASSERTIONS RUN *BEFORE* `monkeypatch.undo()`, AND THAT ORDER IS THE
    # WHOLE TEST. `undo()` drops the ENGINE_DIR / ENGINE_BINARY / MARKER_FILE /
    # VERSION_FILE wiring along with the fakes, so an `is_installed()` evaluated
    # after it reads the OPERATOR'S REAL ~/.persona/engine instead of this tmp
    # tree — and then fails in two opposite directions at once:
    #
    #   * VACUOUS wherever it is green. With no engine at ~/.persona/engine the
    #     gate returns False for that reason alone, so the assertion holds no
    #     matter what the tmp tree contains. Measured: making the tmp tree fully
    #     launchable right here (drop the sentinel, write the marker, write a
    #     good binary) still passed — an assertion that cannot go red.
    #   * RED on any host that HAS an engine installed, for a reason that has
    #     nothing to do with the code under test. version.txt alone satisfies
    #     _install_complete(), so a developer's own machine reddens this.
    #
    # The sentinel line hid it: `(engine_dir / ...).exists()` is a pathlib call
    # on a captured local, so it is correct either way and sat next to a
    # neighbour that was not.
    #
    # The sibling at test_a_second_attempt_cannot_clear_a_sentinel_left_by_a_
    # failed_rollback solves this the other way — it re-wires via
    # _wire_windows_engine() after the undo — because it genuinely needs the
    # fakes dropped mid-test to run a SECOND attempt. Nothing here does, so the
    # assertions simply stay inside the wiring.
    #
    # If you touch this, re-run the falsification: make the tmp tree launchable
    # immediately before the is_installed() line and confirm the test goes RED.
    # A green there means the assertion has stopped reading the tmp tree again.
    assert (engine_dir / ".engine-installing").exists()
    assert updater.is_installed() is False

    monkeypatch.undo()


def test_a_bad_archive_leaves_the_previous_build_launchable(monkeypatch, tmp_path):
    """The pre-promotion failure arm: a zip with no chrome.exe is refused before
    anything outside .staging is written, so the installed tree is byte-for-byte
    as the operator left it — and must stay launchable."""
    _force_os(monkeypatch, win=True)
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    _populate_engine(engine_dir)
    (engine_dir / ".engine-complete").touch()
    (engine_dir / "version.txt").write_text("148.0.7778.215", encoding="utf-8")
    monkeypatch.setattr(updater, "ENGINE_DIR", str(engine_dir))
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(engine_dir / "chrome.exe"))
    monkeypatch.setattr(
        updater, "MARKER_FILE", str(engine_dir / ".engine-complete")
    )
    monkeypatch.setattr(updater, "VERSION_FILE", str(engine_dir / "version.txt"))

    zip_path = tmp_path / "engine.zip"
    _make_windows_zip(zip_path, {"chrome-win/readme.txt": b"no exe here"})
    monkeypatch.setattr(
        updater, "_download_to",
        lambda path, *a, **k: (_copy_zip_to(zip_path, path), True)[1],
    )

    assert updater.download_engine("http://x/engine.zip", digest="sha256:aa") is False
    assert (engine_dir / "chrome.exe").read_bytes() == b"OLD-ENGINE-EXE"
    assert not (engine_dir / ".engine-installing").exists()
    assert updater.is_installed() is True


def _wire_windows_engine(monkeypatch, engine_dir):
    """Point the module's four ENGINE_DIR-derived paths at `engine_dir`, for the
    Windows arm. Factored out because the two-attempt test below has to keep the
    wiring alive across a `monkeypatch.undo()` in the middle."""
    monkeypatch.setattr(updater, "ENGINE_DIR", str(engine_dir))
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(engine_dir / "chrome.exe"))
    monkeypatch.setattr(updater, "MARKER_FILE", str(engine_dir / ".engine-complete"))
    monkeypatch.setattr(updater, "VERSION_FILE", str(engine_dir / "version.txt"))


def _pin_staging_order(monkeypatch, staging, order):
    """Make _promote_staging visit `staging`'s entries in exactly `order`.

    `_promote_staging` iterates `os.listdir`, which is UNORDERED BY CONTRACT —
    ext4 hands back insertion-ish order, APFS and NTFS hand back something
    near-sorted. A test that wants a SPECIFIC half-promoted tree (this file
    already needs one at _promote_staging's new-build-only arm, and the
    two-attempt test below needs another) must therefore say which order it
    means, or the shape it asserts is decided by the filesystem underneath it
    rather than by the test. Only `staging` is reordered; every other listdir
    call passes straight through.
    """
    real_listdir = updater.os.listdir
    rank = {name: i for i, name in enumerate(order)}

    def pinned(path):
        names = real_listdir(path)
        if os.path.abspath(path) == os.path.abspath(str(staging)):
            return sorted(names, key=lambda n: (rank.get(n, len(rank)), n))
        return names

    monkeypatch.setattr(updater.os, "listdir", pinned)


def test_a_second_attempt_cannot_clear_a_sentinel_left_by_a_failed_rollback(
    monkeypatch, tmp_path
):
    """⭐ AC3, ACROSS TWO ATTEMPTS — the durability of the keep, which is where
    the single-attempt tests above stop one call too early.

    ensure_engine retries (attempts=3), so on a disk-full host these two
    download_engine calls happen back to back with no unusual operator
    behaviour at all:

      attempt 1 — promotion fails AND its rollback cannot complete. The tree is
                  now part new build / part old, the only good copy is stranded
                  in the backup dir, and the sentinel is correctly KEPT.
      attempt 2 — the extract fails BEFORE promotion starts. This attempt
                  genuinely wrote nothing outside .staging, and the installer
                  says so — truthfully, and about ITSELF.

    The trap is reading attempt 2's honest report as a warrant for the TREE.
    "I did not touch it" and "what is there is a working build" are different
    claims, and only the first is established. If the sentinel may be cleared on
    the first alone, the guard survives exactly one attempt and the next one
    launches the mixed engine PS-24/PS-32 built the mechanism to prevent.

    The precondition every "restored" test above starts from — a HEALTHY tree —
    is the one condition under which the pre-promotion arms' inference is safe.
    This test starts where attempt 1 ended instead.
    """
    _force_os(monkeypatch, win=True)
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    _populate_engine(engine_dir)
    (engine_dir / ".engine-complete").touch()
    (engine_dir / "version.txt").write_text("148.0.7778.215", encoding="utf-8")
    _wire_windows_engine(monkeypatch, engine_dir)
    assert updater.is_installed() is True  # precondition: a working engine

    # ---- attempt 1: promotion fails, and so does its rollback -------------
    zip_path = tmp_path / "engine.zip"
    _new_build_zip(zip_path)

    # WHICH entry is stranded is asserted below, so it must be CHOSEN here and
    # not left to the filesystem. `_promote_staging` walks os.listdir, which is
    # unordered by contract: ext4 yields insertion-ish order while APFS and NTFS
    # yield something near-sorted, so a failure keyed on a call COUNTER strands
    # a different file on every platform and the tree-shape assertions below
    # describe a tree that only one of them produces. Pin the order, then key
    # the failure on the entry's NAME — the test now says out loud that
    # `some.dll` is the file left half-promoted.
    #
    # The AC3 property itself is order-INDEPENDENT (the sentinel is kept under
    # every ordering); this pinning buys determinism in the forensic detail, not
    # coverage, which is why it costs nothing to fix it this way.
    _pin_staging_order(
        monkeypatch,
        engine_dir / ".staging",
        ["chrome.exe", "some.dll", "locales"],
    )

    real_move = updater.shutil.move

    def failing_move(src, dst):
        if os.path.basename(str(dst)) == "some.dll":
            raise OSError("No space left on device")
        return real_move(src, dst)

    backup_root = engine_dir / updater.BACKUP_NAME
    real_replace = updater.httpdl.os.replace

    def locked_replace(src, dst):
        if str(src).startswith(str(backup_root)):
            raise PermissionError(32, "The process cannot access the file")
        return real_replace(src, dst)

    monkeypatch.setattr(updater.shutil, "move", failing_move)
    monkeypatch.setattr(updater.httpdl.os, "replace", locked_replace)
    monkeypatch.setattr(
        updater, "_download_to",
        lambda path, *a, **k: (_copy_zip_to(zip_path, path), True)[1],
    )
    assert updater.download_engine("http://x/engine.zip", digest="sha256:aa") is False
    monkeypatch.undo()          # drop the fakes AND the ENGINE_DIR wiring...
    _wire_windows_engine(monkeypatch, engine_dir)   # ...then put the wiring back
    _force_os(monkeypatch, win=True)

    # The tree really is mixed: the new chrome.exe landed, some.dll was moved
    # aside and its replacement never arrived (so that file is simply GONE from
    # the installed tree), and locales/ is still the old build's. The only
    # complete copy of a working engine is the backup — stranded on purpose
    # (PS-38), because deleting it is the difference between "the upgrade
    # failed" and "there is no engine at all".
    assert (engine_dir / "chrome.exe").read_bytes().startswith(b"MZ")   # NEW exe
    assert not (engine_dir / "some.dll").exists()                       # GONE
    assert (engine_dir / "locales" / "en.pak").read_bytes() == b"OLD-PAK"
    assert (backup_root / "chrome.exe").read_bytes() == b"OLD-ENGINE-EXE"
    assert (backup_root / "some.dll").read_bytes() == b"OLD-DLL"
    # ...and the sentinel is correctly holding that tree shut.
    assert (engine_dir / ".engine-installing").exists()
    assert updater.is_installed() is False

    # ---- attempt 2: fails BEFORE promotion, over that same mixed tree -----
    bad_zip = tmp_path / "bad.zip"
    _make_windows_zip(bad_zip, {"chrome-win/readme.txt": b"no exe here"})
    monkeypatch.setattr(
        updater, "_download_to",
        lambda path, *a, **k: (_copy_zip_to(bad_zip, path), True)[1],
    )
    assert updater.download_engine("http://x/engine.zip", digest="sha256:aa") is False

    # attempt 2 changed nothing on disk — the tree is still exactly as mixed...
    assert (engine_dir / "chrome.exe").read_bytes().startswith(b"MZ")
    assert not (engine_dir / "some.dll").exists()
    assert (backup_root / "chrome.exe").read_bytes() == b"OLD-ENGINE-EXE"
    # ...so the sentinel MUST still be there, and the engine unlaunchable.
    assert (engine_dir / ".engine-installing").exists()
    assert updater.is_installed() is False


def test_a_first_install_with_no_previous_build_keeps_the_sentinel(
    monkeypatch, tmp_path
):
    """The other half of the `was_launchable` term: with nothing installed yet,
    a pre-promotion failure has no previous build to have preserved, so there is
    nothing to make launchable and the sentinel must be kept.

    Reads False for a reason worth stating: not because the installer lied, but
    because "restored" is meaningless where there was never anything to restore
    to. Clearing here would let a half-populated ENGINE_DIR from some earlier
    partial attempt read as ready."""
    _force_os(monkeypatch, win=True)
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    # a stray chrome.exe from an earlier partial attempt — present, non-empty,
    # and NOT a complete install
    (engine_dir / "chrome.exe").write_bytes(b"HALF-EXTRACTED")
    _wire_windows_engine(monkeypatch, engine_dir)
    assert updater.is_installed() is False  # precondition: nothing complete

    bad_zip = tmp_path / "bad.zip"
    _make_windows_zip(bad_zip, {"chrome-win/readme.txt": b"no exe here"})
    monkeypatch.setattr(
        updater, "_download_to",
        lambda path, *a, **k: (_copy_zip_to(bad_zip, path), True)[1],
    )

    assert updater.download_engine("http://x/engine.zip", digest="sha256:aa") is False
    assert (engine_dir / ".engine-installing").exists()
    assert updater.is_installed() is False


# --- PS-310: the Windows member loop must confine every member to staging -----
#
# `_install_windows` does NOT call ZipFile.extractall — it walks `namelist()`
# and writes each member by hand with `open(dest, "wb")`. So it inherits none of
# the member-path sanitization CPython's extractall performs, and PS-228's
# confinement (which covers the FIREFOX engine's `_extract_as`, a different file
# and a different function) never reached this loop.
#
# Every assertion below is about FILES ON DISK OUTSIDE THE DESTINATION — never
# that a helper was called, never a substring in the source. That discipline is
# what tests/test_engine_archive_security.py states for the Firefox arm, applied
# here to the Chromium one.
#
# THE ARCHIVE IS AUTHENTIC. It is sha256-verified against upstream's published
# digest before it ever reaches this function (EngineUnverifiable, PS-49), and
# that gate is not claimed broken. What a checksum attests is the TRANSFER, not
# the CONTENTS — so this is prevention against a hostile member in an archive
# that is genuinely what upstream published, exactly the posture PS-228 shipped
# under for the Firefox engine.


def _engine_dir_at(monkeypatch, tmp_path):
    """An ENGINE_DIR NESTED under tmp_path, so a `../../x` member escaping
    staging lands somewhere the test can still see it.

    staging is `<engine_dir>/.staging`, so two `..` segments land in
    `engine_dir.parent` — which is `home` here and is PERSONA_HOME in
    production, where profiles.json / proxies.json / install_secret live."""
    home = tmp_path / "home"
    engine_dir = home / "engine"
    engine_dir.mkdir(parents=True)
    monkeypatch.setattr(updater, "ENGINE_DIR", str(engine_dir))
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(engine_dir / "chrome.exe"))
    return engine_dir


def _escaped(tmp_path, engine_dir, name):
    """Every file called `name` that landed OUTSIDE the staging tree.

    Deliberately NOT "outside engine_dir": staging is the destination this loop
    writes to, so a member landing anywhere else — including directly in
    ENGINE_DIR beside the working build — has escaped it. Excusing ENGINE_DIR
    would make a member like `chrome-win/../x` (which resolves one level ABOVE
    staging) read as confined, and that member is exactly the traversal being
    guarded against.

    Reported as paths relative to tmp_path so a failure names where it escaped
    to, not merely that it did."""
    staging = engine_dir / ".staging"
    return [
        str(q.relative_to(tmp_path))
        for q in tmp_path.rglob(name)
        if staging not in q.parents
    ]


def test_zipfile_extractall_confines_the_same_hostile_archive(tmp_path):
    """THE CONTROL, and it is the load-bearing half of the pair.

    A fixture that escapes nothing proves nothing — a bare escape below would be
    indistinguishable from a badly-built archive. This drives CPython's
    ZipFile.extractall over the SAME members and shows they are confined by the
    library call, so the escape in the next test is a property of the
    hand-rolled loop and not of the fixture.

    This is also the exact premise `_extract_as`'s docstring rests on
    (engine_install.py: "CPython's ZipFile.extractall already sanitizes member
    paths"). It is TRUE — and it does not transfer to a loop that never calls
    extractall."""
    import zipfile

    zip_path = tmp_path / "hostile.zip"
    _make_windows_zip(
        zip_path,
        {
            "chrome-win/chrome.exe": b"MZ" + b"\x00" * 100,
            "chrome-win/../../../CONTROL_PWNED": b"escaped",
            "chrome-win/sub/../../../../CONTROL_PWNED2": b"escaped deep",
        },
    )
    dst = tmp_path / "home" / "engine" / ".staging"
    dst.mkdir(parents=True)

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dst)

    outside = [
        str(q.relative_to(tmp_path))
        for q in tmp_path.rglob("CONTROL_PWNED*")
        if dst not in q.parents
    ]
    assert outside == [], (
        "the control is broken: extractall did not confine this archive, so an "
        f"escape from the shipped loop would prove nothing -> {outside}"
    )


def test_install_windows_traversal_member_is_not_written_outside_staging(
    monkeypatch, tmp_path
):
    """AC1. A member resolving outside staging writes NOTHING outside it.

    Everything is nested under `chrome-win/` because that is what engages the
    prefix-flattening branch, which is the shape a real Chromium Windows asset
    takes — a traversal tested without it would not exercise the code path the
    real archive drives."""
    _force_os(monkeypatch, win=True)
    engine_dir = _engine_dir_at(monkeypatch, tmp_path)

    zip_path = tmp_path / "hostile.zip"
    _make_windows_zip(
        zip_path,
        {
            "chrome-win/chrome.exe": b"MZ" + b"\x00" * 100,
            "chrome-win/some.dll": b"\x00" * 50,
            "chrome-win/../../../PWNED": b"escaped",
        },
    )

    # Refusing the archive and skipping the member are both acceptable
    # confinements; only the FILESYSTEM distinguishes a fix from the defect, so
    # the return value is deliberately not asserted here (AC6 pins the chosen
    # behaviour separately). Gating on it would make this a test about control
    # flow in a file whose whole point is disk state.
    updater._install_windows(str(zip_path))

    escaped = _escaped(tmp_path, engine_dir, "PWNED")
    assert escaped == [], f"traversal member escaped staging: {escaped}"


def test_install_windows_confines_a_member_traversing_from_a_nested_path(
    monkeypatch, tmp_path
):
    """AC3 — DEPTH, not just the top level, and it is a distinct case.

    `os.path.join(staging, *rel.split("/"))` reassembles `..` at ANY depth, and
    `os.makedirs(os.path.dirname(dest))` then CREATES the escaping directory
    chain before the write. So a fix that only rejected a `..` sitting
    immediately under the prefix would leave this one green-looking and open."""
    _force_os(monkeypatch, win=True)
    engine_dir = _engine_dir_at(monkeypatch, tmp_path)

    zip_path = tmp_path / "hostile-deep.zip"
    _make_windows_zip(
        zip_path,
        {
            "chrome-win/chrome.exe": b"MZ" + b"\x00" * 100,
            "chrome-win/sub/../../../../PWNED_DEEP": b"escaped deep",
        },
    )

    updater._install_windows(str(zip_path))

    escaped = _escaped(tmp_path, engine_dir, "PWNED_DEEP")
    assert escaped == [], f"nested traversal member escaped staging: {escaped}"


def test_install_windows_confines_a_backslash_separated_traversal_member(
    monkeypatch, tmp_path
):
    """The loop normalises `\\` to `/` before deciding anything (`norm`), which
    is what makes a Windows-authored member name reach the same join. A
    confinement keyed to the RAW member name would miss this one; one keyed to
    the RESOLVED destination cannot."""
    _force_os(monkeypatch, win=True)
    engine_dir = _engine_dir_at(monkeypatch, tmp_path)

    zip_path = tmp_path / "hostile-bs.zip"
    _make_windows_zip(
        zip_path,
        {
            "chrome-win/chrome.exe": b"MZ" + b"\x00" * 100,
            "chrome-win\\..\\..\\..\\PWNED_BS": b"escaped via backslashes",
        },
    )

    updater._install_windows(str(zip_path))

    escaped = _escaped(tmp_path, engine_dir, "PWNED_BS")
    assert escaped == [], f"backslash traversal member escaped staging: {escaped}"


def test_install_windows_does_not_overwrite_a_persona_home_file(
    monkeypatch, tmp_path
):
    """What the escape actually REACHES, asserted as content rather than as a
    path shape. staging is `<PERSONA_HOME>/engine/.staging`, so two `..`
    segments land in PERSONA_HOME itself — where `install_secret` (the salt
    behind a profile's presented machine) and `profiles.json` live. Overwriting
    that file is the write that matters most in this tree."""
    _force_os(monkeypatch, win=True)
    engine_dir = _engine_dir_at(monkeypatch, tmp_path)
    secret = engine_dir.parent / "install_secret"
    secret.write_bytes(b"the real install secret")

    zip_path = tmp_path / "hostile-secret.zip"
    _make_windows_zip(
        zip_path,
        {
            "chrome-win/chrome.exe": b"MZ" + b"\x00" * 100,
            "chrome-win/../../install_secret": b"attacker-chosen salt",
        },
    )

    updater._install_windows(str(zip_path))

    assert secret.read_bytes() == b"the real install secret", (
        "a hostile member overwrote a PERSONA_HOME file two directories above "
        "staging"
    )


def test_install_windows_refuses_the_archive_and_promotes_nothing(
    monkeypatch, tmp_path
):
    """AC6 — the chosen behaviour, pinned so it cannot drift by omission.

    A hostile member ABORTS the install (`return False`); it is not skipped and
    the rest installed. Two reasons, and the second is the one that decides it:

    1. A member resolving outside its destination cannot occur in an honest
       build, so its presence says the archive is not what it claims to be. The
       right answer to "this archive is not what it claims" is to refuse it, not
       to install the parts of it we happened to like.
    2. Skipping would let `_promote_staging` run over a tree assembled from an
       archive we have already judged hostile — a HALF-TREE from a rejected
       archive moved into ENGINE_DIR over the working build, which is exactly
       what AC6 forbids. Aborting keeps `chrome.exe` and every sibling out of
       ENGINE_DIR entirely, and the existing `finally` then removes staging.

    Note what refusing at WRITE time buys that cleanup cannot: the `finally`
    removes staging, but it never removed what escaped — a failed install used
    to tear down staging and leave every escaped file on disk, with the operator
    told only that the update failed."""
    _force_os(monkeypatch, win=True)
    engine_dir = _engine_dir_at(monkeypatch, tmp_path)
    previous = engine_dir / "chrome.exe"
    previous.write_bytes(b"MZ-the-working-build")

    zip_path = tmp_path / "hostile-promote.zip"
    _make_windows_zip(
        zip_path,
        {
            "chrome-win/chrome.exe": b"MZ-the-hostile-build",
            "chrome-win/../../../PWNED_PROMOTE": b"escaped",
        },
    )

    assert updater._install_windows(str(zip_path)) is False, (
        "a hostile member must abort the install, not be skipped"
    )

    assert _escaped(tmp_path, engine_dir, "PWNED_PROMOTE") == []
    # Nothing from the rejected archive was promoted, and the build that was
    # working is untouched.
    assert previous.read_bytes() == b"MZ-the-working-build"
    assert not (engine_dir / ".staging").exists(), "staging survived the refusal"
    assert zip_path.exists(), "a refused archive must not be consumed"


def test_install_windows_still_installs_a_benign_engine_shaped_zip(
    monkeypatch, tmp_path
):
    """AC4/AC5 regression guard, stated HERE as well as in the pinned
    `test_install_windows_atomic_via_staging` — this file's own copy, so the
    confinement is falsifiable in both directions from one place.

    The prefix flattening is the thing most easily broken by a naive rewrite:
    everything nested under `chrome-win/` must still land at `staging/chrome.exe`
    (not `staging/chrome-win/chrome.exe`), because that flattening is why the
    launcher's `ENGINE_DIR/chrome.exe` resolves after promotion."""
    _force_os(monkeypatch, win=True)
    engine_dir = _engine_dir_at(monkeypatch, tmp_path)

    zip_path = tmp_path / "benign.zip"
    _make_windows_zip(
        zip_path,
        {
            "chrome-win/": b"",
            "chrome-win/chrome.exe": b"MZ" + b"\x00" * 100,
            "chrome-win/some.dll": b"\x00" * 50,
            "chrome-win/locales/": b"",
            "chrome-win/locales/en.pak": b"pak",
            "chrome-win/swiftshader/vk_swiftshader.dll": b"\x00" * 8,
        },
    )

    assert updater._install_windows(str(zip_path)) is True
    assert (engine_dir / "chrome.exe").read_bytes().startswith(b"MZ")
    assert (engine_dir / "some.dll").exists()
    assert (engine_dir / "locales" / "en.pak").read_bytes() == b"pak"
    assert (engine_dir / "swiftshader" / "vk_swiftshader.dll").exists()
    # The flattening actually happened — the top-level folder is not a directory
    # inside ENGINE_DIR.
    assert not (engine_dir / "chrome-win").exists()
    assert not any(p.name.startswith(".staging") for p in engine_dir.iterdir())


def test_install_windows_installs_an_unnested_zip_unchanged(monkeypatch, tmp_path):
    """The `prefix == ""` branch — a zip with chrome.exe at the top level, where
    `rel` is the raw member name and the flattening never engages. Confinement
    must not depend on a prefix having been resolved."""
    _force_os(monkeypatch, win=True)
    engine_dir = _engine_dir_at(monkeypatch, tmp_path)

    zip_path = tmp_path / "flat.zip"
    _make_windows_zip(
        zip_path,
        {
            "chrome.exe": b"MZ" + b"\x00" * 100,
            "locales/en.pak": b"pak",
        },
    )

    assert updater._install_windows(str(zip_path)) is True
    assert (engine_dir / "chrome.exe").read_bytes().startswith(b"MZ")
    assert (engine_dir / "locales" / "en.pak").read_bytes() == b"pak"


def test_install_windows_tolerates_a_dot_directory_member(monkeypatch, tmp_path):
    """A benign shape the confinement must NOT refuse: a "./" directory entry
    resolves to staging ITSELF, and makedirs of staging is a no-op. Equality
    with the base is allowed on the directory arm for exactly that reason — and
    only there, since a FILE written at the base path is a write nobody asked
    for."""
    _force_os(monkeypatch, win=True)
    engine_dir = _engine_dir_at(monkeypatch, tmp_path)

    zip_path = tmp_path / "dotdir.zip"
    _make_windows_zip(
        zip_path,
        {
            "./": b"",
            "chrome.exe": b"MZ" + b"\x00" * 100,
            "locales/en.pak": b"pak",
        },
    )

    assert updater._install_windows(str(zip_path)) is True
    assert (engine_dir / "chrome.exe").read_bytes().startswith(b"MZ")
    assert (engine_dir / "locales" / "en.pak").read_bytes() == b"pak"


def test_install_windows_confines_a_single_level_traversal_into_engine_dir(
    monkeypatch, tmp_path
):
    """A traversal of exactly ONE level, which lands in ENGINE_DIR itself —
    beside the working build rather than up in PERSONA_HOME.

    Distinct from the two-level case above and worth its own test, because it is
    the escape a confinement that merely keeps writes "somewhere under
    ENGINE_DIR" would wave through. staging is the destination this loop writes
    to; ENGINE_DIR is where _promote_staging moves that tree afterwards, and a
    member that writes there directly has bypassed staging and the promotion's
    rollback with it."""
    _force_os(monkeypatch, win=True)
    engine_dir = _engine_dir_at(monkeypatch, tmp_path)

    zip_path = tmp_path / "one-level.zip"
    _make_windows_zip(
        zip_path,
        {
            "chrome-win/chrome.exe": b"MZ" + b"\x00" * 100,
            "chrome-win/../SIBLING_PWNED": b"escaped one level",
        },
    )

    updater._install_windows(str(zip_path))

    assert _escaped(tmp_path, engine_dir, "SIBLING_PWNED") == []
