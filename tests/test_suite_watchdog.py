"""The suite-wide bound, demonstrated by INDUCING hangs rather than asserting config.

PS-140. ``pyproject.toml``'s ``timeout = 120`` belongs to ``pytest-timeout``, and
pytest ignores an ini key whose plugin is not loaded — so in an agent container
that installs only the project, the per-test bound silently does not exist. A
worker ran ``pytest tests/ -q``, it wedged at 74–76%, and it stayed there for
roughly ninety minutes; the platform reclaimed the ticket underneath them as
inactive. ``conftest.py`` had already printed ``per-test timeout: INERT`` on that
run. The banner was accurate and it bounded nothing, which is the finding this
module's subject exists to answer: a notice is not a bound.

WHAT THESE TESTS ASSERT, AND WHY THEY ARE SHAPED THIS WAY
---------------------------------------------------------
The ticket is explicit that the observable is **that the run terminates and says
which test hung**, shown by making a test hang — "not by asserting that a setting
is present". A test reading back ``timeout = 120`` would pass in precisely the
environment where the bound does not exist: it asserts on the string this project
wrote down rather than on what the system does, which is the always-green shape
this project has catalogued six times over in its own knowledge base.

So every behavioural test here spawns a REAL pytest run that REALLY hangs, and
asserts on what that run did — its exit code, its output, and how long it took.

``-p no:timeout`` ON EVERY INNER RUN. That is what makes these tests mean the
same thing in both environments. CI installs ``pytest-timeout`` and a developer
container does not; disabling it outright reproduces the agent-container
condition everywhere, so these tests cannot pass by accident in CI merely
because the plugin was there. It is the same technique PS-104 used for the same
reason.

WHY THE INNER FILES LIVE IN THE REPO AND NOT IN ``tmp_path``
------------------------------------------------------------
The bound being tested is wired in the ROOT ``conftest.py``, and pytest only
loads a conftest that is an ancestor of the file under test. An inner file in
``tmp_path`` therefore runs with no conftest, no watchdog, and would prove
nothing — verified before relying on it, not assumed.

They are named ``wedge_*.py`` rather than ``test_*.py`` precisely because these
cases provoke hangs and hard kills. A kill between writing a file and the
cleanup in ``finally`` would otherwise leave a real, collectable, HANGING test
in the suite for every later run — turning a test of the safety net into a
permanent hazard. Under the default ``python_files = test_*.py``, a stray
``wedge_*.py`` is inert: pytest runs it when named explicitly and never collects
it in a sweep. Both halves of that were verified before this file was written.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
import time
import uuid

import pytest

from tests.suite_watchdog import (
    BANNER_TOKEN,
    TIMEOUT_EXIT_CODE,
    configured_timeout_s,
    marker_timeout_s,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Inner probe files live here — inside the repo so the root conftest loads,
#: outside any ``test_*.py`` name so no sweep ever collects them.
PROBE_DIR = os.path.join(REPO_ROOT, "tests", "_watchdog_probes")

#: The bound used by inner runs that are MEANT to fire. Small enough that the
#: outer suite stays fast, comfortably larger than a pytest startup.
FAST_BOUND = "3"

#: An outer ceiling on every inner run. This is the assertion that a hang was
#: really bounded: if the inner bound does not work we get a ``TimeoutExpired``
#: here rather than a false pass, so the failure mode is loud rather than green.
OUTER_LIMIT = 120


def _run_inner(
    body: str,
    *,
    bound: str | None = FAST_BOUND,
    extra_env=None,
    extra_args=(),
    header: bool = False,
):
    """Run ``body`` as its own pytest session and return (result, elapsed).

    The session is deliberately a real subprocess: the behaviour under test is
    that a RUN terminates, and a run that terminates itself cannot be observed
    from inside itself.

    ``header=True`` keeps ``pytest_report_header``'s output. That is what lets a
    test assert on what the run CLAIMS about its own bound, not merely on what
    it did — the two drifted apart in round 1 of PS-140 and the claim was the
    half nothing covered.

    It drops ``-q`` ENTIRELY rather than only ``--no-header``, which is measured
    rather than assumed: ``-q`` alone already suppresses the header, so a
    ``-q``-plus-something spelling would leave every header assertion below
    trivially true against absent output. That is the always-green shape this
    project has catalogued, and it would hide exactly the defect these tests
    were added for.

    ``bound=None`` leaves ``PERSONA_SUITE_TIMEOUT`` unset rather than setting
    it, so a test can exercise the ``pyproject.toml`` path. Passing ``"0"``
    would disable the bound too, but by the OTHER knob, and the whole point of
    those tests is telling the two apart.
    """
    os.makedirs(PROBE_DIR, exist_ok=True)
    path = os.path.join(PROBE_DIR, f"wedge_{uuid.uuid4().hex}.py")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(textwrap.dedent(body))

    env = dict(os.environ)
    if bound is None:
        env.pop("PERSONA_SUITE_TIMEOUT", None)
    else:
        env["PERSONA_SUITE_TIMEOUT"] = bound
    env.update(extra_env or {})

    # `-q` alone already suppresses the header, so keeping it here would make
    # every header assertion below vacuous. Measured, not assumed — see above.
    quiet_args = [] if header else ["-q", "--no-header"]

    started = time.monotonic()
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest", path,
                "-p", "no:timeout",     # the plugin is genuinely gone
                "-p", "no:randomly",    # keep declaration order meaningful
                *quiet_args,
                *extra_args,
            ],
            capture_output=True, text=True, cwd=REPO_ROOT,
            env=env, timeout=OUTER_LIMIT,
         encoding="utf-8")
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"the inner run HUNG for {OUTER_LIMIT}s with a {bound}s bound and "
            f"pytest-timeout disabled. The suite-wide bound did not fire, "
            f"which is exactly the ninety-minute defect PS-140 exists to remove."
        )
    finally:
        with open(os.devnull, "w", encoding="utf-8"):
            pass
        if os.path.exists(path):
            os.remove(path)

    return result, time.monotonic() - started


def _cleanup_probe_dir():
    if os.path.isdir(PROBE_DIR) and not os.listdir(PROBE_DIR):
        shutil.rmtree(PROBE_DIR, ignore_errors=True)


# --------------------------------------------------------------------------
# 1. The headline: a hang ends the run, and the run says which test hung
# --------------------------------------------------------------------------

_HANGING_TEST = """
    import time

    def test_runs_fine():
        assert True

    def test_wedges_forever():
        time.sleep(9999)

    def test_never_reached():
        assert True
"""


def test_a_hung_test_ends_the_run_instead_of_hanging_it():
    """The whole ticket, in one assertion: the run ENDED.

    An unbounded run sits in ``time.sleep(9999)`` until something outside kills
    it — which is what ``_run_inner``'s subprocess timeout would report as a
    failure. Reaching any assertion below at all is the property being proven.
    """
    result, elapsed = _run_inner(_HANGING_TEST)
    output = result.stdout + result.stderr

    assert elapsed < 60, (
        f"the run ended, but after {elapsed:.0f}s against a {FAST_BOUND}s "
        f"bound — so it did not end ON its bound:\n{output[-2000:]}"
    )
    assert result.returncode == TIMEOUT_EXIT_CODE, (
        f"expected exit {TIMEOUT_EXIT_CODE} from a fired bound, got "
        f"{result.returncode}:\n{output[-2000:]}"
    )
    _cleanup_probe_dir()


def test_the_run_names_the_test_that_hung():
    """Ending is not enough — an unexplained kill is a mystery, not a diagnosis.

    PS-140 asks for the hang to be reported, and a bound that ends the run
    without naming the culprit leaves the reader exactly where the ninety
    minutes did: knowing something wedged, not knowing what.
    """
    result, _ = _run_inner(_HANGING_TEST)
    output = result.stdout + result.stderr

    assert "test_wedges_forever" in output, (
        f"the run was killed but never named the hung test:\n{output[-2000:]}"
    )
    # The faulthandler dump is what makes it actionable: not merely which test,
    # but which FRAME stopped returning.
    assert "in test_wedges_forever" in output, (
        f"no stack frame for the hung test — the faulthandler dump is what "
        f"turns 'something hung' into a line number:\n{output[-2000:]}"
    )
    _cleanup_probe_dir()


def test_the_diagnostic_survives_the_default_capture_mode():
    """The banner must reach the terminal on a PLAIN run, not only under ``-s``.

    This is a regression test for a real defect found while building this: the
    bound fired correctly, exited 124 in five seconds, and printed NOTHING.
    Pytest's default ``--capture=fd`` dup2s over file descriptor 2, and
    ``os._exit`` means the captured buffer is never replayed — so the whole
    diagnostic went into a temp file nobody would ever read. A silent kill is
    most of the way back to the silent hang.

    None of the inner runs here pass ``-s``, so this asserts the fixed path.
    """
    result, _ = _run_inner(_HANGING_TEST)
    output = result.stdout + result.stderr

    assert BANNER_TOKEN in output, (
        f"the run was killed but the explanation never reached the terminal "
        f"under default capture:\n{output[-2000:]}"
    )
    _cleanup_probe_dir()


# --------------------------------------------------------------------------
# 2. The differential PS-140 requires: a killed run vs. a merely failing one
# --------------------------------------------------------------------------

_FAILING_TEST = """
    def test_ordinary_wrong_value():
        assert 2 + 2 == 5
"""


def test_a_fired_bound_is_distinguishable_from_an_ordinary_failure():
    """Two runs, same watchdog, same bound — one hangs, one merely fails.

    PS-140 makes this a requirement in its own right: "a hang cut short must not
    read as an ordinary assertion failure ... or the bound turns hangs into a
    mysterious flake and buys a worse problem than it solves."

    Asserted as a DIFFERENTIAL rather than as two independent facts, because the
    claim is about telling the two apart. Checking only that a kill exits 124
    would still hold if ordinary failures exited 124 too, at which point the
    signal distinguishes nothing.
    """
    killed, _ = _run_inner(_HANGING_TEST)
    failed, _ = _run_inner(_FAILING_TEST)

    killed_output = killed.stdout + killed.stderr
    failed_output = failed.stdout + failed.stderr

    # Exit codes: different, and the failing one is pytest's ordinary "1".
    assert killed.returncode == TIMEOUT_EXIT_CODE
    assert failed.returncode == 1, (
        f"expected pytest's ordinary failure code 1, got "
        f"{failed.returncode}:\n{failed_output[-2000:]}"
    )
    assert killed.returncode != failed.returncode

    # The token: present on the kill, ABSENT on the genuine failure. The second
    # half is the one that matters — a marker that appears on every run marks
    # nothing.
    assert BANNER_TOKEN in killed_output
    assert BANNER_TOKEN not in failed_output, (
        f"an ordinary assertion failure carried the watchdog's banner, so the "
        f"two are NOT distinguishable:\n{failed_output[-2000:]}"
    )

    # And the ordinary failure still reports as a normal test failure.
    assert "1 failed" in failed_output
    _cleanup_probe_dir()


# --------------------------------------------------------------------------
# 3. The bound must not fire on healthy work
# --------------------------------------------------------------------------

_SLOW_BUT_FINE = """
    import time

    def test_slower_than_nothing_but_well_inside_the_bound():
        time.sleep(1)
        assert True
"""


def test_a_test_that_finishes_inside_its_bound_is_untouched():
    """A bound that fires on healthy tests gets deleted by the first victim.

    The watchdog measures ONE item at a time; a suite of many short tests must
    never accumulate toward a single deadline.
    """
    result, _ = _run_inner(_SLOW_BUT_FINE, bound="10")
    output = result.stdout + result.stderr

    assert result.returncode == 0, (
        f"a healthy test was disturbed by the bound:\n{output[-2000:]}"
    )
    assert BANNER_TOKEN not in output
    _cleanup_probe_dir()


_MANY_SHORT_TESTS = """
    import time

    def test_one():
        time.sleep(0.4)

    def test_two():
        time.sleep(0.4)

    def test_three():
        time.sleep(0.4)

    def test_four():
        time.sleep(0.4)

    def test_five():
        time.sleep(0.4)
"""


def test_the_clock_is_per_item_and_does_not_accumulate():
    """Five tests totalling ~2s must survive a 1.5s PER-ITEM bound.

    The distinction is not academic: a watchdog armed once for the session would
    kill a perfectly healthy suite the moment its total runtime crossed the
    per-test number, and ``timeout = 120`` over a suite of hundreds would fire
    on every run.
    """
    result, _ = _run_inner(_MANY_SHORT_TESTS, bound="1.5")
    output = result.stdout + result.stderr

    assert result.returncode == 0, (
        f"a per-item bound behaved like a whole-session one:\n{output[-2000:]}"
    )
    assert "5 passed" in output
    _cleanup_probe_dir()


# --------------------------------------------------------------------------
# 4. Honouring what the suite already declares
# --------------------------------------------------------------------------

_MARKED_LONGER_THAN_DEFAULT = """
    import time
    import pytest

    @pytest.mark.timeout(30)
    def test_declares_it_needs_longer_than_the_default():
        time.sleep(3)
        assert True
"""


def test_a_per_test_timeout_marker_is_honoured_without_the_plugin():
    """``@pytest.mark.timeout(30)`` must beat a 2s default, plugin or no plugin.

    Without ``pytest-timeout`` an unknown marker is inert metadata that nothing
    reads. Two modules in this suite declare 600s and 900s because they boot a
    real browser per test; a flat default applied over the top of them would
    fail healthy tests, and a safety net that fails healthy tests is one that
    gets switched off.
    """
    result, _ = _run_inner(_MARKED_LONGER_THAN_DEFAULT, bound="2")
    output = result.stdout + result.stderr

    assert result.returncode == 0, (
        f"the marker's 30s bound was ignored and the 2s default killed a "
        f"healthy test:\n{output[-2000:]}"
    )
    assert BANNER_TOKEN not in output
    _cleanup_probe_dir()


_MARKED_SHORTER_THAN_DEFAULT = """
    import time
    import pytest

    @pytest.mark.timeout(2)
    def test_declares_a_tighter_bound_and_then_wedges():
        time.sleep(9999)
"""


def test_a_tighter_marker_bound_still_fires():
    """The marker is READ, not merely used as an excuse to wait longer.

    The complement of the test above, and the reason both exist: honouring a
    marker only when it relaxes the bound would be indistinguishable from
    ignoring markers and raising the default.
    """
    result, elapsed = _run_inner(_MARKED_SHORTER_THAN_DEFAULT, bound="3600")
    output = result.stdout + result.stderr

    assert result.returncode == TIMEOUT_EXIT_CODE, (
        f"the marker's 2s bound never fired under a 3600s default:\n"
        f"{output[-2000:]}"
    )
    assert elapsed < 60, f"ended after {elapsed:.0f}s, not on the marker's 2s bound"
    _cleanup_probe_dir()


# --------------------------------------------------------------------------
# 5. A hang during COLLECTION is the same defect, arriving earlier
# --------------------------------------------------------------------------

_HANGS_AT_IMPORT = """
    import time

    time.sleep(9999)

    def test_never_collected():
        assert True
"""


def test_a_module_that_hangs_at_import_is_bounded_too():
    """"The suite cannot hang" is not true if collection can.

    An import that never returns wedges the run just as completely as a test
    body that never returns, and it does so before a single test has started —
    so the per-test hooks never fire and nothing else would catch it.
    """
    result, elapsed = _run_inner(_HANGS_AT_IMPORT)
    output = result.stdout + result.stderr

    assert result.returncode == TIMEOUT_EXIT_CODE, (
        f"a hang during collection was not bounded:\n{output[-2000:]}"
    )
    assert elapsed < 60
    assert "collection" in output.lower(), (
        f"the run was killed but did not say the hang was in collection, so a "
        f"reader would look for a hung TEST that never ran:\n{output[-2000:]}"
    )
    _cleanup_probe_dir()


def test_a_collect_only_run_is_bounded_too():
    """``--collect-only`` faces the import hazard and must be bounded in it.

    Round 1 returned early for this mode on the reasoning that no items will
    run — true, and beside the point: a module that hangs at IMPORT hangs
    collection, and collection is the ENTIRE work of a ``--collect-only`` run.
    So the one mode whose whole job is importing was the one mode left
    unbounded, while the header went on advertising a 120s bound. Measured
    before the fix: this ran to an external cap with the watchdog token absent.

    The arming call already labels the collection phase, so the fix was simply
    deleting the early return.
    """
    result, elapsed = _run_inner(_HANGS_AT_IMPORT, extra_args=("--collect-only",))
    output = result.stdout + result.stderr

    assert result.returncode == TIMEOUT_EXIT_CODE, (
        f"a --collect-only run hung on a module that never finished "
        f"importing:\n{output[-2000:]}"
    )
    assert elapsed < 60
    assert "collection" in output.lower(), (
        f"the run was killed but did not name collection as the hang:\n"
        f"{output[-2000:]}"
    )
    _cleanup_probe_dir()


# --------------------------------------------------------------------------
# 6. The escape hatch, and deferring to the real plugin
# --------------------------------------------------------------------------

def test_a_zero_bound_disables_the_watchdog_entirely():
    """``0`` means unbounded, matching what ``pytest-timeout`` reads it as.

    Matching the plugin matters more than picking the stricter reading: the
    defect being fixed is two environments disagreeing about a load-bearing
    property, and a fallback that interpreted the same config differently would
    re-create that disagreement with the sign flipped.
    """
    result, _ = _run_inner(_SLOW_BUT_FINE, bound="0")
    output = result.stdout + result.stderr

    assert result.returncode == 0, output[-2000:]
    assert BANNER_TOKEN not in output
    _cleanup_probe_dir()


# --------------------------------------------------------------------------
# 6b. What the run CLAIMS while it is disabled — PS-140 round 2
# --------------------------------------------------------------------------
#
# The test above covers that `0` DISABLES the bound. Round 1 shipped with that
# green while the header went on printing "FALLBACK BOUND ACTIVE ... bounds each
# item at 0s ... so a blocking test cannot hang this run" — a flat assertion,
# false in the one state where a reader most needs it to be true.
#
# That is worse than the stale-hazard header this feature originally replaced:
# an outdated warning merely nags, whereas this one REASSURES. It is also the
# original defect wearing a new coat — `timeout = 120` likewise "worked" until
# its environment changed and said nothing. The behaviour was right and the
# CLAIM was wrong, so a test that only watches behaviour cannot see it. These
# assert the claim.


def _header_line(output: str) -> str:
    """The ``per-test timeout:`` line alone.

    Header assertions are scoped to this rather than run against the whole
    capture, because the whole capture contains pytest's own summary and a
    naive substring test collides with it: ``"0s" in output`` is satisfied by
    the timing line ``1 passed in 1.10s``. A test that passes for a reason
    other than the one it names is worthless the day the timing shifts.
    """
    for line in output.splitlines():
        if "per-test timeout:" in line:
            return line
    return ""


def test_a_disabled_bound_does_not_advertise_itself_as_active():
    """The header must not promise a safety net the run has just declined to arm.

    Asserts the CLAIM, not the behaviour: with the bound off, the run is
    genuinely unbounded and the header's job is to say so. The banned strings
    are the exact ones round 1 printed here.
    """
    result, _ = _run_inner(_SLOW_BUT_FINE, bound="0", header=True)
    output = result.stdout + result.stderr
    header = _header_line(output)

    assert "per-test timeout: NONE" in header, (
        f"a run with no bound of any kind did not report itself as unbounded:\n"
        f"{output[-2000:]}"
    )
    assert "FALLBACK BOUND ACTIVE" not in header, (
        f"the header advertised a fallback that is not armed:\n{output[-2000:]}"
    )
    assert "cannot hang this run" not in header, (
        f"the header flatly promised the run cannot hang, while nothing was "
        f"bounding it — the reassuring-header defect:\n{output[-2000:]}"
    )
    assert "0s" not in header, (
        f"the header rendered a nonsensical 0s bound instead of saying "
        f"disabled:\n{output[-2000:]}"
    )
    _cleanup_probe_dir()


def test_a_disabled_bound_names_the_knob_that_disabled_it():
    """"Not armed" is not actionable; WHICH knob turned it off is.

    The two knobs have different remedies — unset an environment variable, or
    edit ``pyproject.toml`` — so naming the wrong one sends a reader to change
    a setting that is not the cause.
    """
    result, _ = _run_inner(_SLOW_BUT_FINE, bound="0", header=True)
    header = _header_line(result.stdout + result.stderr)

    assert "PERSONA_SUITE_TIMEOUT=0" in header, (
        f"the header did not name the environment variable holding the bound "
        f"off, so a reader cannot act on it:\n{header}"
    )
    _cleanup_probe_dir()


def test_an_armed_bound_reports_the_bound_that_will_actually_fire():
    """The advertised number is the ARMED one, not the one re-read from the ini.

    Round 1's header re-derived its number independently of the watchdog. This
    pins them together from the outside: the override is what fires, so it is
    what the header must show.
    """
    result, _ = _run_inner(_SLOW_BUT_FINE, bound="37", header=True)
    header = _header_line(result.stdout + result.stderr)

    assert "FALLBACK BOUND ACTIVE" in header, (
        f"an armed fallback did not report itself:\n{header}"
    )
    assert "37s" in header, (
        f"the header advertised a different bound from the one armed, so the "
        f"two can drift again:\n{header}"
    )
    _cleanup_probe_dir()


def test_the_fallback_stands_down_when_pytest_timeout_is_active():
    """With the real plugin loaded, the fallback must not arm.

    The plugin bounds every test AND fails only that test, which this cannot do
    — it can only end the process. Running both would mean the cruder bound
    sometimes fires first and destroys a run the plugin would merely have
    reddened, changing CI's behaviour for the worse in the name of improving a
    container's.

    Driven through the same predicate the header uses, with a stub standing in
    for the plugin manager, because asserting this for real requires an
    environment where ``pytest-timeout`` IS installed — which is exactly the
    environment this test must also pass in when it is NOT.
    """
    import conftest

    class _StubPluginManager:
        def __init__(self, present):
            self._present = present

        def hasplugin(self, name):
            return self._present

        def getplugin(self, name):
            return None

    class _StubConfig:
        def __init__(self, present):
            self.pluginmanager = _StubPluginManager(present)

        def getoption(self, name, default=None):
            return default

    bounded_elsewhere = _StubConfig(present=True)
    conftest._start_suite_watchdog(bounded_elsewhere)
    assert conftest._watchdog(bounded_elsewhere) is None, (
        "the fallback armed alongside pytest-timeout; the weaker bound could "
        "then kill a run the plugin would have merely failed"
    )

    unbounded = _StubConfig(present=False)
    try:
        conftest._start_suite_watchdog(unbounded)
        watchdog = conftest._watchdog(unbounded)
        assert watchdog is not None, (
            "no plugin and no fallback — this is the unbounded agent container "
            "PS-140 was filed about"
        )
    finally:
        found = conftest._watchdog(unbounded)
        if found is not None:
            found.stop()


# --------------------------------------------------------------------------
# 7. If the fallback cannot load, the run says so instead of looking bounded
# --------------------------------------------------------------------------

def test_a_conftest_copied_away_from_the_repo_still_starts(tmp_path):
    """The root conftest must survive being copied somewhere ``tests`` is absent.

    Not hypothetical, and not a style point: ``tests/test_skip_visibility.py``
    copies the real ``conftest.py`` into a throwaway sandbox to exercise its
    hooks for real. A hard ``from tests.suite_watchdog import ...`` at the top
    of that file raised ``ModuleNotFoundError`` before pytest could start and
    took 19 of those tests down with it — measured, not predicted.

    The sandbox is ``tmp_path``, deliberately OUTSIDE the repo. A copy placed
    under ``tests/`` is worse than useless here: pytest would load the real root
    conftest AND the copy, and the run would die registering
    ``--require-capability`` twice — an error about duplicate options, which
    proves nothing about the import this test is actually about.
    """
    sandbox = tmp_path / "away"
    sandbox.mkdir()
    shutil.copy(os.path.join(REPO_ROOT, "conftest.py"), sandbox)
    (sandbox / "test_probe.py").write_text("def test_trivial():\n    assert True\n", encoding="utf-8")

    # Deliberately NOT `-q`: the bound's status is printed by
    # pytest_report_header, and `-q` suppresses the header entirely — so a
    # quiet run cannot answer the second half of this test.
    #
    # `-p no:timeout` for the reason stated at the top of this module: it goes
    # on EVERY inner run. This one was missing it and that reddened all three CI
    # legs — CI installs pytest-timeout, so the sandbox reported the plugin
    # ACTIVE and never reached the NONE branch this test is about, while the
    # same test passed in a plugin-less container. A test that means one thing
    # in CI and another locally is this ticket's own defect wearing a third
    # coat, so the inner run is pinned to the unbounded condition everywhere.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:timeout", "-p", "no:randomly"],
        capture_output=True, text=True, cwd=str(sandbox), timeout=OUTER_LIMIT,
     encoding="utf-8")
    output = result.stdout + result.stderr

    assert result.returncode == 0, (
        f"the conftest could not even load outside the repo:\n{output[-2000:]}"
    )
    # And it degrades HONESTLY: with no fallback importable and no plugin, this
    # run is genuinely unbounded and must SAY so rather than printing the
    # reassuring FALLBACK-ACTIVE line.
    assert "per-test timeout: NONE" in output, (
        f"the copied conftest started but did not report the missing bound:\n"
        f"{output[-2000:]}"
    )
    assert "FALLBACK BOUND ACTIVE" not in output, (
        f"an unbounded run claimed a fallback it does not have:\n{output[-2000:]}"
    )


def test_an_unloadable_fallback_is_reported_and_never_looks_bounded():
    """The one state that must never be dressed up: NOTHING is bounding this run.

    A degraded fallback that failed quietly would be this ticket's own defect
    reproduced one level up — ``timeout = 120`` also "worked" until its
    environment changed, and said nothing. So when the import fails the header
    must say ``NONE`` and warn that a blocking test WILL hang, rather than
    printing the reassuring INERT/FALLBACK-ACTIVE line.
    """
    import conftest

    class _StubPluginManager:
        def hasplugin(self, name):
            return False

    class _StubConfig:
        pluginmanager = _StubPluginManager()

        def getoption(self, name, default=None):
            return default

    original = conftest._WATCHDOG_IMPORT_ERROR
    try:
        conftest._WATCHDOG_IMPORT_ERROR = "No module named 'tests'"
        header = " ".join(conftest.pytest_report_header(_StubConfig()))

        assert "per-test timeout: NONE" in header, header
        assert "WILL hang" in header, header
        # The reassuring line must NOT appear: that is the whole point.
        assert "FALLBACK BOUND ACTIVE" not in header, header
        # And it names the CAUSE, so the reader knows which of the several
        # ways to be unbounded they are looking at.
        assert "No module named 'tests'" in header, header
    finally:
        conftest._WATCHDOG_IMPORT_ERROR = original

    # And with a fallback genuinely ARMED, the run reports it as active.
    #
    # PS-140 round 2 changed what this half is allowed to assert. It used to
    # call the header on a bare config and expect FALLBACK-ACTIVE purely
    # because the IMPORT was healthy — which is the re-derivation that caused
    # the defect: an importable module is not an armed bound, and treating the
    # two as the same fact is how the header came to advertise a bound that
    # PERSONA_SUITE_TIMEOUT=0 had already declined to arm. So the watchdog is
    # actually armed here, and the header is asserted against that.
    armed = _StubConfig()
    conftest._start_suite_watchdog(armed)
    try:
        assert conftest._watchdog(armed) is not None, (
            "nothing armed on a config with no plugin and a healthy import — "
            "this is the unbounded agent container PS-140 was filed about"
        )
        healthy = " ".join(conftest.pytest_report_header(armed))
        assert "FALLBACK BOUND ACTIVE" in healthy, healthy
    finally:
        found = conftest._watchdog(armed)
        if found is not None:
            found.stop()


# --------------------------------------------------------------------------
# 8. The configured bound is READ, not duplicated
# --------------------------------------------------------------------------

def test_the_bound_is_read_from_pyproject_rather_than_hardcoded():
    """The fallback and the plugin must bound at the SAME number.

    ``config.getini("timeout")`` cannot answer here — the key is registered BY
    the plugin, so it raises in the one environment this code exists for. The
    value is therefore parsed from ``pyproject.toml`` directly, and this test
    pins that it tracks the file instead of a constant that goes stale the day
    someone edits the ini.
    """
    import tomllib

    with open(os.path.join(REPO_ROOT, "pyproject.toml"), "rb") as handle:
        declared = tomllib.load(handle)["tool"]["pytest"]["ini_options"]["timeout"]

    assert configured_timeout_s() == float(declared)


def test_the_environment_override_wins_over_the_file(monkeypatch):
    """The escape hatch a developer with a genuinely long run needs.

    Without one, the only way past the bound is deleting it — and a safety net
    that must be deleted to be worked around does not survive contact with a
    deadline.
    """
    monkeypatch.setenv("PERSONA_SUITE_TIMEOUT", "7.5")
    assert configured_timeout_s() == 7.5

    # A value that is not a number falls back rather than crashing the run: a
    # typo in an env var must not be able to prevent the suite from starting.
    monkeypatch.setenv("PERSONA_SUITE_TIMEOUT", "not-a-number")
    assert configured_timeout_s() > 0


def test_marker_timeout_reading_tolerates_junk():
    """A malformed marker yields "no opinion", never an exception.

    This runs on the arming path for every single test, so raising here would
    turn one bad marker into a suite that cannot run at all — strictly worse
    than the hang being prevented.
    """

    class _Marker:
        def __init__(self, args, kwargs=None):
            self.args = args
            self.kwargs = kwargs or {}

    class _Item:
        def __init__(self, marker):
            self._marker = marker

        def get_closest_marker(self, name):
            return self._marker

    assert marker_timeout_s(_Item(None)) is None
    assert marker_timeout_s(_Item(_Marker((30,)))) == 30.0
    assert marker_timeout_s(_Item(_Marker((), {"timeout": 45}))) == 45.0
    assert marker_timeout_s(_Item(_Marker(("banana",)))) is None
    assert marker_timeout_s(_Item(_Marker(()))) is None
