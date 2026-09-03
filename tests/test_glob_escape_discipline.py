"""Ratchet: no NEW unescaped operator-derived glob may enter src/ (PS-265).

DEFECT CLASS: "a glob pattern built by joining a directory the operator controls
onto a metacharacter-bearing filename half, without glob.escape() on the
directory."  glob interprets metacharacters across the WHOLE pattern, the
directory portion included, so a directory named `Apps[old]` makes `[old]` a
character class: the pattern names a path that does not exist, glob.glob returns
[], the calling loop's body never runs, and the sweep or scan reports success
while doing nothing.  No exception, no empty-result branch, no signal.

This repo has fixed that same one-word defect FOUR times — 19e7f82, cbfefc8,
PS-227 (two sites), PS-265 (three arms of `_clear_stale_staged`) — and each fix
landed only where its ticket pointed.  A comment asking the next author to
remember is not a guard.  This fails the suite instead, in the shape
`tests/test_encoding_discipline.py` (PS-184) established for exactly this
situation: a test that catches a new INSTANCE of the known shape, plus INJECTION
PROOFS that each modeled shape can actually go red.

WHAT THE DETECTOR DOES NOT MODEL — stated here as the encoding guard states its
own ("no *modeled* platform-dependent decode", not "no conceivable one"), so
this file cannot be read as claiming more than it checks:

  * SAME-FUNCTION, SINGLE-ASSIGNMENT resolution only.  `glob.glob(pattern)`
    where `pattern` was computed in another function, or arrived as a parameter,
    is reported UNRESOLVED and does NOT fail this ratchet.
  * A BARE `escape(...)` call is trusted whatever module it came from.  The
    ATTRIBUTE form is receiver-checked — only `glob.escape(...)` is accepted, so
    the plausible half-fix `re.escape(dir)` fires (it emits backslash escapes
    glob does not honour and matches nothing under a bracketed directory, just
    like the unescaped form).  But a single `ast.Name` node carries no receiver,
    so `from re import escape` is indistinguishable from `from glob import
    escape` at that node.  There are no bare `escape` imports under src/ today.
  * STRING CONCATENATION and %-formatting are not decomposed.  `dir + "/*.log"`
    is caught by the fallback rule (its value is not literal-only and not
    escaped), but a mixed concatenation's component structure is not analysed,
    so a partially-escaped concatenation is judged as a whole.
  * `str.join` (`sep.join([...])`) is not a path join.  The join receiver is
    checked, so it is not decomposed as one: a literal-only `str.join` is
    recognised as safe, and any other is reported by the fallback rule as a
    whole rather than per component.
  * `Path.glob` / `Path.rglob` are NOT modeled at all.  There are zero
    occurrences under src/ today (PS-265 measured this); the gap is named rather
    than built for, and a future author reaching for pathlib's globbing is
    outside this ratchet entirely.
  * `fnmatch`, shell globbing via subprocess, and any glob whose pattern is
    assembled at runtime from a data structure are likewise outside it.

So read a green run as "no MODELED unescaped operator-derived glob under src/",
never as "no conceivable one".

Every proof below was verified by INJECTING the defect into synthetic source and
watching the detector fire (PS-11: a check that could not have failed is not
coverage).  If you change the detector, re-run them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from glob_escape_audit import find, scan  # noqa: E402  the ONE definition


def _write(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "probe.py"
    path.write_text(source, encoding="utf-8")
    return path


def _verdicts(tmp_path: Path, source: str) -> list[tuple[int, str, str]]:
    return find(_write(tmp_path, source))


# --- the ratchet --------------------------------------------------------


def test_no_unescaped_operator_derived_glob_in_src():
    """src/ must stay at zero MISS.

    SCOPE, stated precisely so this message does not overclaim: zero glob sites
    matching the shapes `tools/glob_escape_audit.py` models — see this module's
    docstring for what it does not.  A new site that joins an operator-derived
    directory into a glob pattern without glob.escape() fails here.
    """
    hits = [h for h in scan(REPO_ROOT / "src") if h[2] == "MISS"]
    rel = [
        (Path(p).relative_to(REPO_ROOT).as_posix(), ln, what)
        for p, ln, _v, what in hits
    ]
    assert rel == [], (
        f"{len(rel)} glob pattern(s) under src/ join an operator-derived "
        "directory without glob.escape(), so a directory containing `[` makes "
        "the sweep silently match nothing:\n"
        + "\n".join(f"  {p}:{ln}: {what}" for p, ln, what in rel)
        + "\n\nWrap the DIRECTORY half in glob.escape(). The filename half must "
        "keep its metacharacters."
    )


def test_the_known_fixed_sites_are_seen_and_judged_ok():
    """The detector must actually REACH the four historical sites.

    A detector that saw nothing would also report zero MISS, so the ratchet
    above would be green on a detector that had quietly stopped working. This
    pins that each previously-defective file is still being decided.
    """
    seen = {
        Path(p).relative_to(REPO_ROOT).as_posix()
        for p, _ln, verdict, _what in scan(REPO_ROOT / "src")
        if verdict in {"OK", "MISS"}
    }
    for expected in (
        "src/services/app_update/updater.py",
        "src/services/profile/manager.py",
        "src/ui/state.py",
    ):
        assert expected in seen, (
            f"{expected} carries a glob site the detector no longer decides; "
            "the ratchet would go green regardless of what is in it."
        )


def test_every_arm_of_a_multi_arm_function_is_classified(tmp_path):
    """One fixed arm must not hide a broken sibling.

    `_clear_stale_staged` assigns `pattern` once per OS arm. A resolver that
    took only the first (or only the last) assignment would have reported the
    PS-265 function as clean after fixing a single arm.
    """
    verdicts = _verdicts(
        tmp_path,
        "import glob, os, tempfile\n"
        "def f(keep):\n"
        "    if WIN:\n"
        "        pattern = os.path.join(glob.escape(tempfile.gettempdir()), 'a*.exe')\n"
        "    elif MAC:\n"
        "        pattern = os.path.join(tempfile.gettempdir(), 'a*.dmg')\n"
        "    else:\n"
        "        pattern = os.path.join(glob.escape(os.path.dirname(keep)), 'a*.part')\n"
        "    for p in glob.glob(pattern):\n"
        "        os.remove(p)\n",
    )
    assert [v for _ln, v, _w in verdicts] == ["OK", "MISS", "OK"], verdicts
    assert [ln for ln, v, _w in verdicts if v == "MISS"] == [6]


# --- injection proofs ---------------------------------------------------


@pytest.mark.parametrize(
    "shape, source, defective_lines",
    [
        (
            "assigned os.path.join — the shape ALL FOUR real sites take",
            "import glob, os\n"
            "def f(d):\n"
            "    pattern = os.path.join(d, '*.log')\n"                       # MISS
            "    return glob.glob(pattern)\n"
            "def g(d):\n"
            "    pattern = os.path.join(glob.escape(d), '*.log')\n"          # OK
            "    return glob.glob(pattern)\n",
            {3},
        ),
        (
            "inline argument, no local assignment",
            "import glob, os\n"
            "def f(d):\n"
            "    return glob.glob(os.path.join(d, '*.log'))\n"               # MISS
            "def g(d):\n"
            "    return glob.glob(os.path.join(glob.escape(d), '*.log'))\n", # OK
            {3},
        ),
        (
            "f-string interpolation of a directory",
            "import glob\n"
            "def f(d):\n"
            "    pattern = f'{d}/*.log'\n"                                   # MISS
            "    return glob.glob(pattern)\n"
            "def g():\n"
            "    return glob.glob(f'/fixed/path/*.log')\n",                  # OK: literal
            {3},
        ),
        (
            "string concatenation",
            "import glob\n"
            "def f(d):\n"
            "    return glob.glob(d + '/*.log')\n"                           # MISS
            "def g():\n"
            "    return glob.glob('/fixed' + '/*.log')\n",                   # OK: literals
            {3},
        ),
        (
            "iglob, and a bare `from glob import` form",
            "from glob import iglob, escape\n"
            "import os\n"
            "def f(d):\n"
            "    return iglob(os.path.join(d, '*.log'))\n"                   # MISS
            "def g(d):\n"
            "    return iglob(os.path.join(escape(d), '*.log'))\n",          # OK
            {4},
        ),
        (
            "a call result used directly as the directory",
            "import glob, os, tempfile\n"
            "def f():\n"
            "    pattern = os.path.join(tempfile.gettempdir(), 'x*.exe')\n"  # MISS
            "    return glob.glob(pattern)\n"
            "def g():\n"
            "    pattern = os.path.join(glob.escape(tempfile.gettempdir()), 'x*.exe')\n"
            "    return glob.glob(pattern)\n",                               # OK
            {3},
        ),
        (
            "re.escape used as if it were glob.escape — the plausible half-fix",
            # `re.escape` is already in this tree three times, so it is the
            # escape helper an author here already has in hand — and it does NOT
            # work: it emits backslash escapes glob does not honour, so an
            # re.escape-wrapped bracketed directory matches nothing exactly as
            # the unescaped form does (measured: 0 matches vs glob.escape's 2).
            # A detector that blessed any attribute named `escape` would be
            # GREEN on that broken sweep.
            "import glob, os, re\n"
            "def f(d):\n"
            "    pattern = os.path.join(re.escape(d), '*.log')\n"            # MISS
            "    return glob.glob(pattern)\n"
            "def g(d):\n"
            "    pattern = os.path.join(glob.escape(d), '*.log')\n"          # OK
            "    return glob.glob(pattern)\n",
            {3},
        ),
        (
            "str.join with an operator-derived part is not a safe path join",
            # `fn.attr == "join"` alone matches `sep.join(...)` as readily as
            # `os.path.join(...)`. The receiver is checked, so this is NOT
            # decomposed as a path join; it falls to the fallback rule and is
            # reported as a whole.
            "import glob\n"
            "def f(sep, d):\n"
            "    return glob.glob(sep.join([d, '*.log']))\n",                # MISS
            {3},
        ),
    ],
)
def test_the_detector_fires_on_each_injected_shape(
    tmp_path, shape, source, defective_lines
):
    """Prove the detector fires per shape, so this file cannot pass vacuously.

    If it ever silently stopped matching one shape, the ratchet above would be
    green forever and that shape would rot back into src/ — which is exactly how
    this defect reached a fourth instance.
    """
    verdicts = _verdicts(tmp_path, source)
    missed = {ln for ln, verdict, _w in verdicts if verdict == "MISS"}
    assert missed == defective_lines, (
        f"shape {shape!r}: detector reported MISS on lines {sorted(missed)}, "
        f"expected {sorted(defective_lines)}. Full report: {verdicts}"
    )


@pytest.mark.parametrize(
    "label, source",
    [
        (
            "literal-only pattern is not operator-derived",
            "import glob\n"
            "def f():\n"
            "    return glob.glob('/var/log/persona_*.log')\n",
        ),
        (
            "literal join is not operator-derived",
            "import glob, os\n"
            "def f():\n"
            "    pattern = os.path.join('/var/log', 'persona_*.log')\n"
            "    return glob.glob(pattern)\n",
        ),
        (
            "the whole pattern escaped",
            "import glob\n"
            "def f(d):\n"
            "    return glob.glob(glob.escape(d))\n",
        ),
        (
            "glob.escape on the directory half is still ACCEPTED",
            # The companion to the re.escape injection proof above: tightening
            # the receiver check must not make the CORRECT fix look defective,
            # or the ratchet would fire on the very shape it exists to enforce.
            "import glob, os, tempfile\n"
            "def f():\n"
            "    pattern = os.path.join(glob.escape(tempfile.gettempdir()), 'p*.exe')\n"
            "    return glob.glob(pattern)\n",
        ),
        (
            "a literal-only str.join is not operator-derived",
            "import glob\n"
            "def f():\n"
            "    return glob.glob('/'.join(['/fixed', '*.log']))\n",
        ),
    ],
)
def test_the_detector_does_not_fire_on_safe_shapes(tmp_path, label, source):
    """False positives would make the ratchet noise, and noise gets deleted."""
    verdicts = _verdicts(tmp_path, source)
    assert [v for _ln, v, _w in verdicts] and all(
        v == "OK" for _ln, v, _w in verdicts
    ), f"{label}: expected all OK, got {verdicts}"


def test_an_unresolvable_pattern_is_reported_as_such_not_as_clean(tmp_path):
    """The model's boundary is VISIBLE in the report rather than silent.

    A pattern arriving as a parameter is outside same-function resolution. It
    must not be silently judged OK — it is reported UNRESOLVED, which is how a
    reader learns the detector declined to decide rather than approved.
    """
    verdicts = _verdicts(
        tmp_path,
        "import glob\n"
        "def f(pattern):\n"
        "    return glob.glob(pattern)\n",
    )
    assert [v for _ln, v, _w in verdicts] == ["UNRESOLVED"], verdicts
