#!/usr/bin/env python3
"""Audit text decodes whose encoding is platform-dependent (PS-184).

DEFECT CLASS: "a read whose encoding is platform-dependent, against a write
that is not."  An unnamed text encoding resolves via
locale.getpreferredencoding(False) -> utf-8 on Linux/macOS, cp1252 on Windows.
The product names utf-8 on its writes, so an unnamed read disagrees with it,
and the disagreement is invisible until a non-ASCII byte appears.

WHERE THE SCOPE OF THIS FILE COMES FROM -- this is the load-bearing part, and
the first version of this tool got it wrong.

The shapes below are NOT a list assembled from the instances we happened to
find.  A list shaped from known instances finds the known instances; that is
the PS-176 failure mode, one level up from a narrow grep.  They are the
shapes CPython ITSELF defines as this defect class, arrived at two ways that
agree:

  1. PEP 597.  `python -X warn_default_encoding` makes the interpreter emit an
     EncodingWarning at every text decode that fell back to the locale.  That
     is an EXTERNALLY OWNED definition of the class -- CPython's, not ours --
     and it fires on exactly the shapes handled here and stays silent on every
     correct form (encoding= named, "rb"/"wb" binary mode).

  2. Introspection.  Intersecting {stdlib callables with an `encoding=None`
     parameter} against {names actually called in tests/} yields a candidate
     set mechanically, with no human deciding what belongs in it.

`tests/test_encoding_discipline.py` re-derives (2) at test time and asserts
this file models everything in it.  So an unmodeled shape fails the suite --
the guard covers new SHAPES, not merely new instances of known shapes.

ONE KNOWN LIMIT OF THE DERIVATION, stated rather than papered over:
subprocess.run/check_output/call/check_call forward **kwargs to Popen, so
introspection cannot see an `encoding` parameter on them.  They are modeled
explicitly below and pinned by a test, because derivation (2) structurally
cannot discover them.  Derivation (1) does see them.

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
# Always text, never binary: constructing one without an encoding is in class.
ALWAYS_TEXT = {"TextIOWrapper"}
# Default to BINARY (mode="w+b"), so only in class when a text mode is named.
TEMPFILE_FNS = {"NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile"}
# Config parsers read a file on the caller's behalf under the locale codec.
CONFIGPARSER_CLASSES = {"ConfigParser", "RawConfigParser", "SafeConfigParser"}
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


def _opaque_text_indicator(node):
    """A POSITIVE text-decode indicator on a call we cannot fully enumerate.

    PS-211.  `_opaque_kwargs` is a sound reason not to report a call as a
    CONFIRMED offender -- but the first version of this tool turned that into a
    bare `continue`, which DROPPED the call from the report entirely.  That is
    an unsound silence, and it cost exactly the sites that mattered most:

        subprocess.check_output([... powershell.exe ...],
                                text=True, **_platform.no_window_kwargs())

    Both Windows-only PowerShell sites in invisible_launch.py -- the two whose
    stdout embeds a profile path and is decoded as cp1252 on the one platform
    where that is the locale default -- were invisible to this tool for that
    reason.  PS-184's file-level freeze was therefore covering call sites its
    own detector could not see, and a sweep run with it would have reported
    5 of 7 and called the file clean at 0.

    `text=True` is a POSITIVE assertion that this call decodes.  The **kwargs
    makes the ENCODING undecidable, not the DECODING.  So the honest verdict is
    AMBIGUOUS -- hand-triage this -- never silence.  Ambiguous hits are excluded
    from the ratchet in tests/test_encoding_discipline.py (`_scan` drops them),
    so this reports without spuriously failing the suite.
    """
    return _kw_true(node, "text") or _kw_true(node, "universal_newlines")


def _kw_true(node, name):
    k = _kw(node, name)
    return k is not None and isinstance(k.value, ast.Constant) and k.value.value is True


def _span(n):
    return (n.lineno, n.col_offset, n.end_lineno, n.end_col_offset)


def _literal_mode(node, positional):
    """The mode string if it is a literal, None if absent, False if undecidable."""
    m = _kw(node, "mode")
    if m is not None:
        return m.value.value if isinstance(m.value, ast.Constant) else False
    if positional is None:
        return None
    if isinstance(positional, ast.Constant) and isinstance(positional.value, str):
        return positional.value
    return False


def _configparser_names(tree):
    """Local names bound to a config parser, so `parser.read(p)` is decidable.

    `read` is far too common a method name to match on the name alone -- every
    file handle in the repo has one.  So a `.read()` call is only in class when
    its receiver is KNOWN to be a parser: either constructed inline
    (`ConfigParser().read(p)`) or assigned to a name in this module.
    """
    names = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Assign) or not isinstance(n.value, ast.Call):
            continue
        f = n.value.func
        ctor = f.attr if isinstance(f, ast.Attribute) else (
               f.id if isinstance(f, ast.Name) else None)
        if ctor in CONFIGPARSER_CLASSES:
            for t in n.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def _is_parser_receiver(fn, parser_names):
    recv = fn.value
    if isinstance(recv, ast.Name) and recv.id in parser_names:
        return True
    if isinstance(recv, ast.Call):                      # ConfigParser().read(p)
        rf = recv.func
        ctor = rf.attr if isinstance(rf, ast.Attribute) else (
               rf.id if isinstance(rf, ast.Name) else None)
        return ctor in CONFIGPARSER_CLASSES
    return False


def find(path):
    try:
        tree = ast.parse(path.read_bytes(), filename=str(path))
    except SyntaxError:
        return []
    parser_names = _configparser_names(tree)
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        if _opaque_kwargs(n):
            # **kwargs: encoding MAY already be supplied, so this cannot be
            # reported as a confirmed offender.  But PS-211: do not fall
            # silent either.  A `text=True` on the same call is a positive
            # assertion that it DECODES, so report it as AMBIGUOUS for hand
            # triage rather than dropping it from the report entirely.
            if _opaque_text_indicator(n):
                fn = n.func
                nm = fn.attr if isinstance(fn, ast.Attribute) else (
                     fn.id if isinstance(fn, ast.Name) else None)
                if nm in SUBPROCESS_FNS:
                    out.append((*_span(n), f"subprocess.{nm}()?",
                                "AMBIGUOUS text=True with **kwargs - "
                                "triage by hand"))
            continue
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

        if name in ALWAYS_TEXT:
            if not has_enc:
                out.append((*_span(n), f"{name}()", "text wrapper without encoding="))
            continue

        if name == "read" and isinstance(fn, ast.Attribute):
            # configparser decodes the file itself, under the locale codec.
            if _is_parser_receiver(fn, parser_names) and not has_enc:
                out.append((*_span(n), "ConfigParser.read()",
                            "parses file under locale default"))
            continue

        if name == "fdopen":
            # os.fdopen wraps a raw fd; mode defaults to "r", i.e. TEXT.
            mode = _literal_mode(n, n.args[1] if len(n.args) > 1 else None)
            if mode is False:
                continue                              # non-literal: cannot decide
            if isinstance(mode, str) and "b" in mode:
                continue
            if not has_enc:
                out.append((*_span(n), "os.fdopen()",
                            f"text mode ({mode or 'r'}) no encoding="))
            continue

        if name in TEMPFILE_FNS:
            # Default mode is "w+b" (BINARY), so only a NAMED text mode is in class.
            mode = _literal_mode(n, n.args[0] if n.args else None)
            if not isinstance(mode, str) or "b" in mode:
                continue
            if not has_enc:
                out.append((*_span(n), f"{name}()",
                            f"text mode ({mode}) no encoding="))
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
            mode = _literal_mode(n, mode_arg)
            if mode is False:
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
