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

⚠️ **CORRECTION, and it is the reason this harness has a seam at all.** An
earlier revision of this file claimed the engine import *fails* here, so
``_launch_and_watch`` stopped one statement after the write. **That was false**,
and it was false in the way that matters: ``invisible_playwright`` IS installed
on CI (every leg) and in this container, the import SUCCEEDS, and the real
function went on to spawn a real Firefox. On Linux with no ``DISPLAY`` that
Firefox died in ~2s and the tests passed for a reason that had nothing to do
with the property under test; on macOS and Windows runners, which HAVE a window
server, the launch proceeded and five tests hung to the 120s ``pytest-timeout``.
The bound was a property of *this host*, stated as a property of the code path.

So the engine is now held off by a **seam**, not by an accident of provisioning:
``_stub_the_engine_enter`` replaces ``_enter_on_worker`` / ``_enter_with_timeout``
— the two functions that actually start a browser — while the rest of the real
shipped ``_launch_and_watch`` runs, mutation included. Measured: **0.19s and no
process spawned**, against 2.44s and a real ``firefox`` before. The stub asserts
it was CALLED (see ``test_the_engine_seam_is_really_stubbed``), so a renamed
seam goes red rather than quietly launching browsers again.

What that costs: the assertion is on **the value a starting engine would read**,
not on what a live Firefox does with it. Whether a live engine reads the
variable at that instant is not measured here, on any platform.

This is NOT an Invariant #0 concern and none is claimed: ``MOZ_APP_REMOTINGNAME``
is a Wayland app_id / DBus name, not a JS-visible surface. Nothing a page
observes changes. The property is measurement integrity in the recorder lane.
"""

import ast
import json
import os
import pathlib
import select
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


ENTERED: dict = {"thread": 0, "fork": 0}


def _stub_the_engine_enter(monkeypatch):
    """Hold the ENGINE off while the real ``_launch_and_watch`` runs.

    ⭐ THE SEAM IS CHOSEN, NOT CONVENIENT. ``_enter_on_worker`` (thread arm) and
    ``_enter_with_timeout`` (fork arm) are the two functions that actually
    start a browser, and they sit BELOW every statement this file asserts on —
    so the real shipped function executes the mutation under test, decides its
    guard, builds its ``extra_args``, and only then finds no engine to enter.
    Returning the "every attempt overran" value each one already documents
    (``None`` / ``(None, None)``) drives the shipped ``if ctx is None`` arm, so
    the function still unwinds through its own code rather than an exception.

    ⚠️ Why not patch ``_launch_and_watch`` itself, or copy the two-line body?
    A copied statement tests the copy. The sibling
    ``tests/test_browser_env_policy.py:_child_environ_after_fork`` faces the
    same problem one level UP and substitutes the launch there; this is that
    same move at the level this file needs.

    The counters make the substitution ASSERTED rather than assumed — see
    ``test_the_engine_seam_is_really_stubbed``. If either name is refactored
    away, the stub silently stops applying and the harness goes back to
    launching real browsers on every developer desktop; that test is what
    turns such a rename into a red run instead.
    """
    ENTERED["thread"] = ENTERED["fork"] = 0

    def _no_worker_enter(*_a, **_kw):
        ENTERED["thread"] += 1
        return None  # "every attempt overran or STOP cancelled"

    def _no_timeout_enter(*_a, **_kw):
        ENTERED["fork"] += 1
        return None, None  # "every attempt timed out"

    monkeypatch.setattr(il, "_enter_on_worker", _no_worker_enter)
    monkeypatch.setattr(il, "_enter_with_timeout", _no_timeout_enter)


@pytest.fixture(autouse=True)
def _linux_and_clean(monkeypatch):
    """Hold ``IS_LINUX`` TRUE throughout, start from a clean variable, and keep
    the engine out of the process.

    ⭐ IS_LINUX is deliberately forced TRUE even on a non-Linux runner. That is
    what ISOLATES the ``in_thread`` half of the guard: a fix that guarded on
    platform alone would pass every test below on a Windows runner and fail
    the property on the Linux ``in_process=True`` path, which is the reachable
    one. The same reasoning the sibling
    ``test_thread_path_child_leaves_its_process_environment_alone`` states.
    """
    monkeypatch.setattr(il._platform, "IS_LINUX", True)
    monkeypatch.delenv(VAR, raising=False)
    _stub_the_engine_enter(monkeypatch)
    yield


def _run_thread_path(tmp_path, profile_name):
    """Drive the REAL ``_launch_and_watch`` on the thread arm and report the
    manager's own ``os.environ`` before and after.

    Deliberately not a reproduction of the line's body: a copied statement
    tests the copy. This calls the shipped function; the engine ENTER beneath
    it is stubbed (see ``_stub_the_engine_enter``), so no browser starts on any
    platform.
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


def test_the_engine_seam_is_really_stubbed(tmp_path):
    """⭐ THE HARNESS'S OWN CONTROL, and the one this file previously lacked.

    Every other test here would pass just as well if the stub silently stopped
    applying — they would simply take 2s (Linux, no DISPLAY) or 120s (a runner
    with a window server) and spawn a Firefox each. This asserts the seam was
    ENTERED, so a rename of ``_enter_on_worker`` turns that into a red run.
    """
    _b, _a, emitted = _run_thread_path(tmp_path, "Acme Bank")
    assert ENTERED["thread"] == 1, (
        "the thread-arm engine seam was not reached through the stub. Either "
        "`_enter_on_worker` was renamed (patch the new name) or the function "
        "returned before it — in which case nothing below is measuring the "
        "shipped path."
    )
    assert ENTERED["fork"] == 0, "the thread arm must not enter via the fork seam"
    # The shipped `if ctx is None` arm ran, rather than an exception escaping.
    assert emitted == ["LAUNCH_FAILED: launch timed out", "BROWSER_CLOSED"]


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

    ⚠️ THE CHILD RE-APPLIES THE ENGINE SEAM EXPLICITLY rather than leaning on
    inheriting it across the fork. It DOES inherit it — ``monkeypatch`` has
    already rebound the module attributes by the time ``os.fork`` runs — but a
    fork inside a multi-threaded pytest process that then entered a real engine
    is its own hazard, and an earlier revision of this test came back with an
    EMPTY report on macOS for exactly that reason. Restating the substitution
    here costs two lines and makes the child's independence from fixture
    ordering readable at the site.

    The report is written in a ``finally`` and the parent's read is BOUNDED, so
    a child that dies mid-launch produces a failed assertion with a message
    rather than a suite that hangs until the job timeout.
    """
    profile_dir = pathlib.Path(tmp_path) / ".invisible-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    report_r, report_w = os.pipe()

    pid = os.fork()
    if pid == 0:  # pragma: no cover - runs in the forked child
        report: dict = {"error": "the child never reached its report"}
        try:
            os.close(report_r)
            il._platform.IS_LINUX = True
            # Belt and braces — see the docstring. No engine, in the child too.
            il._enter_on_worker = lambda *_a, **_kw: None
            il._enter_with_timeout = lambda *_a, **_kw: (None, None)
            il._launch_and_watch(
                {"profile_name": "Acme Bank", "profile_dir": str(profile_dir)},
                str(profile_dir),
                lambda line: None,
                lambda: None,
                None,  # no stop_event: the fork arm
                False,  # in_thread=False
            )
            report = {VAR: os.environ.get(VAR)}
        except BaseException as exc:
            report = {"error": f"{type(exc).__name__}: {exc}"}
        finally:
            try:
                with os.fdopen(report_w, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(report))
            except OSError:
                pass
            os._exit(0)

    os.close(report_w)
    # BOUNDED. A wedged child must fail this test, not hang the job.
    payload = ""
    with os.fdopen(report_r, encoding="utf-8") as fh:
        ready, _, _ = select.select([fh], [], [], 60)
        if ready:
            payload = fh.read()
    os.waitpid(pid, 0)

    assert payload, (
        "the forked child reported no environment within 60s — it died or "
        "wedged before its `finally` could write. Nothing below is measured."
    )
    child_env = json.loads(payload)
    assert "error" not in child_env, (
        f"the forked child failed before reporting: {child_env['error']}"
    )
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
    engine constructor, which this harness holds off on purpose. A source read
    is a weaker instrument than the environ assertions above, and is labelled
    as such rather than dressed up — its job is to make a future edit to that
    guard a deliberate act.

    ⚠️ DERIVED BY AST, NOT BY SUBSTRING. An earlier revision matched the guard
    line PLUS the first line of the comment beneath it, so rewording a comment
    broke this test with a message about the X11 taskbar icon and sent the
    reader looking in the wrong place. The property is about the ``if`` that
    ENCLOSES the ``extra_args.append``, so it is now read off the tree: prose
    above, beside or inside it is free to change.
    """
    source = pathlib.Path(il.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_launch_and_watch"
    )

    # Find the `--name=` append, and the `if` statement lexically enclosing it.
    def _appends_the_name_arg(node):
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "extra_args"
            and "--name=" in ast.unparse(node)
        )

    guards = [
        stmt
        for stmt in ast.walk(fn)
        if isinstance(stmt, ast.If)
        and any(_appends_the_name_arg(n) for n in ast.walk(stmt))
    ]
    assert guards, (
        "the `--name=` launch argument is gone from `_launch_and_watch`, or "
        "is no longer built through `extra_args.append`. The X11 WM_CLASS "
        "half of the taskbar identity is what this test tracks."
    )
    # The INNERMOST enclosing `if` is the guard on that append.
    guard = min(guards, key=lambda s: s.end_lineno - s.lineno)
    names_in_test = {
        n.id for n in ast.walk(guard.test) if isinstance(n, ast.Name)
    }
    assert "in_thread" not in names_in_test, (
        "the `--name` guard gained an `in_thread` term. The X11 taskbar icon "
        "is now lost on the thread path too — which is a real behaviour "
        "change and needs its own decision, not a tidy-up. Only the WAYLAND "
        "half (the environ write) is deliberately absent there (PS-360)."
    )
    # ...and it is still the platform guard it always was, so this test is
    # not passing merely because the `if` disappeared.
    assert "name" in names_in_test and "_platform" in ast.unparse(guard.test)
