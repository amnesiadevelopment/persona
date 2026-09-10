"""Pin the DEGRADED survivor arm PS-388 added, and the ways it can go vacuous.

⭐ WHY A SEPARATE FILE FROM `test_behaviour_checks.py`. That file pins the
registry's SHAPE — names present, profile names read from the constant — and
those assertions are satisfied by an arm that launches, does nothing to the
session, and tears it down. That is exactly section 8's measurement wearing
this arm's name, and it is the one failure mode this slice can produce that
looks completely healthy from outside: same tree, same teardown, same green.

So this file asserts the things that make the arm DIFFERENT from its sibling,
and the things that make an escaped wedge impossible:

  * the degradation is READ BACK from the process table and refuses when it
    cannot be confirmed (`_stop_group_or_refuse`);
  * the undo runs on EVERY exit path, including the instrument-failure ones,
    and it runs BEFORE the sweep;
  * the falsification models the pre-PS-192 defect the SHIPPED way — `os.kill`
    on the held pid — rather than through the handle, whose `kill()` IS the
    fix;
  * the settle precondition is INHERITED (`_launch_and_grow`) rather than
    re-implemented with a looser guard;
  * the arm is in the launch lane's floor, not merely in the registry.

⚠️ WHAT THIS FILE CAN AND CANNOT DO, STATED SO A GREEN HERE IS NOT OVER-READ.
NOTHING HERE LAUNCHES A BROWSER OR STOPS A REAL PROCESS. This container has no
usable sandbox for chromium (unprivileged user namespaces are denied; the
engine exits FATAL "No usable sandbox!" ~3s in — measured on this branch), and
the suite must not spend two 90-second-bounded chromium launches per run. The
real-process behaviour of `_resume_group` and `_stop_group_or_refuse` is driven
here against ORDINARY POSIX PROCESSES this file starts itself — `sleep`, in its
own process group — which is a genuine SIGSTOP/SIGCONT round trip through the
same code the arm uses, and is NOT a claim that the chromium arm passed. That
it reached a verdict on a runner is recorded in the PR, not asserted here.
"""

from __future__ import annotations

import inspect
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from src.services.verify import behaviour_checks

POSIX_ONLY = pytest.mark.skipif(
    not hasattr(os, "killpg") or sys.platform.startswith("win"),
    reason="SIGSTOP/SIGCONT and process groups are POSIX facts",
)


def _spawn_stopped_group() -> "tuple[subprocess.Popen, int]":
    """A real process in its OWN group, ready to be signalled. Never chromium.

    `start_new_session=True` is what gives it a group of its own, so a group
    signal aimed at it cannot reach this test runner — the same self-kill
    hazard `signallable_group` guards, exercised rather than assumed.
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # The child IS its own group leader, so the pgid is its pid.
    return proc, proc.pid


def _status(pid: int) -> str:
    psutil = pytest.importorskip("psutil")
    return psutil.Process(pid).status()


# --- the arm exists and is REQUIRED, not merely present ---------------------


def test_the_degraded_arm_is_in_the_registry_and_needs_a_launch() -> None:
    """It must not be able to run without a browser.

    A survivor count taken with no launch is the vacuous zero BOTH survivor
    checks exist to refuse, so `needs_launch=False` would be the cheapest way
    to make this arm permanently green.
    """
    from src.services.verify.behaviour_checks import CHECKS

    entry = next(
        (c for c in CHECKS if c.name == "no-process-survives-a-degraded-session"),
        None,
    )
    assert entry is not None, "the degraded arm is not in the registry"
    assert entry.needs_launch is True, (
        "the degraded arm was moved into the no-launch lane, where it has no "
        "tree to count and no session to wedge — a permanently green check"
    )
    assert entry.run is not entry.falsify


def test_the_two_survivor_arms_are_separate_checks() -> None:
    """⭐ THE SHAPE DECISION, PINNED SO IT IS NOT QUIETLY UNDONE.

    `run_check` falsifies PER CHECK, once, and a check that fails its self-test
    never reaches its verdict. Folding the degraded gesture into the healthy
    check as a second step would make it ride on the HEALTHY arm's
    falsification — the one thing this slice adds would be the one thing never
    shown capable of failing — and would make one verdict cover two surfaces,
    so a red could not say which teardown broke.
    """
    from src.services.verify.behaviour_checks import CHECKS

    names = [c.name for c in CHECKS]
    assert "no-process-survives-a-closed-session" in names
    assert "no-process-survives-a-degraded-session" in names

    healthy = next(c for c in CHECKS if c.name.endswith("a-closed-session"))
    degraded = next(c for c in CHECKS if c.name.endswith("a-degraded-session"))
    assert healthy.run is not degraded.run
    assert healthy.falsify is not degraded.falsify
    assert healthy.surface != degraded.surface, (
        "the two arms report the same surface, so a reader of the report "
        "cannot tell which teardown was measured"
    )


def test_the_healthy_arm_is_the_control_and_does_not_degrade_anything() -> None:
    """AC5: the healthy arm stays byte-identical — it is this change's control.

    Asserted as an absence rather than a diff: if the degradation ever leaks
    into the sibling, the two arms measure the same thing and the control is
    gone. Source inspection is the right instrument here precisely because
    nothing in a passing report would show it.
    """
    healthy = inspect.getsource(
        behaviour_checks._run_no_process_survives_a_closed_session
    )
    for forbidden in ("SIGSTOP", "_stop_group_or_refuse", "_resume_group"):
        assert forbidden not in healthy, (
            f"{forbidden} appeared in the HEALTHY arm, which is the control "
            "for the degraded one. Two arms measuring a wedged session leave "
            "the ordinary teardown unmeasured."
        )


def test_the_degraded_arm_is_in_the_launch_lanes_floor() -> None:
    """In the registry is not the same as REQUIRED TO PASS.

    A check that is selected but not in `EXPECTED_CHECKS` can report CANNOT RUN
    forever while the lane exits 0.
    """
    import importlib.util
    from pathlib import Path

    script = (
        Path(__file__).resolve().parent.parent
        / ".github"
        / "scripts"
        / "run_launch_behaviour_checks.py"
    )
    spec = importlib.util.spec_from_file_location("_ps388_runner", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert "no-process-survives-a-degraded-session" in module.SELECTED_CHECKS
    assert "no-process-survives-a-degraded-session" in module.EXPECTED_CHECKS


# --- the degradation is VERIFIED, not assumed -------------------------------


def test_the_degradation_is_read_back_from_the_process_table() -> None:
    """⛔ THE VACUOUS-GREEN GUARD, and the reason this arm is worth anything.

    A `SIGSTOP` that reached nothing raises nothing: `os.kill` on a live pid
    succeeds whether or not the process was ever going to stop, and a
    still-healthy session tears down exactly as section 8's does. The result is
    a clean, confident and completely false green of the shape this project has
    recorded twice (PS-299's rebase probe reading "81/81 hunks, 0 rejects"
    against an empty directory; PS-341's `--dump-dom` reading "8 of 8 moved").
    Both were produced by the INSTRUMENT, not the subject.
    """
    source = inspect.getsource(behaviour_checks._stop_group_or_refuse)
    assert "STATUS_STOPPED" in source, (
        "the wedge is not read back from the process table, so an ineffective "
        "SIGSTOP produces a green from a session that was never degraded"
    )
    assert "BehaviourCheckError" in source, (
        "an unverifiable wedge must be CANNOT_RUN (exit 2), never a pass"
    )


@POSIX_ONLY
def test_a_real_process_is_actually_stopped_and_the_check_confirms_it() -> None:
    """The round trip against a REAL process — not chromium, and not a mock.

    This is the arm's central claim reduced to something this container can
    run: signal a live process group, and have `_stop_group_or_refuse` report
    the members it can PROVE are stopped.
    """
    pytest.importorskip("psutil")
    proc, pgid = _spawn_stopped_group()
    try:
        stopped = behaviour_checks._stop_group_or_refuse(pgid)
        assert stopped == [proc.pid]
        assert _status(proc.pid) == "stopped", (
            "the process reports as stopped to psutil but the check's own "
            "predicate did not agree — they must be the same reading"
        )
    finally:
        behaviour_checks._resume_group(pgid)
        proc.kill()
        proc.wait(timeout=5)


@POSIX_ONLY
def test_the_check_refuses_rather_than_reporting_an_empty_wedge() -> None:
    """A group with nothing in it is "nothing was measured", never a pass.

    This is the same rule `_survivors_or_refuse` states one level up, and it
    matters more here: an empty group at the DEGRADE step means the tree
    vanished between the settle and the wedge, which is PS-347's own defect
    (a survivor count of zero from a teardown that had nothing to tear down)
    reproduced inside this arm.
    """
    proc, pgid = _spawn_stopped_group()
    proc.kill()
    proc.wait(timeout=5)
    # Give the group a moment to actually empty.
    for _ in range(20):
        try:
            if not behaviour_checks._survivors_or_refuse(pgid):
                break
        except Exception:
            break
        time.sleep(0.1)

    with pytest.raises(behaviour_checks.BehaviourCheckError) as exc:
        behaviour_checks._stop_group_or_refuse(pgid)
    assert "nothing to degrade" in str(exc.value)
    assert "Nothing was measured" in str(exc.value)


# --- the undo runs on EVERY exit path ---------------------------------------


@POSIX_ONLY
def test_a_stopped_process_is_resumed_by_the_undo() -> None:
    """AC4's mechanism, exercised on a real process rather than reasoned about.

    A leaked STOPPED process is worse than a leaked running one: it holds its
    RSS forever, it is invisible to anything sampling CPU, and no ordinary
    teardown will touch it again.
    """
    pytest.importorskip("psutil")
    proc, pgid = _spawn_stopped_group()
    try:
        behaviour_checks._stop_group_or_refuse(pgid)
        assert _status(proc.pid) == "stopped"
        behaviour_checks._resume_group(pgid)
        # SIGCONT is asynchronous; give the scheduler a moment.
        for _ in range(20):
            if _status(proc.pid) != "stopped":
                break
            time.sleep(0.05)
        assert _status(proc.pid) != "stopped", (
            "the undo did not resume the tree, so a wedge escapes this arm — "
            "the leak this whole direction exists to prevent, arriving through "
            "the gate that watches for it"
        )
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_the_undo_runs_before_the_sweep_on_every_exit_path() -> None:
    """⛔ ORDER MATTERS, and the reason is the paths where the sweep is a NO-OP.

    `_sweep_group` sends SIGKILL, which a stopped process DOES receive — so on
    the paths that reach a working sweep, the resume is redundant. It is here
    for the paths that do not: a `signallable_group` refusal (our own group, no
    `killpg`) makes the sweep a silent no-op, and an EPERM from `os.killpg` is
    swallowed by its own `contextlib.suppress`. Both leave a tree that is
    stopped AND unreaped.

    Asserted structurally, on BOTH arms, because there is no report in which
    the wrong order would be visible.
    """
    for fn in (
        behaviour_checks._run_no_process_survives_a_degraded_session,
        behaviour_checks._falsify_no_process_survives_a_degraded_session,
    ):
        source = inspect.getsource(fn)
        assert "finally:" in source, f"{fn.__name__} has no guaranteed exit path"
        tail = source.rsplit("finally:", 1)[1]
        assert "_resume_group(pgid)" in tail, (
            f"{fn.__name__}'s finally does not UNDO the degradation, so any "
            "raise between the wedge and the teardown leaves a stopped tree"
        )
        assert "_sweep_group(pgid)" in tail, (
            f"{fn.__name__}'s finally does not sweep the tree it launched"
        )
        assert tail.index("_resume_group(pgid)") < tail.index("_sweep_group(pgid)"), (
            f"{fn.__name__} sweeps BEFORE it resumes. On the paths where the "
            "sweep is a no-op (our own group, no killpg, EPERM) that order "
            "leaves a SIGSTOPped tree behind — alive, unreapable, invisible to "
            "any CPU sampler, holding its RSS indefinitely."
        )


def test_the_degradation_is_anchored_on_the_group_never_on_a_name() -> None:
    """`_sweep_group`'s rule, applied to the DEGRADE step as well as the sweep.

    A wedge planted over a wider set than the sweep can reach is precisely the
    escape this arm must not be able to produce. PS-185 lost two cycles to a
    `pkill -f chromium` that matched its own command line.
    """
    source = inspect.getsource(behaviour_checks._degradable_pids)
    assert "_survivors_or_refuse" in source, (
        "the pids to stop are not resolved from the recorded group"
    )
    for fn in (
        behaviour_checks._run_no_process_survives_a_degraded_session,
        behaviour_checks._falsify_no_process_survives_a_degraded_session,
        behaviour_checks._stop_group_or_refuse,
    ):
        source = inspect.getsource(fn)
        for forbidden in ("pkill", "process_iter", "psutil.process_iter", "chrome"):
            assert forbidden not in source, (
                f"{fn.__name__} reaches for {forbidden!r} — a name match can "
                "signal a process this check never launched"
            )


@POSIX_ONLY
def test_the_undo_is_not_gated_on_the_guard_that_defines_its_reason_to_exist() -> None:
    """⛔⛔ THE PATH THE UNDO WAS WRITTEN FOR, DRIVEN RATHER THAN REASONED ABOUT.

    `_resume_group` exists for the paths where `_sweep_group` is a NO-OP — a
    `signallable_group` refusal, or a `killpg` that fails and is swallowed by
    the sweep's own `contextlib.suppress`. An earlier revision of this arm
    gated the resume on the SAME `signallable_group` call as the sweep, which
    made the two bodies identical modulo the signal number: when the guard
    refused, BOTH returned without signalling and the tree was left alive AND
    stopped. That is AC4's stated failure mode arriving through the function
    written to prevent it, and NO source-ordering assertion can see it — the
    call is still there, still first, and still does nothing.

    So this drives the refusal for real: `signallable_group` is forced to
    refuse (exactly as it does for our own group, or on a platform with no
    `killpg`), and the wedge must STILL be undone.
    """
    pytest.importorskip("psutil")
    proc, pgid = _spawn_stopped_group()
    try:
        behaviour_checks._stop_group_or_refuse(pgid)
        assert _status(proc.pid) == "stopped"

        import src.services.browser.process_group as process_group

        original = process_group.signallable_group
        process_group.signallable_group = lambda _pgid: None  # the refusal
        try:
            behaviour_checks._resume_group(pgid)
        finally:
            process_group.signallable_group = original

        for _ in range(20):
            if _status(proc.pid) != "stopped":
                break
            time.sleep(0.05)
        assert _status(proc.pid) != "stopped", (
            "with the group guard REFUSING, the undo left the tree stopped. "
            "That is the exact state `_resume_group` exists to prevent: a "
            "sweep that is a no-op on the same condition leaves a SIGSTOPped "
            "tree alive, unreapable, invisible to any CPU sampler, holding its "
            "RSS indefinitely — the leak this whole direction watches for, "
            "arriving through the gate that watches for it."
        )
    finally:
        behaviour_checks._resume_group(pgid)
        proc.kill()
        proc.wait(timeout=5)


def test_the_undo_does_not_reuse_the_sweeps_group_guard_as_its_own_gate() -> None:
    """The structural half of the test above: the per-pid leg is UNCONDITIONAL.

    Stated as source because the behavioural test can only prove the undo works
    on ONE forced refusal, while the defect it replaces was a *shape*: the undo
    early-returning on the guard, so that every no-op path of the sweep was
    also a no-op path of the undo.
    """
    source = inspect.getsource(behaviour_checks._resume_group)
    assert "_degradable_pids(pgid)" in source, (
        "the undo does not resolve per-pid targets from the recorded group, so "
        "it can only ever act when `killpg` is available and permitted — which "
        "is exactly the case where the sweep already handles it"
    )
    guard_index = source.index("signallable_group(pgid)")
    perpid_index = source.index("_degradable_pids(pgid)")
    between = source[guard_index:perpid_index]
    assert "return" not in between, (
        "the undo RETURNS between consulting the group guard and its per-pid "
        "leg, so a refusal skips the only leg that can act on a refusal"
    )


@POSIX_ONLY
def test_the_undo_is_safe_against_our_own_group() -> None:
    """A SIGCONT to our own group is harmless, and this proves it in situ.

    ⚠️ NOT a claim that the undo REFUSES our own group — it deliberately does
    not, because refusing is what made it inert (see above). The self-kill
    hazard `signallable_group` guards is a SIGKILL hazard; every member of our
    own group is running by construction (we are executing), so a SIGCONT to it
    is a no-op at the kernel level rather than a danger.

    ⚠️ POSIX-ONLY, AND THE MARKER IS LOAD-BEARING RATHER THAN TIDINESS:
    `os.getpgrp` does not exist on Windows, so without it this test raises
    `AttributeError` in its FIRST line — which is a fact about the test's own
    fixture and not about the undo. Measured: the marker was dropped when this
    test replaced its predecessor and `tests (windows-latest, main)` went red
    on exactly that, while every POSIX shard stayed green.
    """
    behaviour_checks._resume_group(os.getpgrp())  # must not raise, must not stop us
    assert True  # reaching this line IS the assertion: we are still running


def test_every_test_touching_a_posix_api_carries_the_posix_marker() -> None:
    """⛔ THE MARKER IS A CORRECTNESS PROPERTY OF THIS FILE, NOT HOUSEKEEPING.

    Half the tests here drive REAL process groups and half only read source
    text, and the two look identical in a listing — both mention `SIGSTOP`,
    because one signals with it and the other searches for the word. Get the
    split wrong in the portable direction and the file is merely over-skipped;
    get it wrong in the other and a POSIX-only API is CALLED on Windows, where
    it does not exist.

    ⚠️ MEASURED, NOT ANTICIPATED. `test_the_undo_is_safe_against_our_own_group`
    replaced a predecessor and did not inherit its marker, so
    `tests (windows-latest, main)` went red on `AttributeError: module 'os' has
    no attribute 'getpgrp'` — in the test's FIRST line, a fact about the
    fixture and not about the undo — while every POSIX shard stayed green. A
    single-platform red that no local run can reproduce is exactly the kind
    this file should be able to catch itself.

    So the rule is checked on the AST rather than on the text: a test is
    required to carry the marker only when it CALLS one of these APIs. A test
    that merely names one in a string is portable and must stay unmarked,
    which is why a grep cannot express this.
    """
    import ast

    posix_calls = {
        "os.killpg",
        "os.getpgrp",
        "os.getpgid",
        "os.kill",
        "os.fork",
        "behaviour_checks._resume_group",
        "behaviour_checks._stop_group_or_refuse",
        "behaviour_checks._sweep_group",
        "_spawn_stopped_group",
        "_status",
    }

    tree = ast.parse(inspect.getsource(sys.modules[__name__]))
    offenders: "list[tuple[str, list[str]]]" = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        marked = any(
            getattr(dec, "id", None) == "POSIX_ONLY" for dec in node.decorator_list
        )
        if marked:
            continue
        statements = node.body
        if (
            statements
            and isinstance(statements[0], ast.Expr)
            and isinstance(statements[0].value, ast.Constant)
        ):
            statements = statements[1:]
        called = set()
        for statement in statements:
            for sub in ast.walk(statement):
                if isinstance(sub, ast.Call):
                    called.add(ast.unparse(sub.func))
        hits = sorted(called & posix_calls)
        if hits:
            offenders.append((node.name, hits))

    assert not offenders, (
        "these tests CALL a POSIX-only API without @POSIX_ONLY, so they raise "
        "AttributeError on Windows rather than skipping — a red that says "
        f"nothing about the code under test: {offenders}"
    )


# --- the falsification is the SHIPPED shape ---------------------------------


def test_the_falsification_signals_the_held_pid_not_the_handle() -> None:
    """⛔ THE CONTROL MUST NOT BE MEASURING THE FIX.

    On persona's Linux FORK path the handle's own `kill()` is group-aware — it
    IS the PS-192 fix — so a "pre-fix shape" control built on `proc.kill()` /
    `proc.terminate()` would tear the whole group down, report a comfortable
    zero survivors, and certify nothing. Section 8's falsification records this
    in capitals; this arm inherits the shape rather than re-deriving it.

    ⚠️ THE EXECUTABLE BODY, NOT THE DOCSTRING OR THE COMMENTS. Both of those
    NAME the forbidden calls in order to forbid them, so a substring search
    over raw source fails on the very prose that gets this right.

    ⛔ AND THE OBVIOUS WAY TO STRIP THE DOCSTRING IS VERSION-DEPENDENT — this
    test failed on all three CI platforms while passing locally for exactly
    that reason, so the mechanism is recorded rather than left to be
    rediscovered. `source.replace(fn.__doc__, "")` works on <=3.12 and SILENTLY
    NO-OPS on 3.13+: gh-81283 made the compiler DEDENT docstrings, so `__doc__`
    is no longer a substring of the source it came from and the replace removes
    nothing. A strip that quietly stops stripping leaves the prose in the
    haystack, and the assertion then fires on the sentence forbidding the call.
    So the body is extracted from the AST instead, which drops the docstring
    AND the comments by construction and depends on no version's formatting.
    """
    import ast

    source = textwrap.dedent(
        inspect.getsource(
            behaviour_checks._falsify_no_process_survives_a_degraded_session
        )
    )
    fn_node = ast.parse(source).body[0]
    statements = fn_node.body
    if (
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        statements = statements[1:]
    assert statements, "the falsification has no body once its docstring is dropped"
    body = "\n".join(ast.unparse(node) for node in statements)

    # The strip must be REAL: the docstring names the forbidden call, so if it
    # survived, the assertions below would be reading prose rather than code.
    assert "would be measuring the fix" not in body, (
        "the docstring survived the AST extraction, so every assertion below "
        "is searching the very sentence that forbids the call"
    )
    assert "os.kill(proc.pid" in body, (
        "the falsification does not signal the HELD PID directly"
    )
    assert "proc.terminate()" not in body, (
        "the falsification signals through the handle, whose kill() is itself "
        "the PS-192 fix — the control would be measuring the fix"
    )
    assert "proc.kill()" not in body, (
        "same defect through the other handle method: `kill()` IS the "
        "group-aware PS-192 fix on this path"
    )
    assert "reap_process_group" not in body
    assert "terminate(proc" not in body, (
        "the falsification calls the product's teardown, which is the thing it "
        "is modelling the ABSENCE of"
    )


def test_the_falsification_refuses_when_it_observes_no_survivor() -> None:
    """A self-test that cannot see its own planted defect must not pass.

    `run_check` turns this raise into CANNOT_RUN (exit 2) — "this check is
    broken" — which is a different message from a FINDING and certifies
    nothing.
    """
    source = inspect.getsource(
        behaviour_checks._falsify_no_process_survives_a_degraded_session
    )
    assert "if not survivors:" in source
    assert "BehaviourCheckError" in source
    assert "certify nothing" in source


# --- the settle precondition is INHERITED, not relaxed ----------------------


def test_the_degraded_arm_inherits_the_settle_guard() -> None:
    """AC3. A survivor count of zero because the WEDGE killed the tree is
    PS-347's own defect reproduced inside its extension.

    The guard lives in `_launch_and_grow` and raises `BehaviourCheckError`,
    which lands as CANNOT_RUN (exit 2) — never as a pass. Both arms must go
    through it rather than launching by hand.
    """
    for fn in (
        behaviour_checks._run_no_process_survives_a_degraded_session,
        behaviour_checks._falsify_no_process_survives_a_degraded_session,
    ):
        source = inspect.getsource(fn)
        assert "_launch_and_grow(ctx, profile)" in source, (
            f"{fn.__name__} does not launch through `_launch_and_grow`, so the "
            "settle precondition (_MIN_LIVE_TREE / _STABLE_SAMPLES) is not "
            "inherited and a half-started tree could be measured"
        )
        assert "spawn_browser" not in source, (
            f"{fn.__name__} launches the browser itself, bypassing the guard "
            "that sweeps every failing path out of the launch"
        )


def test_the_thresholds_are_read_not_redefined() -> None:
    """⛔ Out of scope: the thresholds were MEASURED and must not move.

    A degraded arm that needed a wider grace to go green would be absorbing a
    product finding into a constant — which AC7 forbids by name.
    """
    source = inspect.getsource(behaviour_checks)
    # Exactly one definition of each, still.
    for constant in ("_MIN_LIVE_TREE", "_STABLE_SAMPLES", "_TEARDOWN_GRACE"):
        definitions = [
            line
            for line in source.splitlines()
            if line.startswith(f"{constant} = ")
        ]
        assert len(definitions) == 1, (
            f"{constant} is defined {len(definitions)} times — the degraded arm "
            "must READ the measured thresholds, never restate them"
        )
    assert behaviour_checks._TEARDOWN_GRACE == 5.0
    assert behaviour_checks._MIN_LIVE_TREE == 3
    assert behaviour_checks._STABLE_SAMPLES == 8


def test_the_arm_reports_its_teardown_timing_on_every_verdict() -> None:
    """AC7. The FINDING, if there is one, is in the TIMING.

    PS-349's orphaned tree was reaped 95s after its session's last confirmed
    state, by something the record cannot name. A pass that does not say how
    long the teardown took cannot distinguish "escalated promptly" from "took
    four seconds of a five-second grace".

    ⚠️ ASSERTED ON THE BEHAVIOUR, NOT ON THE SPELLING. An earlier revision of
    this test counted the substring "timing" in the source, which passes when
    the word appears in a comment and fails on an innocuous rename — a tally
    over source text is exactly the brittle instrument the rest of this file
    avoids. What must hold is that BOTH `Outcome` branches carry the timing
    into their `evidence`, so this walks the function's syntax tree and reads
    the `evidence=` list of every `Outcome(...)` it constructs.
    """
    import ast

    source = textwrap.dedent(
        inspect.getsource(behaviour_checks._run_no_process_survives_a_degraded_session)
    )
    tree = ast.parse(source)

    evidence_lists: "list[list[str]]" = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "Outcome"):
            continue
        for kw in node.keywords:
            if kw.arg == "evidence":
                assert isinstance(kw.value, ast.List), (
                    "an Outcome's evidence is not a literal list, so this test "
                    "cannot read what it carries"
                )
                evidence_lists.append([ast.unparse(e) for e in kw.value.elts])

    assert len(evidence_lists) == 2, (
        "expected exactly two Outcome branches (PASS and FINDING); found "
        f"{len(evidence_lists)}"
    )
    for entries in evidence_lists:
        assert "timing" in entries, (
            "one of the arm's verdicts does not carry the teardown timing into "
            "its evidence, so AC7's report is missing on that branch. PS-349's "
            "finding was a 95s reap; a verdict that does not say how long the "
            "teardown took cannot carry a timing finding at all."
        )

    # And the timing itself must be stated against the grace rather than bare.
    assert "teardown_seconds" in source
    assert "_TEARDOWN_GRACE" in source


def test_the_arm_states_what_it_does_not_observe() -> None:
    """The firefox in_process arm is KNOWN to differ and is named as absent.

    `InvisibleProcess.terminate()` only sets a stop event, so a session that
    ignores it leaves a live browser thread behind while the registry entry is
    wiped (`baseline._teardown` records this). That arm is unreachable from a
    chromium check at any parameter value, and an unadmitted gap reads as
    covered.
    """
    source = inspect.getsource(
        behaviour_checks._run_no_process_survives_a_degraded_session
    )
    assert "firefox" in source.lower()
    assert "chromium on Linux only" in source

    from src.services.verify.behaviour import UNCOVERED_SURFACES

    whys = " ".join(w for _, w in UNCOVERED_SURFACES)
    assert "no-process-survives-a-degraded-session" in whys, (
        "the report's own not-covered section does not mention the degraded "
        "arm, so a reader cannot tell which engine it observed"
    )
    assert "stop event" in whys, (
        "the firefox arm's KNOWN difference is not stated, so its absence "
        "reads as 'unobserved' rather than 'observed elsewhere to differ'"
    )
