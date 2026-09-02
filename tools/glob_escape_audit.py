#!/usr/bin/env python3
"""Audit globs whose DIRECTORY half is operator-derived and unescaped (PS-265).

DEFECT CLASS: "a glob pattern built by joining a directory the operator controls
onto a metacharacter-bearing filename half, without glob.escape() on the
directory."

glob interprets metacharacters across the WHOLE pattern, the directory portion
included.  A directory named `Apps[old]` makes `[old]` a character class, so the
pattern names a path that does not exist, glob.glob returns [], and the calling
loop's body never runs.  There is no exception, no empty-result branch and no
signal of any kind — the sweep/scan reports success while doing nothing.  `[`
and `]` are legal on POSIX and Windows alike, and hand-named portable
directories are exactly where an overridden home or TMPDIR comes from.

This repo has fixed the SAME one-word defect four times (19e7f82 -> cbfefc8 ->
PS-227's two sites -> PS-265's three arms).  Each fix landed only where its
ticket pointed.  This file exists so the fifth instance fails the suite instead.

WHY IT IS AN AST WALK AND NOT A GREP — the load-bearing part:

  A grep for `glob.glob(os.path.join(` finds NONE of the four known sites,
  because all four assign the pattern to a local first:

      pattern = os.path.join(log_dir, "persona_*.log")
      for path in glob.glob(pattern):

  So the detector resolves the call's argument back to its assignment WITHIN THE
  SAME FUNCTION.  A function may assign the name several times (updater.py's
  `_clear_stale_staged` has one assignment per OS arm); every assignment is
  classified, so one fixed arm cannot hide a broken one.

WHAT COUNTS AS SAFE:

  * a pattern built entirely from string literals — nothing operator-derived,
    so there is nothing to escape;
  * an os.path.join whose every non-literal component is wrapped in
    glob.escape(...);
  * a whole pattern that is itself glob.escape(...).

Anything else — a bare name, an f-string interpolating a value, a call — is
operator-derived and unescaped, and is reported.

WHAT THIS DOES NOT MODEL (stated rather than left to be discovered):

  * resolution is SAME-FUNCTION and ASSIGNMENT-ONLY.  A pattern computed in one
    function and globbed in another, or arriving as a parameter, is reported as
    UNRESOLVED and does not fire.
  * string CONCATENATION (`dir + "/*.log"`) and %-formatting are not classified
    as joins; a concatenation of non-literals is reported by the fallback rule,
    but its component structure is not analysed.
  * Path.glob / Path.rglob are not modeled at all.  There are zero occurrences
    in src/ today; the gap is named here instead of built for.

Read a clean report as "no MODELED unescaped operator-derived glob", not as
"no conceivable one".
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# The glob entry points this detector decides.  Path.glob/rglob deliberately
# absent — see the docstring's boundary statement.
GLOB_FNS = {"glob", "iglob"}


def _is_glob_call(node: ast.Call) -> bool:
    """glob.glob(...) / glob.iglob(...) / a bare glob(...)/iglob(...) import."""
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr in GLOB_FNS
    if isinstance(fn, ast.Name):
        return fn.id in GLOB_FNS
    return False


def _is_escape_call(node: ast.AST) -> bool:
    """glob.escape(...) — or a bare escape(...) from `from glob import escape`."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr == "escape"
    return isinstance(fn, ast.Name) and fn.id == "escape"


def _is_join_call(node: ast.AST) -> bool:
    """os.path.join(...) / a bare join(...)."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr == "join"
    return isinstance(fn, ast.Name) and fn.id == "join"


def _literal_only(node: ast.AST) -> bool:
    """True when the expression's value comes from string literals alone."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.JoinedStr):
        return all(isinstance(v, ast.Constant) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal_only(node.left) and _literal_only(node.right)
    if _is_join_call(node):
        return all(_literal_only(a) for a in node.args) and not node.keywords
    return False


def _safe(node: ast.AST) -> bool:
    """True when this expression cannot carry an unescaped operator directory."""
    if _literal_only(node):
        return True
    if _is_escape_call(node):
        return True
    if _is_join_call(node):
        # Every component must be either a literal or individually escaped.
        # Starred/keyword forms are not classifiable — treat as unsafe so an
        # exotic construction is looked at rather than waved through.
        if node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
            return False
        return all(_safe(a) for a in node.args)
    return False


def _describe(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - unparse is total on parsed trees
        return "<expr>"


def _owning_scopes(tree: ast.Module) -> dict[int, ast.AST]:
    """{id(glob call) -> innermost function (or Module) containing it}."""
    owner: dict[int, ast.AST] = {}

    def walk(node: ast.AST, scope: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            inner = (
                child
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else scope
            )
            if isinstance(child, ast.Call) and _is_glob_call(child):
                owner[id(child)] = inner
            walk(child, inner)

    walk(tree, tree)
    return owner


def _assignments_to(scope: ast.AST, name: str) -> list[ast.AST]:
    """Every value assigned to `name` in this scope, excluding nested functions.

    Nested definitions are skipped so a helper's local `pattern` is not
    attributed to its enclosing function's glob call.
    """
    values: list[ast.AST] = []

    def visit(node: ast.AST, *, top: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if not top and isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            ):
                continue
            if isinstance(child, ast.Assign):
                if any(
                    isinstance(t, ast.Name) and t.id == name for t in child.targets
                ):
                    values.append(child.value)
            elif isinstance(child, ast.AnnAssign):
                if (
                    isinstance(child.target, ast.Name)
                    and child.target.id == name
                    and child.value is not None
                ):
                    values.append(child.value)
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            visit(child, top=False)

    visit(scope, top=True)
    return values


def find(path: Path) -> list[tuple[int, str, str]]:
    """Every decided glob site in `path`, as (lineno, verdict, description).

    Verdicts: "MISS"       — operator-derived directory, not escaped
              "OK"         — literal-only or escaped
              "UNRESOLVED" — outside the model (see the module docstring)
    """
    try:
        tree = ast.parse(path.read_bytes(), filename=str(path))
    except SyntaxError:
        return []

    # Map each glob call to the innermost function that contains it.
    owner = _owning_scopes(tree)

    results: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_glob_call(node)):
            continue
        if not node.args:
            continue
        arg = node.args[0]

        if isinstance(arg, ast.Name):
            scope = owner.get(id(node), tree)
            values = _assignments_to(scope, arg.id)
            if not values:
                results.append(
                    (node.lineno, "UNRESOLVED", f"{arg.id} (no assignment in scope)")
                )
                continue
            for value in values:
                verdict = "OK" if _safe(value) else "MISS"
                results.append((value.lineno, verdict, _describe(value)))
        else:
            verdict = "OK" if _safe(arg) else "MISS"
            results.append((node.lineno, verdict, _describe(arg)))
    return results


def scan(root: Path) -> list[tuple[str, int, str, str]]:
    hits: list[tuple[str, int, str, str]] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for lineno, verdict, what in find(path):
            hits.append((path.as_posix(), lineno, verdict, what))
    return hits


if __name__ == "__main__":  # pragma: no cover - developer convenience
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "src")
    for p, ln, verdict, what in scan(target):
        print(f"{verdict:<10} {p}:{ln}  {what}")
