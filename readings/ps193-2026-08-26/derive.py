#!/usr/bin/env python3
"""PS-193 — re-derive every number this reading claims, from the committed records.

PS-16's maintenance rule is "re-derive, never edit-to-match". This script is how
the PS-16 patch was produced: it reads the raw JSON in live/ and loopback/ and
prints the figures. Nothing in EVIDENCE.md or PS-16-PATCH.md is typed from
memory.

    python3 readings/ps193-2026-08-26/derive.py
"""
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent

CENSUS_ARMS = ["census-arm1", "census-arm2", "census-arm3", "census-arm4"]
SPOOF_ARMS = ["spoof-seed1337", "spoof-seed4242"]


def load(sub, name):
    return json.loads((HERE / sub / f"{name}.json").read_text())


def main():
    print("=" * 78)
    print("1. THE CENSUS — bytes in CreepJS's sampled region, and bytes passing")
    print("   the shipped guard `v > 1 && v < 254`.")
    print("=" * 78)
    print("%-14s %-17s %-34s %6s %6s %6s" % (
        "arm", "exit", "asn/org", "total", "elig", "ctxs"))
    totals, eligs = set(), set()
    for a in CENSUS_ARMS:
        d = load("live", a)
        recs = [r for r in d.get("records", []) if "total_bytes" in r]
        t = {r["total_bytes"] for r in recs}
        e = {r["guard_eligible"] for r in recs}
        totals |= t
        eligs |= e
        ex = d.get("exit", {})
        print("%-14s %-17s %-34s %6s %6s %6d" % (
            a, ex.get("ip", "?"), (ex.get("org") or "?")[:34],
            ",".join(map(str, sorted(t))), ",".join(map(str, sorted(e))), len(recs)))
    print()
    print("  DISTINCT total_bytes across all census arms :", sorted(totals))
    print("  DISTINCT guard_eligible across all arms     :", sorted(eligs))
    assert len(totals) == 1 and len(eligs) == 1, "arms disagree — do not quote a single figure"
    total, elig = totals.pop(), eligs.pop()
    print()
    print("  >>> TOTAL BYTES    = %d" % total)
    print("  >>> GUARD-ELIGIBLE = %d   (%.2f%% of the region)" % (elig, 100.0 * elig / total))

    d0 = load("live", CENSUS_ARMS[0])
    r0 = [r for r in d0["records"] if "total_bytes" in r][0]
    print()
    print("  geometry as PASSED to readPixels (recorded, not assumed):")
    print("    w=%r (integer: %s)  h=%r (integer: %s)" % (
        r0["w"], r0["w_is_int"], r0["h"], r0["h_is_int"]))
    print("    drawingBuffer=%sx%s  canvas_class=%s" % (
        r0["dbw"], r0["dbh"], r0.get("canvas_class")))
    print("    engine=%s" % d0.get("engine_build"))
    print("    renderer=%s" % (d0.get("gl") or {}).get("renderer"))
    print("    creepjs published pixel hash = %s" % d0.get("creep_webgl_row"))

    print()
    print("=" * 78)
    print("2. THE VERDICT vs the two candidates")
    print("=" * 78)
    print("  candidate 1 (region STARVED, PS-182 geometry C = ZERO eligible):")
    print("     measured eligible = %d  ->  %s" % (
        elig, "REFUTED (non-zero)" if elig else "SUPPORTED"))
    print("     PS-182 geometry B had 16 eligible and yielded 4 DISTINCT digests;")
    print("     this region has %d, i.e. %.1fx B's headroom." % (elig, elig / 16.0))
    print()
    print("  candidate 2 (delta never reaches the realm CreepJS reads):")
    print("     %-12s %-14s %-14s" % ("seed", "creepjs got", "page realm"))
    creep, page = {}, {}
    for a in SPOOF_ARMS:
        d = load("live", a)
        seed = d.get("spoof_seed")
        cs = {r["fnv1a"] for r in d["records"] if r.get("context_type")}
        ctl = (d.get("spoof_control") or {}).get("canvas", {}).get("fnv")
        creep[seed] = cs
        page[seed] = ctl
        print("     %-12s %-14s %-14s" % (
            seed, ",".join(map(str, sorted(cs))), ctl))
    seeds = sorted(creep)
    creep_same = creep[seeds[0]] == creep[seeds[1]]
    page_diff = page[seeds[0]] != page[seeds[1]]
    print()
    print("     creepjs digests IDENTICAL across seeds : %s" % creep_same)
    print("     page-realm digests DIFFER across seeds : %s   <- the positive control" % page_diff)
    if creep_same and page_diff:
        print("     >>> the spoof RAN and its delta did NOT reach CreepJS's realm")
        print("     >>> candidate 2 CONFIRMED")

    print()
    print("=" * 78)
    print("3. THE MECHANISM (loopback, no exit) — which realms the spoof reaches")
    print("=" * 78)
    rp = json.loads((HERE / "loopback" / "realm-probe.json").read_text())
    print("  %-20s %-12s %-12s %-12s  %s" % (
        "realm", "unspoofed", "seed1337", "seed4242", "verdict"))
    for realm in ("top_canvas", "top_offscreen", "phantom_canvas", "phantom_offscreen"):
        vals = [(rp.get(k) or {}).get(realm, {}).get("fnv")
                for k in ("unspoofed", "seed_1337", "seed_4242")]
        moved = vals[1] is not None and vals[1] != vals[2]
        print("  %-20s %-12s %-12s %-12s  %s" % (
            realm, vals[0], vals[1], vals[2],
            "REACHED (seed-dependent)" if moved else "NOT REACHED (identical)"))
    print()
    print("  It is the REALM, not OffscreenCanvas: top_offscreen IS reached,")
    print("  phantom_canvas is NOT.")


if __name__ == "__main__":
    main()
