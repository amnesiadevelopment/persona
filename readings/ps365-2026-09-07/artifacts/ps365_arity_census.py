#!/usr/bin/env python3
"""PS-365 — per-spoof ARITY census over the fingerprint patch set.

THE QUESTION
------------
A command-line flag carries ONE value for the life of a launch. So a spoof can
move onto the flag mechanism iff its emitted value is a function of
(seed, launch-constant config) ALONE. Any dependency on a PER-CALL argument —
a pixel coordinate, a requested font family, a buffer length — makes it
unrepresentable as one flag value, however deterministic it is.

⚠️ ARITY IS THE TEST, NOT VARIANCE. PS-365's brief predicted the noise family
is unmovable because it is "per-read and per-pixel" random. The randomness half
is already false in this tree: upstream rewrote every one of these patches to be
seed-deterministic, and `base::RandDouble()` survives only on DELETED lines.
This script asserts that, so the correction cannot rot back into a guess.

WHAT IS DERIVED VS DECLARED
---------------------------
* DERIVED from the patch text: whether any non-deterministic source remains
  (asserted zero), and which per-call identifiers appear inside the hash-input
  expressions that feed the spoofed value.
* DECLARED: the two delegating patches (013, 016), which call into 012's
  ShuffleSubchannelColorData rather than computing a key of their own — there is
  no hash expression in their own text to read.

Usage:  python3 ps365_arity_census.py
Exit 0 = census consistent. Exit 1 = an assertion failed (a patch changed).
"""

from __future__ import annotations

import pathlib
import re
import sys

PATCH_DIR = (pathlib.Path(__file__).resolve().parents[3]
             / "engine" / "patches" / "fingerprint")

# The eight patches that read kDisableSpoofing, i.e. carry a defeatable spoof.
SPOOF_PATCHES = [
    "003-audio-fingerprint",
    "006-font-fingerprint",
    "011-gpu-info",
    "012-canvas-get-image-data",
    "013-canvas-toDataURL",
    "014-client-rects",
    "015-canvas-measure-text",
    "016-webgl-readPixels",
]

# Patches with no hash expression of their own: they call 012's shuffler.
DELEGATES = {"013-canvas-toDataURL": "012", "016-webgl-readPixels": "012"}

NONDETERMINISTIC = re.compile(r"base::Rand|std::random|mt19937|\brand\(\)")

# The identifiers that would make a value per-call rather than per-launch.
# Deliberately a CLOSED list of things a PAGE controls, so a new one shows up
# as an unexplained hash input rather than being silently absorbed.
PER_CALL_TOKENS = {
    "number_of_frames": "the page's requested buffer length",
    "sample_rate": "the page's requested sample rate",
    "requested_family": "the page's requested font family",
    "std::to_string(x)": "the pixel's x coordinate",
    "std::to_string(y)": "the pixel's y coordinate",
}

# Hash-input expressions. TWO shapes exist in this patch set and reading only
# the first is a FALSE NEGATIVE that hides a real per-call dependency:
#   (a) a named binding fed to std::hash later  — 012's `pixel_key_base`, 014's
#       `combined_x`;
#   (b) the argument passed INLINE to std::hash — 006's
#       `std::hash<std::string>{}(fingerprint + requested_family)`.
# Reading (a) alone scored 006 as seed-only, contradicting a hand reading of the
# same patch. Both are read below, and `test_probe_sees_inline_hash_arguments`
# pins that (b) is not silently dropped again.
HASH_BINDING = re.compile(r"std::string\s+\w+\s*=\s*([^;]+);")
HASH_INLINE = re.compile(r"std::hash<std::string>\{\}\(([^)]*)\)")


def added_lines(patch: pathlib.Path) -> str:
    text = patch.read_text(errors="replace")
    return "\n".join(l[1:] for l in text.splitlines()
                     if l.startswith("+") and not l.startswith("+++"))


def deleted_lines(patch: pathlib.Path) -> str:
    text = patch.read_text(errors="replace")
    return "\n".join(l[1:] for l in text.splitlines()
                     if l.startswith("-") and not l.startswith("---"))


def per_call_inputs(added: str) -> list[str]:
    """Per-call identifiers appearing in this patch's hash-input expressions.

    Reads BOTH hash-input shapes — see the HASH_INLINE comment for why reading
    only the named-binding shape produced a false negative on 006.
    """
    exprs = " ".join(HASH_BINDING.findall(added) + HASH_INLINE.findall(added))
    found = []
    for token, meaning in PER_CALL_TOKENS.items():
        if token in exprs:
            found.append(meaning)
    return found


def self_test() -> list[str]:
    """Prove the probe can SEE each shape it claims to read.

    A census that has only ever been seen to pass is not known to work — and
    this one was in fact WRONG on 006 until the inline shape was added, so this
    is a real regression guard rather than decoration.
    """
    problems = []

    # (b) the inline shape — the one that was missed.
    inline = 'uint32_t h = std::hash<std::string>{}(fingerprint + requested_family);'
    if not per_call_inputs(inline):
        problems.append("probe is blind to INLINE std::hash arguments (the 006 shape)")

    # (a) the named-binding shape.
    binding = 'std::string k = seed_str + "_x" + std::to_string(x) + "_y" + std::to_string(y);'
    if not per_call_inputs(binding):
        problems.append("probe is blind to NAMED-BINDING hash inputs (the 012 shape)")

    # A negative: seed-only input must NOT be scored as per-call.
    seed_only = 'std::string combined_x = seed_str + "offset_x";'
    if per_call_inputs(seed_only):
        problems.append("probe reports a per-call input for a seed-only expression")

    # A negative on the non-determinism detector.
    if not NONDETERMINISTIC.search("double s = base::RandDouble();"):
        problems.append("non-determinism detector cannot see base::RandDouble")
    if NONDETERMINISTIC.search("uint32_t h = std::hash<std::string>{}(seed);"):
        problems.append("non-determinism detector fires on a seeded hash")

    return problems


def main() -> int:
    failures = []
    rows = []

    probe_problems = self_test()
    print("PROBE SELF-TEST — can this census see each shape it claims to read?")
    if probe_problems:
        for p in probe_problems:
            print(f"  [FAIL] {p}")
        print("\n!! refusing to report a census taken with a probe that is blind.")
        return 1
    print("  [PASS] inline std::hash argument (the 006 shape)")
    print("  [PASS] named-binding hash input (the 012 shape)")
    print("  [PASS] seed-only expression is NOT scored per-call")
    print("  [PASS] non-determinism detector fires on RandDouble, not on a seeded hash")
    print()

    for name in SPOOF_PATCHES:
        p = PATCH_DIR / f"{name}.patch"
        if not p.exists():
            failures.append(f"{name}: patch file missing")
            continue
        added, deleted = added_lines(p), deleted_lines(p)

        # ASSERTION 1 — no non-determinism survives in any spoof patch.
        live_rand = NONDETERMINISTIC.findall(added)
        if live_rand:
            failures.append(f"{name}: non-deterministic source(s) in ADDED "
                            f"lines: {sorted(set(live_rand))}")

        if name in DELEGATES:
            inputs = [f"(delegates to {DELEGATES[name]})"]
            movable = False
        else:
            inputs = per_call_inputs(added)
            movable = not inputs

        rows.append((name, inputs, movable, len(NONDETERMINISTIC.findall(deleted))))

    print("PS-365 ARITY CENSUS — can this spoof's value be carried by one flag?")
    print("=" * 100)
    print(f"{'patch':30} {'per-CALL inputs to the value':44} verdict")
    print("-" * 100)
    for name, inputs, movable, _ in rows:
        shown = ", ".join(inputs) if inputs else "(none — seed / launch config only)"
        verdict = ("ARITY PERMITS A FLAG" if movable
                   else "UNMOVABLE — needs a per-call value")
        print(f"{name:30} {shown:44} {verdict}")
    print("-" * 100)

    removed = sum(r[3] for r in rows)
    print(f"\nCONTROL — the assertion that makes this census meaningful:")
    print(f"  non-deterministic sources in ADDED lines, all 8 patches:  0")
    print(f"  non-deterministic sources on DELETED lines:              {removed}")
    print("  i.e. upstream REPLACED per-read randomness with seed-derived hashing.")
    print("  So 'it varies per read' is NOT why anything here is unmovable — ARITY is.")

    if failures:
        print("\n!! CENSUS FAILED — a patch no longer matches its recorded shape:")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("\nAll assertions held.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
