"""PS-360 — the process-global mutations in the firefox launch child must be
guarded on ``in_thread``, and that set must be DERIVED rather than remembered.

## Why this file exists

``invisible_launch._child`` / ``_launch_and_watch`` run as a forked PROCESS on
Linux and as a THREAD of the manager process otherwise — and on Linux too when
``in_process=True`` forces it (``verify/baseline.py``'s recorder). A fork has
its own memory, so mutating process-global state there touches nothing but the
child. On the thread path the same statement mutates *persona itself* and every
concurrently-open profile.

Four such mutations shipped with a ``not in_thread`` guard. The fifth —
``os.environ["MOZ_APP_REMOTINGNAME"]`` — did not, for two months, and nothing
could have noticed: there was no artefact anywhere that said how many there
were supposed to be. A comment saying "guard these" is a rule someone has to
remember; this file is a check that fires.

⭐ **The point is the DERIVATION, not the count.** The gate walks the two
functions and finds the mutations itself, so a SIXTH one cannot arrive
unguarded — and cannot arrive unnoticed even if it is guarded correctly, since
the census test below pins the number and names them.

## ⛔ What this gate can and cannot see — READ THIS BEFORE TRUSTING IT

Its input domain is ``_PROCESS_GLOBAL_OPERATORS`` below, an EXPLICIT list. That
list is the one remembered thing here and it is deliberately visible rather
than buried in a matcher, because the whole argument for this file dies if the
operator set is itself an invisible remembered list.

**Forms it WILL catch** are exactly those enumerated below. **Forms it will
NOT catch**, stated so nobody reads a green run as more than it is:

* an aliased operator — ``from os import chdir`` then ``chdir(...)``, or
  ``_env = os.environ`` then ``_env[...] = ...``
* a mutation reached through a helper this file does not name (the four named
  helpers below are named individually for that reason — the gate cannot see
  inside ``env_policy``)
* a mutation inside a nested function or lambda whose guard is established by
  its CALL SITE rather than lexically (see ``_guarding_tests`` — containment is
  lexical, so a mutation inside a nested def is reported at its own position
  and is not credited with an enclosing ``if``)
* anything reached via ``exec``/``setattr``/``globals()``

Adding an operator to the list is the intended way to widen the gate.

## Why the guard test follows TAINT rather than direct containment

The four shipped guards are NOT four instances of one pattern — they are three
shapes:

===========  =================================================  ==============
line shape   example                                            how it guards
===========  =================================================  ==============
direct       ``if not in_thread:``                              containment
direct+plat  ``if not in_thread and _platform.IS_LINUX:``       containment
computed     ``_pin_tmpdir_here = not in_thread and IS_LINUX``  a local, read
                                                                far below
===========  =================================================  ==============

A gate matching only "sits inside an ``if`` mentioning ``in_thread``" reports
the two COMPUTED-boolean guards as violations. That is not a tuning detail: the
cheapest repair for such false positives is to allowlist the two lines, at
which point the gate stops watching the very guards it exists to protect. So
the gate follows locals whose value derives from ``in_thread`` to a fixpoint
and accepts an ``if`` testing any of them.
"""

import ast
import pathlib

import pytest

LAUNCH_FILE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "services"
    / "browser"
    / "invisible_launch.py"
)

# The two functions that run as a fork on one platform and a thread on another.
# This is the perimeter: a process-global mutation ANYWHERE ELSE in the module
# is a different question, because it does not straddle that split.
GUARDED_FUNCTIONS = ("_child", "_launch_and_watch")

# The parameter that says which of the two we are.
GUARD_PARAM = "in_thread"

# The commit this file's subject shipped unguarded at — the input to the
# strongest falsification below, which runs the gate against the real defect
# rather than a planted one. Pinned to a SHA rather than to `origin/main` so
# the test keeps measuring the same thing after this branch merges.
_PRE_FIX_REV = "360c4881fcf8145340f6fdf998c00be41eb97681"


# ---------------------------------------------------------------------------
# THE OPERATOR SET — the one remembered list in this file, kept visible.
#
# Each entry is something that mutates state belonging to the PROCESS rather
# than to a value the caller owns. Enumerated deliberately rather than probed:
# `os.environ[...] = ...` alone would have caught PS-360's subject and missed
# `os.environ.update(...)` writing the same variable the next time.
# ---------------------------------------------------------------------------

# Subscript assignment / deletion on the process environment.
_ENVIRON_SUBSCRIPT_BASES = ("os.environ",)

# Method calls that mutate a mapping in place, applied to os.environ.
_ENVIRON_MUTATING_METHODS = ("update", "setdefault", "pop", "clear")

# Bare callables that change process-global state directly.
_PROCESS_GLOBAL_CALLS = (
    "os.chdir",
    "os.putenv",
    "os.unsetenv",
    "os.umask",
    "os.setsid",
    "os.setuid",
    "os.setgid",
    "os.nice",
    "signal.signal",
    "signal.setitimer",
    "locale.setlocale",
    "sys.setrecursionlimit",
    # This module's own named helpers. Each mutates the CURRENT process, which
    # the gate cannot see by reading this file — hence naming them one by one.
    # `env_policy` marks them for exactly this: the "current_process" in each
    # name is the warning.
    "scrub_current_process_environ",
    "pin_current_process_tmpdir",
    "chdir_current_process",
    "start_own_session",
)

# ⛔ DELIBERATELY EXCLUDED, with the reason, so an omission is a decision that
# can be argued with rather than a gap that looks like one:
#
#   register_ff_eval / unregister_ff_eval  — they mutate the module-level
#   `_ff_eval_registry`, which IS process-global, but it is keyed per profile
#   and is a different property (PS-360 puts it out of scope explicitly, and a
#   probe there found no defect). Guarding them on `in_thread` would be wrong:
#   the thread path is precisely the path whose whole purpose is that the
#   parent can see the hook.
#
#   sys.path mutation — none exists in these two functions today, and adding it
#   speculatively would make the gate assert about code that is not there.
_EXCLUDED_WITH_REASON = ("register_ff_eval", "unregister_ff_eval")


def _dotted(node):
    """``os.environ`` / ``signal.signal`` as a dotted string, else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _describe(node):
    """What kind of process-global mutation this node is, or None."""
    # os.environ["X"] = ... / del os.environ["X"]
    if isinstance(node, (ast.Assign, ast.AugAssign, ast.Delete)):
        targets = (
            node.targets
            if isinstance(node, (ast.Assign, ast.Delete))
            else [node.target]
        )
        for t in targets:
            if isinstance(t, ast.Subscript):
                base = _dotted(t.value)
                if base in _ENVIRON_SUBSCRIPT_BASES:
                    verb = "del" if isinstance(node, ast.Delete) else "="
                    return f"{base}[...] {verb}"
        return None
    if isinstance(node, ast.Call):
        dotted = _dotted(node.func)
        if dotted is None:
            return None
        if dotted in _PROCESS_GLOBAL_CALLS:
            return f"{dotted}()"
        for base in _ENVIRON_SUBSCRIPT_BASES:
            for meth in _ENVIRON_MUTATING_METHODS:
                if dotted == f"{base}.{meth}":
                    return f"{dotted}()"
    return None


def _tainted_locals(fn):
    """Locals in ``fn`` whose value derives from ``in_thread``, to a fixpoint.

    ``_pin_tmpdir_here = not in_thread and IS_LINUX`` makes ``_pin_tmpdir_here``
    a legitimate guard, and ``_child_cwd = ... if _apply_child_cwd else None``
    makes ``_child_cwd`` one in turn — hence the fixpoint rather than one pass.
    """
    tainted = {GUARD_PARAM}
    for _ in range(5):
        grew = False
        for node in ast.walk(fn):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if value is None:
                continue
            mentioned = {
                n.id for n in ast.walk(value) if isinstance(n, ast.Name)
            }
            if not (mentioned & tainted):
                continue
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            for t in targets:
                if isinstance(t, ast.Name) and t.id not in tainted:
                    tainted.add(t.id)
                    grew = True
        if not grew:
            break
    return tainted - {GUARD_PARAM}


def _guarding_tests(fn):
    """Map each statement's id() to the ``if``/``while`` tests enclosing it.

    Lexical containment only — see the module docstring's list of what this
    gate cannot see.
    """
    enclosing = {}

    def walk(body, tests):
        for stmt in body:
            enclosing[id(stmt)] = tests
            if isinstance(stmt, (ast.If, ast.While)):
                walk(stmt.body, tests + [stmt.test])
                walk(stmt.orelse, tests)
            elif isinstance(stmt, (ast.For, ast.AsyncFor)):
                walk(stmt.body, tests)
                walk(stmt.orelse, tests)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                walk(stmt.body, tests)
            elif isinstance(stmt, ast.Try):
                for part in (
                    stmt.body,
                    stmt.orelse,
                    stmt.finalbody,
                    *[h.body for h in stmt.handlers],
                ):
                    walk(part, tests)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # A nested def's guard is established by its call site, not
                # lexically. Do NOT credit it with the enclosing tests.
                walk(stmt.body, [])

    walk(fn.body, [])
    return enclosing


def _statement_of(fn, node, enclosing):
    """The outermost statement of ``fn`` that contains ``node``."""
    for stmt in ast.walk(fn):
        if id(stmt) not in enclosing:
            continue
        if node is stmt:
            return stmt
        for child in ast.walk(stmt):
            if child is node:
                # Prefer the innermost statement carrying this node.
                inner = [
                    s
                    for s in ast.walk(stmt)
                    if id(s) in enclosing
                    and s is not stmt
                    and any(c is node for c in ast.walk(s))
                ]
                return inner[-1] if inner else stmt
    return None


def _scan(source):
    """Return (guarded, unguarded) lists of (function, line, description).

    Both lists are ordered by function name then by LINE, so a census reads
    top-to-bottom through the source it describes.
    """
    tree = ast.parse(source)
    guarded, unguarded = [], []
    for fn in ast.walk(tree):
        if not (
            isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
            and fn.name in GUARDED_FUNCTIONS
        ):
            continue
        tainted = _tainted_locals(fn)
        accept = tainted | {GUARD_PARAM}
        enclosing = _guarding_tests(fn)
        for node in ast.walk(fn):
            what = _describe(node)
            if what is None:
                continue
            stmt = _statement_of(fn, node, enclosing)
            tests = enclosing.get(id(stmt), []) if stmt is not None else []
            covered = any(
                any(
                    isinstance(n, ast.Name) and n.id in accept
                    for n in ast.walk(test)
                )
                for test in tests
            )
            row = (fn.name, node.lineno, what)
            (guarded if covered else unguarded).append(row)
    return sorted(set(guarded)), sorted(set(unguarded))


@pytest.fixture(scope="module")
def launch_source():
    return LAUNCH_FILE.read_text(encoding="utf-8")


def test_every_process_global_mutation_is_guarded_on_in_thread(launch_source):
    """THE GATE. Not "the five known lines are guarded" — "whatever is there
    is guarded", so a sixth cannot arrive unguarded.
    """
    _guarded, unguarded = _scan(launch_source)
    assert not unguarded, (
        "process-global mutation(s) in the firefox launch child are NOT "
        "guarded on `in_thread`. On the thread path (Windows/macOS, and Linux "
        "under in_process=True) these mutate persona's OWN process and every "
        "concurrently-open profile:\n"
        + "\n".join(
            f"  {fn}  line {line}  {what}" for fn, line, what in unguarded
        )
    )


def test_the_guarded_mutation_census_is_what_this_file_claims(launch_source):
    """The gate above passes vacuously if it finds nothing. This says what it
    found, so a mutation that DISAPPEARS (or a sixth that arrives correctly
    guarded) is also a visible event and not a silent one.

    ⚠️ Update this list when the set legitimately changes — that edit is the
    point, not an inconvenience: it puts a human on the change.

    ⭐ NOTE THE COUNT: this census is SIX, and PS-360's brief said five. The
    sixth is ``signal.signal()`` in ``_launch_and_watch`` — a SIGTERM handler,
    which is process-global state exactly like an environment variable and is
    settable only on the main thread. It is correctly guarded on
    ``not in_thread`` and always was; the brief's AST walk simply did not have
    it in its operator set. That is the derivation earning its keep on its
    first run: a remembered list of five would have been wrong the day it was
    written, and would have gone on being wrong silently.
    """
    guarded, unguarded = _scan(launch_source)
    assert not unguarded
    # Ordered as `_scan` returns them: by function name, then by LINE — so
    # this reads top-to-bottom through the file, which is how a reviewer
    # checks it against the source.
    assert [(fn, what) for fn, _line, what in guarded] == [
        ("_child", "start_own_session()"),
        ("_child", "scrub_current_process_environ()"),
        ("_child", "pin_current_process_tmpdir()"),
        ("_child", "chdir_current_process()"),
        ("_launch_and_watch", "os.environ[...] ="),
        ("_launch_and_watch", "signal.signal()"),
    ], (
        "the process-global mutation census changed:\n"
        + "\n".join(f"  {fn}  line {line}  {what}" for fn, line, what in guarded)
    )


# ---------------------------------------------------------------------------
# THE GATE'S OWN FALSIFICATION.
#
# A gate only ever seen to pass is indistinguishable from a broken one, and
# ARM A alone is satisfied by a gate that flags every mutation unconditionally
# — which would be red on main forever. Both arms, always.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sibling_line_fragment,shape",
    [
        # A DIRECT-`if` guard...
        ("if not in_thread and _platform.IS_LINUX:", "direct if"),
        # ...and a COMPUTED-boolean one. These are different code paths in the
        # gate (containment vs taint), and a gate proven on one is not proven
        # on the other.
        ("_pin_tmpdir_here = not in_thread and _platform.IS_LINUX", "taint"),
    ],
)
def test_gate_goes_red_when_a_sibling_guard_is_removed(
    launch_source, sibling_line_fragment, shape
):
    """ARM A — remove a real guard term, confirm RED, and confirm the report
    NAMES the line that lost it."""
    assert sibling_line_fragment in launch_source, (
        f"the {shape} sibling guard is no longer in the file as written; "
        "this falsification is measuring nothing"
    )
    sabotaged_fragment = sibling_line_fragment.replace("not in_thread and ", "")
    sabotaged = launch_source.replace(sibling_line_fragment, sabotaged_fragment)
    assert sabotaged != launch_source

    # Which line lost its guard, so we can assert the report names IT.
    victim_line = next(
        i
        for i, text in enumerate(launch_source.splitlines(), start=1)
        if sibling_line_fragment in text
    )

    _guarded, unguarded = _scan(sabotaged)
    named = {line for _fn, line, _what in unguarded}
    if shape == "direct if":
        # The mutation the `if` was guarding is now bare; it sits below the
        # test, so assert the gate names a line the pristine scan did not.
        _pristine_g, pristine_u = _scan(launch_source)
        assert named - {ln for _f, ln, _w in pristine_u}, (
            "removing a direct-`if` guard produced no new violation — the "
            "gate is not watching that shape"
        )
    else:
        # The computed boolean is consumed far below; the newly-bare mutation
        # is the one that reads it.
        _pristine_g, pristine_u = _scan(launch_source)
        new = named - {ln for _f, ln, _w in pristine_u}
        assert new, (
            f"removing the taint-path guard at line {victim_line} produced no "
            "new violation — the gate is not following computed booleans"
        )


def test_gate_stays_quiet_about_a_correctly_guarded_sixth_mutation(launch_source):
    """ARM B — plant a sixth mutation that IS correctly guarded and confirm the
    gate does not flag it. Without this, a gate that fires on every environ
    write would pass ARM A while being useless.
    """
    anchor = "    profile_dir = cfg.get(\"profile_dir\", \"\")"
    assert anchor in launch_source, "the anchor for the planted mutation moved"
    planted = launch_source.replace(
        anchor,
        "    if not in_thread:\n"
        "        os.environ[\"PS360_SYNTHETIC_GUARDED\"] = \"1\"\n" + anchor,
        1,
    )
    _guarded, unguarded = _scan(planted)
    assert not unguarded, (
        "the gate flagged a CORRECTLY GUARDED mutation — it is firing on the "
        f"presence of a mutation rather than on its guard: {unguarded}"
    )
    # ...and it did see it, rather than passing because it saw nothing.
    guarded_lines = {what for _fn, _line, what in _guarded}
    assert "os.environ[...] =" in guarded_lines


def test_gate_catches_an_unguarded_sixth_mutation(launch_source):
    """ARM A, the other direction — plant a sixth mutation with NO guard and
    confirm the gate names it at its own line."""
    anchor = "    profile_dir = cfg.get(\"profile_dir\", \"\")"
    planted = launch_source.replace(
        anchor, "    os.environ[\"PS360_SYNTHETIC_BARE\"] = \"1\"\n" + anchor, 1
    )
    planted_line = next(
        i
        for i, text in enumerate(planted.splitlines(), start=1)
        if "PS360_SYNTHETIC_BARE" in text
    )
    _guarded, unguarded = _scan(planted)
    assert planted_line in {line for _fn, line, _what in unguarded}, (
        f"the gate did not name the unguarded planted mutation at line "
        f"{planted_line}: {unguarded}"
    )


def test_gate_catches_the_non_subscript_environ_forms(launch_source):
    """The blind spot the researcher pre-work named: a subscript-only matcher
    lets `os.environ.update(...)` write the same variable and pass. Each
    enumerated form is exercised, so the operator list is a tested claim rather
    than a hopeful one.
    """
    anchor = "    profile_dir = cfg.get(\"profile_dir\", \"\")"
    for snippet in (
        '    os.environ.update({"PS360": "1"})',
        '    os.environ.setdefault("PS360", "1")',
        '    os.environ.pop("PS360", None)',
        '    del os.environ["PS360"]',
        '    os.putenv("PS360", "1")',
        '    os.chdir("/tmp")',
        '    os.umask(0)',
    ):
        planted = launch_source.replace(anchor, snippet + "\n" + anchor, 1)
        _guarded, unguarded = _scan(planted)
        assert unguarded, f"the gate did not see {snippet.strip()!r}"


def test_gate_names_the_real_pre_fix_defect(launch_source):
    """⭐ THE STRONGEST FALSIFICATION AVAILABLE, and it needs no synthetic
    sabotage at all: run this gate against the ACTUAL SOURCE AS IT SHIPPED
    BEFORE PS-360, and confirm it names the real defect at its real line.

    Every other falsification here plants a mutation and checks the gate sees
    it — which proves the gate reacts to something the test itself wrote. This
    one proves it would have caught the historical bug, which is the only
    claim that matters.

    Skipped rather than failed when the pre-fix blob cannot be reached (a
    shallow clone, an exported tree, no git). An absence declared out loud,
    per tests/KNOWN_SKIPS.md.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "show", f"{_PRE_FIX_REV}:src/services/browser/invisible_launch.py"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(LAUNCH_FILE.parents[3]),
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        pytest.skip(f"git unavailable for the pre-fix blob: {exc}")
    if result.returncode != 0 or not result.stdout:  # pragma: no cover
        pytest.skip(
            f"the pre-fix revision {_PRE_FIX_REV} is not in this checkout "
            "(shallow clone or exported tree)"
        )

    pre_fix = result.stdout
    assert 'os.environ["MOZ_APP_REMOTINGNAME"]' in pre_fix
    _guarded, unguarded = _scan(pre_fix)

    offenders = [
        (fn, line, what)
        for fn, line, what in unguarded
        if fn == "_launch_and_watch" and what == "os.environ[...] ="
    ]
    assert offenders, (
        "the gate did NOT flag the pre-fix MOZ_APP_REMOTINGNAME write. It "
        "would not have caught the bug it was written for."
    )
    # ...and it names the right LINE, not merely the right function.
    expected_line = next(
        i
        for i, text in enumerate(pre_fix.splitlines(), start=1)
        if 'os.environ["MOZ_APP_REMOTINGNAME"]' in text
    )
    assert offenders[0][1] == expected_line

    # The four siblings that were ALREADY guarded must NOT be among the
    # violations — AC5's control. A gate that also accused them would be
    # repaired by allowlisting them, at which point it stops watching them.
    assert len(unguarded) == 1, (
        "the gate accused a correctly-guarded sibling on the pre-fix source: "
        f"{unguarded}"
    )


def test_the_remoting_name_write_is_the_line_this_ticket_guarded(launch_source):
    tree = ast.parse(launch_source)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_launch_and_watch"
    )
    writes = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Subscript)
            and _dotted(t.value) == "os.environ"
            and isinstance(t.slice, ast.Constant)
            and t.slice.value == "MOZ_APP_REMOTINGNAME"
            for t in node.targets
        )
    ]
    assert len(writes) == 1, "MOZ_APP_REMOTINGNAME is no longer written once"
    enclosing = _guarding_tests(fn)
    stmt = _statement_of(fn, writes[0], enclosing)
    tests = enclosing.get(id(stmt), [])
    assert any(
        any(isinstance(n, ast.Name) and n.id == GUARD_PARAM for n in ast.walk(t))
        for t in tests
    ), (
        "the MOZ_APP_REMOTINGNAME write lost its `in_thread` guard — on the "
        "thread path that write lands in persona's OWN environ, is on no "
        "scrub list, and is never cleared (PS-360)"
    )
