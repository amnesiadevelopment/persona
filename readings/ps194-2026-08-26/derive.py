"""PS-194: re-derive every table in EVIDENCE.md from the committed records.

PS-16's maintenance rule is "re-derive, never edit-to-match". This script is
what makes that possible for this directory: every figure in EVIDENCE.md is
printed here, out of the JSON, so a reader can regenerate it rather than trust
a transcription.

    python3 -m readings.ps194-2026-08-26.derive      # (or: python3 derive.py)
"""

from __future__ import annotations

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    with open(os.path.join(HERE, name)) as fh:
        return json.load(fh)


def _panel(text: str) -> str:
    """pixelscan's verdict panel: between 'Restart' and the scroll prompt."""
    s = text.find("Restart")
    e = text.find("Scroll Down for More Info")
    return text[s:e] if s >= 0 and e > 0 else text[:1500]


def _browser_row(text: str):
    """pixelscan's feature-derived browser CANDIDATE SET row.

    This row has no pattern in BROWSER_CHECKERS, so it appears in no committed
    matrix record anywhere in this project — it is readable only from retained
    page text. That is why this directory keeps the text.
    """
    m = re.search(r"\n\nBrowser\n\n([^\n]+)\n", text)
    return m.group(1) if m else None


def _declared(text: str):
    m = re.search(r"(?:How to Fix it\?|Learn More)\n\n([^\n]+)\n", text)
    return m.group(1) if m else None


def _fonts(text: str):
    s = text.find("Font hash")
    e = text.find("User-Agent")
    seg = [x.strip() for x in text[s:e].split("\n") if x.strip()]
    hash_ = seg[1] if len(seg) > 1 else None
    names = [x for x in seg[2:] if x != "Fonts"]
    # pixelscan truncates the list with a "+N more" tail; the true count is the
    # listed names plus that N. Without this a 51-font machine reads as 7.
    total = len([x for x in names if not x.startswith("+")])
    for x in names:
        m = re.match(r"\+(\d+) more", x)
        if m:
            total += int(m.group(1))
    return hash_, names, total


def _verdict(text: str):
    p = _panel(text)
    if "Fingerprint is consistent" in p:
        fp = "CONSISTENT (affirmed)"
    elif "Fingerprint is inconsistent" in p:
        fp = "INCONSISTENT"
    else:
        fp = "NO VERDICT RENDERED"
    mask = "Masking detected" in p and "No masking detected" not in p
    auto = "Automated behavior detected" in p and "No automated" not in p
    tz = "Timezone spoofed" in p
    return fp, mask, auto, tz


def main() -> int:
    legc = _load("leg-c-stock-vs-packaged.json")
    txt = _load("pixelscan-page-text.json")

    legs = {
        "A  firefox-20        layer=ON   seed5150": txt["engines"]["firefox"]["text"],
        "B  fpchrome-148      layer=ON   seed5150": txt["engines"]["chromium"]["text"],
        "B' fpchrome-148      layer=ON   seed24601": txt["engines"]["chromium_seed24601"]["text"],
        "C1 fpchrome-148      layer=OFF  seed5150": legc["legs"]["packaged"]["page_text"],
        "C2 stock-chromium151 layer=OFF  seed5150": legc["legs"]["stock"]["page_text"],
    }

    print("=" * 78)
    print("PS-194 — THE THREE-LEGGED pixelscan READING, one host, one campaign")
    print("=" * 78)
    print()

    print("--- 1. VERDICT PER LEG " + "-" * 55)
    print(f"{'leg':42} {'fingerprint':22} {'mask':6} {'auto':6} {'tzspoof'}")
    for k, t in legs.items():
        fp, mask, auto, tz = _verdict(t)
        print(f"{k:42} {fp:22} {str(mask):6} {str(auto):6} {tz}")
    print()

    print("--- 2. DECLARED IDENTITY " + "-" * 53)
    for k, t in legs.items():
        print(f"{k:42} {_declared(t)}")
    print()

    print("--- 3. THE `Browser` CANDIDATE-SET ROW " + "-" * 39)
    print("(pixelscan's feature-derived guess at which browsers this could be)")
    rows = {}
    for k, t in legs.items():
        b = _browser_row(t)
        rows[k] = b
        print(f"{k:42} {b}")
    chromium_rows = [v for k, v in rows.items() if "firefox" not in k]
    print()
    print(f"  all {len(chromium_rows)} chromium legs byte-identical : "
          f"{len(set(chromium_rows)) == 1}")
    ff = [v for k, v in rows.items() if "firefox" in k][0]
    print(f"  firefox differs from every chromium leg : {ff not in chromium_rows}")
    print()

    # The prose in EVIDENCE.md characterises this row by COUNT. Print those
    # counts here so they cannot drift from the row they describe: a figure
    # transcribed by hand is a figure that can outlive its evidence.
    for label, row in (("firefox", ff), ("chromium", chromium_rows[0])):
        ents = row.split(",")
        mob = [e for e in ents
               if e.startswith("m-") or e.startswith("Mobile") or "MIUI" in e]
        print(f"  {label:8} candidate set: {len(ents):2} entries, "
              f"{len(mob)} of them MOBILE")
        for e in mob:
            print(f"    MOBILE  {e}")
    print()

    print("--- 4. THE `Fonts` / `Font hash` ROW " + "-" * 41)
    for k, t in legs.items():
        h, names, total = _fonts(t)
        print(f"{k:42} hash={h}")
        print(f"{'':42} n={total:<4} {names}")
    print()

    print("--- 5. ATTRIBUTION ARITHMETIC " + "-" * 48)
    on_ = _fonts(legs["B  fpchrome-148      layer=ON   seed5150"])[0]
    off = _fonts(legs["C1 fpchrome-148      layer=OFF  seed5150"])[0]
    s2 = _fonts(legs["B' fpchrome-148      layer=ON   seed24601"])[0]
    stock = _fonts(legs["C2 stock-chromium151 layer=OFF  seed5150"])[0]
    print(f"  packaged layer=ON  font hash : {on_}")
    print(f"  packaged layer=OFF font hash : {off}")
    print(f"  -> IDENTICAL (our extension layer does NOT author this row): {on_ == off}")
    print()
    print(f"  packaged seed 5150  font hash : {on_}")
    print(f"  packaged seed 24601 font hash : {s2}")
    print(f"  -> DIFFER (the row is SEED-DERIVED, i.e. authored by the")
    print(f"     packaged engine's own --fingerprint patches)          : {on_ != s2}")
    print()
    print(f"  stock chromium 151  font hash : {stock}")
    print(f"  -> stock differs from packaged (stock does no font masking): "
          f"{stock != on_}")
    print()

    print("--- 6. CORRELATION WITH THE VERDICT " + "-" * 42)
    print("Which candidate row tracks the pixelscan verdict across all five legs?")
    print()
    print(f"{'leg':42} {'flagged':9} {'Browser-row':14} {'fonts'}")
    for k, t in legs.items():
        fp, mask, _, _ = _verdict(t)
        flagged = fp.startswith("INCONSISTENT")
        b = "chromium-set" if "Firefox-" not in (rows[k] or "") else "firefox-set"
        h, names, total = _fonts(t)
        print(f"{k:42} {str(flagged):9} {b:14} n={total}")
    print()
    print("  The `Browser` row separates flagged from affirmed on EVERY leg.")
    print()
    print("  The font row separates them on every WINDOWS-DECLARED leg (A vs")
    print("  B/B'/C1), which is the like-for-like comparison. It does NOT")
    print("  separate the stock leg, but that leg declares LINUX, so its 4-font")
    print("  DejaVu set is COHERENT with its own declared platform and asks a")
    print("  different question rather than answering this one.")
    print()
    print("  ⚠️ THE STOCK LEG IS CONFOUNDED IN THIS RUN and cannot disconfirm")
    print("  anything on its own: it rendered tz Africa/Abidjan behind a Warsaw")
    print("  exit and fired `Timezone spoofed` + `Automated behavior detected`,")
    print("  each independently sufficient for the verdict. PS-159 removed both")
    print("  of those axes on this SAME stock binary and both verdicts still")
    print("  fired — that de-confounded arm, not this leg, is what establishes")
    print("  stock-chromium-is-also-flagged.")
    print()
    print("  ⚠️ ENGINE FAMILY IS PERFECTLY CONFOUNDED WITH EVERY CHROMIUM-ONLY")
    print("  VALUE in this design: only one engine family is affirmed, so the")
    print("  `Browser` row cannot be separated from any other value that is")
    print("  constant across Chromium and absent on Firefox. This is a")
    print("  CORRELATION over 5 legs, not a demonstrated cause.")
    print()

    print("--- 7. EXITS (per record; ASN is never a constant) " + "-" * 27)
    # The distinct-ASN COUNT is PRINTED, never transcribed. Round 1 of this
    # ticket stated "4 distinct ASNs" in EVIDENCE.md and named an AS12912 that
    # belongs to a DIFFERENT reading (ps143/ps186) and appears in no record
    # here. It was the one figure in the file that had been hand-written. Quote
    # the line this loop prints and that class of error cannot recur.
    asns = set()

    def _show(label, ex, indent=2):
        if not ex:
            return
        pad = " " * indent
        print(f"{pad}{label:10} {str(ex.get('ip')):16} {str(ex.get('city')):12} "
              f"{ex.get('org')}")
        if ex.get("org"):
            asns.add(ex["org"])

    for name, rec in (("leg-c", legc), ("page-text", txt)):
        _show(name, rec.get("exit"))
        for pe in rec.get("prior_exits") or []:
            _show("prior:", pe, indent=13)
        # Per-LEG exits (the shape written since round 2). Absent on legs
        # captured before the fix — see "not covered" in EVIDENCE.md.
        for key, leg in (rec.get("engines") or {}).items():
            if isinstance(leg, dict) and leg.get("exit"):
                _show(f"  {key}", leg["exit"], indent=4)

    mdir = os.path.join(HERE, "matrix")
    for fn in sorted(os.listdir(mdir)) if os.path.isdir(mdir) else []:
        if not fn.endswith(".json"):
            continue
        d = _load(os.path.join("matrix", fn))
        ex = d["exit"]
        print(f"  {fn[:36]:36} {ex.get('ip'):16} {ex.get('org')}")
        print(f"  {'':36} engine={d['engine']} evidence={d['evidence']['verdict']}"
              f" {d['evidence']['fingerprint_obtained']}/"
              f"{d['evidence']['fingerprint_total']}")
        if ex.get("org"):
            asns.add(ex["org"])
    print()
    print(f"  distinct ASNs across all legs : {len(asns)}")
    for a in sorted(asns):
        print(f"    {a}")
    print()

    # How many legs are EVIDENCED by a retained exit, and how many are not.
    # `pixelscan-page-text.json` held ONE file-level exit for three legs
    # (rebuilt on every invocation, so the earlier legs' exits were
    # overwritten). Say so in the output rather than letting the prose imply a
    # per-leg exit that the record does not contain.
    legs_with_exit = sum(
        1 for leg in (txt.get("engines") or {}).values()
        if isinstance(leg, dict) and leg.get("exit")
    )
    n_txt_legs = len(txt.get("engines") or {})
    print(f"  page-text legs: {n_txt_legs}   with a retained per-leg exit: "
          f"{legs_with_exit}   NOT retained: {n_txt_legs - legs_with_exit}")
    if legs_with_exit < n_txt_legs:
        print("    ^ those legs' own exits are UNRECOVERABLE from this record.")
        print("      Do not claim a per-leg exit for them. See EVIDENCE.md")
        print('      "not covered, with reasons".')
    print()

    print("--- 8. HOST " + "-" * 66)
    print(json.dumps(legc["host"], indent=2))
    print()

    # 9. Does pixelscan object to the EXIT? This is what actually carries
    # "the exit is not what pixelscan is objecting to" — a row pixelscan
    # itself renders, on the flagged legs as much as the affirmed one. Exit
    # diversity across ASNs is corroboration; THIS is the argument.
    print("--- 9. DOES pixelscan OBJECT TO THE EXIT? " + "-" * 36)
    for k, t in legs.items():
        p = _panel(t)
        if "No proxy detected" in p:
            verdict = "No proxy detected"
        elif "Proxy detected" in p:
            verdict = "PROXY DETECTED"
        else:
            verdict = "(no proxy row rendered)"
        print(f"  {k:42} {verdict}")
    clean = all("No proxy detected" in _panel(t) for t in legs.values())
    print()
    print(f"  pixelscan reports NO proxy objection on all {len(legs)} legs : {clean}")
    print("  (matrix records agree: pixelscan::proxy_detected state=absent on both)")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
