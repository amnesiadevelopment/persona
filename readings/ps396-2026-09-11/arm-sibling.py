#!/usr/bin/env python3
"""ARM-SIBLING — the round-2 blocker, measured against two LIVE chromium trees.

WHY THIS EXISTS. The reviewer's blocker 1 is correct: `profile_dir in cmdline`
is an unanchored substring test, so a prefix-sibling profile (`work` matching
`work2`) folds a neighbour's cpu and context switches into the wrong series —
the repo's own #150 wrong-kill class (`invisible_launch.py:5150-5156`) arriving
through a recorder.

⛔ BUT THE SUGGESTED FIX IS MEASURED WRONG, WHICH IS WHY THIS IS COMMITTED AS AN
INSTRUMENT AND NOT QUOTED AS A NUMBER. The review's candidate tokenises the
cmdline on NUL and requires a whole-token or path-prefix match. Against a real
chromium tree that drops EVERY child — 11 processes to 1, on both siblings —
because chromium's
children do not have a conventional argv: the whole command line arrives as ONE
NUL-terminated entry with spaces inside it, so a whole-token comparison sees the
two parents alone. That is the PS-171 arm-H undercount arriving through the fix
for the overcount, and trading an overcount for an undercount is not a fix.

Run it against two prefix-sibling profiles:

    chromium --headless --no-sandbox --user-data-dir=/tmp/ps396-sib/work  about:blank &
    chromium --headless --no-sandbox --user-data-dir=/tmp/ps396-sib/work2 about:blank &
    python3 readings/ps396-2026-09-11/arm-sibling.py /tmp/ps396-sib/work /tmp/ps396-sib/work2

⚠️ It takes the dirs as argv, which is exactly the shape that made the matcher
return the observer's own pid (see `test_the_matcher_never_returns_the_observer`).
That is deliberate: the shipped matcher excludes `os.getpid()`, and re-running
this harness re-exercises that exclusion rather than tiptoeing around it.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.services.browser.session_series import engine_pids_for  # noqa: E402


def match_bare_in(raw: bytes, profile_dir: str) -> bool:
    """What shipped in round 1 — the unanchored substring the reviewer rejected."""
    return profile_dir in raw.decode("utf-8", "replace")


def match_token_split(raw: bytes, profile_dir: str) -> bool:
    """The review's suggested candidate, verbatim from the review comment."""
    for tok in raw.decode("utf-8", "replace").split("\0"):
        if not tok:
            continue
        val = tok.split("=", 1)[1] if tok.startswith("-") and "=" in tok else tok
        if val == profile_dir or val.startswith(profile_dir + os.sep):
            return True
    return False


def _scan(profile_dir: str, predicate) -> "list[int]":
    out = []
    self_pid = os.getpid()
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == self_pid:
            continue
        try:
            with open(os.path.join("/proc", entry, "cmdline"), "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        if predicate(raw, profile_dir):
            out.append(int(entry))
    return sorted(out)


def argv_shape() -> None:
    """WHY the token-split candidate fails: how many argv entries each proc has."""
    hist: "dict[int, int]" = {}
    sample_one_token = None
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join("/proc", entry, "cmdline"), "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        if b"chromium" not in raw and b"chrome" not in raw:
            continue
        toks = [t for t in raw.decode("utf-8", "replace").split("\0") if t]
        hist[len(toks)] = hist.get(len(toks), 0) + 1
        if len(toks) == 1 and "--type=renderer" in toks[0] and sample_one_token is None:
            sample_one_token = toks
    print("\nargv shape of the chromium processes on this box:")
    for n in sorted(hist):
        print(f"  {n:2d} token(s): {hist[n]:3d} processes")
    if sample_one_token is not None:
        blob = sample_one_token[0]
        print("\n  /proc/<renderer>/cmdline  ->  1 token:")
        print(f"    [{blob[:150]!r}...]")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    dirs = sys.argv[1:3]
    print("⚠️ the three scans are SEQUENTIAL, not atomic: a renderer that starts or")
    print("   exits between them moves a count by one. Read the SHAPE (overcount /")
    print("   undercount / correct), not the last digit.\n")
    print("matcher                            " + "  ".join(f"{os.path.basename(d):>8}" for d in dirs))
    rows = [
        ("bare `in` (what shipped)   ", lambda raw, d: match_bare_in(raw, d)),
        ("token-split (review's fix) ", lambda raw, d: match_token_split(raw, d)),
    ]
    for label, pred in rows:
        counts = [len(_scan(d, pred)) for d in dirs]
        print(f"{label}        " + "  ".join(f"{c:>8d}" for c in counts))
    counts = [len(engine_pids_for(d)) for d in dirs]
    print("boundary-anchored (SHIPPED)        " + "  ".join(f"{c:>8d}" for c in counts))
    argv_shape()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
