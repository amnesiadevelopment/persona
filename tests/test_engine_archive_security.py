"""The engine archive is UNTRUSTED at unpack time, even though it is authentic.

`_extract_as` runs only after the caller has verified the archive's sha256
against upstream's own checksums.txt, so a tampered download never reaches it.
What it does NOT establish is that authentic bytes are safe to unpack: an
upstream compromise or a malicious/buggy published build ships an archive that
passes its digest and still carries a hostile member. This is the same principle
tests/test_transfer_security.py states for imported profile zips, applied to the
engine tarball — and, like that file, every assertion here is about FILES ON
DISK after driving the real function, never about a helper being called or a
substring appearing in the source.

At HEAD before PS-228 the tar arm called `tf.extractall(dst)` with no `filter=`
and these tests were RED on all three hostile members: a `../../x` member landed
outside the destination, and both an absolute symlink and a relative symlink
resolving outside the destination survived on disk. The zip arm is the control —
CPython's ZipFile.extractall already sanitizes member paths — and is untouched.
"""

import io
import os
import sys
import tarfile
import zipfile

from src.services.browser.engine_install import _extract_as


def _dst(tmp_path):
    """A destination NESTED under tmp_path, so a `../../x` member escaping it
    lands inside tmp_path where the test can see it (rather than somewhere the
    test cannot assert about)."""
    return tmp_path / "cache" / "firefox-20_151.0"


def _tar_with(tmp_path, build):
    """Write a .tar.gz whose members `build(tf)` adds, and return its path.

    The file is named ".download" exactly as the real caller names it — the
    format is chosen from the ASSET name passed to `_extract_as`, not from the
    file's own suffix."""
    p = tmp_path / "asset.download"
    with tarfile.open(p, "w:gz") as tf:
        build(tf)
    return str(p)


def _symlink_member(tf, name, target):
    ti = tarfile.TarInfo(name)
    ti.type = tarfile.SYMTYPE
    ti.linkname = target
    tf.addfile(ti)


def _regular_member(tf, name, data=b"payload"):
    ti = tarfile.TarInfo(name)
    ti.size = len(data)
    tf.addfile(ti, io.BytesIO(data))


def _extract_ignoring_refusal(archive, dst, asset_name):
    """Drive the REAL `_extract_as` and swallow a refusal.

    Refusing the archive and rewriting the member under `dst` are both
    acceptable confinements; only the resulting filesystem distinguishes a fix
    from the defect. Swallowing the exception here is what keeps every hostile
    test above asserting on DISK STATE rather than on control flow — so the
    falsification in AC6 goes red on files, not on a missing raise."""
    try:
        _extract_as(str(archive), str(dst), asset_name)
    except Exception:
        pass


# --------------------------------------------------------------------------
# AC1 — a traversal member is not written outside the destination
# --------------------------------------------------------------------------


def test_tar_traversal_member_is_not_written_outside_destination(tmp_path):
    archive = _tar_with(
        tmp_path,
        lambda tf: (
            _regular_member(tf, "firefox"),
            _regular_member(tf, "../../PWNED.txt", b"escaped"),
        ),
    )
    dst = _dst(tmp_path)

    # Refusing is one acceptable behaviour and rewriting the member under dst is
    # another — the ticket's requirement is about the FILESYSTEM, so the
    # exception is swallowed deliberately and the disk is what is asserted on.
    # Gating on pytest.raises instead would make this a test about control flow.
    _extract_ignoring_refusal(archive, dst, "firefox-151.0-stealth-linux-x86_64.tar.gz")

    escaped = [
        str(q.relative_to(tmp_path))
        for q in tmp_path.rglob("PWNED.txt")
        if dst not in q.parents
    ]
    assert escaped == [], f"traversal member escaped the destination: {escaped}"


# --------------------------------------------------------------------------
# AC3 — the two symlink forms are distinct, and `filter="tar"` closes neither
# --------------------------------------------------------------------------


def test_tar_absolute_symlink_member_is_not_created(tmp_path):
    archive = _tar_with(
        tmp_path,
        lambda tf: (
            _regular_member(tf, "firefox"),
            _symlink_member(tf, "libxul.so", "/etc/passwd"),
        ),
    )
    dst = _dst(tmp_path)
    _extract_ignoring_refusal(archive, dst, "firefox-151.0-stealth-linux-x86_64.tar.gz")

    link = dst / "libxul.so"
    assert not link.is_symlink(), (
        "absolute symlink survived on disk -> "
        f"{os.readlink(link) if link.is_symlink() else ''}"
    )


def test_tar_relative_symlink_pointing_outside_destination_is_not_created(tmp_path):
    # Distinct from the absolute case: filter="tar" keeps BOTH, so a fix that
    # only closed the absolute form would leave this one green-looking and open.
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"host secret")

    archive = _tar_with(
        tmp_path,
        lambda tf: (
            _regular_member(tf, "firefox"),
            _symlink_member(tf, "libxul.so", "../../secret.txt"),
        ),
    )
    dst = _dst(tmp_path)
    _extract_ignoring_refusal(archive, dst, "firefox-151.0-stealth-linux-x86_64.tar.gz")

    link = dst / "libxul.so"
    assert not link.is_symlink(), (
        "outside-pointing relative symlink survived on disk -> "
        f"{os.readlink(link) if link.is_symlink() else ''}"
    )
    # And nothing under the destination can be used to read the host file.
    # NOTE the parentheses: `a if c else b != x` binds the conditional LOOSER
    # than `!=`, so writing it unparenthesised asserts the truthiness of the
    # link's own bytes and PASSES while the host secret is readable through the
    # destination. That is a false green in a file whose whole point is disk
    # state; the parens are what make this assertion the one the comment claims.
    assert (link.read_bytes() if link.exists() else b"") != b"host secret"


# --------------------------------------------------------------------------
# AC4 — a legitimate engine archive still installs
# --------------------------------------------------------------------------


def test_tar_benign_engine_shaped_archive_still_extracts(tmp_path):
    """The shape a real published engine build actually has, measured rather
    than assumed (PS-228): regular files, nested dirs, an executable launcher,
    and hardlinks to siblings INSIDE the tree. All four published firefox-20
    assets were fetched, checksum-verified, and extracted under no filter,
    filter="tar" and filter="data" — the trees came out identical file-for-file
    and mode-for-mode. The Linux builds carry 10 sibling hardlinks (branding
    icons and locale .properties); the macOS .app bundles carry no link members
    at all. Nothing in a legitimate build points outside the destination."""

    def build(tf):
        import io

        launcher = tarfile.TarInfo("firefox")
        launcher.size = 4
        launcher.mode = 0o755
        tf.addfile(launcher, io.BytesIO(b"ELF\n"))

        _regular_member(tf, "browser/chrome/icons/default/default32.png", b"png")
        _regular_member(tf, "res/locale/necko/necko.properties", b"k=v\n")

        # Sibling hardlink — exactly what the real Linux builds contain.
        hl = tarfile.TarInfo("browser/chrome/browser/content/branding/icon32.png")
        hl.type = tarfile.LNKTYPE
        hl.linkname = "browser/chrome/icons/default/default32.png"
        tf.addfile(hl)

        # Sibling symlink — benign, resolves inside the destination.
        _symlink_member(tf, "libxul.so", "browser/chrome/icons/default/default32.png")

    archive = _tar_with(tmp_path, build)
    dst = _dst(tmp_path)

    _extract_as(archive, str(dst), "firefox-151.0-stealth-linux-x86_64.tar.gz")

    launcher = dst / "firefox"
    assert launcher.is_file()
    if sys.platform != "win32":
        # NTFS surfaces 0o666 — there is no POSIX execute bit to keep, so this
        # one assertion cannot hold on Windows. The rest of the test is the AC4
        # regression guard and is exactly as meaningful there, so only the
        # permission-bit line is gated, never the whole test.
        assert launcher.stat().st_mode & 0o111, "launcher lost its executable bit"
    assert (dst / "browser/chrome/icons/default/default32.png").is_file()
    assert (dst / "res/locale/necko/necko.properties").is_file()
    assert (dst / "browser/chrome/browser/content/branding/icon32.png").is_file()
    assert (dst / "libxul.so").exists(), "benign sibling symlink was dropped"


# --------------------------------------------------------------------------
# AC5 — the zip arm is the CONTROL. It already sanitizes; it is not the target.
# --------------------------------------------------------------------------


def test_zip_arm_still_confines_a_traversal_member(tmp_path):
    p = tmp_path / "asset.download"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("firefox.exe", "payload")
        zf.writestr("../../PWNED.txt", "escaped")

    dst = _dst(tmp_path)
    _extract_as(str(p), str(dst), "firefox-151.0-stealth-win-x86_64.zip")

    escaped = [str(q.relative_to(tmp_path)) for q in tmp_path.rglob("PWNED.txt")]
    # CPython rewrites the member to land under dst — it is not written outside.
    assert all(str(dst.relative_to(tmp_path)) in e for e in escaped), escaped
    assert not (tmp_path / "PWNED.txt").exists()
    assert (dst / "firefox.exe").is_file()
