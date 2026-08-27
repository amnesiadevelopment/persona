"""Guard: no text decode in this repo may depend on the platform's locale.

PS-184. An unnamed text encoding resolves via locale.getpreferredencoding(False)
-> utf-8 on Linux/macOS, cp1252 on Windows.  The product writes with an explicit
encoding="utf-8", so an unnamed READ disagrees with it, and the disagreement is
invisible until a non-ASCII byte lands in an emitted artifact.  PS-161: a single
U+FE0F failed four tests on windows-latest and passed on every other runner.

DEFECT CLASS: "a read whose encoding is platform-dependent, against a write that
is not."

This guard exists because the sweep that fixed 335 call sites cannot stop the
336th from being written tomorrow.  A comment asking authors to remember is not
a guard; this fails the suite instead.

TWO DIFFERENT THINGS HAVE TO BE GUARDED, and the first version of this file only
guarded one of them:

  * a new INSTANCE of a known shape  -- `test_no_platform_dependent_..._in_tests`
  * a new SHAPE the detector does not model -- `test_detector_models_every_...`

The second is the one that matters for the sweep's central claim.  A detector
whose scope is a hand-written list of shapes is a narrow grep one level up: it
finds precisely the shapes someone already thought of.  The first version of
this file modeled three shapes and passed green while 13 in-class call sites
(os.fdopen, ConfigParser.read) sat untouched in tests/ -- the guard was as
green as it would have been on a clean tree, which is exactly the PS-11 shape
this repo keeps re-learning.

WHY IT IS AN AST WALK AND NOT A GREP -- three ways a grep gets this wrong:

  1. A call spans lines, so `encoding=` routinely sits on a line other than
     `text=True`.  src/services/browser/process.py is the proof: `text=True` on
     one line, `encoding="utf-8"` on the next.  A line-based `grep -v encoding=`
     both misses correct sites and reports false ones.
  2. `open` is not one function.  os.open() returns a raw fd; tarfile, zipfile
     and gzip .open() are archive APIs; urllib opener.open() yields bytes.  None
     of them decode text, and a name match cannot tell them apart.
  3. capture_output=True ALONE returns bytes and is NOT in the class.  Only
     text=True / universal_newlines=True decode.

Every guard here was verified by INJECTING the defect and watching it go red,
not by observing it pass on a clean tree (PS-11: a check that could not have
failed is not coverage).  If you change the detector, re-run those proofs.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from encoding_audit import (  # noqa: E402  the ONE definition of the class
    ALWAYS_TEXT,
    CONFIGPARSER_CLASSES,
    SUBPROCESS_FNS,
    TEMPFILE_FNS,
    TEXT_METHODS,
    find,
)

# Every callable name the detector knows how to decide.  Kept here rather than
# imported as one set because the audit models them through different code
# paths, and this is the list the derivation test holds it to.
MODELED = (
    TEXT_METHODS
    | SUBPROCESS_FNS
    | ALWAYS_TEXT
    | TEMPFILE_FNS
    | {"open", "fdopen", "read"}   # `read` = ConfigParser.read, receiver-gated
)


def _scan(rel: str):
    """Every non-ambiguous hit under `rel`, as (relative_path, lineno, what)."""
    hits = []
    for path in sorted((REPO_ROOT / rel).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for lineno, _col, _el, _ec, what, why in find(path):
            if "AMBIGUOUS" in why:
                continue
            hits.append((path.relative_to(REPO_ROOT).as_posix(), lineno, what))
    return hits


def test_no_platform_dependent_text_decode_in_tests():
    """tests/ must stay at zero.  This is the sweep's ratchet.

    SCOPE, stated precisely so this message does not overclaim: zero call sites
    matching the shapes `tools/encoding_audit.py` models.  That set is not
    hand-picked -- `test_detector_models_every_derived_shape` below holds it to
    a mechanically derived one -- but it is still a set of SHAPES, so read this
    as "no modeled platform-dependent decode", not "no conceivable one".
    """
    hits = _scan("tests")
    assert hits == [], (
        f"{len(hits)} text decode(s) in tests/ do not name an encoding, so they "
        f"resolve to cp1252 on Windows while the product writes utf-8:\n"
        + "\n".join(f"  {p}:{ln}: {what}" for p, ln, what in hits)
        + "\n\nAdd encoding=\"utf-8\" to the call.  On a site that genuinely "
        "reads bytes, use a binary mode instead."
    )


# --- the completeness argument ------------------------------------------
#
# DoD item 2 asks that the sweep's completeness be DEMONSTRATED rather than
# asserted.  A claim resting on a list someone wrote by hand cannot do that.
# So the candidate set is DERIVED, two independent ways, and the detector is
# held to the derivation at test time.


def _derived_candidate_names() -> dict[str, set[str]]:
    """{callable name -> stdlib origins} for everything that takes encoding=None.

    Mechanically derived: walk the standard library, keep every callable with an
    `encoding` parameter defaulting to None.  No human decides what is in this
    set, which is the whole point -- a shape nobody thought of still appears.
    """
    accepts: dict[str, set[str]] = {"open": {"builtins.open"}}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for mod_name in sorted(sys.stdlib_module_names):
            if mod_name.startswith("_") or mod_name in {
                "antigravity", "this", "idlelib", "turtle", "tkinter", "test",
            }:
                continue
            try:
                mod = __import__(mod_name)
            except Exception:
                continue
            for name, obj in list(vars(mod).items()):
                if name.startswith("_"):
                    continue
                targets = []
                if inspect.isfunction(obj) or inspect.isbuiltin(obj):
                    targets = [(name, obj)]
                elif inspect.isclass(obj):
                    targets = [(name, obj)]
                    for meth_name, meth in list(vars(obj).items()):
                        if not meth_name.startswith("_") and inspect.isfunction(meth):
                            targets.append((meth_name, meth))
                for target_name, target in targets:
                    try:
                        sig = inspect.signature(target)
                    except Exception:
                        continue
                    param = sig.parameters.get("encoding")
                    if param is not None and param.default is None:
                        accepts.setdefault(target_name, set()).add(
                            f"{mod_name}.{target_name}"
                        )
    return accepts


def _names_called_under(rel: str) -> set[str]:
    """Every attribute/function name actually CALLED under `rel`."""
    called: set[str] = set()
    for path in sorted((REPO_ROOT / rel).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_bytes(), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else (
                       fn.id if isinstance(fn, ast.Name) else None)
                if name:
                    called.add(name)
    return called


def test_detector_models_every_derived_shape():
    """A NEW SHAPE must fail the suite, not just a new instance of a known one.

    This is the test that makes the completeness claim checkable.  It intersects
    a mechanically derived set (stdlib callables taking `encoding=None`) with the
    names this repo's tests actually call, and requires the detector to model
    every survivor.  Import a module with a new locale-defaulting API, call it in
    tests/, and this goes red -- without anyone remembering to widen a list.
    """
    derived = _derived_candidate_names()
    in_use = _names_called_under("tests")
    candidates = set(derived) & in_use
    unmodeled = candidates - MODELED
    assert not unmodeled, (
        "tests/ calls stdlib API(s) that decode text under the platform locale, "
        "and tools/encoding_audit.py does not model them:\n"
        + "\n".join(
            f"  {name}  (stdlib: {', '.join(sorted(derived[name])[:4])})"
            for name in sorted(unmodeled)
        )
        + "\n\nThe ratchet is only as wide as the detector, so an unmodeled "
        "shape means the sweep silently stopped covering the class.\n"
        "Teach encoding_audit.find() to decide this shape, then re-run the "
        "injection proofs in this file."
    )


def test_the_subprocess_wrappers_are_modeled_despite_being_underivable():
    """State the derivation's ONE blind spot instead of letting it hide.

    subprocess.run/check_output/call/check_call forward **kwargs to Popen, so
    they carry no visible `encoding` parameter and the derivation above cannot
    see them -- yet text=True on any of them decodes under the locale.  They are
    modeled explicitly in the detector; this pins that so a refactor cannot drop
    them and leave the derivation test green (it would never have covered them).
    """
    for name in ("run", "check_output", "call", "check_call"):
        sig = inspect.signature(getattr(subprocess, name))
        assert "encoding" not in sig.parameters, (
            f"subprocess.{name} now exposes `encoding` directly; the derivation "
            "test can see it, so this special case may be simplified."
        )
        assert name in SUBPROCESS_FNS, (
            f"subprocess.{name} decodes under the locale with text=True but is "
            "no longer modeled -- and the derivation test structurally cannot "
            "catch that, which is why this test exists."
        )


def test_pep597_agrees_that_the_modeled_shapes_are_the_class():
    """Cross-check the shape list against CPython's OWN definition of the class.

    `python -X warn_default_encoding` (PEP 597) makes the interpreter emit an
    EncodingWarning at every text decode that fell back to the locale.  That is
    an externally owned definition -- if our modeled shapes and CPython's
    warning disagree, one of them is wrong and it is not CPython.

    Run in a SUBPROCESS because the flag is interpreter startup state.
    """
    probe = textwrap.dedent(
        """
        import io, os, pathlib, subprocess, tempfile, warnings, configparser
        p = pathlib.Path(tempfile.mkdtemp()) / "f.txt"
        p.write_bytes(b"[s]\\nk = 1\\n")

        def check(label, fn):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                try:
                    fn()
                except Exception:
                    pass
                names = [c.category.__name__ for c in caught]
            print(f"{label}:{'EncodingWarning' in names}")

        # modeled shapes -- every one must WARN
        check("read_text", lambda: p.read_text())
        check("write_text", lambda: (p.parent / "w.txt").write_text("a"))
        check("open", lambda: open(p).read())
        check("fdopen", lambda: os.fdopen(os.open(p, os.O_RDONLY)).read())
        check("TextIOWrapper", lambda: io.TextIOWrapper(io.BytesIO(b"a")))
        check("NamedTemporaryFile", lambda: tempfile.NamedTemporaryFile(mode="w"))
        check("ConfigParser.read", lambda: configparser.ConfigParser().read(p))
        check("subprocess", lambda: subprocess.run(
            [__import__("sys").executable, "-c", "pass"], text=True, capture_output=True))

        # correct forms -- every one must stay SILENT
        check("ok_read_text", lambda: p.read_text(encoding="utf-8"))
        check("ok_open_binary", lambda: open(p, "rb").read())
        check("ok_tempfile_binary", lambda: tempfile.NamedTemporaryFile(mode="wb"))
        check("ok_configparser", lambda: configparser.ConfigParser().read(p, encoding="utf-8"))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-X", "warn_default_encoding", "-c", probe],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, f"probe failed: {proc.stderr}"
    verdict = dict(
        line.split(":") for line in proc.stdout.strip().splitlines() if ":" in line
    )
    warned = {k: v == "True" for k, v in verdict.items()}

    in_class = [k for k in warned if not k.startswith("ok_")]
    missing = [k for k in in_class if not warned[k]]
    assert not missing, (
        "CPython's own PEP 597 check does NOT consider these shapes "
        f"locale-dependent, but the detector treats them as in class: {missing}. "
        "One of the two is wrong."
    )
    false_alarms = [k for k in warned if k.startswith("ok_") and warned[k]]
    assert not false_alarms, (
        f"PEP 597 warns on forms we consider correct: {false_alarms}. The "
        "detector would be under-reporting the class."
    )


# --- product side -------------------------------------------------------
#
# src/ is NOT clean, and this guard deliberately does not pretend otherwise.
# These are a SEPARATE finding (a shipped bug, not a test bug) and fixing them
# is out of scope for PS-184 -- see the finding filed against this ticket.
# The frozen set stops the population GROWING while that is decided.
#
# Frozen by FILE, not by line number, so ordinary edits above a call site do
# not fail the suite for an unrelated reason.
KNOWN_PRODUCT_FILES = {
    # subprocess(text=True) sites -- certutil (NSS), pgrep, ps, and, the one
    # that actually bites on the owner's platform, two PowerShell calls whose
    # stdout is decoded under cp1252 on Windows -- plus three os.fdopen text
    # handles the WIDENED detector additionally surfaced here.
    "src/services/browser/invisible_launch.py",
    # open(path, "a+") that never reads: seek/truncate/write of an ASCII pid.
    # Kept in the frozen set rather than silently excused -- it is in the shape
    # even though it cannot currently misdecode.
    "src/core/single_instance.py",
}


def test_product_side_platform_dependent_decodes_do_not_grow():
    """A new bare decode in src/ is a SHIPPED bug and must not slip in quietly."""
    offenders = {p for p, _ln, _what in _scan("src")}
    new = offenders - KNOWN_PRODUCT_FILES
    assert not new, (
        "New platform-dependent text decode(s) in PRODUCT code: "
        + ", ".join(sorted(new))
        + "\nThis ships to users on Windows. Name encoding=\"utf-8\" on the call."
    )


# --- injection proofs ---------------------------------------------------
#
# PS-11: a guard observed passing on a clean tree has not been shown to work.
# Each modeled shape gets an injected instance and must be caught.


@pytest.mark.parametrize(
    "shape, source, defective_lines",
    [
        (
            "read_text / open / subprocess",
            "import pathlib, subprocess\n"
            "pathlib.Path('a').read_text()\n"                      # bare read
            "open('b').read()\n"                                   # bare text open
            "subprocess.run(['x'], text=True)\n"                   # locale-decoded
            "pathlib.Path('c').read_text(encoding='utf-8')\n"      # correct
            "open('d', 'rb').read()\n"                             # binary, correct
            "subprocess.run(['y'], capture_output=True)\n",        # bytes, correct
            {2, 3, 4},
        ),
        (
            "os.fdopen",
            "import os\n"
            "os.fdopen(3).read()\n"                                # text default
            "os.fdopen(4, 'w').write('x')\n"                       # text write
            "os.fdopen(5, encoding='utf-8').read()\n"              # correct
            "os.fdopen(6, 'rb').read()\n",                         # binary, correct
            {2, 3},
        ),
        (
            "configparser",
            "import configparser\n"
            "configparser.ConfigParser().read('a.ini')\n"          # inline ctor
            "p = configparser.ConfigParser()\n"
            "p.read('b.ini')\n"                                    # via local name
            "p.read('c.ini', encoding='utf-8')\n"                  # correct
            "open('d', 'rb').read()\n",                            # a .read() that is NOT a parser
            {2, 4},
        ),
        (
            "tempfile / TextIOWrapper",
            "import tempfile, io\n"
            "tempfile.NamedTemporaryFile(mode='w')\n"              # text mode
            "io.TextIOWrapper(buf)\n"                              # always text
            "tempfile.NamedTemporaryFile(mode='w', encoding='utf-8')\n"   # correct
            "tempfile.NamedTemporaryFile()\n"                      # defaults BINARY
            "tempfile.NamedTemporaryFile(mode='wb')\n"             # binary, correct
            "io.TextIOWrapper(buf, encoding='utf-8')\n",           # correct
            {2, 3},
        ),
    ],
)
def test_the_guard_actually_detects_each_injected_shape(
    tmp_path, shape, source, defective_lines
):
    """Prove the detector fires per shape, so this file cannot pass vacuously.

    If the detector ever silently stops matching one shape, the ratchet above
    would go green forever and that shape would rot back into the tree -- which
    is precisely what happened to os.fdopen and configparser.read.
    """
    sample = tmp_path / f"{shape.split()[0]}_sample.py"
    sample.write_text(source, encoding="utf-8")
    flagged = {ln for ln, _c, _el, _ec, _what, why in find(sample)
               if "AMBIGUOUS" not in why}
    assert flagged == defective_lines, (
        f"[{shape}] detector flagged {sorted(flagged)}, "
        f"expected exactly {sorted(defective_lines)}"
    )


def test_the_guard_does_not_flag_an_already_correct_call(tmp_path):
    """The **kwargs shape must NOT be flagged.

    A `**{"encoding": "utf-8"}` entry has arg=None, so the absence of a keyword
    literally named `encoding` does not mean none was passed.  Adding one raises
    "got multiple values for keyword argument 'encoding'" -- which is exactly
    how this sweep broke tests/test_locale_ext.py before the detector learned
    the shape.
    """
    sample = tmp_path / "kw.py"
    sample.write_text(
        "import pathlib\n"
        "_UTF8 = {'encoding': 'utf-8'}\n"
        "pathlib.Path('a').read_text(**_UTF8)\n",
        encoding="utf-8",
    )
    assert [h for h in find(sample) if "AMBIGUOUS" not in h[5]] == []
