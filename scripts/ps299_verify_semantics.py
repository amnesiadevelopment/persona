#!/usr/bin/env python3
"""PS-299 semantic gate: does the REBASED patch set still make the same changes?

A patch that APPLIES is not a patch that still DOES anything — a re-anchored
hunk can silently land in the wrong place, or a call we insert can be dropped.
So this compares the two patch sets by the SYMBOLS they inject, not by their
text: every fingerprint identifier the 144 set adds must also be added by the
152 set, and any identifier that disappears must be explained.
"""
import re, os, sys

import argparse
_ap = argparse.ArgumentParser(description="compare two fingerprint patch sets by the SYMBOLS they inject")
_ap.add_argument("old", help="patch dir BEFORE the rebase (e.g. a git worktree at the previous commit)")
_ap.add_argument("new", help="patch dir AFTER the rebase (e.g. engine/patches/fingerprint)")
_a = _ap.parse_args()
OLD, NEW = _a.old, _a.new
# identifiers that ARE the patch layer's behaviour
PAT = re.compile(r"\b(?:switches::k\w+|GetUserAgentFingerprint\w+|UpdateUserAgentMetadata\w+|"
                 r"kFingerprint\w*|GetFingerprint\w+|ShuffleSubchannelColorData|"
                 r"gpu_fingerprint|GpuFingerprint|fingerprint_data|GetGpuInfo\w*|"
                 r"FingerprintingCanvas\w*|seed_str)\b")

def injected(path):
    """multiset of fingerprint identifiers on INSERTED lines"""
    counts = {}
    for l in open(path, encoding="utf-8", errors="replace"):
        if l.startswith("+++") or l.startswith("---"):
            continue
        if l.startswith("+"):
            for m in PAT.findall(l):
                counts[m] = counts.get(m, 0) + 1
    return counts

fail = 0
for f in sorted(x for x in os.listdir(NEW) if x.endswith(".patch")):
    op = os.path.join(OLD, f)
    if not os.path.exists(op):
        print("== %s  NEW PATCH (no counterpart in %s) — nothing to compare" % (f, OLD))
        continue
    o, n = injected(op), injected(os.path.join(NEW, f))
    lost = {k: o[k] - n.get(k, 0) for k in o if o[k] > n.get(k, 0)}
    gained = {k: n[k] - o.get(k, 0) for k in n if n[k] > o.get(k, 0)}
    if lost or gained:
        print("== %s" % f)
        for k, v in sorted(lost.items()):
            print("   LOST   %-45s x%d" % (k, v)); fail += 1
        for k, v in sorted(gained.items()):
            print("   GAINED %-45s x%d" % (k, v))
for f in sorted(x for x in os.listdir(OLD) if x.endswith(".patch")):
    if not os.path.exists(os.path.join(NEW, f)):
        print("== %s  PATCH DISAPPEARED from the rebased set" % f); fail += 1

print()
print("FAIL: %d fingerprint identifiers lost" % fail if fail else
      "PASS: every fingerprint identifier injected at 144 is still injected at 152")
sys.exit(1 if fail else 0)
