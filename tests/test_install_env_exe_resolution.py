"""`install_env.installed_windows_exe()` — the candidate ladder that decides
WHERE a Windows update relaunches persona from.

WHY THIS FILE EXISTS (PS-281). Every one of the 12 references to this function
in `tests/` is `monkeypatch.setattr(au, "_installed_windows_exe", lambda: ...)`
— the caller tests all REPLACE it, so its own body had never executed. A
mutation that raised on the first line of the body left the whole app_update
suite green. That is not a cosmetic gap: `updater.py` guards the relaunch with
`if exe:` and `fast_update.py` refuses the fast path on a falsy answer, so a
wrong or empty result does not crash — the update installs and persona never
comes back.

These tests drive the real body. No Windows is needed: the ladder reads only
`sys.executable`, `os.environ` and `os.path.isfile`, all of which are drivable
on any platform with `tmp_path` + `monkeypatch`.

The ORDER of the ladder is the contract, not an implementation detail — a
per-user (LOCALAPPDATA) install and a legacy per-machine (ProgramFiles) install
can both exist on one machine, and relaunching the wrong one silently runs the
build the user did not just update. So the ordering tests deliberately put BOTH
candidates on disk and assert WHICH is returned; a test that only made one exist
would pass under a reordered ladder.

Scope note: this file pins the ladder as it ships. It does not argue the order
is right.
"""

import os
import sys

import pytest

from src.services.app_update import install_env


@pytest.fixture(autouse=True)
def _no_inherited_install_locations(monkeypatch):
    """The ladder reads two environment variables that the machine running the
    suite may genuinely have set (a Windows dev box has both). Clear them so
    each test states its own world; a test that wants one sets it explicitly."""
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("ProgramFiles", raising=False)


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ")  # not read; the ladder only asks os.path.isfile
    return str(path)


def _install_locations(tmp_path, monkeypatch, *, local=True, program_files=True):
    """Materialise the two install-location candidates and point the ladder's
    environment variables at them. Returns (local_exe, pf_exe) — either is None
    when that location was not created."""
    local_exe = pf_exe = None
    if local:
        root = tmp_path / "local"
        local_exe = _touch(root / "persona" / "persona.exe")
        monkeypatch.setenv("LOCALAPPDATA", str(root))
    if program_files:
        root = tmp_path / "pf"
        pf_exe = _touch(root / "persona" / "persona.exe")
        monkeypatch.setenv("ProgramFiles", str(root))
    return local_exe, pf_exe


# --- rung A: sys.executable IS the installed persona.exe ---


def test_sys_executable_named_persona_exe_is_returned_verbatim(tmp_path, monkeypatch):
    """In a flet build `sys.executable` IS persona.exe, and the ladder returns
    that exact string rather than a path it reassembled.

    The name is spelled PERSONA.EXE so the assertion can tell rung A from rung
    B: rung B would hand back `dirname(exe)/persona.exe` (lowercase), a
    DIFFERENT path on a case-sensitive filesystem, and only the uppercase file
    exists here. A ladder that dropped rung A would therefore return "" and this
    test would fail — which is the point of not naming the file persona.exe."""
    exe = _touch(tmp_path / "inst" / "PERSONA.EXE")
    monkeypatch.setattr(sys, "executable", exe)
    # both install locations also exist, so returning `exe` is a choice
    _install_locations(tmp_path, monkeypatch)

    assert install_env.installed_windows_exe() == exe


# --- rung B: the sibling persona.exe beside sys.executable ---


def test_sibling_of_sys_executable_beats_both_install_locations(tmp_path, monkeypatch):
    """When the running executable is not itself persona.exe, the persona.exe
    sitting NEXT to it wins over both install locations — the running install is
    the one being updated, whatever else is on the machine."""
    monkeypatch.setattr(sys, "executable", str(tmp_path / "inst" / "python.exe"))
    sibling = _touch(tmp_path / "inst" / "persona.exe")
    local_exe, pf_exe = _install_locations(tmp_path, monkeypatch)

    got = install_env.installed_windows_exe()

    assert got == sibling
    assert got not in (local_exe, pf_exe)


# --- rung C: per-user (current) before per-machine (legacy) ---


def test_localappdata_install_beats_programfiles_install(tmp_path, monkeypatch):
    """Both install locations exist — the per-user LOCALAPPDATA one (where
    persona installs today) must be preferred over the legacy per-machine
    ProgramFiles one. Relaunching the ProgramFiles copy here would start a build
    the update did not touch."""
    monkeypatch.setattr(sys, "executable", str(tmp_path / "elsewhere" / "python.exe"))
    local_exe, pf_exe = _install_locations(tmp_path, monkeypatch)
    assert os.path.isfile(pf_exe)  # the loser really is on disk

    assert install_env.installed_windows_exe() == local_exe


# --- rung D: ProgramFiles is reached when the earlier candidates do not exist ---


def test_programfiles_install_is_used_when_no_earlier_candidate_exists(
    tmp_path, monkeypatch
):
    """A legacy per-machine install is still found: no sibling exe, LOCALAPPDATA
    set but holding no persona.exe, so the ladder walks down to ProgramFiles."""
    monkeypatch.setattr(sys, "executable", str(tmp_path / "inst" / "python.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))  # dir has no exe
    _, pf_exe = _install_locations(tmp_path, monkeypatch, local=False)

    assert install_env.installed_windows_exe() == pf_exe


# --- rung E: nothing exists ---


def test_returns_empty_string_when_no_candidate_exists(tmp_path, monkeypatch):
    """Every candidate is absent -> "". Empty, not None and not a guessed path:
    the callers branch on it (`if exe:` in updater._run_windows_installer,
    `if not exe:` in fast_update), so a truthy guess would send the relaunch at
    a file that isn't there."""
    monkeypatch.setattr(sys, "executable", str(tmp_path / "inst" / "python.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))

    got = install_env.installed_windows_exe()

    assert got == ""
    assert not got  # this is what the callers actually read


# --- rung F: LOCALAPPDATA unset ---


def test_unset_localappdata_falls_through_to_the_programfiles_default(
    tmp_path, monkeypatch
):
    """LOCALAPPDATA absent must not raise (an unset variable is a KeyError
    waiting to happen) — the ladder skips that rung and the ProgramFiles
    candidate still applies, defaulting to r"C:\\Program Files" when that
    variable is missing too. Nothing exists here, so the answer is ""; the
    assertion that matters is that we got an answer at all."""
    monkeypatch.setattr(sys, "executable", str(tmp_path / "inst" / "python.exe"))
    # both env vars are absent (autouse fixture) — the literal default is used

    assert install_env.installed_windows_exe() == ""


def test_programfiles_default_is_used_when_the_variable_is_unset(
    tmp_path, monkeypatch
):
    """The r"C:\\Program Files" default is a real rung, not decoration: point
    os.path.isfile at that literal and the ladder returns the path built from
    it. Asserted through the function's own answer rather than by reading the
    source string."""
    monkeypatch.setattr(sys, "executable", str(tmp_path / "inst" / "python.exe"))
    default_exe = os.path.join(r"C:\Program Files", "persona", "persona.exe")
    monkeypatch.setattr(
        install_env.os.path, "isfile", lambda p: p == default_exe
    )

    assert install_env.installed_windows_exe() == default_exe


# --- the ladder must not explode on a hostile sys.executable ---


def test_a_raising_sys_executable_does_not_break_the_ladder(tmp_path, monkeypatch):
    """`sys.executable` can be empty or unreadable in an embedded interpreter;
    the ladder guards it with try/except and must still reach the install
    locations. Here it is empty, which skips both of its rungs."""
    monkeypatch.setattr(sys, "executable", "")
    local_exe, _ = _install_locations(tmp_path, monkeypatch, program_files=False)

    assert install_env.installed_windows_exe() == local_exe
