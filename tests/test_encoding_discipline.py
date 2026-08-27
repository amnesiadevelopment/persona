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
import os
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
    # PS-211 REMOVED src/services/browser/invisible_launch.py from this set.
    # All ten in-class sites in that file now name encoding="utf-8" and
    # errors="replace": the seven subprocess(text=True) calls (certutil x2,
    # pgrep -P, ps -o command=, PowerShell x2, pgrep -f) and the three
    # os.fdopen text handles, which are the two ends of ONE pipe and were
    # therefore moved together -- pinning one end of a pair that previously
    # agreed would have CREATED a disagreement rather than removed one.
    #
    # open(path, "a+") that never reads: seek/truncate/write of an ASCII pid.
    # Kept in the frozen set rather than silently excused -- it is in the shape
    # even though it cannot currently misdecode.  PS-211 deliberately did NOT
    # fix it: the ticket dismissed it with a reason and scoped it out, so it
    # stays frozen rather than being quietly swept in on an unrelated ticket.
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


def test_opaque_kwargs_with_text_true_is_reported_not_dropped(tmp_path):
    """PS-211: **kwargs must make a call AMBIGUOUS, never INVISIBLE.

    This is the blind spot that made this ticket necessary, and it is the one
    the previous round could not see.  `_opaque_kwargs` is a sound reason not
    to call a site a CONFIRMED offender -- but the tool turned that into a bare
    `continue`, dropping the call from the report entirely.

    That silence landed on precisely the two sites this ticket says matter
    most: both Windows-only PowerShell calls in invisible_launch.py are

        subprocess.check_output([... powershell.exe ...],
                                text=True, **_platform.no_window_kwargs())

    so a sweep run with this tool reported 5 of 7 and called the file clean at
    0 -- while PS-184's file-level freeze sat over sites its own detector could
    not see.  `text=True` is a POSITIVE assertion that the call decodes; the
    **kwargs makes the ENCODING undecidable, not the DECODING.

    Asserting the two real sites are AMBIGUOUS rather than absent is the point:
    absent is what the bug looked like.
    """
    sample = tmp_path / "opaque.py"
    sample.write_text(
        "import subprocess\n"
        "subprocess.check_output(['x'], text=True, **kw)\n"      # opaque + text
        "subprocess.run(['y'], **kw)\n"                          # opaque, no text
        "subprocess.run(['z'], capture_output=True, **kw)\n",    # bytes, not in class
        encoding="utf-8",
    )
    hits = {ln: why for ln, _c, _el, _ec, _what, why in find(sample)}

    assert 2 in hits, (
        "a text=True call carrying **kwargs was DROPPED from the report. That "
        "silence is what hid both Windows-only PowerShell sites from the sweep."
    )
    assert "AMBIGUOUS" in hits[2], (
        "an opaque call must be AMBIGUOUS (hand-triage), not a confirmed "
        "offender -- **kwargs may legitimately supply encoding="
    )
    # No text indicator => nothing is asserted about decoding => stay silent.
    assert 3 not in hits and 4 not in hits, (
        "a call with **kwargs but NO text=True asserts no decode; reporting it "
        "would make the tool noisy enough to be ignored."
    )


def test_the_real_powershell_sites_are_visible_to_the_detector():
    """The regression, pinned against the SHIPPED file rather than a sample.

    PS-211's DoD item 2 warns that a search shaped from the seven sites handed
    over returns exactly those seven.  This asserts the inverse property on the
    file itself: the two PowerShell call sites must APPEAR in the detector's
    output.  Before the fix they appeared nowhere, at any severity.
    """
    target = REPO_ROOT / "src/services/browser/invisible_launch.py"
    source = target.read_text(encoding="utf-8").splitlines()

    # Read each hit's FULL SPAN (lineno..end_lineno), not a fixed-size window
    # from the start line.  These calls span 10-15 lines and carry explanatory
    # comments, so a guessed window silently clips the very kwarg being
    # asserted on -- which is a defect in the TEST, not in the product.
    subprocess_sites = [
        (ln, el) for ln, _c, el, _ec, what, _why in find(target)
        if "subprocess" in what
    ]
    assert subprocess_sites, (
        "no subprocess site in invisible_launch.py is reported at all. The two "
        "PowerShell calls carry **_platform.no_window_kwargs(), so if the "
        "opaque-kwargs path fell silent again they would vanish from the sweep "
        "exactly as they did before PS-211."
    )

    powershell = [
        (ln, el) for ln, el in subprocess_sites
        if "powershell.exe" in "\n".join(source[ln - 1:el])
    ]
    assert len(powershell) == 2, (
        f"expected BOTH Windows-only PowerShell sites to be reported, got "
        f"{len(powershell)}: {[ln for ln, _ in powershell]}. These are the "
        "sites whose stdout embeds a profile path and is decoded as cp1252 "
        "on Windows."
    )
    for ln, el in powershell:
        body = "\n".join(source[ln - 1:el])
        assert "no_window_kwargs" in body, (
            f"line {ln} was expected to be one of the **kwargs-opaque sites"
        )
        # The fix itself: the call names the codec despite the opaque kwargs.
        assert 'encoding="utf-8"' in body and 'errors="replace"' in body, (
            f"PowerShell site at line {ln} does not name the encoding, so its "
            "stdout is decoded with the parent's locale codec on Windows."
        )


# --- the property, exercised ---------------------------------------------
#
# PS-11: "a test asserting encoding= appears in the source is not coverage."
# Every assertion above this line is about the SHAPE of the code. These two are
# about the BEHAVIOUR: a non-ASCII byte written by a real child process must
# survive the round trip into the parent, under a locale whose default codec
# cannot represent it.


def _ascii_only(argv):
    return all(ord(c) < 128 for a in argv for c in a)


# A child that writes UTF-8 BYTES directly to its stdout buffer.
#
# TWO INSTRUMENT TRAPS, both hit while writing this and both recorded so the
# next person does not re-diagnose them as product defects (PS-14):
#
#  1. argv MUST STAY PURE ASCII. Under LC_ALL=C the fork/exec path encodes argv
#     at the filesystem encoding, so a non-ASCII character in the COMMAND raises
#     UnicodeEncodeError inside _fork_exec -- BEFORE the child ever runs. That
#     is the harness failing, not the decode under test. The non-ASCII therefore
#     lives only in the bytes the child WRITES.
#  2. PRINTING the decoded result under an ASCII stdout raises on its own. Any
#     diagnostic here must go through ascii()/repr().
_EMITTER = (
    "import sys;"
    "sys.stdout.buffer.write(b'caf\\xc3\\xa9-\\xc3\\xbcber\\n');"
    "sys.stdout.buffer.flush()"
)
# The same text, decoded correctly: "café-über".
_EXPECTED = "caf\u00e9-\u00fcber\n"

# Forces locale.getpreferredencoding(False) to ANSI_X3.4-1968 (ASCII) on Linux,
# which reproduces the Windows cp1252 CLASS of failure on this runner. BOTH
# PYTHONUTF8=0 and -X utf8=0 are required, or CPython's UTF-8 mode overrides it.
_ASCII_LOCALE_ENV = {"LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0"}


def _run_under_ascii_locale(body):
    """Run `body` in a subprocess whose locale default codec is ASCII."""
    env = {**os.environ, **_ASCII_LOCALE_ENV}
    argv = [sys.executable, "-X", "utf8=0", "-c", body]
    assert _ascii_only(argv), "harness bug: argv must be ASCII under LC_ALL=C"
    return subprocess.run(
        argv, capture_output=True, timeout=120,
        # This harness's OWN read is pinned, so the test cannot fail for the
        # very defect it is testing for.
        text=True, encoding="utf-8", errors="replace", env=env,
    )


def test_unpinned_child_decode_actually_breaks_under_an_ascii_locale():
    """THE DEFECT, REPRODUCED -- not asserted from the source text.

    This is the control. Without it, the test below could pass on a runner
    where the locale was already UTF-8 and would prove nothing (PS-11: a check
    that could not have failed is not coverage).
    """
    proc = _run_under_ascii_locale(
        "import subprocess, sys\n"
        f"child = [sys.executable, '-c', {_EMITTER!r}]\n"
        "try:\n"
        "    subprocess.check_output(child, text=True)\n"
        "    sys.stdout.buffer.write(b'NO_ERROR')\n"
        "except UnicodeDecodeError:\n"
        "    sys.stdout.buffer.write(b'DECODE_ERROR')\n"
    )
    assert proc.returncode == 0, f"harness failed: {ascii(proc.stderr)}"
    if proc.stdout.strip() == "NO_ERROR":
        pytest.skip(
            "this runner's locale default is not ASCII, so the unpinned form "
            "cannot be shown to break here -- the pinned test below still runs"
        )
    assert proc.stdout.strip() == "DECODE_ERROR", (
        "expected `text=True` with no encoding= to raise UnicodeDecodeError on "
        f"a non-ASCII byte under an ASCII locale, got {ascii(proc.stdout)}"
    )


def test_pinned_child_decode_survives_the_round_trip_under_an_ascii_locale():
    """THE FIX, EXERCISED: the convention this ticket conforms to actually works.

    `text=True, encoding="utf-8", errors="replace"` -- process.py's convention,
    now on all seven subprocess sites in invisible_launch.py -- must return the
    child's bytes decoded correctly even where the locale default cannot
    represent them. This is the property the seven sites are being changed FOR.
    """
    proc = _run_under_ascii_locale(
        "import subprocess, sys\n"
        f"child = [sys.executable, '-c', {_EMITTER!r}]\n"
        "out = subprocess.check_output(\n"
        "    child, text=True, encoding='utf-8', errors='replace')\n"
        # Write BYTES back: printing the str would raise under ASCII stdout.
        "sys.stdout.buffer.write(out.encode('utf-8'))\n"
    )
    assert proc.returncode == 0, (
        "the pinned form must not raise under an ASCII locale; stderr: "
        f"{ascii(proc.stderr)}"
    )
    assert proc.stdout == _EXPECTED, (
        f"round trip lost data: got {ascii(proc.stdout)}, "
        f"expected {ascii(_EXPECTED)}"
    )


def test_the_windows_failure_mode_is_silent_corruption_not_an_exception():
    """THE WINDOWS SHAPE, PINNED -- and it is NOT the one above.

    The two tests above force an ASCII locale, where the unpinned form RAISES
    UnicodeDecodeError.  Windows is worse, and this is the whole reason a green
    Windows CI arm is "necessary but not sufficient" (PS-211 DoD item 4):

        cp1252 is a SINGLE-BYTE codec with almost no undefined slots, so it
        decodes arbitrary bytes WITHOUT raising.  b"caf\\xc3\\xa9" comes back as
        "cafÃ©" -- mojibake, not an exception.

    So on the platform this ticket is actually about, the defect does not
    announce itself.  It returns a WRONG STRING that a caller then matches a
    profile path against, and the match quietly fails -- which for
    _profile_firefox_pids means "no such process" rather than a crash.

    This runs on EVERY platform (the codec is named explicitly rather than
    inherited from the locale), so unlike the ASCII-locale pair it cannot be
    skipped into vacuity on a UTF-8 runner.
    """
    emitter = [sys.executable, "-c", _EMITTER]
    assert _ascii_only(emitter), "harness bug: argv must be ASCII"

    pinned = subprocess.check_output(
        emitter, text=True, encoding="utf-8", errors="replace", timeout=120)
    as_cp1252 = subprocess.check_output(
        emitter, text=True, encoding="cp1252", timeout=120)

    assert pinned == _EXPECTED, (
        f"the convention lost data: {ascii(pinned)} != {ascii(_EXPECTED)}")
    # The point: the WRONG codec did not raise. It returned a wrong answer.
    assert as_cp1252 != pinned, (
        "cp1252 and utf-8 decoded these bytes identically, so this fixture no "
        "longer carries a byte that distinguishes them and the test is vacuous"
    )
    assert "\u00c3" in as_cp1252, (
        f"expected classic utf-8-through-cp1252 mojibake, got {ascii(as_cp1252)}"
    )
