"""AC3 instrument: classify the WHOLE authored message corpus with the SHIPPED
classifier, and print a stable, diffable report.

Corpus = every entry of core.strings.STRINGS + every string LITERAL passed to a
log sink (`log(`, `log_callback(`, `_log(`, `add_log(`) anywhere in src/,
including the literal parts of f-strings (an f-string's interpolations are
un-authored text and cannot be part of a stable corpus, so each placeholder is
rendered as a fixed token).

Run at the merge-base and at HEAD; `diff` the two outputs. Any line that moves
is a behaviour change that must be named and justified.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
SRC = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "src")

SINKS = {"log", "log_callback", "_log", "add_log"}

# The shipped classifier is imported by TEXT, not by `import src.ui.log_console`
# — that module imports flet, and this instrument must run at any commit
# regardless of what is installed. The function body is extracted verbatim and
# exec'd, so what runs here is literally the shipped source.
# `severity` lives in log_console.py before PS-282 and in log_severity.py after,
# so BOTH are searched. Whichever module defines it is the shipped classifier at
# that commit; the point of the diff is that its ANSWERS are unchanged.
console_src = "\n".join(
    (SRC / "ui" / n).read_text(encoding="utf-8")
    for n in ("log_console.py", "log_severity.py")
    if (SRC / "ui" / n).exists()
)
tree = ast.parse(console_src)
ns: dict = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id.startswith("SEV_") for t in node.targets
    ):
        try:
            exec(compile(ast.Module([node], []), "<sev>", "exec"), ns)
        except Exception:
            pass
    if isinstance(node, ast.FunctionDef) and node.name == "severity":
        exec(compile(ast.Module([node], []), "<sev>", "exec"), ns)
severity = ns["severity"]


def _fstring_text(node: ast.JoinedStr) -> str:
    out = []
    for v in node.values:
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            out.append(v.value)
        else:
            out.append("{}")
    return "".join(out)


def _literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return _fstring_text(node)
    return None


def _sink_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


corpus: set[tuple[str, str, str | None]] = set()

# 1. The STRINGS table.
strings_src = (SRC / "core" / "strings.py").read_text(encoding="utf-8")
for m in re.finditer(r'^\s*"([a-z0-9_]+)":\s*"((?:[^"\\]|\\.)*)",?\s*$', strings_src, re.M):
    corpus.add(("STRINGS", m.group(2).encode().decode("unicode_escape"), None))

# 2. Every literal reaching a log sink anywhere in src/.
for path in sorted(SRC.rglob("*.py")):
    if "__pycache__" in path.parts:
        continue
    try:
        mod = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        continue
    rel = path.relative_to(SRC.parent).as_posix()
    for node in ast.walk(mod):
        if not isinstance(node, ast.Call):
            continue
        if _sink_name(node.func) not in SINKS or not node.args:
            continue
        arg = node.args[0]
        # A CONVERTED site: `log(declare("...", SEV_FAIL))`. Report the value
        # the site DECLARES, marked, so the diff shows the conversion as one
        # changed line rather than as a message that vanished from the corpus.
        declared = None
        if isinstance(arg, ast.Call) and _sink_name(arg.func) == "declare":
            declared = _sink_name(arg.args[1]) if len(arg.args) > 1 else "?"
            arg = arg.args[0]
        text = _literal(arg)
        if text is None:
            continue
        corpus.add((rel, text, declared))

# LOCATION IS DELIBERATELY NOT PRINTED. Every line in app.py moves when an
# import is added, and a diff full of renumbered-but-identical rows hides the
# handful of rows that genuinely changed. What this instrument asserts is that
# the CLASSIFICATION of each authored message is unchanged, so the message and
# its verdict are the whole record.
seen = set()
for where, text, declared in sorted(corpus, key=lambda p: (p[1], p[0])):
    verdict = f"DECLARED:{declared}" if declared else severity(text)
    row = f"{verdict:14} | {text!r}"
    if row in seen:
        continue
    seen.add(row)
    print(row)
print(f"# corpus size: {len(corpus)}", file=sys.stderr)
