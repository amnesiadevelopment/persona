# PS-16 patch — the cells PS-177 fills, and the ones it explicitly does not

**DoD #2 of PS-177 is: "Every cell it fills is written into knowledge article PS-16, in the same
ticket."** This file exists because **I have no MCP tool that can write a knowledge article.** The
worker toolset exposes `get_knowledge_article`, `search_knowledge_articles`, `list_knowledge_articles`
and `link_ticket_to_knowledge` — all read-or-link. There is no create/update counterpart, and
`add_comment` does not accept a knowledge article as a commentable.

So the edit is delivered here, **fully derived and ready to apply verbatim**, rather than skipped or
described vaguely. Every figure below was printed by `derive.py` from the committed record
(`derived-output.txt` is that script's output, committed alongside). Nothing was hand-typed, per
PS-16's own maintenance rule.

**Whoever applies this: apply it as written.** If you re-type a number, re-run `derive.py` instead.

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
    demonstrably readable on this engine: `readings/ps128-2026-08-23/reading.firefox.windows.
    seed1337.json` has creepjs 9 read / pixelscan 8 read, and the seed4242 record has creepjs
    9 read / pixelscan 7 read. The owner's `~100` / `~75` eyesight figures are NOT carried
    forward into these cells: they were never in `readings/`, cannot be diffed, and a blank is
    honest where a borrowed number would be a false green.
```

**Note the direction of this edit.** Four cells go from blank to a measured score; two cells go from
a `⚠` eyesight figure to `—`. The second half is deliberate and follows PS-16's own rule — *"Move a
cell to `—` if the thing it described changed. A stale score is a false green. Blank is honest."*

---

## Edit 3 — §"The headline", the Level 2 bullet

The bullet currently states Level 2 is structurally unmeasured. **That is still true and must not be
softened.** Append to that bullet:

```
  PS-177 was written to close exactly this and DID NOT: it planned 8 configurations across both
  engines, three declared machines and two seeds (5150, 24601), and obtained 1 — the proxy
  credential stopped authenticating 96 seconds in, and the other 7 configurations were REFUSED
  rather than read over a direct connection. One record is one profile, so Level 2 remains
  **structurally unmeasured**. The comparator that answers it is written and committed
  (`readings/ps177-2026-08-25/derive.py`); it needs a second record at a different seed on any
  one arm. The cheapest path to closing this bar level is `firefox / windows / seed 24601` —
  a single ~3 minute run that pairs with the record already committed.
```

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
