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
import select
import sys

import pytest

from src.services.browser import env_policy
from src.services.browser import invisible_launch as il


# The kernel's limit, not ours: sizeof(sockaddr_un.sun_path) is 108 on Linux, one byte
# of which is the terminator. Written here so a reader does not have to take 107 on
# faith. Darwin's sockaddr_un is 3 bytes smaller (104 usable) — the tests below pin the
# per-platform split rather than assuming this number everywhere (see
# test_darwin_gets_its_own_smaller_sun_path).
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
    under the home directory it was found under. Measured against the PLATFORM's
    limit, not Linux's — the same path is 4 characters tighter on darwin, and a
    test hardcoded to 107 would agree with a comment instead of with the machine.
    """
    profile_dir = f"{REAL_PROFILE_ROOT}/CR-control-noproxy"
    length = _socket_path_length(profile_dir)
    assert length <= env_policy.unix_socket_path_max(), (
        f"the profile name that crashed the engine still does not fit: "
        f"{length} > {env_policy.unix_socket_path_max()}"
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
    assert str(env_policy.unix_socket_path_max()) in message, (
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
    assert budget == env_policy.unix_socket_path_max() - _socket_path_length(profile_dir)


@pytest.mark.skipif(os.name == "nt", reason="sun_path is a POSIX limit")
def test_the_limit_matches_this_platform():
    """Pin the constant against THIS platform's kernel, not against a number.

    The gate is POSIX-wide and the kernel's answer differs by platform (Linux
    107, darwin 104), so the honest pin is measured live: a path exactly at the
    constant's length binds, and one character more is refused. Hardcoding
    Linux's 107 here would pass on darwin while the guard budgets 104 — a test
    agreeing with a comment instead of with the machine it runs on.
    """
    import socket

    limit = env_policy.unix_socket_path_max()
    assert env_policy.UNIX_SOCKET_PATH_MAX == limit
    base = "/tmp/ps438-sock-limit"  # 21 chars — short on every POSIX
    at_limit = base + "a" * (limit - len(base))
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        try:
            os.unlink(at_limit)  # a stale file from an earlier run reads as EADDRINUSE
        except OSError:
            pass
        s.bind(at_limit)  # exactly at the constant: the kernel must accept it
        over = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError):
                over.bind(base + "b" * (limit - len(base) + 1))  # one over: refused
        finally:
            over.close()
    finally:
        s.close()
        try:
            os.unlink(at_limit)
        except OSError:
            pass


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


def test_darwin_gets_its_own_smaller_sun_path(monkeypatch):
    """The gate is POSIX-wide, so its number must follow the platform.

    sizeof(``sockaddr_un.sun_path``): Linux 108 bytes, darwin 104 — one byte each
    way is the terminator, so the usable lengths are 107 and 104. A gate that
    activates on every POSIX platform but budgets Linux's number passes launches
    on darwin that the engine dies on, in the 22–25-character band of long home
    paths — the exact outcome a pre-launch guard exists to prevent, which is why
    this is a defect of the guard and not of the engine. Both arms are forced
    here so the pin runs on any host: darwin's value is 104, and the budget the
    operator's name spends is exactly the Linux budget minus darwin's 3
    smaller bytes.
    """
    profile_dir = f"{REAL_PROFILE_ROOT}/CR-control-noproxy"

    monkeypatch.setattr(sys, "platform", "linux")
    linux_budget = env_policy.child_tmpdir_budget(profile_dir)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert env_policy.unix_socket_path_max() == 104, (
        "darwin's sockaddr_un.sun_path is 104 usable bytes, not Linux's 107 — "
        "a POSIX-wide gate must budget the platform it is actually on"
    )
    assert env_policy.child_tmpdir_budget(profile_dir) == linux_budget - 3, (
        "the darwin budget must be the Linux budget minus darwin's 3-byte "
        "smaller sun_path — the operator pays that difference out of their name"
    )
    # The default arm: anything not darwin reads Linux's number. Unused on
    # Windows (the guard returns before consulting any socket arithmetic
    # there), but the default should be the common unix case, not an accident.
    monkeypatch.setattr(sys, "platform", "win32")
    assert env_policy.unix_socket_path_max() == 107


@pytest.mark.skipif(os.name == "nt", reason="the scratch pin is the POSIX fork path")
@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the pin runs only on the Linux fork arm (_pin_tmpdir_here = not "
    "in_thread and IS_LINUX); macOS takes the thread path, where nothing is "
    "pinned and so nothing can refuse",
)
def test_the_firefox_seam_reports_the_refusal_on_its_status_pipe(tmp_path):
    """A guard that raises is not yet a refusal an operator can READ.

    The chromium seam's raise lands in the parent (launcher's catch-all → a log
    line that carries the sentence). The firefox seam runs the pin INSIDE the
    forked child, where the only road out is this pipe — and before the catch
    below existed, a `ProfilePathTooLong` (which subclasses Exception, not the
    `OSError` the seam caught) escaped it: the child died with a traceback on
    inherited stderr and ZERO bytes on the pipe, and the parent read a bare EOF
    before BROWSER_STARTED. An unexplained session end — the exact operator
    experience this ticket exists to eliminate, recreated on the second engine.

    So this test drives the REAL `_child` body in a REAL forked child — the
    shape production uses (multiprocessing fork → `_child`) — against an
    over-budget profile dir. `_launch_and_watch` is stubbed INSIDE the child so
    a regressed guard (one that stops refusing) can only say so on the pipe; it
    can never launch an engine from here. The assertions are on what the
    parent's monitor reads: a LAUNCH_FAILED line carrying the guard's sentence,
    then BROWSER_CLOSED.
    """
    profile_data_dir = str(tmp_path / ("z" * 200))  # over budget by construction
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - runs in the forked child
        try:
            os.close(r)

            def _no_engine(cfg, profile_dir, emit, finish, stop_event, in_thread):
                emit("SEAM-TEST: the scratch pin did not refuse; nothing launched")
                finish()

            il._launch_and_watch = _no_engine
            il._child({"profile_data_dir": profile_data_dir}, w)
        except BaseException as exc:  # the PRE-fix path lands here — say so
            try:
                os.write(w, f"CHILD-RAISED: {type(exc).__name__}: {exc}\n".encode())
            except OSError:
                pass
        finally:
            os._exit(0)
    os.close(w)
    # BOUNDED. A wedged child must fail this test, not hang the job.
    lines = []
    with os.fdopen(r, encoding="utf-8") as fh:
        ready, _, _ = select.select([fh], [], [], 60)
        if ready:
            lines = fh.read().splitlines()
    os.waitpid(pid, 0)

    assert lines, (
        "the forked child wrote NOTHING to the status pipe — it died without "
        "saying anything: the silent session end this seam test exists to prevent"
    )
    assert not any(line.startswith("CHILD-RAISED") for line in lines), (
        f"the pin's exception escaped the seam's catch unreported: {lines}"
    )
    refused = [line for line in lines if line.startswith("LAUNCH_FAILED:")]
    assert refused, f"expected a LAUNCH_FAILED refusal on the pipe, got: {lines}"
    assert "too long" in refused[0] and "Shorten the profile name" in refused[0], (
        "the refusal must carry the guard's whole sentence — which limit, which "
        f"profile, how many characters to cut — got: {refused[0]!r}"
    )
    assert "BROWSER_CLOSED" in lines, (
        f"the child must end the session properly after the refusal, got: {lines}"
    )


def test_prepare_child_tmpdir_sweeps_a_legacy_scratch_directory():
    """PS-438 renamed the scratch directory; a profile that last launched before
    the rename still holds a `.persona-tmp` — per-launch scratch, and under it
    the engine's ~714MB AppImage extraction. Sweeping only the NEW name strands
    that forever: nothing else in the tree ever deletes it, and the first export
    of such a profile grows by the exact bulk PS-129 exists to prevent. The
    spelling here is a LITERAL on purpose — the old name is frozen history, and
    a test that read the legacy constant would follow a rename of it and stop
    pinning the past.
    """
    import shutil
    import tempfile

    base = tempfile.mkdtemp(prefix="pn", dir=tempfile.gettempdir())
    try:
        profile_dir = os.path.join(base, "p")
        os.makedirs(profile_dir)
        legacy = os.path.join(profile_dir, ".persona-tmp")
        os.makedirs(os.path.join(legacy, "appimage_extracted_deadbeef"))
        with open(
            os.path.join(legacy, "appimage_extracted_deadbeef", "chrome"),
            "w",
            encoding="utf-8",
        ) as fh:
            fh.write("x" * 1024)
        current = os.path.join(profile_dir, env_policy.CHILD_TMPDIR_NAME)
        os.makedirs(os.path.join(current, "stale-from-last-launch"))

        target = env_policy.prepare_child_tmpdir(profile_dir)

        assert os.path.isdir(target)
        assert not os.path.exists(legacy), (
            "the pre-rename scratch directory survived the sweep — dead weight "
            "on the profile forever"
        )
        assert os.listdir(target) == [], (
            "the NEW scratch must still be swept and recreated empty"
        )
    finally:
        shutil.rmtree(base, ignore_errors=True)
