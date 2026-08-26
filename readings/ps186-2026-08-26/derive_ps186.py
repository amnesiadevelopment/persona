#!/usr/bin/env python3
"""PS-186 — re-derive the claims THIS sweep adds, beyond the inherited instrument.

`derive.py` (inherited verbatim from PS-177, unmodified) already re-derives the
per-record coverage and the per-arm Level 2 verdict. This script does NOT repeat
it. It derives the four claims PS-186 makes that PS-177's script has no reason to
know about, each of which spans records the inherited tool never compares:

  1. THE CROSS-CORPUS webgl_pixel_hash BOUND. PS-16 records `n = 2` for the
     firefox readback failure. Raising it requires reading seeds from the OLD
     corpus alongside this sweep's, which is a cross-DIRECTORY comparison.
  2. THE POOL / ASN TABLE. The ticket's premise about the new credential is
     checked against what the records actually carry, and against the old corpus.
  3. THE pixelscan VERDICT BLOCK, all five rows, printed EVEN WHEN ABSENT —
     the ticket requires `proxy_detected` recorded explicitly either way.
  4. THE "STRUCTURALLY UNOBTAINABLE" LIST. PS-16 names 8 checkers no automated
     run can obtain. This sweep contradicts that for two of them, so the claim
     is re-counted from the records rather than inherited.

    ./derive_ps186.py            # reads ./matrix and ../ for the old corpus

WHAT THIS SCRIPT REFUSES TO DO — the same discipline as the inherited one:

  * It never reports a checker clean because it was silent. `unobtainable`
    means NOBODY LOOKED and is never a pass.
  * It never counts an `adverse` PATTERN on a non-`read` row as a finding. That
    flag is the catalogue's definition ("if this matched it would be bad"), not
    a verdict.
  * It states the residual bound on every claim rather than rounding it off.
"""
from __future__ import annotations

import glob
import json
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.dirname(HERE)  # readings/

# The five pixelscan rows the ticket names. `proxy_detected` is the load-bearing
# one: it is the cheapest witness to whether the mobile->residential pool change
# is visible to a checker at all, and the ticket requires it recorded EVEN WHEN
# ABSENT — an absent row is a real observation here, not a missing one.
PIXELSCAN_VERDICTS = [
    "proxy_detected",
    "fingerprint_inconsistent",
    "masking_detected",
    "automation_detected",
    "timezone_spoofed",
]

# PS-16's list of checkers it says no automated run can obtain. Re-counted from
# the records rather than trusted, because this sweep's pool is not the pool
# that list was written against.
PS16_UNOBTAINABLE = [
    "browserscan.net",
    "amiunique.org",
    "coveryourtracks.eff.org",
    "whoer.net",
    "fv.pro",
    "bot-detector.rebrowser.net",
    "deviceandbrowserinfo.com",
    "tools.scrapfly.io",
]


def engine_of(rec: dict) -> str:
    """PS-16's engine name, from the record's own build string.

    The build string's first segment is the DRIVER, not the engine
    (`invisible_playwright/firefox-20`), so this keys on content.
    """
    build = rec.get("engine", "")
    if "firefox" in build or "invisible_playwright" in build:
        return "firefox"
    if "chromium" in build:
        return "chromium"
    return build or "unknown"


def load_dir(path: str) -> list:
    out = []
    for f in sorted(glob.glob(os.path.join(path, "**", "*.json"), recursive=True)):
        try:
            with open(f) as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and "readings" in d and "seed" in d:
            out.append((f, d))
    return out


def row(rec: dict, checker: str, item: str):
    for r in rec["readings"]:
        if r["checker"] == checker and r["item"] == item:
            return r
    return None


def hr(title: str) -> None:
    print(f"\n\n{'#' * 78}\n# {title}\n{'#' * 78}")


# ---------------------------------------------------------------------------
# 1. THE CROSS-CORPUS BOUND
# ---------------------------------------------------------------------------
def webgl_bound(new: list, old: list) -> None:
    hr("1. creepjs :: webgl_pixel_hash — THE BOUND PS-16 RECORDS AS n=2")
    print("""
PS-16: the firefox readback FAILS — `51df3565` on both firefox seeds — at a
bound of n=2, and 'a third firefox seed settles it'. That bound is raised only
by comparing THIS sweep's seeds against the OLD corpus's, so both are read here.
A row is counted only when its state is `read`; an absent or unobtainable row
contributes nothing in either direction.
""")
    per_engine = defaultdict(list)
    for path, rec in old + new:
        r = row(rec, "creepjs", "webgl_pixel_hash")
        if not r or r.get("state") != "read":
            continue
        ex = rec.get("exit") or {}
        per_engine[engine_of(rec)].append(
            {
                "seed": rec["seed"],
                "hash": r.get("value"),
                "ip": ex.get("ip"),
                "org": ex.get("org"),
                "day": (rec.get("observed_at") or "")[:10],
                "src": os.path.relpath(path, CORPUS),
                "new": (path, rec) in new,
            }
        )

    for engine in sorted(per_engine):
        obs = sorted(per_engine[engine], key=lambda o: (o["seed"], o["day"]))
        seeds = sorted({o["seed"] for o in obs})
        hashes = sorted({o["hash"] for o in obs})
        exits = sorted({o["ip"] for o in obs if o["ip"]})
        days = sorted({o["day"] for o in obs if o["day"]})
        print(f"\n  === {engine.upper()} ===")
        for o in obs:
            mark = "NEW " if o["new"] else "old "
            print(
                f"    {mark}seed {str(o['seed']):<6} hash {str(o['hash']):<10} "
                f"exit {str(o['ip']):<16} {str(o['org'])[:34]:<34} {o['day']}"
            )
        print(
            f"    -> {len(obs)} reading(s), {len(seeds)} distinct seed(s) "
            f"{seeds}, {len(hashes)} distinct hash value(s), "
            f"{len(exits)} exit(s), {len(days)} day(s)"
        )
        if len(hashes) == 1 and len(seeds) > 1:
            print(
                f"    ⚠ SEED-INVARIANT: every seed reads {hashes[0]}. Two profiles on "
                f"this engine\n"
                f"      carry an IDENTICAL high-entropy row -> they are LINKABLE. "
                f"BOUND: n = {len(seeds)}."
            )
        elif len(hashes) == len(seeds):
            print(
                "    SEED-DERIVED: every seed reads a distinct value — this row does "
                "not link profiles."
            )
        else:
            print("    MIXED — neither cleanly seed-derived nor seed-invariant.")


# ---------------------------------------------------------------------------
# 2. THE POOL
# ---------------------------------------------------------------------------
def pool(new: list, old: list) -> None:
    hr("2. THE EXIT POOL — old corpus vs this sweep")
    print("""
The ticket states the new credential is `AS12912 T-Mobile Polska`, 'the same ASN
already in the corpus'. Counted from the records rather than assumed, because a
record that hard-codes one ASN is false for every run that drew another.
""")
    for label, group in (("OLD CORPUS (pre-PS-186)", old), ("THIS SWEEP (PS-186)", new)):
        c = Counter()
        for _, rec in group:
            ex = rec.get("exit") or {}
            if ex.get("org"):
                c[ex["org"]] += 1
        print(f"\n  {label} — {sum(c.values())} record(s) carrying an exit:")
        for org, n in c.most_common():
            print(f"    {n:3d}  {org}")

    old_orgs = {(r.get("exit") or {}).get("org") for _, r in old}
    new_orgs = {(r.get("exit") or {}).get("org") for _, r in new}
    fresh = sorted(o for o in new_orgs - old_orgs if o)
    print("\n  ASNs in this sweep that appear NOWHERE in the old corpus:")
    print("    " + (", ".join(fresh) if fresh else "(none)"))
    print("\n  per-record exits (never a constant — the pool rotates per call):")
    for path, rec in new:
        ex = rec.get("exit") or {}
        print(
            f"    {engine_of(rec):9s}/{rec['declared_machine']:8s}/seed "
            f"{str(rec['seed']):<6} {str(ex.get('ip')):<16} "
            f"{str(ex.get('city')):<12} {ex.get('org')}"
        )


# ---------------------------------------------------------------------------
# 3. pixelscan, ALL FIVE ROWS, ABSENT ONES INCLUDED
# ---------------------------------------------------------------------------
def pixelscan(new: list) -> None:
    hr("3. pixelscan VERDICTS — all five rows, printed even when ABSENT")
    print("""
`state` is the whole point of this block:
  read + adverse -> the checker OBJECTED (a finding)
  read           -> asked, did not object (evidence)
  absent         -> the page was read, the adverse pattern did NOT match
  unobtainable   -> NOBODY LOOKED (never a pass)
""")
    for path, rec in new:
        print(f"\n  {engine_of(rec)}/{rec['declared_machine']}/seed {rec['seed']}")
        for item in PIXELSCAN_VERDICTS:
            r = row(rec, "pixelscan.net", item)
            if r is None:
                print(f"    {item:26s} ROW NOT IN CATALOGUE")
                continue
            fired = r.get("state") == "read" and r.get("adverse")
            print(
                f"    {item:26s} state={r['state']:<13} "
                f"value={str(r.get('value')):<6} "
                f"{'<- FIRED' if fired else ''}"
            )


# ---------------------------------------------------------------------------
# 4. THE UNOBTAINABLE LIST
# ---------------------------------------------------------------------------
def pixelscan_rendered(new: list) -> None:
    """Did pixelscan's VERDICT BLOCK render at all on this run?

    This is the difference between "pixelscan cleared us" and "pixelscan never
    said anything", and the two look IDENTICAL if you only read
    `fingerprint_inconsistent`: both show `absent`.

    pixelscan states the same fact in two opposite-polarity rows —
    `fingerprint_consistent` ("Your Browser Fingerprint is consistent") and
    `fingerprint_inconsistent`. If NEITHER was read, the verdict block did not
    render and the run has NO pixelscan verdict, which is a COVERAGE HOLE and
    must never be scored as a pass. If either was read, the block rendered and
    the reading is real.

    PS-16 says these verdicts fire in every arm we own. A run where they are
    merely absent would look like a contradiction of that; this function is what
    tells the two apart.
    """
    hr("3b. DID pixelscan's VERDICT BLOCK RENDER? (absent-because-clean vs "
       "absent-because-nothing-rendered)")
    print("""
    read `fingerprint_consistent`   -> block rendered, verdict = CONSISTENT
    read `fingerprint_inconsistent` -> block rendered, verdict = INCONSISTENT
    NEITHER read                    -> NO VERDICT RENDERED. Coverage hole.
                                       NOT a pass, and NOT evidence that the
                                       arm is clean.
""")
    for _, rec in new:
        pos = row(rec, "pixelscan.net", "fingerprint_consistent")
        neg = row(rec, "pixelscan.net", "fingerprint_inconsistent")
        pos_read = bool(pos and pos.get("state") == "read")
        neg_read = bool(neg and neg.get("state") == "read")
        label = f"{engine_of(rec):9s}/{rec['declared_machine']:8s}/seed {str(rec['seed']):<6}"
        if neg_read:
            print(f"  {label} RENDERED -> INCONSISTENT (adverse fired)")
        elif pos_read:
            print(f"  {label} RENDERED -> CONSISTENT  (pixelscan affirms the "
                  f"fingerprint is consistent)")
        else:
            print(f"  {label} ⚠ NO VERDICT RENDERED — coverage hole, NOT a pass")


def gpu_agreement(new: list) -> None:
    """Do creepjs and pixelscan see the SAME graphics card on one run?

    This is the PS-170 defect class: ONE profile handing TWO different cards to
    two checkers on the same run. PS-16 records it as fixed and verified — but
    ONLY on chromium/windows, the single arm that had any checker coverage.
    macos and linux were `—` in every column, so the fix was never read there.

    ⚠️ NORMALISATION IS LOAD-BEARING, NOT COSMETIC. pixelscan renders the
    renderer string with a trailing ", or similar" that creepjs does not. A raw
    == comparison therefore reports DISAGREE on two records where the two
    checkers in fact report a byte-identical card, which would be a FABRICATED
    finding of exactly the kind this project's own PS-11 article warns about.
    The suffix is stripped before comparing, and the raw values are printed so a
    reader can check the stripping rather than trust it.
    """
    hr("6. GPU IDENTITY AGREEMENT — creepjs vs pixelscan, same run (the PS-170 "
       "defect class)")
    print("""
PS-170 fixed 'one profile, two different graphics cards to two checkers' and
PS-16 records it verified — on chromium/windows ONLY, the one arm that had
checker coverage. These are the first readings that can check the other arms.

pixelscan suffixes its renderer string with ", or similar"; creepjs does not.
That suffix is stripped before comparing (raw values printed below), because
comparing them raw manufactures a disagreement that is not there.
""")
    suffix = ", or similar"

    def norm(v):
        if v is None:
            return None
        t = str(v).strip()
        return t[: -len(suffix)].strip() if t.endswith(suffix) else t

    for _, rec in new:
        def read_value(checker, item):
            r = row(rec, checker, item)
            return r.get("value") if r and r.get("state") == "read" else None

        cr = read_value("creepjs", "gpu_renderer")
        pr = read_value("pixelscan.net", "webgl_renderer")
        label = f"{engine_of(rec):9s}/{rec['declared_machine']:8s}/seed {str(rec['seed']):<6}"
        if cr is None or pr is None:
            print(f"\n  {label} one side not read — CANNOT COMPARE (coverage, "
                  f"not a pass)")
            continue
        agree = norm(cr) == norm(pr)
        print(f"\n  {label} {'AGREE' if agree else '⚠ DISAGREE — TWO CARDS ON ONE PROFILE'}")
        print(f"      creepjs   gpu_renderer   = {cr}")
        print(f"      pixelscan webgl_renderer = {pr}")
        if not agree:
            print("      ^ the two checkers were handed DIFFERENT graphics "
                  "cards on the SAME run.")


def unobtainable(new: list) -> None:
    hr("4. PS-16's 8 'STRUCTURALLY UNOBTAINABLE' CHECKERS — re-counted")
    print("""
PS-16 lists 8 checkers it says no automated run can obtain, several because they
'refuse our SOCKS exit'. That claim was written against the OLD pool, so it is
re-counted here rather than inherited — a checker that refused one exit has not
necessarily refused this one.
""")
    agg = defaultdict(Counter)
    for _, rec in new:
        for r in rec["readings"]:
            if r["checker"] in PS16_UNOBTAINABLE:
                agg[r["checker"]][r["state"]] += 1
    for ck in PS16_UNOBTAINABLE:
        states = dict(agg[ck])
        readable = states.get("read", 0) > 0
        flag = "  <- CONTRADICTS PS-16: rows were READ" if readable else ""
        print(f"    {ck:28s} {states}{flag}")


# ---------------------------------------------------------------------------
# 5. WHAT FIRED, AND THE CLASSIFIER CAVEAT
# ---------------------------------------------------------------------------
def fired_table(new: list) -> None:
    hr("5. ADVERSE VERDICTS THAT ACTUALLY FIRED (read AND adverse)")
    for _, rec in new:
        f = [r for r in rec["readings"] if r.get("state") == "read" and r.get("adverse")]
        print(
            f"\n  {engine_of(rec):9s}/{rec['declared_machine']:8s}/seed "
            f"{str(rec['seed']):<6} fired={len(f)}"
        )
        for r in f:
            print(f"    FIRED  {r['checker']} :: {r['item']} = {r.get('value')}")

    hr("5b. CAVEAT ON THE INHERITED LINKAGE CLASSIFIER — read before quoting it")
    print("""
`derive.py` classifies `bot-detector.rebrowser.net :: detected =
'navigatorWebdriver'` as ENTROPY-BEARING, so it appears in its "rows that tie the
two profiles together" list on all three chromium arms. That classification is
WRONG, and it is wrong in the direction the inherited script's own docstring
warns about.

The value is a DETECTOR'S REASON TOKEN — the name of the signal that tripped —
not an attribute of the profile. It reaches the entropy class only because it is
a non-numeric string of >= 8 characters and carries no `vector` tag. Two profiles
both reading `navigatorWebdriver` are BOTH DETECTED FOR THE SAME REASON; they are
not thereby tied to each other, any more than two profiles both reading
"webdriver check: passed" are.

This does NOT weaken any Level 2 verdict below, because every arm it appears on
FAILS on other, genuinely entropy-bearing rows (GPU renderer/vendor strings and,
on firefox, the pixel hash). It is recorded because a later reader quoting
derive.py's row list unamended would be overstating the linkage surface by one
row per chromium arm.

The row is a REAL FIRED VERDICT and is reported as such in §5 — the caveat is
about what it can LINK, not about whether it fired.
""")


def main() -> int:
    new = load_dir(os.path.join(HERE, "matrix"))
    old = [
        (p, d)
        for p, d in load_dir(CORPUS)
        if not os.path.abspath(p).startswith(os.path.abspath(HERE))
    ]
    if not new:
        print("no PS-186 records found", file=sys.stderr)
        return 2
    print(f"PS-186 derivation — {len(new)} new record(s), {len(old)} prior corpus record(s)")
    webgl_bound(new, old)
    pool(new, old)
    pixelscan(new)
    pixelscan_rendered(new)
    gpu_agreement(new)
    unobtainable(new)
    fired_table(new)
    print("\n\ndone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
