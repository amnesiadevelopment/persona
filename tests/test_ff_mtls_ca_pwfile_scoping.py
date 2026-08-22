"""The certutil empty-password scratch file belongs to the PROFILE, not to the
host's shared temp dir — and it must be named so the sweep can actually reach it.

Two independent properties, both of which the pre-fix code failed:

  WHERE it lands. `tempfile.mkstemp(prefix="persona-nsspw-")` with no `dir=`
  resolves through TMPDIR/tmp — outside PERSONA_HOME, outside the profile, and
  therefore outside everything delete_profile, the trash and wipe_all_profiles
  can reach. The `finally` in _import_mtls_ca covers only the subprocess block,
  so an abort between creation and cleanup strands a product-identifying
  `persona-*` artifact on the host indefinitely, one per crashed import.

  WHAT it is called. sweep_key_material filters by NAME, not by directory
  (terminator.py: `name.startswith("persona-mtls-")`). Moving the file inside
  .persona-mtls without renaming it produces a file that is inside the perimeter
  but never swept — permanent residue that LOOKS fixed. Hence the crash-residue
  test below hands the leftover to the REAL sweep and asserts it is gone.

Every assertion here is on the RESOLVED PATH of a file that actually exists on
disk, never on the arguments mkstemp was called with — a call-shape assertion
passes on an implementation that ignores them.
"""
import os
import tempfile

import src.services.browser.invisible_launch as il
from src.services.cert import terminator


class _R:
    returncode = 0
    stdout = ""
    stderr = ""


def _profile_with_ca(tmp_path):
    """A profile whose cert session has already written its CA, exactly as
    start_cert_session leaves it: <profile>/.persona-mtls/term_ca.crt."""
    profile = tmp_path / "profile"
    certdir = profile / ".persona-mtls"
    certdir.mkdir(parents=True)
    ca = certdir / "term_ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\n")
    return profile, certdir, ca


def _hermetic_tempdir(monkeypatch, tmp_path):
    """Point tempfile's default at a dir of our own and return it.

    This is what makes "did it land in the host's temp dir?" answerable without
    depending on whatever else is lying around in the real /tmp: an unscoped
    mkstemp lands HERE, so this directory staying empty IS the property.
    """
    host_tmp = tmp_path / "host_tmp"
    host_tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(host_tmp))
    assert tempfile.gettempdir() == str(host_tmp)
    return host_tmp


def _linux_certutil(monkeypatch):
    monkeypatch.setattr(il._platform, "IS_WINDOWS", False)
    monkeypatch.setattr(il._platform, "IS_MACOS", False)
    monkeypatch.setattr(il, "_certutil_path", lambda: "/bundle/certutil")
    monkeypatch.setattr(il, "_engine_lib_dir", lambda: "/eng")


def _residue(d):
    """Every persona-minted scratch file sitting in `d`, whichever prefix it
    carries — so the check cannot be satisfied by renaming the file."""
    return sorted(n for n in os.listdir(d) if "nsspw" in n or n.startswith("persona-"))


def test_password_file_is_created_inside_the_profile_not_the_host_temp_dir(
    monkeypatch, tmp_path
):
    """AC1 — while certutil runs, the file exists inside <profile>/.persona-mtls
    and the host temp dir has nothing in it.

    FAILS ON MAIN: :1571 passed no `dir=`, so the file was minted in the host
    temp dir and this asserted the opposite of what happened.
    """
    _linux_certutil(monkeypatch)
    host_tmp = _hermetic_tempdir(monkeypatch, tmp_path)
    profile, certdir, ca = _profile_with_ca(tmp_path)

    seen = {}

    def fake_run(argv, env=None, **k):
        # Observed WHILE the file is alive — the happy path deletes it before
        # the function returns, so this is the only moment it can be looked at.
        seen["in_profile"] = _residue(certdir)
        seen["in_host_tmp"] = _residue(host_tmp)
        return _R()

    monkeypatch.setattr(il.subprocess, "run", fake_run)

    assert il._import_mtls_ca(str(profile), str(ca)) is True  # AC6: unchanged

    assert seen["in_host_tmp"] == [], (
        f"scratch file leaked into the host temp dir: {seen['in_host_tmp']}"
    )
    scratch = [n for n in seen["in_profile"] if "nsspw" in n]
    assert len(scratch) == 1, f"expected one scratch file in the profile, got {scratch}"
    # ...and the happy path still cleans it up.
    assert [n for n in _residue(certdir) if "nsspw" in n] == []
    assert _residue(host_tmp) == []


def test_crash_residue_stays_inside_the_profile_and_is_swept(monkeypatch, tmp_path):
    """AC3 + AC4 — the criterion that matters. The happy path already cleaned
    up before this change, so only the abort case proves anything.

    A real SIGKILL cannot be simulated in-process (`finally` runs even when a
    BaseException propagates), so the kill is modelled where it is observable:
    cleanup does not happen. The leftover must then be (a) inside the profile,
    (b) absent from the host temp dir, and (c) actually removed by the REAL
    sweep_key_material — (c) is what the prefix rename buys, and it is asserted
    by the file's ABSENCE from the directory, never by its name.
    """
    _linux_certutil(monkeypatch)
    host_tmp = _hermetic_tempdir(monkeypatch, tmp_path)
    profile, certdir, ca = _profile_with_ca(tmp_path)

    def die(argv, env=None, **k):
        raise BaseException("SIGKILL")  # noqa: TRY002 — not caught by `except Exception`

    monkeypatch.setattr(il.subprocess, "run", die)
    # The process is gone: the finally's os.remove never gets to run.
    monkeypatch.setattr(os, "remove", lambda *a, **k: None)

    try:
        il._import_mtls_ca(str(profile), str(ca))
    except BaseException as e:  # noqa: BLE001 — the simulated kill, re-raised by design
        assert str(e) == "SIGKILL"
    monkeypatch.undo()

    leftover = [n for n in _residue(certdir) if "nsspw" in n]
    assert len(leftover) == 1, f"expected crash residue in the profile, got {leftover}"
    assert _residue(host_tmp) == [], "crash residue leaked outside the profile"

    # (c) The residue is genuinely reachable by the mechanism that cleans this
    # directory at the start of the next session. Real function, real directory.
    terminator.sweep_key_material(str(certdir))
    assert [n for n in _residue(certdir) if "nsspw" in n] == [], (
        "crash residue survived sweep_key_material — it is inside the perimeter "
        "but permanent, which is worse than being in /tmp"
    )


def test_macos_returns_before_any_scratch_file_is_created(monkeypatch, tmp_path):
    """AC7 — macOS uses in-process NSS and never reaches this code, so it mints
    no scratch file anywhere."""
    from src.services.cert import nssdb

    monkeypatch.setattr(il._platform, "IS_WINDOWS", False)
    monkeypatch.setattr(il._platform, "IS_MACOS", True)
    monkeypatch.setattr(il, "_engine_lib_dir", lambda: "/eng")
    host_tmp = _hermetic_tempdir(monkeypatch, tmp_path)
    profile, certdir, ca = _profile_with_ca(tmp_path)

    trust_calls = []
    monkeypatch.setattr(
        nssdb, "trust_ca", lambda *a: trust_calls.append(a) or True
    )

    def never(*a, **k):
        raise AssertionError("macOS must not shell out to certutil")

    monkeypatch.setattr(il.subprocess, "run", never)

    assert il._import_mtls_ca(str(profile), str(ca)) is True
    assert len(trust_calls) == 1, "the in-process NSS path was not the one taken"
    assert [n for n in _residue(certdir) if "nsspw" in n] == []
    assert _residue(host_tmp) == []


def test_no_persona_mtls_directory_is_conjured(monkeypatch, tmp_path):
    """AC8 — the change adds no makedirs. Handed a CA that does NOT live in a
    .persona-mtls dir, the import still works and brings no such directory into
    existence (sweep_key_material's "never creates out_dir" property).
    """
    _linux_certutil(monkeypatch)
    _hermetic_tempdir(monkeypatch, tmp_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    ca = profile / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\n")

    monkeypatch.setattr(il.subprocess, "run", lambda argv, env=None, **k: _R())

    assert il._import_mtls_ca(str(profile), str(ca)) is True
    assert not (profile / ".persona-mtls").exists()
