"""A profile name must not be able to kill the browser silently.

MEASURED, ON THE DEV VM. A profile called `CR-control-noproxy` — eighteen ordinary
characters — made the engine abort at startup:

    FATAL:chrome/browser/process_singleton_posix.cc:313] Socket path too long:
    /home/user/.persona/persona_data/CR-control-noproxy/.persona-tmp/
    org.chromium.Chromium.J1CooM/SingletonSocket

Exit code 133. What the operator saw was "Session ended unexpectedly", which names
nothing and points nowhere. Renaming the profile to `ctl` and changing nothing else
launched it in three seconds.

The cause is a hard kernel limit, not a Chromium choice: `sockaddr_un.sun_path` holds
108 bytes, so a unix socket path may be 107 characters. Chromium puts its singleton
socket under TMPDIR, persona points TMPDIR inside the profile directory (deliberately —
everything there is reached by delete, trash and wipe), and the profile NAME sits in the
middle of that path.

So the budget is fixed and the name spends it. These tests pin both halves of the fix:
the scratch directory name is short enough to leave a usable budget, and a name that
still overruns is REFUSED BY NAME instead of arriving as a fatal log line nobody reads.
"""
import os

import pytest

from src.services.browser import env_policy


# The kernel's limit, not ours: sizeof(sockaddr_un.sun_path) is 108, one byte of which
# is the terminator. Written here so a reader does not have to take 107 on faith.
SUN_PATH_USABLE = 107

# What Chromium appends beneath TMPDIR, measured from the crash above:
#   "/org.chromium.Chromium.XXXXXX" (29) + "/SingletonSocket" (16)
CHROMIUM_SOCKET_SUFFIX = len("/org.chromium.Chromium.XXXXXX") + len("/SingletonSocket")


def _socket_path_length(profile_dir):
    """The length Chromium will actually try to bind, for a given profile dir."""
    return len(env_policy.browser_child_tmpdir(profile_dir)) + CHROMIUM_SOCKET_SUFFIX


def test_the_scratch_directory_name_is_short():
    """Every character here is taken from the operator's name budget.

    `.persona-tmp` cost thirteen. The name is load-bearing only in that it must be
    recognisable and hidden; it does not need to be a sentence.
    """
    assert len(env_policy.CHILD_TMPDIR_NAME) <= 4, (
        f"{env_policy.CHILD_TMPDIR_NAME!r} is "
        f"{len(env_policy.CHILD_TMPDIR_NAME)} characters, and every one of them "
        "shortens the longest profile name an operator may use"
    )
    assert env_policy.CHILD_TMPDIR_NAME.startswith("."), (
        "the scratch directory stays hidden; it is not the operator's business"
    )


#: The real layout the crash happened in, written literally rather than built from
#: `tmp_path`. pytest's temp directories are long and platform-shaped — measuring one
#: would tell us about the test runner's path, not about an operator's machine, and the
#: first version of this test failed for exactly that reason.
REAL_PROFILE_ROOT = "/home/user/.persona/persona_data"


def test_an_ordinary_profile_name_fits():
    """The name that actually broke a machine must now work.

    Not a synthetic maximum: `CR-control-noproxy` is the profile this was found on,
    under the home directory it was found under.
    """
    profile_dir = f"{REAL_PROFILE_ROOT}/CR-control-noproxy"
    length = _socket_path_length(profile_dir)
    assert length <= SUN_PATH_USABLE, (
        f"the profile name that crashed the engine still does not fit: "
        f"{length} > {SUN_PATH_USABLE}"
    )


def test_the_budget_leaves_room_for_a_name_worth_reading():
    """A limit nobody can live inside is a limit that gets worked around.

    Twenty-six characters is enough for `Mail for persona` and
    `Test Direct Conection` — two names already in use that could not launch before.
    """
    longest = SUN_PATH_USABLE - _socket_path_length(REAL_PROFILE_ROOT + "/")
    assert longest >= 24, (
        f"only {longest} characters left for a profile name; that is not enough "
        "for the names operators actually choose"
    )


@pytest.mark.skipif(os.name == "nt", reason="the guard is POSIX-only: Windows single-instance uses a named mutex, which has no path ceiling")
def test_a_name_that_cannot_fit_is_refused_by_name(tmp_path):
    """The refusal must say WHICH name and WHY, because the alternative is a fatal
    log line inside the engine's own output that no operator reads."""
    profile_dir = tmp_path / ("x" * 200)
    with pytest.raises(env_policy.ProfilePathTooLong) as exc:
        env_policy.check_child_tmpdir_fits(str(profile_dir))
    message = str(exc.value)
    assert "socket" in message.lower()
    assert str(SUN_PATH_USABLE) in message, (
        "the refusal should state the budget, so the operator can judge how much "
        "to shorten by rather than guessing"
    )


def test_a_name_that_fits_is_not_refused():
    """The guard must not become a second way to fail a launch that would work."""
    env_policy.check_child_tmpdir_fits(f"{REAL_PROFILE_ROOT}/ck1")  # must not raise


def test_the_guard_measures_the_real_path_not_an_estimate(tmp_path):
    """The check has to use the same function the launch uses.

    A guard computing its own idea of the path would pass while the launch still
    failed — which is the shape of every check that is measured somewhere other than
    where the work happens.
    """
    profile_dir = str(tmp_path / "some-profile")
    budget = env_policy.child_tmpdir_budget(profile_dir)
    assert budget == SUN_PATH_USABLE - _socket_path_length(profile_dir)


@pytest.mark.skipif(os.name == "nt", reason="sun_path is a POSIX limit")
def test_the_limit_matches_this_platform():
    """Pin the constant against the platform rather than trusting a comment."""
    import socket
    import struct

    # sockaddr_un is: sa_family_t (2 bytes) + sun_path[108]
    assert env_policy.UNIX_SOCKET_PATH_MAX == SUN_PATH_USABLE
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            s.bind("/tmp/" + "y" * 200)
    finally:
        s.close()
    assert struct.calcsize("H") == 2


@pytest.mark.skipif(os.name == "nt", reason="the guard is POSIX-only: Windows single-instance uses a named mutex, which has no path ceiling")
def test_the_guard_is_actually_called_before_a_launch(tmp_path, monkeypatch):
    """A check nobody calls is a comment.

    `prepare_child_tmpdir` is the one place both engine seams pass through on the
    way to a launch, and it runs before the engine starts — so a refusal there
    arrives while there is still nothing to clean up.
    """
    too_long = tmp_path / ("z" * 200)
    with pytest.raises(env_policy.ProfilePathTooLong):
        env_policy.prepare_child_tmpdir(str(too_long))


def test_a_workable_profile_still_gets_its_scratch_directory():
    """And the guard must not stop the ordinary case from being prepared.

    Built under a SHORT base rather than `tmp_path`: pytest's temp directories are
    long enough to exhaust the budget on their own, so this test refused its own
    fixture on Linux and reported a guard bug that was not there. What is under test
    is a profile path of realistic length, which is what an operator has.
    """
    import shutil
    import tempfile

    base = tempfile.mkdtemp(prefix="pn", dir=tempfile.gettempdir())
    try:
        target = env_policy.prepare_child_tmpdir(os.path.join(base, "p"))
        assert os.path.isdir(target)
        assert os.path.basename(target) == env_policy.CHILD_TMPDIR_NAME
    finally:
        shutil.rmtree(base, ignore_errors=True)
