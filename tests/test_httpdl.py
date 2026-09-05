"""The range-preserving redirect handler must carry the Range header onto the
follow-up request so a resumed download gets the tail (206), not the whole file
(200), after GitHub's 302 to a signed CDN URL."""

import urllib.request

from src.utils.httpdl import KeepRangeRedirect, range_opener


class _Headers(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)


def test_redirect_reattaches_range_header():
    handler = KeepRangeRedirect()
    req = urllib.request.Request("http://gh/asset")
    req.add_header("Range", "bytes=500-")
    new = handler.redirect_request(
        req, fp=None, code=302, msg="Found",
        headers=_Headers({"location": "http://cdn/asset"}),
        newurl="http://cdn/asset",
    )
    assert new is not None
    assert new.get_header("Range") == "bytes=500-"


def test_redirect_without_range_leaves_it_absent():
    handler = KeepRangeRedirect()
    req = urllib.request.Request("http://gh/asset")
    new = handler.redirect_request(
        req, fp=None, code=302, msg="Found",
        headers=_Headers({"location": "http://cdn/asset"}),
        newurl="http://cdn/asset",
    )
    assert new is not None
    assert new.get_header("Range") is None


def test_range_opener_installs_the_handler():
    opener = range_opener()
    assert any(isinstance(h, KeepRangeRedirect) for h in opener.handlers)


# --- atomic_replace's outcome side channel (PS-245) --------------------------
#
# The helper had NO direct coverage here before this slice, and its three
# distinct False states all returned the same bare False — so a caller could not
# tell a destination provably never touched, from one restored to the working
# artifact, from one left half-new. These pin each state on REAL on-disk bytes.

import errno  # noqa: E402
import os  # noqa: E402

import pytest  # noqa: E402

from src.utils import httpdl  # noqa: E402


def _fail_replace_onto(monkeypatch, target, err=errno.ENOSPC):
    """Make os.replace raise only for the SWAP onto `target` — not for the
    backup staging rename, and not for the RESTORE rename that puts the backup
    back. That models ENOSPC honestly: writing the new, larger artifact is what
    runs the disk out, while renaming the backup into space already allocated to
    it succeeds. A blunter fake that fails every rename onto `target` also fails
    the restore, and then `dst` merely still holds the old bytes because nothing
    ever overwrote them — which LOOKS like a successful rollback and is not one.
    """
    real = os.replace
    backup = os.path.abspath(str(target)) + ".bak"

    def fake(a, b, *args, **kwargs):
        if (
            os.path.abspath(str(b)) == os.path.abspath(str(target))
            and os.path.abspath(str(a)) != backup
        ):
            raise OSError(err, "No space left on device")
        return real(a, b, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fake)
    return real


def test_atomic_replace_reports_ok_on_success(tmp_path):
    src = tmp_path / "new"
    dst = tmp_path / "live"
    src.write_bytes(b"NEW")
    dst.write_bytes(b"OLD")

    outcome = {}
    assert httpdl.atomic_replace(
        str(src), str(dst), mode=None, outcome=outcome
    ) is True
    assert outcome["state"] == httpdl.REPLACE_OK
    assert dst.read_bytes() == b"NEW"


def test_atomic_replace_reports_restored_when_the_swap_fails(tmp_path, monkeypatch):
    """The arm whose restore result used to be swallowed by a bare
    `except: pass`. The previous artifact IS back — assert on its bytes, and on
    the outcome now saying so."""
    src = tmp_path / "new"
    dst = tmp_path / "live"
    src.write_bytes(b"NEW")
    dst.write_bytes(b"WORKING-BUILD")

    _fail_replace_onto(monkeypatch, dst)
    outcome = {}
    assert httpdl.atomic_replace(
        str(src), str(dst), mode=None, outcome=outcome
    ) is False
    monkeypatch.undo()

    assert dst.read_bytes() == b"WORKING-BUILD"
    assert outcome["state"] == httpdl.REPLACE_RESTORED
    assert outcome["state"] in httpdl.REPLACE_PREVIOUS_INTACT


def test_atomic_replace_reports_not_restored_when_the_restore_also_fails(
    tmp_path, monkeypatch
):
    """The state that must NEVER read as restored: the swap failed AND putting
    the backup back failed too, so `dst` is not the working artifact."""
    src = tmp_path / "new"
    dst = tmp_path / "live"
    src.write_bytes(b"NEW")
    dst.write_bytes(b"WORKING-BUILD")

    backup = str(dst) + ".bak"
    real = os.replace

    def fake(a, b, *args, **kwargs):
        # fail the swap onto dst...
        if os.path.abspath(str(b)) == os.path.abspath(str(dst)):
            raise OSError(errno.ENOSPC, "No space left on device")
        # ...which also fails the restore, since it renames backup -> dst
        return real(a, b, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fake)
    outcome = {}
    assert httpdl.atomic_replace(
        str(src), str(dst), mode=None, outcome=outcome
    ) is False
    monkeypatch.undo()

    assert outcome["state"] == httpdl.REPLACE_NOT_RESTORED
    assert outcome["state"] not in httpdl.REPLACE_PREVIOUS_INTACT
    # the backup is still there — it is the last surviving copy
    assert os.path.exists(backup)


def test_atomic_replace_reports_dst_untouched_when_the_backup_fails(
    tmp_path, monkeypatch
):
    """The couldn't-back-up arm: it returns BEFORE os.replace is attempted, so
    `dst` is provably the same working artifact it was. PS-245 AC6 decides that
    this counts as intact, and the bytes are the evidence."""
    src = tmp_path / "new"
    dst = tmp_path / "live"
    src.write_bytes(b"NEW")
    dst.write_bytes(b"WORKING-BUILD")

    def boom(*a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(httpdl.shutil, "copy2", boom)
    outcome = {}
    assert httpdl.atomic_replace(
        str(src), str(dst), mode=None, outcome=outcome
    ) is False
    monkeypatch.undo()

    assert dst.read_bytes() == b"WORKING-BUILD"
    assert outcome["state"] == httpdl.REPLACE_DST_UNTOUCHED
    assert outcome["state"] in httpdl.REPLACE_PREVIOUS_INTACT


def test_atomic_replace_first_install_failure_is_not_restored(tmp_path, monkeypatch):
    """A FIRST install has no previous artifact, so a failed swap leaves nothing
    intact to go back to. It must not read as restored just because no restore
    was needed."""
    src = tmp_path / "new"
    dst = tmp_path / "live"  # deliberately absent
    src.write_bytes(b"NEW")

    _fail_replace_onto(monkeypatch, dst)
    outcome = {}
    assert httpdl.atomic_replace(
        str(src), str(dst), mode=None, outcome=outcome
    ) is False
    monkeypatch.undo()

    assert not dst.exists()
    assert outcome["state"] == httpdl.REPLACE_NOT_RESTORED


def test_atomic_replace_outcome_is_opt_in_and_default_free(tmp_path):
    """The sink is OPT-IN: every existing caller passes nothing, and both the
    return value and the on-disk effect are exactly what they always were."""
    src = tmp_path / "new"
    dst = tmp_path / "live"
    src.write_bytes(b"NEW")
    dst.write_bytes(b"OLD")
    assert httpdl.atomic_replace(str(src), str(dst), mode=None) is True
    assert dst.read_bytes() == b"NEW"
    assert not os.path.exists(str(dst) + ".bak")


def test_atomic_replace_retain_backup_still_retains_with_outcome(tmp_path):
    """The app updater's caller (retain_backup=True) is untouched: the .bak
    still survives a successful replace, whether or not an outcome is asked for.
    """
    src = tmp_path / "new"
    dst = tmp_path / "live"
    src.write_bytes(b"NEW")
    dst.write_bytes(b"OLD")
    outcome = {}
    assert httpdl.atomic_replace(
        str(src), str(dst), mode=None, retain_backup=True, outcome=outcome
    ) is True
    assert outcome["state"] == httpdl.REPLACE_OK
    assert (tmp_path / "live.bak").read_bytes() == b"OLD"
