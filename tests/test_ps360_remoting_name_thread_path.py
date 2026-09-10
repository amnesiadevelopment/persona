"""PS-360 — ``MOZ_APP_REMOTINGNAME`` must not be written on the THREAD path.

``_launch_and_watch`` sets that variable in ``os.environ`` so Firefox picks it
up as its DBus remoting name and Wayland app_id. On the FORK path that is
correct and deliberate: a forked child has its own memory, so the write reaches
its own browser and nothing else.

On the THREAD path — Windows/macOS, where re-exec cannot work, and Linux under
``in_process=True`` (``verify/baseline.py``'s recorder) — ``os.environ`` IS
persona's own. The write shipped there unguarded for two months while its four
sibling process-global mutations all carried ``not in_thread``.

## What these tests assert, and what they refuse to assert

Everything here is asserted on **the environment that actually exists** — the
manager's own ``os.environ``, a real forked child's ``os.environ`` read back
over a pipe, and the ``env`` dict the chromium seam would hand to ``Popen``.
Nothing asserts that a helper was called: an inert implementation passes that,
and it is exactly the assertion that would have let this ship.

The three consequences below were MEASURED on the pre-fix tree before any of
these tests were written; each one is a test rather than a claim.

## ⛔ Bounds — what these tests do NOT establish

No browser is launched. ``invisible_playwright`` is absent in this container
and ``DISPLAY`` is unset, so ``_launch_and_watch`` is driven up to its engine
import (which is the statement immediately AFTER the write) and fails there.
That means the mutation under test is the real shipped one, executed — but the
assertion is on **the value a starting engine would read**, not on what a live
Firefox does with it. Whether a live engine reads the variable at that instant
is not measured here.

This is NOT an Invariant #0 concern and none is claimed: ``MOZ_APP_REMOTINGNAME``
is a Wayland app_id / DBus name, not a JS-visible surface. Nothing a page
observes changes. The property is measurement integrity in the recorder lane.
"""

import json
import os
import pathlib
import threading

import pytest

import src.services.browser.invisible_launch as il
from src.services.browser.env_policy import scrub_inherited_environment
from src.services.browser.window_entry import app_id_for

VAR = "MOZ_APP_REMOTINGNAME"

# Guarded on the CAPABILITY rather than on `sys.platform`, matching
# tests/test_browser_env_policy.py: fork is what the harness needs.
requires_fork = pytest.mark.skipif(
    not hasattr(os, "fork"), reason="POSIX fork only"
)


@pytest.fixture(autouse=True)
def _linux_and_clean(monkeypatch):
    """Hold ``IS_LINUX`` TRUE throughout, and start from a clean variable.

    ⭐ IS_LINUX is deliberately forced TRUE even on a non-Linux runner. That is
    what ISOLATES the ``in_thread`` half of the guard: a fix that guarded on
    platform alone would pass every test below on a Windows runner and fail
    the property on the Linux ``in_process=True`` path, which is the reachable
    one. The same reasoning the sibling
    ``test_thread_path_child_leaves_its_process_environment_alone`` states.
    """
    monkeypatch.setattr(il._platform, "IS_LINUX", True)
    monkeypatch.delenv(VAR, raising=False)
    yield


def _run_thread_path(tmp_path, profile_name):
    """Drive the REAL ``_launch_and_watch`` on the thread arm and report the
    manager's own ``os.environ`` before and after.

    Deliberately not a reproduction of the line's body: a copied statement
    tests the copy. This calls the shipped function, which reaches the write
    and then fails at its engine import one statement later.
    """
    profile_dir = pathlib.Path(tmp_path) / ".invisible-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    before = os.environ.get(VAR)
    emitted: list = []
    il._launch_and_watch(
        {"profile_name": profile_name, "profile_dir": str(profile_dir)},
        str(profile_dir),
        emitted.append,
        lambda: None,
        threading.Event(),  # stop_event set => this is the thread arm
        True,  # in_thread
    )
    return before, os.environ.get(VAR), emitted


# --------------------------------------------------------------------------
# THE DEFECT — leg 1: the write must not reach the manager's own environ
# --------------------------------------------------------------------------


def test_thread_path_does_not_write_the_remoting_name_into_persona_environ(
    tmp_path,
):
    """LEG 1. On the pre-fix tree this left ``persona_Acme_Bank_<crc>`` in
    persona's own environment, where nothing ever removed it."""
    before, after, _emitted = _run_thread_path(tmp_path, "Acme Bank")
    assert before is None
    assert after is None, (
        f"{VAR} was written to persona's OWN environment on the thread path "
        f"as {after!r}. There is no child environment here: this is the "
        "manager process, shared with every concurrently-open profile."
    )


def test_the_write_still_happens_on_the_fork_path(tmp_path):
    """⭐ THE CONTROL, and the one that makes leg 1 mean anything. A fix that
    simply deleted the line would pass every other test in this file. This
    fails on such a fix: the fork path must still get its remoting name.

    Driven in-process with ``in_thread=False``, which is what the forked child
    executes — so the assertion is on the same statement, taking the other
    branch, rather than on a different code path.
    """
    profile_dir = pathlib.Path(tmp_path) / ".invisible-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    il._launch_and_watch(
        {"profile_name": "Acme Bank", "profile_dir": str(profile_dir)},
        str(profile_dir),
        lambda line: None,
        lambda: None,
        None,  # no stop_event
        False,  # in_thread=False — the fork arm
    )
    assert os.environ.get(VAR) == app_id_for("Acme Bank"), (
        "the fork path lost its remoting name. Firefox needs it as its DBus "
        "name for two profiles to open at once, and as the Wayland app_id for "
        "the taskbar icon — guarding the thread path must not remove it here."
    )
    del os.environ[VAR]


# --------------------------------------------------------------------------
# THE DEFECT — leg 2: what a later child inherits
# --------------------------------------------------------------------------


def test_the_value_is_on_no_scrub_list_so_a_child_would_inherit_it(tmp_path):
    """LEG 2, with its control. The real single scrub entry point both engine
    seams call does NOT remove this variable — so anything left in the
    manager's environ reaches every later child.

    ⚠️ THE CONTROL IS LOAD-BEARING. Asserting only that the subject survives
    the scrub reads as "the scrub is weak". The control shows the scrub firing
    correctly in the same run, which locates the gap precisely: this variable
    is simply on no list.
    """
    polluted = {
        "SSH_AUTH_SOCK": "/tmp/ssh-XXXXcAgEnT/agent.1337",
        "HOSTNAME": "operator-laptop",
        VAR: "persona_Leftover_deadbeef",
    }
    env = dict(os.environ, **polluted)
    scrub_inherited_environment(env)

    # CONTROL: the scrub really ran in this same call.
    assert env.get("SSH_AUTH_SOCK") is None, "the scrub did not fire"
    assert env.get("HOSTNAME") is None, "the scrub did not fire"

    # SUBJECT: and it does not cover this name.
    assert env.get(VAR) == "persona_Leftover_deadbeef", (
        f"{VAR} is now scrubbed. That is a different (also acceptable) fix "
        "shape than the one PS-360 chose — but the guard is what this ticket "
        "landed, so update this test deliberately rather than in passing."
    )


def test_a_later_child_inherits_nothing_because_nothing_was_written(tmp_path):
    """LEG 1 and LEG 2 composed, which is the consequence that actually
    matters: after a thread-path session, the ``env`` copy a chromium child
    would be handed carries no remoting name at all."""
    _before, after, _emitted = _run_thread_path(tmp_path, "Acme Bank")
    assert after is None

    env = os.environ.copy()
    scrub_inherited_environment(env)
    assert env.get(VAR) is None, (
        f"a later child would inherit {env.get(VAR)!r} from a thread-path "
        "session that has already ended"
    )


# --------------------------------------------------------------------------
# THE THREE MEASURED CONSEQUENCES, each pinned
# --------------------------------------------------------------------------


def test_the_value_does_not_survive_between_two_sequential_sessions(tmp_path):
    """CONSEQUENCE 1 — RESIDUE. ``verify/baseline.py``'s recorder runs
    sequential ``in_process=True`` recordings in ONE process. Pre-fix, profile
    A's remoting name was still in the environ when profile B's session
    started."""
    _b, after_a, _e = _run_thread_path(tmp_path / "a", "Profile A")
    entry_b, after_b, _e2 = _run_thread_path(tmp_path / "b", "Profile B")
    assert after_a is None
    assert entry_b is None, (
        f"profile B's session began with {entry_b!r} still in the environ — "
        "profile A's identity, carried across into B's recording"
    )
    assert after_b is None


def test_an_unnamed_profile_does_not_start_under_a_previous_profiles_identity(
    tmp_path,
):
    """CONSEQUENCE 2 — MISATTRIBUTION, the sharp case.

    The write is gated on ``if name and ...``, so a profile with no name never
    writes. Pre-fix that did not mean "no remoting name": it meant the
    PREVIOUS profile's name was still there, and this session's engine would
    have started under it.
    """
    _b, after_a, _e = _run_thread_path(tmp_path / "a", "Profile A")
    assert after_a is None

    entry_b, after_b, _e2 = _run_thread_path(tmp_path / "b", "")
    assert after_b is None, (
        f"an UNNAMED profile's session would start as {after_b!r} — the "
        "previous profile's identity, which it never asked for and cannot "
        "clear"
    )
    assert entry_b is None


def test_two_concurrent_thread_path_sessions_do_not_collide(tmp_path):
    """CONSEQUENCE 3 — THE COLLISION, and the reason set-and-restore was
    refused rather than merely argued against.

    The line's own comment says it exists for "a per-profile-unique remoting
    name so multiple profiles open at once". Pre-fix, two CONCURRENT
    thread-path sessions BOTH reached their engine holding the last writer's
    name — so on that path the line produced the very collision it was written
    to prevent. A ``finally``-based restore makes that strictly worse: one
    session's restore clears the other's live value.

    Both threads are held at a barrier until each has passed the write, so the
    interleave is forced rather than hoped for.
    """
    seen: dict = {}
    both_written = threading.Barrier(2, timeout=30)

    def session(profile_name, tag):
        d = pathlib.Path(tmp_path) / tag
        d.mkdir(parents=True, exist_ok=True)
        il._launch_and_watch(
            {"profile_name": profile_name, "profile_dir": str(d)},
            str(d),
            lambda line: None,
            lambda: None,
            threading.Event(),
            True,
        )
        # Both writes (had they happened) are now behind us.
        try:
            both_written.wait()
        except threading.BrokenBarrierError:  # pragma: no cover
            pass
        seen[tag] = os.environ.get(VAR)

    threads = [
        threading.Thread(target=session, args=("Profile A", "A")),
        threading.Thread(target=session, args=("Profile B", "B")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    assert set(seen) == {"A", "B"}, f"a session did not finish: {seen}"
    assert seen["A"] is None and seen["B"] is None, (
        "a concurrent thread-path session observed a remoting name "
        f"({seen}). Process-global state cannot carry a per-session value: "
        "last writer wins, and both engines read the winner."
    )


# --------------------------------------------------------------------------
# The fork path, through a REAL fork — the other half of the guarantee
# --------------------------------------------------------------------------


@requires_fork
def test_the_forked_child_gets_the_name_and_the_parent_does_not(tmp_path):
    """The whole point of the guard stated as one property, measured through a
    real fork rather than argued: the CHILD's own environ carries the remoting
    name, and the PARENT's does not.

    That asymmetry is the entire justification for the line existing at all,
    and it is what the thread path cannot provide — which is why the thread
    path is now guarded out instead of imitated.
    """
    profile_dir = pathlib.Path(tmp_path) / ".invisible-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    report_r, report_w = os.pipe()

    pid = os.fork()
    if pid == 0:  # pragma: no cover - runs in the forked child
        try:
            os.close(report_r)
            il._platform.IS_LINUX = True
            il._launch_and_watch(
                {"profile_name": "Acme Bank", "profile_dir": str(profile_dir)},
                str(profile_dir),
                lambda line: None,
                lambda: None,
                None,  # no stop_event: the fork arm
                False,  # in_thread=False
            )
            with os.fdopen(report_w, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({VAR: os.environ.get(VAR)}))
        except BaseException:
            try:
                os.close(report_w)
            except OSError:
                pass
        os._exit(0)

    os.close(report_w)
    with os.fdopen(report_r, encoding="utf-8") as fh:
        payload = fh.read()
    os.waitpid(pid, 0)

    assert payload, "the forked child reported no environment"
    child_env = json.loads(payload)
    assert child_env[VAR] == app_id_for("Acme Bank"), (
        "the FORKED child did not get its remoting name — the fork path is "
        "the one path where this write is correct, and it must keep working"
    )
    assert os.environ.get(VAR) is None, (
        "the fork's write reached the PARENT. It cannot (separate memory), so "
        "this failing means the harness is not actually forking."
    )


# --------------------------------------------------------------------------
# The X11 half, which is NOT guarded — and deliberately so
# --------------------------------------------------------------------------


def test_the_name_launch_argument_is_unaffected_on_the_thread_path(tmp_path):
    """``--name`` carries the same string to the engine, and is deliberately
    NOT guarded on ``in_thread``: it is a per-launch ARGUMENT, so it reaches
    only this session's browser and mutates nothing global.

    Asserting this keeps the two halves from being "simplified" into one
    guard later. The cost of PS-360's fix is the WAYLAND app_id on the thread
    path only; the X11 WM_CLASS half is untouched on every path.

    A source assertion rather than a behavioural one, and deliberately so:
    ``extra_args`` is local to ``_launch_and_watch`` and is consumed by the
    engine constructor, which cannot be reached in this container. A source
    read is a weaker instrument than the environ assertions above, and is
    labelled as such rather than dressed up — its job is to make a future
    edit to that guard a deliberate act.
    """
    source = pathlib.Path(il.__file__).read_text(encoding="utf-8")
    assert 'extra_args.append(f"--name={_remoting_name(name)}")' in source
    marker = "if name and _platform.IS_LINUX:\n        # --name sets the X11"
    assert marker in source, (
        "the --name guard changed shape. If `not in_thread` was added to it, "
        "the X11 taskbar icon is now lost on the thread path too — which is a "
        "real behaviour change and needs its own decision, not a tidy-up."
    )
