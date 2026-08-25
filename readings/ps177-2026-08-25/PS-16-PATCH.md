# PS-16 patch — the cells PS-177 fills, and the ones it explicitly does not

**DoD #2 of PS-177 is: "Every cell it fills is written into knowledge article PS-16, in the same
ticket."** This file exists because **I have no MCP tool that can write a knowledge article.** The
worker toolset exposes `get_knowledge_article`, `search_knowledge_articles`, `list_knowledge_articles`
and `link_ticket_to_knowledge` — all read-or-link. There is no create/update counterpart, and
`add_comment` does not accept a knowledge article as a commentable.

So the edit is delivered here, ready to apply verbatim, rather than skipped or described vaguely.

**Which figures are derived, and which are judgement — read this before applying.** The original
version of this file claimed *"every figure below was printed by `derive.py`… nothing was hand-typed"*.
**That was false for the four scores in Edit 2**, and the reviewer was right to block on it: a false
provenance claim in a document whose entire value is provenance is worse than no claim at all.
The honest split:

* **Derived** — printed by `derive.py` from the committed record, and reproducible by re-running it
  (`derived-output.txt` is that output, committed alongside): all row counts, per-checker
  read/absent/unobtainable tallies, the evidence verdict and its fraction, the fired-verdict count,
  and the Level 2 comparison in Edit 6.
* **Judgement** — the four 0-100 scores in Edit 2 (`90 / 90 / 90 / 85`). `derive.py` emits no scores;
  the word `score` appears nowhere in its output. These are **my reading of the derived row data**,
  which is what a PS-16 score has always been — the article itself says a score is *"my judgement
  from the row data, not a number any checker emits"*. A score is inherently judgement and **cannot**
  be derived. Each one's reasoning is stated inline so it can be argued with.

**Whoever applies this: apply it as written.** If you re-type a *derived* number, re-run `derive.py`
instead. If you disagree with a *judgement* score, change it — and say why.

---

## Edit 1 — `Last re-derived` line at the top

Replace:

```
**Last re-derived: 2026-08-25, against `origin/main` @ `3989f97`.**
```

with:

```
**Last re-derived: 2026-08-25, against `origin/main` @ `c04e15d`.**
```

---

## Edit 2 — Table 1, the `firefox / windows / desktop` row

That row currently reads, with `~100 ⚠` and `~75 ⚠` sourced from the owner's eyesight:

```
| **firefox / windows / desktop** | ~100 ⚠ | ~75 ⚠ | — | — | — | — |
```

Replace with:

```
| **firefox / windows / desktop** | — ‡ | — ‡ | **90** † | **90** † | **90** † | **85** † |
```

**The four scores are JUDGEMENT, not derived output** — see the provenance note at the top of this
file. `derive.py` emits no scores; what it derives is the row data underneath them. Each score is my
reading of that data, in PS-16's own convention:

* **iphey 90, sannysoft 90, ipleak 90** — every row these checkers returned was read and *none* was
  adverse (0 rows both `read` and `adverse`), but each checker contributed only 2-6 rows and the
  entropy-bearing vectors were never reached, so the cell is "asked and silent", not "proven clean".
  90 rather than 100 is the discount for that narrowness.
* **TLS 85** — lower than the others for a specific, checkable reason: `tls.peet.ws ::
  akamai_fingerprint` is `absent` in this record, so one of the two TLS endpoints did not yield its
  headline fingerprint. The other 9 TLS rows read clean.

⚠️ These four values coincide with the chromium/windows row of Table 1. That is **not** a
transplant — it is what the same judgement scale produces on similar row data — but if you are
re-deriving this row later, treat the coincidence as a prompt to re-argue the numbers rather than
as corroboration.

And add these two footnotes below the existing `⚠` note:

```
† = **PS-177, seed 5150** (`readings/ps177-2026-08-25/reading.firefox.windows.seed5150.json`).
    FIRST automated Firefox reading in the corpus — this row was previously the owner's eyesight
    only. Asked and silent: `iphey :: not_trustworthy` absent (trustworthy / hardware_fine /
    software_fine all true, 3 rows), `sannysoft :: webdriver_present` and `:: phantom_js` both
    absent (2 rows read), `ipleak` 6 rows, TLS 9 rows across both endpoints. **ZERO adverse
    verdicts fired** (0 rows that are both `read` and `adverse`). Evidence verdict `sufficient`:
    7/28 fingerprint rows (25.0%), from 2 checkers.
    ⚠ The TLS and ipleak rows are JSON-tier — fetched by curl through the SOCKS proxy, not by the
    engine (their `user_agent` reads `curl/8.14.1`). They describe THE EXIT and are identical
    whichever engine is nominally under test. Only iphey / sannysoft / engine-exit describe the
    Firefox profile here.

‡ = **NOT MEASURED, and specifically NOT a Firefox limitation.** pixelscan (12 rows) and creepjs
    (9 rows) came back `unobtainable` with `NS_ERROR_CONNECTION_REFUSED` because THE EXIT DIED
    MID-RECORD — they are the last two checkers in browser-tier catalogue order. Both are
    demonstrably readable on this engine: `readings/ps128-2026-08-23/run1-matrix/reading.firefox.
    windows.seed1337.json` has creepjs 9 read / pixelscan 8 read, and the sibling
    `…/run1-matrix/reading.firefox.windows.seed4242.json` has creepjs 9 read / pixelscan 7 read.
    The owner's `~100` / `~75` eyesight figures are NOT carried
    forward into these cells: they were never in `readings/`, cannot be diffed, and a blank is
    honest where a borrowed number would be a false green.
```

**Note the direction of this edit.** Four cells go from blank to a measured score; two cells go from
a `⚠` eyesight figure to `—`. The second half is deliberate and follows PS-16's own rule — *"Move a
cell to `—` if the thing it described changed. A stale score is a false green. Blank is honest."*

---

## Edit 3 — §"The headline", the Level 2 bullet

The bullet currently states Level 2 is structurally unmeasured. **That claim needs a narrower
correction than "still true", and it must not be softened in the wrong direction.** Append to that
bullet:

```
  PS-177 was written to close exactly this and did not close it BY SWEEP: it planned 8
  configurations across both engines, three declared machines and two seeds (5150, 24601), and
  obtained 1 — the proxy credential stopped authenticating 96 seconds in, and the other 7
  configurations were REFUSED rather than read over a direct connection. One record is one
  profile, so the ARM PS-177 READ remains unmeasured for Level 2.

  But building the comparator surfaced that the CORPUS already held a two-seed arm nobody had
  ever diffed: readings/ps128-2026-08-23/run1-matrix/, firefox/windows at seeds 1337 and 4242,
  same exit (95.49.113.111), same masking layer, 3.5 minutes apart. Diffed, it ANSWERS Level 2
  for that one arm, and the answer is a FAILURE:

      8 of 9 entropy-bearing rows differ across the two seeds (canvas_data_hash,
      webgl_image_hash, gpu_renderer, gpu_vendor on creepjs; canvas/webgl/renderer/vendor on
      pixelscan) — but creepjs :: webgl_pixel_hash reads 51df3565 for BOTH profiles.

  A checker reading that row can tie the two profiles to each other. The same row across every
  chromium record in readings/ takes three distinct values at three seeds (f801a1b3 @1337,
  a96eedf0 @2024, b8dba17f @9001), so it is seed-derived on chromium and appears seed-INVARIANT
  on firefox — the shape of a masking gap on the firefox leg, not a constant of the checker.

  BOUND: n = 2. Two firefox records at two seeds raise this; they do not prove invariance across
  all seeds. A third firefox seed through a healthy exit settles it, and that is now the cheapest
  high-value reading available on this project. Level 2 is therefore MEASURED-AND-FAILING on
  firefox/windows/desktop (n=2, one linking row) and STRUCTURALLY UNMEASURED on every other arm.
  Re-derive with readings/ps177-2026-08-25/derive.py; the comparison is committed as
  derived-output.ps128-level2.txt.
```

**Note for whoever applies this.** The comparator behind that result was blocking-rejected in review
and rebuilt: it previously treated boolean detector verdicts (`webdriver_passed=True`,
`trustworthy=True`) as linkage evidence, which reports two *unlinkable* profiles as linked. It now
classifies every row as entropy-bearing or verdict and refuses to answer from verdicts alone. That
matters here because **the arm PS-177 itself read would still be UNANSWERABLE** under the corrected
tool — its only overlapping rows are those verdicts.

---

## Edit 4 — §"The headline", first paragraph

Currently: *"Every full checker-matrix reading we own was taken in ONE cell: chromium, declared
machine `windows`, desktop, seed `9001`…"*. That is no longer strictly true — there is now a Firefox
record. Append:

```
As of PS-177 there is ALSO one Firefox reading (`ps177`, windows/desktop/seed 5150), which is the
first automated Firefox coverage in the corpus. It does not change the shape of the problem: it is
still a SINGLE profile, on the one OS arm Firefox is able to present, with pixelscan and creepjs
lost to an exit failure. Every CHROMIUM reading we own is still seed 9001.
```

---

## Edit 5 — §"What to measure first", item 1

Item 1 is *"Firefox, anything at all."* Replace its body with:

```
1. **Firefox, a COMPLETE reading.** Partially closed by PS-177: `ps177` is the first automated
   Firefox record, and it fired zero adverse verdicts across iphey and sannysoft. But pixelscan
   and creepjs — the two checkers the owner actually judges Firefox by — were lost to the exit
   failure in that run, so the surface he evaluates STILL has no automated reading. One clean
   Firefox run through a healthy exit closes this.
```

---

## What this patch deliberately does NOT do

- It does **not** touch Table 2 (leak vectors). PS-177 obtained no GPU, canvas, audio or font rows —
  those live in the browser-tier checkers that the exit failure took. Every Table 2 cell stays as it
  was.
- It does **not** enter anything for chromium/macos, chromium/linux, or any seed-24601 arm. Those
  were **refused, not read** (see EVIDENCE.md §4). They stay blank.
- It does **not** let the existing chromium/windows/seed9001 corpus stand in for
  chromium/windows/seed5150. A different seed is a different profile — that is the entire premise of
  the seed axis.
