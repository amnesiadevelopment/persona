#!/usr/bin/env python3
"""Audit text decodes whose encoding is platform-dependent (PS-184).

DEFECT CLASS: "a read whose encoding is platform-dependent, against a write
that is not."  An unnamed text encoding resolves via
locale.getpreferredencoding(False) -> utf-8 on Linux/macOS, cp1252 on Windows.
The product names utf-8 on its writes, so an unnamed read disagrees with it,
and the disagreement is invisible until a non-ASCII byte appears.

Line-based grep CANNOT decide this class: a call spans lines, so `encoding=`
routinely sits on a different line from `text=True`.  This walks the AST.

Each hit is reported with its FULL SPAN (lineno, col, end_lineno, end_col).
A start position alone is NOT a unique key: in `Path(x).read_text()` the outer
call and the inner `Path(x)` share a start offset, so keying on it patches both.
"""
import ast, sys
from pathlib import Path

TEXT_METHODS = {"read_text", "write_text"}
SUBPROCESS_FNS = {"run", "check_output", "Popen", "call", "check_call"}
# .open() on these is not a text file read: raw fd, archive, or byte stream.
NON_FILE = {"os", "tarfile", "zipfile", "gzip", "bz2", "lzma", "io", "socket",
            "urllib", "opener", "webbrowser", "shelve", "subprocess", "dbm"}

def _kw(node, name):
    for k in node.keywords:
        if k.arg == name:
            return k
    return None

def _opaque_kwargs(node):
    """True if the call passes **kwargs, so its keywords cannot be enumerated.

    A `**{"encoding": "utf-8"}` entry has arg=None, so the absence of a keyword
    literally NAMED encoding does NOT mean no encoding was passed.  Adding one
    then raises TypeError: got multiple values for keyword argument 'encoding'.
    tests/test_locale_ext.py does exactly this via a shared _UTF8 dict.
    """
    return any(k.arg is None for k in node.keywords)

def _kw_true(node, name):
    k = _kw(node, name)
    return k is not None and isinstance(k.value, ast.Constant) and k.value.value is True

def _span(n):
    return (n.lineno, n.col_offset, n.end_lineno, n.end_col_offset)

def find(path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        if _opaque_kwargs(n):
            continue          # **kwargs: encoding may already be supplied
        has_enc = _kw(n, "encoding") is not None
        fn = n.func
        name = fn.attr if isinstance(fn, ast.Attribute) else (
               fn.id if isinstance(fn, ast.Name) else None)
        if name is None:
            continue

        if name in TEXT_METHODS:
            if not has_enc:
                out.append((*_span(n), f"{name}()", "no encoding= -> locale default"))
            continue

        if name == "open":
            if isinstance(fn, ast.Attribute):
                recv = fn.value
                base = recv.id if isinstance(recv, ast.Name) else (
                       recv.attr if isinstance(recv, ast.Attribute) else None)
                if base in NON_FILE:
                    continue
                # A bare `x.open()` is undecidable from syntax: Path.open()
                # decodes, but src/ui/app.py `ob.open()` opens a DIALOG.
                if not n.args and not n.keywords:
                    out.append((*_span(n), "open()?", "AMBIGUOUS bare x.open() - triage by hand"))
                    continue
                mode_arg = n.args[0] if n.args else None
            else:
                if not n.args:
                    continue
                mode_arg = n.args[1] if len(n.args) > 1 else None
            m = _kw(n, "mode")
            mode = None
            if m is not None and isinstance(m.value, ast.Constant):
                mode = m.value.value
            elif isinstance(mode_arg, ast.Constant) and isinstance(mode_arg.value, str):
                mode = mode_arg.value
            elif mode_arg is not None:
                continue                      # non-literal mode: cannot decide
            if isinstance(mode, str) and "b" in mode:
                continue                      # binary: no decode, not in class
            if not has_enc:
                out.append((*_span(n), "open()", f"text mode ({mode or 'r'}) no encoding="))
            continue

        if name in SUBPROCESS_FNS:
            # Decoding happens ONLY with text=True/universal_newlines=True.
            # capture_output=True ALONE returns bytes -> NOT in the class.
            if (_kw_true(n, "text") or _kw_true(n, "universal_newlines")) and not has_enc:
                out.append((*_span(n), f"subprocess.{name}()", "text=True without encoding="))
    return out

def main(roots):
    total = 0
    for root in roots:
        for p in sorted(Path(root).rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            for lineno, _c, _el, _ec, what, why in find(p):
                print(f"{p}:{lineno}: {what}: {why}")
                total += 1
    print(f"TOTAL: {total}", file=sys.stderr)
    return total

if __name__ == "__main__":
    sys.exit(0 if main(sys.argv[1:] or ["tests"]) == 0 else 1)
