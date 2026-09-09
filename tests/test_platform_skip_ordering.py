"""A PLATFORM skip must be decided BEFORE the test spends a subprocess on it.

PS-381: `test_a_missing_display_is_reported_as_cannot_run_not_as_a_finding`
guarded itself with

    result = subprocess.run([sys.executable, str(driver)], ...)   # minutes
    if not sys.platform.startswith("linux"):
        pytest.skip("the display preflight is linux-only, ...")

which reads correctly and is inert. The preflight it drives is linux-only, so
OFF linux the child does not short-circuit at all — it falls through into the
real harness and launches browsers. The skip is never reached because
pytest-timeout's 120s bound fires first, and a timeout is a FAILURE:

    tests (windows-latest, main)  Failed: Timeout (>120.0s)   run 34398018696
    tests (macos-latest,  main)   Failed: Timeout (>120.0s)   run 34384660379

Two red `main` runs on two different platforms, one root cause, and the
mis-ordering is invisible in review because both statements are individually
right. This file makes the ORDER checkable.

⚠️ WHAT IS AND IS NOT AN OFFENCE, because the naive rule ("no pytest.skip after
a subprocess") has real false positives in this suite and a check that cries
wolf gets deleted:

  * an offence is a skip whose condition is a STATIC PLATFORM FACT —
    `sys.platform`, `os.name`, `platform.system()`. That is knowable at the
    first line of the test, so spawning first can only ever waste the spawn.

  * NOT an offence is a skip whose condition depends on the subprocess's own
    RESULT. `test_unpinned_child_decode_actually_breaks_under_an_ascii_locale`
    skips on `proc.stdout == "NO_ERROR"` and
    `test_prune_defers_when_a_running_name_is_unaccounted_for` skips when its
    helper process never became probeable — neither could be decided earlier,
    and demanding they move would be demanding they guess.

The discriminator is therefore the skip's CONDITION, not its position alone.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

#: Expressions that are decidable before the test does any work. A skip gated
#: on one of these has no reason to sit downstream of a spawn.
_STATIC_PLATFORM_TOKENS = ("sys.platform", "os.name", "platform.system")

#: Callables that start a child process. Anything whose dotted name ends in one
#: of these counts — `subprocess.run`, `subprocess.check_output`,
#: `subprocess.Popen`, a module-local `_run_under_ascii_locale` does not (it is
#: matched via its own body, not here; this stays conservative on purpose).
_SPAWN_ATTRS = ("run", "Popen", "call", "check_call", "check_output")


def _dotted(node: ast.AST) -> str:
    """Best-effort dotted name for a call target, '' when it is not a name."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    else:
        return ""
    return ".".join(reversed(parts))


def _is_spawn(call: ast.Call) -> bool:
    name = _dotted(call.func)
    if not name:
        return False
    head, _, tail = name.rpartition(".")
    return head == "subprocess" and tail in _SPAWN_ATTRS


def _is_pytest_skip(call: ast.Call) -> bool:
    return _dotted(call.func) in ("pytest.skip", "skip")


def _mentions_static_platform(node: ast.AST) -> bool:
    src = ast.unparse(node)
    return any(tok in src for tok in _STATIC_PLATFORM_TOKENS)


def _offences_in(path: Path) -> list[str]:
    """Every `pytest.skip` on a static platform fact that sits after a spawn."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []

    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not func.name.startswith("test_"):
            continue

        first_spawn: int | None = None
        for node in ast.walk(func):
            if isinstance(node, ast.Call) and _is_spawn(node):
                line = node.lineno
                first_spawn = line if first_spawn is None else min(first_spawn, line)
        if first_spawn is None:
            continue

        # A skip is attributed to the `if` that guards it, since that `if` is
        # what carries the condition we are classifying.
        for node in ast.walk(func):
            if not isinstance(node, ast.If):
                continue
            has_skip = any(
                isinstance(c, ast.Call) and _is_pytest_skip(c)
                for stmt in node.body
                for c in ast.walk(stmt)
            )
            if not has_skip:
                continue
            if node.lineno <= first_spawn:
                continue  # correctly ordered — the guard runs first
            if not _mentions_static_platform(node.test):
                continue  # result-dependent skip; could not have been earlier
            found.append(
                f"{path.name}::{func.name} — pytest.skip on a static platform "
                f"fact at line {node.lineno} sits AFTER a subprocess spawn at "
                f"line {first_spawn}. The spawn happens on the very platform "
                f"the test means to skip, and on this suite that spawn can "
                f"outlive pytest-timeout's bound and fail the job."
            )
    return found


def _all_test_files() -> list[Path]:
    return sorted(TESTS_DIR.glob("test_*.py"))


def test_no_platform_skip_is_decided_after_a_subprocess_spawn() -> None:
    """The regression PS-381 fixed, held shut across the whole suite."""
    offences = [o for path in _all_test_files() for o in _offences_in(path)]
    assert not offences, "\n".join(
        ["a platform skip is gated behind work it was supposed to prevent:", *offences]
    )


def test_the_ps381_test_decides_its_platform_before_it_spawns() -> None:
    """The specific test that went red, pinned by name.

    The sweep above would also catch it, but a sweep can be satisfied by
    deleting the test it complains about. This names the venue test and asserts
    the ORDER directly, so removing the guard from that file fails here with
    the ticket's own name attached.
    """
    path = TESTS_DIR / "test_ps336_launch_behaviour_venue.py"
    assert path.is_file(), f"{path} is missing"

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    target = "test_a_missing_display_is_reported_as_cannot_run_not_as_a_finding"
    func = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == target
        ),
        None,
    )
    assert func is not None, f"{target} is gone from {path.name}"

    skip_lines = [
        node.lineno
        for node in ast.walk(func)
        if isinstance(node, ast.Call) and _is_pytest_skip(node)
    ]
    spawn_lines = [
        node.lineno
        for node in ast.walk(func)
        if isinstance(node, ast.Call) and _is_spawn(node)
    ]
    assert skip_lines, (
        f"{target} no longer skips off linux at all — on windows and macOS it "
        "then runs the real launch harness and times out"
    )
    assert spawn_lines, f"{target} no longer spawns the runner; is it still the venue test?"
    assert min(skip_lines) < min(spawn_lines), (
        f"{target} decides its linux-only skip at line {min(skip_lines)}, "
        f"AFTER spawning the runner at line {min(spawn_lines)} — that is the "
        "exact ordering that failed windows-latest and macos-latest on main"
    )


def test_the_detector_fires_on_the_shape_it_is_meant_to_catch(tmp_path) -> None:
    """The control. A check that cannot fail is not coverage (PS-11).

    Written as the pre-fix source verbatim in shape: spawn, then a
    `sys.platform` skip.
    """
    offender = tmp_path / "test_offender.py"
    offender.write_text(
        "import subprocess, sys, pytest\n"
        "def test_thing():\n"
        "    result = subprocess.run([sys.executable, '-c', 'pass'])\n"
        "    if not sys.platform.startswith('linux'):\n"
        "        pytest.skip('linux-only')\n"
        "    assert result.returncode == 0\n",
        encoding="utf-8",
    )
    offences = _offences_in(offender)
    assert len(offences) == 1, f"the detector missed the offending shape: {offences}"
    assert "test_thing" in offences[0]


def test_the_detector_leaves_a_result_dependent_skip_alone(tmp_path) -> None:
    """The false-positive control.

    A skip that reads the subprocess's OUTPUT could not have been decided
    earlier. Flagging it would make this file demand an impossible ordering,
    and this suite has two such tests today.
    """
    innocent = tmp_path / "test_innocent.py"
    innocent.write_text(
        "import subprocess, sys, pytest\n"
        "def test_thing():\n"
        "    proc = subprocess.run([sys.executable, '-c', 'print(1)'], text=True,\n"
        "                          capture_output=True)\n"
        "    if proc.stdout.strip() == 'NO_ERROR':\n"
        "        pytest.skip('this runner could not be shown to break')\n"
        "    assert proc.returncode == 0\n",
        encoding="utf-8",
    )
    assert _offences_in(innocent) == []


@pytest.mark.parametrize(
    "name",
    [
        "test_encoding_discipline.py",
        "test_engine_firefox.py",
    ],
)
def test_the_known_result_dependent_skips_are_not_flagged(name: str) -> None:
    """The two real files whose skips legitimately follow a spawn.

    Named rather than counted so that if one is ever rewritten into the
    offending shape, THIS is the test that says which file changed.
    """
    path = TESTS_DIR / name
    if not path.is_file():
        pytest.skip(f"{name} is not in this tree")
    assert _offences_in(path) == []
