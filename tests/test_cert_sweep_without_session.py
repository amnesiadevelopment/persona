"""Key material must be swept on every launch that decides there is NO mTLS
session — not only on the path that starts one.

``sweep_key_material`` shipped with a written guarantee (terminator.py: "a new
session sweeps the directory before writing to it, so at most one session's key
material is ever on disk"). It had exactly two call sites and both sat inside
``start_cert_session``, so every guard that returned earlier made that sentence
silently false: the operator's decrypted client key stayed in the profile's own
data dir with nothing left in the tree that would ever remove it. Unassigning a
certificate is an ordinary, supported edit (``models/profile.py``:
``certificate: str | None``), and it is the sharp case — no future launch of that
profile would reach the sweep again.

METHOD — every assertion here reads FILE CONTENTS in the directory, never "was a
helper called". A call-shape assertion passes against an inert implementation,
which would defeat the whole point: the claim under test is that the plaintext
key is GONE, not that a function was reached.
"""
import os

import pytest

import src.services.browser.process as process
from src.models.profile import Profile
from src.services.cert import manager as cm

KEY_PEM = "-----BEGIN PRIVATE KEY-----\nMIIBOgIBAAJBAKstale\n-----END PRIVATE KEY-----\n"


def _plant_orphan(work, name="persona-mtls-zl8oz329.pem"):
    """Leave behind exactly what a session that never reached stop() leaves:
    an mkstemp-named plaintext client PEM nothing in the tree knows to look for."""
    os.makedirs(work, exist_ok=True)
    path = os.path.join(work, name)
    with open(path, "w") as f:
        f.write(KEY_PEM)
    # Precondition, asserted rather than assumed: the probe starts dirty.
    assert _private_key_files(work) == [name]
    return path


def _private_key_files(work):
    """Files under ``work`` whose CONTENTS hold a private key.

    Contents, not names and not existence: the defect is a readable plaintext
    key, so that is what the test must look for. A name-based check would pass
    on an implementation that renamed the file and left the bytes readable.
    """
    if not os.path.isdir(work):
        return []
    found = []
    for name in sorted(os.listdir(work)):
        path = os.path.join(work, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", errors="ignore") as f:
                if "PRIVATE KEY" in f.read():
                    found.append(name)
        except OSError:
            continue
    return found


class _NoCertStore:
    """The operator deleted the certificate RECORD while a profile still
    referenced it by name."""

    def get(self, name):
        return None


# --------------------------------------------------------------------------
# AC1 / AC2 — the two guards in _cert_session_for (process.py)
# --------------------------------------------------------------------------


def test_launch_without_a_certificate_sweeps_orphaned_key_material(tmp_path):
    # AC1. This is the ported probe: on main the orphan survived this call
    # verbatim, because both sweep call sites sat behind this very guard.
    profile_dir = str(tmp_path / "profile")
    work = os.path.join(profile_dir, ".persona-mtls")
    _plant_orphan(work)

    session = process._cert_session_for(Profile(name="unassigned"), profile_dir, None)

    assert session is None, "no certificate must still mean no mTLS session"
    assert _private_key_files(work) == [], (
        "a launch with no certificate assigned left the operator's decrypted "
        f"client key on disk: {_private_key_files(work)} in {work}"
    )


def test_launch_with_a_deleted_certificate_record_sweeps_key_material(
    tmp_path, monkeypatch
):
    # AC2. The profile still names a certificate; the record behind it is gone.
    profile_dir = str(tmp_path / "profile")
    work = os.path.join(profile_dir, ".persona-mtls")
    _plant_orphan(work)
    monkeypatch.setattr(process, "CertStore", _NoCertStore)

    session = process._cert_session_for(
        Profile(name="dangling", certificate="deleted-cert"), profile_dir, None
    )

    assert session is None
    assert _private_key_files(work) == [], (
        "a launch whose certificate record no longer exists left the "
        f"decrypted client key on disk: {_private_key_files(work)} in {work}"
    )


# --------------------------------------------------------------------------
# AC3 — the four guards in start_cert_session (cert/manager.py)
# --------------------------------------------------------------------------


def _cert(**kw):
    from src.services.cert.store import Certificate

    base = dict(
        name="admin",
        p12_path="/definitely/not/here.p12",
        password="",
        url="https://admin.example.com/login",
    )
    base.update(kw)
    return Certificate(**base)


@pytest.mark.parametrize(
    "make_cert, why",
    [
        (lambda p12: None, "no certificate passed at all"),
        (lambda p12: _cert(url=""), "certificate has no admin URL"),
        (lambda p12: _cert(p12_path="/gone/moved-by-the-operator.p12"),
         "the .p12 was moved or deleted"),
        (lambda p12: _cert(url="::not a url::"), "unparseable admin URL"),
    ],
    ids=["cert_is_none", "no_admin_url", "p12_missing", "unparseable_url"],
)
def test_unpreparable_certificate_still_sweeps_key_material(tmp_path, make_cert, why):
    # AC3. Each of these guards returns before the sweep that used to be the
    # mechanism's only call site, so each one stranded the previous session's key.
    work = str(tmp_path / "profile" / ".persona-mtls")
    _plant_orphan(work)

    session = cm.start_cert_session(make_cert(None), None, work, verify_upstream=False)

    assert session is None, f"{why}: launch must proceed without mTLS"
    assert _private_key_files(work) == [], (
        f"{why}: the decrypted client key survived — "
        f"{_private_key_files(work)} still in {work}"
    )


# --------------------------------------------------------------------------
# AC6 — the "never creates out_dir" contract is respected, not stumbled past
# --------------------------------------------------------------------------


def test_profile_that_never_had_a_certificate_gains_no_mtls_directory(tmp_path):
    # AC6. sweep_key_material promises it never creates out_dir. Sweeping on
    # every no-session path must not quietly bring .persona-mtls into existence
    # for the overwhelming majority of profiles that have no certificate at all.
    profile_dir = str(tmp_path / "profile")
    os.makedirs(profile_dir)

    assert process._cert_session_for(Profile(name="plain"), profile_dir, None) is None

    assert not os.path.exists(os.path.join(profile_dir, ".persona-mtls")), (
        "sweeping conjured a .persona-mtls directory for a profile that has "
        "no certificate"
    )


def test_unpreparable_certificate_conjures_no_mtls_directory(tmp_path):
    # AC6, the manager half: the same contract on the start_cert_session guards.
    work = str(tmp_path / "profile" / ".persona-mtls")

    assert cm.start_cert_session(_cert(), None, work, verify_upstream=False) is None

    assert not os.path.exists(work), f"sweeping created {work} out of nothing"


# --------------------------------------------------------------------------
# AC7 — a launch still happens in every one of these cases
# --------------------------------------------------------------------------


def test_sweeping_never_aborts_a_launch_that_has_no_mtls(tmp_path, monkeypatch):
    # AC7. The guards return None so the browser opens WITHOUT mTLS; that
    # behaviour is unchanged and is the whole reason sweep_key_material is
    # total. If a sweep failure could ever abort a launch, this change is wrong.
    #
    # The hostile case is an existing-but-unreadable work dir: the sweep cannot
    # enumerate it, and must degrade rather than raise out through the launch.
    if os.geteuid() == 0:
        pytest.skip("root bypasses directory permissions; can't make a dir unlistable")

    profile_dir = str(tmp_path / "profile")
    work = os.path.join(profile_dir, ".persona-mtls")
    _plant_orphan(work)
    os.chmod(work, 0o000)
    try:
        # The assertion is the ABSENCE of a raise — pytest fails the test if
        # _cert_session_for propagates, which is precisely the regression.
        assert process._cert_session_for(
            Profile(name="unreadable"), profile_dir, None
        ) is None
    finally:
        os.chmod(work, 0o700)
