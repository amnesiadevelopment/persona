import os
import hashlib

import pytest

from src.services.engine import updater


class _Opener:
    """Stand-in for the range-preserving opener: .open(req) returns the next
    fake response and records the Range header the caller sent."""

    def __init__(self, factory):
        self._factory = factory
        self.ranges = []

    def open(self, req, timeout=0):
        self.ranges.append(req.headers.get("Range"))
        return self._factory(req)


def _wire_engine_dir(monkeypatch, tmp_path):
    """Point ENGINE_BINARY/MARKER_FILE/VERSION_FILE at tmp_path and force
    non-macOS so is_installed() checks the plain binary + completion marker."""
    monkeypatch.setattr(updater._platform, "IS_MACOS", False)
    monkeypatch.setattr(updater, "ENGINE_DIR", str(tmp_path))
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(tmp_path / "engine.bin"))
    monkeypatch.setattr(updater, "MARKER_FILE", str(tmp_path / ".engine-complete"))
    monkeypatch.setattr(updater, "VERSION_FILE", str(tmp_path / "version.txt"))
    return tmp_path


def test_is_installed_false_when_missing(tmp_path, monkeypatch):
    _wire_engine_dir(monkeypatch, tmp_path)
    assert updater.is_installed() is False


def test_is_installed_true_when_present_and_marked(tmp_path, monkeypatch):
    d = _wire_engine_dir(monkeypatch, tmp_path)
    (d / "engine.bin").write_bytes(b"\x00" * 10)
    (d / ".engine-complete").touch()
    assert updater.is_installed() is True


def test_is_installed_false_when_binary_present_but_no_marker(tmp_path, monkeypatch):
    # binary on disk but NO completion marker and NO version.txt = a partial
    # extract mid-install; must not read as installed (a broken engine can't launch)
    d = _wire_engine_dir(monkeypatch, tmp_path)
    (d / "engine.bin").write_bytes(b"\x00" * 10)
    assert updater.is_installed() is False


def test_is_installed_true_for_legacy_version_txt(tmp_path, monkeypatch):
    # engines installed before the marker existed have version.txt (written last
    # by ensure_engine) — treat that as the completion signal so we don't force a
    # needless re-download on upgrade
    d = _wire_engine_dir(monkeypatch, tmp_path)
    (d / "engine.bin").write_bytes(b"\x00" * 10)
    (d / "version.txt").write_text("148.0.7778.215", encoding="utf-8")
    assert updater.is_installed() is True


def test_is_installed_false_when_empty(tmp_path, monkeypatch):
    # a zero-byte file is a failed/partial download, not a usable engine
    d = _wire_engine_dir(monkeypatch, tmp_path)
    (d / "engine.bin").touch()
    (d / ".engine-complete").touch()
    assert updater.is_installed() is False


# ---------------------------------------------------------------------------
# the in-progress sentinel: an install that died must not read as finished
# ---------------------------------------------------------------------------


def _stage_prior_install(d, version="148.0.7778.215"):
    """A working, COMPLETE prior install: binary + marker + version.txt. This is
    the upgrade precondition — the only state in which a partial install lands
    on top of something that was working."""
    (d / "engine.bin").write_bytes(b"\x00" * 10)
    (d / ".engine-complete").touch()
    (d / "version.txt").write_text(version, encoding="utf-8")


def test_sentinel_makes_is_installed_false_despite_marker_and_version(
    tmp_path, monkeypatch
):
    # The sentinel VETOES both completion signals. Not "instead of" them: with
    # BOTH the marker and version.txt present — the strongest possible "I am
    # installed" the tree can say — an in-progress install still reads False.
    d = _wire_engine_dir(monkeypatch, tmp_path)
    _stage_prior_install(d)
    assert updater.is_installed() is True  # precondition: reads ready

    (d / ".engine-installing").touch()
    assert updater.is_installed() is False


def test_failed_upgrade_does_not_read_as_installed(tmp_path, monkeypatch):
    """AC2 — the premise, red on main before the sentinel existed.

    Drive the REAL download_engine over a working prior install, with the
    install function failing midway. download_engine correctly returns False;
    what this pins is that the ON-DISK READINESS STATE agrees with it, because
    that is the thing a later is_installed() caller launches from. Before the
    sentinel, clearing the marker was inert here (version.txt still answered the
    gate) and this asserted True over a part-old/part-new tree.
    """
    d = _wire_engine_dir(monkeypatch, tmp_path)
    _stage_prior_install(d)
    assert updater.is_installed() is True
    assert updater.current_version() == "148.0.7778.215"

    monkeypatch.setattr(updater, "_download_to", lambda *a, **k: True)
    # promotion dies midway: the old engine is already destroyed on the Windows
    # and macOS paths, so what's on disk now is a mixed tree
    monkeypatch.setattr(updater, "_install_linux", lambda p: False)

    assert updater.download_engine("http://x/e", digest="sha256:aa") is False

    assert updater.is_installed() is False
    # ...and specifically NOT because we destroyed the provenance record: the
    # version is still answerable, which is exactly when someone needs it
    assert (d / "version.txt").exists()
    assert updater.current_version() == "148.0.7778.215"


def test_failed_upgrade_reads_installed_again_without_the_sentinel_check(
    tmp_path, monkeypatch
):
    """AC7 — the falsification criterion, as an executable test.

    Re-run the AC2 scenario against a _install_complete() whose sentinel arm has
    been REMOVED (the pre-fix disjunction, verbatim). It must go back to
    reporting True — proving the AC2 assertion above is carried by the gate
    consulting in-progress state, and not by some incidental side effect of the
    failed install. A test that passes either way would be testing nothing.
    """
    d = _wire_engine_dir(monkeypatch, tmp_path)
    _stage_prior_install(d)

    monkeypatch.setattr(updater, "_download_to", lambda *a, **k: True)
    monkeypatch.setattr(updater, "_install_linux", lambda p: False)
    assert updater.download_engine("http://x/e", digest="sha256:aa") is False

    # the sentinel IS on disk — the install-side half of the fix is intact...
    assert (d / ".engine-installing").exists()

    # ...but with the gate no longer consulting it, the old bug is back
    monkeypatch.setattr(
        updater,
        "_install_complete",
        lambda: os.path.exists(updater.MARKER_FILE)
        or os.path.exists(updater.VERSION_FILE),
    )
    assert updater.is_installed() is True


def test_successful_install_leaves_no_sentinel(tmp_path, monkeypatch):
    # AC3 — the sentinel is removed on success, so a good install reads ready
    # and doesn't strand a file that would force a re-download every start.
    d = _wire_engine_dir(monkeypatch, tmp_path)
    _stage_prior_install(d)

    monkeypatch.setattr(updater, "_download_to", lambda *a, **k: True)

    def _install_ok(p):
        (d / "engine.bin").write_bytes(b"\x01" * 20)  # the new build lands
        return True

    monkeypatch.setattr(updater, "_install_linux", _install_ok)

    assert updater.download_engine("http://x/e", digest="sha256:aa") is True
    assert not (d / ".engine-installing").exists()
    assert (d / ".engine-complete").exists()
    assert updater.is_installed() is True


def test_a_successful_install_survives_an_unremovable_sentinel(tmp_path, monkeypatch):
    # Sentinel removal must be as forgiving as the marker write beside it: it is
    # the last thing a SUCCESSFUL install does, so it must never be the one
    # thing that turns that success into a raised exception / reported failure.
    d = _wire_engine_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(updater, "_download_to", lambda *a, **k: True)

    def _install_ok(p):
        (d / "engine.bin").write_bytes(b"\x01" * 20)
        return True

    monkeypatch.setattr(updater, "_install_linux", _install_ok)

    real_remove = updater.os.remove

    def _stubborn_remove(path):
        if str(path).endswith(".engine-installing"):
            raise OSError("held open by another process")
        return real_remove(path)

    monkeypatch.setattr(updater.os, "remove", _stubborn_remove)

    assert updater.download_engine("http://x/e", digest="sha256:aa") is True


def test_sha256_matches():
    data = b"hello world"
    digest = hashlib.sha256(data).hexdigest()
    assert updater.sha256_ok(data, digest) is True
    assert updater.sha256_ok(data, "deadbeef") is False


def test_sha256_accepts_github_digest_prefix():
    # the GitHub API "digest" field looks like "sha256:abcd..."
    data = b"x"
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    assert updater.sha256_ok(data, digest) is True


def test_sha256_fails_closed_when_no_digest():
    # no digest available -> refuse (an unverifiable file could be a MITM swap)
    assert updater.sha256_ok(b"anything", "") is False
    assert updater.sha256_ok(b"anything", None) is False


def test_sha256_allow_missing_lets_unverified_pass():
    # the one place with no digest source (Linux predictable-URL fallback)
    # opts in explicitly
    assert updater.sha256_ok(b"anything", "", allow_missing=True) is True
    assert updater.sha256_ok(b"anything", None, allow_missing=True) is True
    # a present-but-wrong digest is still rejected even with allow_missing
    assert updater.sha256_ok(b"anything", "deadbeef", allow_missing=True) is False


def test_download_to_refuses_missing_digest(tmp_path, monkeypatch):
    # a download with no digest must not install: the .part is discarded and
    # _download_to returns False (fail-closed)
    path = tmp_path / "engine.bin"
    payload = b"engine-bytes"

    class FakeResp:
        status = 200
        headers = {"Content-Length": str(len(payload))}

        def __enter__(self): return self
        def __exit__(self, *a): return False

        def __init__(self):
            self._data = [payload, b""]

        def read(self, n):
            return self._data.pop(0) if self._data else b""

    monkeypatch.setattr(
        updater, "range_opener", lambda: _Opener(lambda *a, **k: FakeResp())
    )
    ok = updater._download_to(str(path), "http://x/engine", 5, None, None)
    assert ok is False
    assert not path.exists()
    assert not (tmp_path / "engine.bin.part").exists()


def test_download_to_allows_missing_digest_when_opted_in(tmp_path, monkeypatch):
    # Linux fallback path: allow_missing=True lets an un-digested asset install
    path = tmp_path / "engine.bin"
    payload = b"engine-bytes"

    class FakeResp:
        status = 200
        headers = {"Content-Length": str(len(payload))}

        def __enter__(self): return self
        def __exit__(self, *a): return False

        def __init__(self):
            self._data = [payload, b""]

        def read(self, n):
            return self._data.pop(0) if self._data else b""

    monkeypatch.setattr(
        updater, "range_opener", lambda: _Opener(lambda *a, **k: FakeResp())
    )
    ok = updater._download_to(
        str(path), "http://x/engine", 5, None, None, allow_missing=True
    )
    assert ok is True
    assert path.read_bytes() == payload


def test_download_to_uses_range_preserving_opener():
    # audit7 #8: GitHub 302s to a signed CDN URL and the default redirect handler
    # drops the Range header, so every resume re-downloads the whole file (200)
    # — over Tor that never finishes. _download_to must build its opener from the
    # range-preserving one so the tail request survives the redirect.
    import inspect

    src = inspect.getsource(updater._download_to)
    assert "range_opener()" in src
    assert "urlopen" not in src  # no bare urlopen that would lose Range


def test_download_to_resumes_with_206_after_a_dropped_connection(tmp_path, monkeypatch):
    # First open: partial body then EOF (dropped circuit). Second open: sees the
    # Range header, returns 206 with the tail, appends to the .part. The verified
    # digest of the concatenation matches → install succeeds.
    path = tmp_path / "engine.bin"
    full = b"ABCDEFGHIJ"  # 10 bytes
    digest = hashlib.sha256(full).hexdigest()

    class Resp:
        def __init__(self, body, start, total):
            self._data = [body, b""]
            self.status = 206 if start else 200
            self.headers = {
                "Content-Range": f"bytes {start}-{start + len(body) - 1}/{total}"
            }

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return self._data.pop(0) if self._data else b""

    def factory(req):
        rng = req.headers.get("Range")
        start = int(rng.split("=")[1].split("-")[0]) if rng else 0
        # first call drops after 4 bytes; the resume delivers the rest
        return Resp(full[start:4] if start == 0 else full[start:], start, len(full))

    opener = _Opener(factory)
    monkeypatch.setattr(updater, "range_opener", lambda: opener)

    ok = updater._download_to(str(path), "http://x/engine", 5, digest, None)
    assert ok is True
    assert path.read_bytes() == full
    # the resume actually sent a Range header (proves append-not-restart)
    assert any(r and r.startswith("bytes=4-") for r in opener.ranges)


# --- PS-6: the Linux engine install gained the rollback the app updater had ---


def test_install_linux_restores_the_working_engine_when_the_swap_fails(
    tmp_path, monkeypatch
):
    # _install_linux used to be a bare os.replace + chmod with NO backup: a
    # failed swap left the user with no engine at all, while the app updater's
    # AppImage swap had kept a .bak and restored it "so we never lose a
    # launchable app" since v2.1.3. Both now go through the shared
    # atomic_replace, so the engine gets that same guarantee.
    engine = tmp_path / "engine.AppImage"
    engine.write_bytes(b"working-engine")
    asset = tmp_path / "downloaded.AppImage"
    asset.write_bytes(b"new-engine")
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(engine))

    real_replace = updater.os.replace

    def failing_replace(src, dst):
        if str(src) == str(asset):
            raise OSError("Text file busy")  # the asset->engine swap fails
        return real_replace(src, dst)  # the backup->engine restore succeeds

    monkeypatch.setattr(updater.os, "replace", failing_replace)

    assert updater._install_linux(str(asset)) is False
    # the working engine is still there, and still WORKING — not a leftover
    # .bak the launcher can't find, and not a half-written file
    assert engine.read_bytes() == b"working-engine"
    assert not (tmp_path / "engine.AppImage.bak").exists()


def test_install_linux_swaps_in_the_new_engine_and_leaves_no_backup(
    tmp_path, monkeypatch
):
    # the success path: the new engine is in place, executable, and the backup
    # is cleaned up rather than left behind to grow on every update
    engine = tmp_path / "engine.AppImage"
    engine.write_bytes(b"old-engine")
    asset = tmp_path / "downloaded.AppImage"
    asset.write_bytes(b"new-engine")
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(engine))

    assert updater._install_linux(str(asset)) is True
    assert engine.read_bytes() == b"new-engine"
    assert not (tmp_path / "engine.AppImage.bak").exists()
    assert os.access(str(engine), os.X_OK)


# --- PS-38: the shared backup helper, on a DIRECTORY --------------------------
#
# _install_macos's end-to-end path is not exercisable in this container: it
# shells out to hdiutil and ditto, neither of which exists here. Faking a dmg to
# manufacture a green macOS test would be fabricated evidence. Instead the
# backup/restore lives in the shared helper and is tested here DIRECTLY on a
# plain directory — which needs no platform at all — and _install_macos is wired
# to that same helper. The .app bundle IS a directory, which is precisely what
# atomic_replace's file-only shutil.copy2 backup could not handle.


def test_move_aside_then_restore_brings_a_whole_directory_back():
    # AC3. The macOS-shaped case: a previous bundle directory is moved aside,
    # the promotion fails, and the ORIGINAL directory comes back whole —
    # contents, nesting and all.
    import tempfile
    from src.utils import httpdl

    with tempfile.TemporaryDirectory() as tmp:
        bundle = os.path.join(tmp, "Chromium.app")
        os.makedirs(os.path.join(bundle, "Contents", "MacOS"))
        binary = os.path.join(bundle, "Contents", "MacOS", "Chromium")
        with open(binary, "wb") as f:
            f.write(b"WORKING-BUNDLE")
        backup = os.path.join(tmp, ".engine-backup-Chromium.app")

        assert httpdl.move_aside(bundle, backup) is True
        assert not os.path.exists(bundle)  # moved, not copied

        # the failed promotion drops a half-written bundle where it belongs...
        os.makedirs(os.path.join(bundle, "Contents"))
        with open(os.path.join(bundle, "Contents", "junk"), "wb") as f:
            f.write(b"HALF-WRITTEN")

        # ...and the restore replaces it wholesale with the build that worked
        assert httpdl.restore_aside(backup, bundle) is True
        with open(binary, "rb") as f:
            assert f.read() == b"WORKING-BUNDLE"
        assert not os.path.exists(os.path.join(bundle, "Contents", "junk"))
        assert not os.path.exists(backup)


def test_move_aside_is_a_rename_not_a_copy():
    # AC7. Peak disk during an upgrade must not grow by a second copy of the
    # previous build (~300-600MB for Chromium), and a copy would drop the code
    # signature/resource forks a macOS bundle needs. Same inode => renamed.
    import tempfile
    from src.utils import httpdl

    with tempfile.TemporaryDirectory() as tmp:
        artifact = os.path.join(tmp, "engine")
        os.makedirs(artifact)
        inner = os.path.join(artifact, "chrome")
        with open(inner, "wb") as f:
            f.write(b"x" * 1024)
        before = os.stat(inner).st_ino

        assert httpdl.move_aside(artifact, os.path.join(tmp, "bak")) is True
        assert os.stat(os.path.join(tmp, "bak", "chrome")).st_ino == before


def test_move_aside_reports_a_first_install_has_nothing_to_preserve():
    # A first install has no previous artifact. That is not an error, and must
    # not be mistaken for "a backup was taken" — the caller keys its restore on
    # this answer, so a wrong True would try to restore a backup that never
    # existed.
    import tempfile
    from src.utils import httpdl

    with tempfile.TemporaryDirectory() as tmp:
        missing = os.path.join(tmp, "not-there")
        assert httpdl.move_aside(missing, os.path.join(tmp, "bak")) is False
        assert not os.path.exists(os.path.join(tmp, "bak"))


def test_restore_never_raises_so_a_failed_rollback_cannot_crash_the_install():
    # The restore runs on a path that is ALREADY reporting a failure. If it
    # raised, a reported install failure would become a crash — the same
    # forgiveness the marker/sentinel writes use.
    import tempfile
    from src.utils import httpdl

    with tempfile.TemporaryDirectory() as tmp:
        # nothing to restore from: answers False, does not explode
        assert httpdl.restore_aside(
            os.path.join(tmp, "no-backup"), os.path.join(tmp, "dst")
        ) is False
