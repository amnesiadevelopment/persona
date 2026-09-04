"""`_extract_as` chooses the archive FORMAT from the asset name, not the file.

This file is about the DISPATCH DECISION — which arm runs, and from which of
the two names — and it is a different axis from
tests/test_engine_archive_security.py, which is about what a hostile MEMBER may
do once an arm has been chosen. That file drives the real function too, so the
dispatch is no longer wholly unexercised; what it does not pin is the routing
itself. Measured at HEAD 6222c47 by mutation, running that file plus
tests/test_engine_firefox.py:

    key on `archive_path` instead of `asset_name`  -> 2 failed   (caught)
    swap the .zip and .tar.gz arms                 -> 2 failed   (caught)
    drop the ".tgz" alias                          -> 51 passed  (SURVIVED)
    drop the unknown-format raise                  -> 51 passed  (SURVIVED)
    drop makedirs(dst, exist_ok=True)              -> 51 passed  (SURVIVED)

The two caught mutants are caught INCIDENTALLY — by security tests that would
still be honest tests of member confinement if the routing were pinned
elsewhere — so they are pinned here directly rather than left resting on a
neighbour's side effect.

WHY THE ROUTING MATTERS AT ALL: the caller downloads to "<asset>.download"
(engine_install.py:~570), whose suffix hides the real type, and extracts the
partial in place with no rename — that is what avoids the Windows "file in use"
lock on os.replace. So the file's own name is deliberately uninformative, and a
"simplification" that keyed on it would leave every install silently producing
no binary: install_engine_build finds no binary, writes no completion marker,
keeps the previous build active, and the operator is told "will retry next
start" forever.

Every assertion below is about FILES ON DISK (or the raised exception's own
message) after driving the real function — never about a helper being called or
a substring appearing in the source.

SCOPE: this file pins what ships. It asserts no opinion on tar member filtering
(PS-228 owns that) and changes nothing in engine_install.py.
"""

import io
import tarfile
import zipfile

import pytest

from src.services.browser.engine_install import _extract_as

# Real asset names, as published on the engine's GitHub releases.
ASSET_ZIP = "firefox-151.0-stealth-win-x86_64.zip"
ASSET_TARGZ = "firefox-151.0-stealth-linux-x86_64.tar.gz"
ASSET_TGZ = "firefox-151.0-stealth-linux-x86_64.tgz"


def _dst(tmp_path):
    """A destination NESTED two levels under tmp_path, so "was dst created?" is
    a real question (both parents are absent) rather than one tmp_path has
    already answered."""
    return tmp_path / "cache" / "firefox-20_151.0"


def _write_zip(path, entries):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return str(path)


def _write_targz(path, entries):
    with tarfile.open(path, "w:gz") as tf:
        for name, data in entries.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return str(path)


# --------------------------------------------------------------------------
# 1-2 — each arm extracts its OWN format's content
#
# These are the pair that goes red if the two arms' conditions are swapped:
# a zip asset routed to tarfile.open(..., "r:gz") raises ReadError, and a
# tar.gz asset routed to zipfile.ZipFile raises BadZipFile. Asserting the
# extracted CONTENT (not merely "no exception") is what makes them assertions
# about the arm that ran rather than about the call returning.
# --------------------------------------------------------------------------


def test_zip_asset_extracts_zip_content(tmp_path):
    archive = _write_zip(tmp_path / "asset.download", {"firefox.exe": b"ZIPCONTENT"})
    dst = _dst(tmp_path)

    _extract_as(archive, str(dst), ASSET_ZIP)

    assert (dst / "firefox.exe").read_bytes() == b"ZIPCONTENT"


def test_tar_gz_asset_extracts_tar_content(tmp_path):
    archive = _write_targz(tmp_path / "asset.download", {"firefox": b"TARCONTENT"})
    dst = _dst(tmp_path)

    _extract_as(archive, str(dst), ASSET_TARGZ)

    assert (dst / "firefox").read_bytes() == b"TARCONTENT"


# --------------------------------------------------------------------------
# 3 — ".tgz" is an ALIAS of ".tar.gz" and takes the same arm
#
# Unasserted at HEAD: deleting the alias from the condition left the whole
# suite green, which means the tgz spelling could be dropped in a refactor and
# every published .tgz asset would start raising "unknown archive format".
# --------------------------------------------------------------------------


def test_tgz_asset_takes_the_same_arm_as_tar_gz(tmp_path):
    archive = _write_targz(tmp_path / "asset.download", {"firefox": b"TARCONTENT"})
    dst = _dst(tmp_path)

    _extract_as(archive, str(dst), ASSET_TGZ)

    assert (dst / "firefox").read_bytes() == b"TARCONTENT"


# --------------------------------------------------------------------------
# 4 — an unrecognised asset name RAISES, and the message names the asset
#
# The message is asserted, not just the type: the operator-facing log line at
# the call site is `f"{type(e).__name__}: {e}"`, so a raise that does not name
# the asset reports "RuntimeError: unknown archive format" and the operator
# cannot tell WHICH asset the release served them.
#
# It must also NOT have written anything: falling through silently would let a
# later "is the binary there?" check be the only thing standing between an
# unpacked-nothing and an install marked complete.
# --------------------------------------------------------------------------


def test_unknown_asset_extension_raises_naming_the_asset(tmp_path):
    archive = _write_targz(tmp_path / "asset.download", {"firefox": b"TARCONTENT"})
    dst = _dst(tmp_path)
    bad_asset = "firefox-151.0-stealth-linux-x86_64.AppImage"

    with pytest.raises(RuntimeError) as excinfo:
        _extract_as(archive, str(dst), bad_asset)

    assert bad_asset in str(excinfo.value), (
        "the raise must name the asset — the call site logs only "
        f"'{type(excinfo.value).__name__}: {excinfo.value}', so an unnamed "
        "asset leaves the operator unable to tell which build failed"
    )
    assert not (dst / "firefox").exists(), "refused asset still unpacked content"


# --------------------------------------------------------------------------
# 5 — THE POINT OF THE FUNCTION: the file's own suffix is ignored
#
# Asserted by NAME MISMATCH in BOTH directions, because one direction alone is
# ambiguous. A file named "*.download" carrying tar bytes with a ".tar.gz"
# asset only shows that the file's suffix is not REQUIRED; the second case —
# a file whose suffix says ".zip" while the asset says ".tar.gz", carrying tar
# bytes — shows that a misleading file suffix is not merely tolerated but
# actively IGNORED. Keying on archive_path passes the first and fails the
# second.
# --------------------------------------------------------------------------


def test_download_suffixed_file_extracts_from_the_asset_name(tmp_path):
    # Exactly how the caller names it: "<asset>.download", extracted in place
    # with no rename (which is what avoids the Windows os.replace lock).
    archive = _write_targz(
        tmp_path / f"{ASSET_TARGZ}.download", {"firefox": b"TARCONTENT"}
    )
    dst = _dst(tmp_path)

    _extract_as(archive, str(dst), ASSET_TARGZ)

    assert (dst / "firefox").read_bytes() == b"TARCONTENT"


def test_misleading_file_suffix_is_ignored_in_favour_of_the_asset_name(tmp_path):
    # The file claims .zip; it is a tarball; the ASSET says .tar.gz. The asset
    # name is the authority, so this extracts rather than raising BadZipFile.
    archive = _write_targz(tmp_path / "totally-a.zip", {"firefox": b"TARCONTENT"})
    dst = _dst(tmp_path)

    _extract_as(archive, str(dst), ASSET_TARGZ)

    assert (dst / "firefox").read_bytes() == b"TARCONTENT"


# --------------------------------------------------------------------------
# 6 — nested members land under dst, and dst is CREATED when absent
#
# The creation is asserted with an EMPTY archive on purpose: both
# ZipFile.extractall and tarfile.extractall create the destination as a side
# effect of writing a member, so an archive with any content would pass with
# makedirs deleted. Measured: with no members, neither creates it — so this is
# the only shape in which the makedirs line is load-bearing, and dropping it
# left the whole suite green.
# --------------------------------------------------------------------------


def test_nested_members_land_under_the_destination(tmp_path):
    archive = _write_targz(
        tmp_path / "asset.download",
        {
            "firefox": b"ELF\n",
            "browser/chrome/icons/default/default32.png": b"png",
            "res/locale/necko/necko.properties": b"k=v\n",
        },
    )
    dst = _dst(tmp_path)

    _extract_as(archive, str(dst), ASSET_TARGZ)

    assert (dst / "browser/chrome/icons/default/default32.png").read_bytes() == b"png"
    assert (dst / "res/locale/necko/necko.properties").read_bytes() == b"k=v\n"
    # Nothing landed beside the destination instead of inside it.
    assert not (tmp_path / "browser").exists()


@pytest.mark.parametrize(
    "asset,writer",
    [(ASSET_TARGZ, _write_targz), (ASSET_ZIP, _write_zip)],
    ids=["tar", "zip"],
)
def test_destination_is_created_when_absent(tmp_path, asset, writer):
    # Empty archive: see the note above — with members present, extractall
    # would create the destination and this assertion would be vacuous.
    archive = writer(tmp_path / "asset.download", {})
    dst = _dst(tmp_path)
    assert not dst.exists()

    _extract_as(archive, str(dst), asset)

    assert dst.is_dir(), "destination was not created for an empty archive"
