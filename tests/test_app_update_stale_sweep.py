"""The stale-installer sweep must treat its DIRECTORY as a path, not a pattern.

PS-265, the fourth instance of one defect (19e7f82 -> cbfefc8 -> PS-227's two
sites). `_clear_stale_staged` builds its glob by joining a directory it does not
control with a metacharacter-bearing filename half. glob interprets
metacharacters across the WHOLE pattern, the directory portion included, so a
directory named `Apps[old]` turns `[old]` into a character class, the pattern
names a path that does not exist, glob.glob returns [], the loop body never
runs, and the function returns normally — no exception, no empty-result branch,
no signal. Stale AppImage/installer downloads then accumulate without limit.

Both source directories are operator-controlled: tempfile.gettempdir() honours
TMPDIR/TEMP/TMP, and the Linux arm's directory is
os.path.dirname(installed_appimage_path()) — wherever the user put the AppImage,
which for a portable layout is a hand-named directory.

Every assertion here is bound to BYTES ON DISK after the shipped function runs,
never to "a helper was called" (PS-11). Revert the glob.escape in
`_clear_stale_staged` and the bracketed tests go RED with the stale files still
present; the plain-directory control stays green either way, which is what makes
the fix safe to apply unconditionally.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from src.services.app_update import updater as au

BRACKETED = "Apps[old] portable"


def _force_os(monkeypatch, *, win=False, mac=False):
    monkeypatch.setattr(au._platform, "IS_WINDOWS", win)
    monkeypatch.setattr(au._platform, "IS_MACOS", mac)
    if hasattr(au._platform, "IS_LINUX"):
        monkeypatch.setattr(au._platform, "IS_LINUX", not (win or mac))


def _plant(directory, names):
    """Write a real byte into each name and return {name: path}."""
    planted = {}
    for name in names:
        path = os.path.join(str(directory), name)
        with open(path, "wb") as f:
            f.write(b"x")
        planted[name] = path
    assert all(os.path.exists(p) for p in planted.values())
    return planted


def _surviving(directory):
    return set(os.listdir(str(directory)))


# --- the Linux AppImage arm: dir = dirname(installed_appimage_path()) ------


def _appimage_dir(monkeypatch, tmp_path, dirname):
    d = tmp_path / dirname
    d.mkdir()
    app = d / "persona.AppImage"
    app.write_bytes(b"elf")
    monkeypatch.setenv("APPIMAGE", str(app))
    # installed_appimage_path() resolves symlinks; assert the premise so the
    # tests below cannot pass vacuously against some other directory.
    assert os.path.dirname(au.installed_appimage_path()) == os.path.realpath(str(d))
    return d


def test_appimage_sweep_removes_other_versions_under_a_bracketed_directory(
    monkeypatch, tmp_path
):
    """A `[` in the AppImage's own directory must not silence the sweep.

    RED when the glob.escape on the Linux arm is reverted: the pattern becomes
    `.../Apps[old] portable/.persona-update*.AppImage.part`, in which `[old]` is
    a character class, glob.glob returns [], and every stale .part survives."""
    _force_os(monkeypatch)
    d = _appimage_dir(monkeypatch, tmp_path, BRACKETED)
    keep = ".persona-update-v9.9.9.AppImage.part"
    stale = [
        ".persona-update-v8.0.0.AppImage.part",
        ".persona-update-v7.1.2.AppImage.part",
        ".persona-update.AppImage.part",  # the pre-tag legacy name
    ]
    planted = _plant(d, [keep, *stale])

    au._clear_stale_staged(keep=planted[keep])

    survivors = _surviving(d)
    assert keep in survivors, "the current version's staged file must survive"
    leftover = sorted(n for n in stale if n in survivors)
    assert not leftover, (
        "the stale-installer sweep no-opped on a directory containing a glob "
        f"metacharacter; these other versions' files are still on disk: {leftover}"
    )
    assert "persona.AppImage" in survivors, "the installed AppImage is not a .part"


def test_appimage_sweep_is_unchanged_on_a_plain_directory(monkeypatch, tmp_path):
    """The control that makes the fix safe to apply unconditionally.

    With no metacharacter in the directory name, glob.escape is a no-op: this
    test is green both before and after the fix, so a regression here means the
    escaping changed ordinary behaviour rather than only the bracketed case."""
    _force_os(monkeypatch)
    d = _appimage_dir(monkeypatch, tmp_path, "Apps")
    keep = ".persona-update-v9.9.9.AppImage.part"
    stale = ".persona-update-v8.0.0.AppImage.part"
    planted = _plant(d, [keep, stale])

    au._clear_stale_staged(keep=planted[keep])

    survivors = _surviving(d)
    assert keep in survivors
    assert stale not in survivors
    assert "persona.AppImage" in survivors


def test_appimage_sweep_returns_without_a_packaged_appimage(monkeypatch):
    # The early return the escaping must not disturb.
    _force_os(monkeypatch)
    monkeypatch.delenv("APPIMAGE", raising=False)
    au._clear_stale_staged(keep="")  # must not raise


# --- the gettempdir() arms: TMPDIR/TEMP/TMP are operator-controlled --------


def _bracketed_tmpdir(monkeypatch, tmp_path, dirname=BRACKETED):
    d = tmp_path / dirname
    d.mkdir()
    # tempfile.gettempdir() caches its search of TMPDIR/TEMP/TMP at first use,
    # so drive the module's own override — the same value those env vars set.
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    assert tempfile.gettempdir() == str(d)
    return d


@pytest.mark.parametrize(
    "flags, keep_name, stale_names",
    [
        (
            {"win": True},
            "persona-update-setup-v9.9.9.exe",
            ["persona-update-setup-v8.0.0.exe", "persona-update-setup.exe"],
        ),
        (
            {"mac": True},
            "persona-update-v9.9.9.dmg",
            ["persona-update-v8.0.0.dmg", "persona-update.dmg"],
        ),
    ],
    ids=["windows-exe", "macos-dmg"],
)
def test_temp_sweep_removes_other_versions_under_a_bracketed_tmpdir(
    monkeypatch, tmp_path, flags, keep_name, stale_names
):
    """TMPDIR is operator-set, so it can carry a `[` just as a home can.

    RED when the glob.escape on the corresponding arm is reverted: the pattern
    names a character class instead of the directory, glob.glob returns [], and
    every other version's installer stays on disk forever."""
    _force_os(monkeypatch, **flags)
    d = _bracketed_tmpdir(monkeypatch, tmp_path)
    planted = _plant(d, [keep_name, *stale_names])

    au._clear_stale_staged(keep=planted[keep_name])

    survivors = _surviving(d)
    assert keep_name in survivors, "the current version's installer must survive"
    leftover = sorted(n for n in stale_names if n in survivors)
    assert not leftover, (
        "the stale-installer sweep no-opped on a TMPDIR containing a glob "
        f"metacharacter; these other versions' installers are still on disk: "
        f"{leftover}"
    )


@pytest.mark.parametrize(
    "flags, keep_name, stale_name",
    [
        (
            {"win": True},
            "persona-update-setup-v9.9.9.exe",
            "persona-update-setup-v8.0.0.exe",
        ),
        ({"mac": True}, "persona-update-v9.9.9.dmg", "persona-update-v8.0.0.dmg"),
    ],
    ids=["windows-exe", "macos-dmg"],
)
def test_temp_sweep_is_unchanged_on_a_plain_tmpdir(
    monkeypatch, tmp_path, flags, keep_name, stale_name
):
    # Plain-directory control for the gettempdir() arms.
    _force_os(monkeypatch, **flags)
    d = _bracketed_tmpdir(monkeypatch, tmp_path, dirname="tmp")
    planted = _plant(d, [keep_name, stale_name])

    au._clear_stale_staged(keep=planted[keep_name])

    survivors = _surviving(d)
    assert keep_name in survivors
    assert stale_name not in survivors


def test_temp_sweep_leaves_unrelated_files_alone(monkeypatch, tmp_path):
    # The filename half must keep its metacharacters AND its literal prefix:
    # escaping the directory must not widen what the sweep destroys.
    _force_os(monkeypatch, win=True)
    d = _bracketed_tmpdir(monkeypatch, tmp_path)
    planted = _plant(
        d,
        [
            "persona-update-setup-v9.9.9.exe",
            "persona-update-setup-v8.0.0.exe",
            "some-other-installer.exe",
            "persona-update-v8.0.0.dmg",  # another arm's shape, not this one's
            "notes.txt",
        ],
    )

    au._clear_stale_staged(keep=planted["persona-update-setup-v9.9.9.exe"])

    survivors = _surviving(d)
    assert "persona-update-setup-v8.0.0.exe" not in survivors
    for untouched in ("some-other-installer.exe", "persona-update-v8.0.0.dmg", "notes.txt"):
        assert untouched in survivors, f"{untouched} is not a staged Windows installer"
