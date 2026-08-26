# PS-16 patch — the cells PS-186 fills, and the four claims it CORRECTS

**DoD #2 of PS-186: "PS-16 updated in this ticket, per its maintenance rule — re-derived with a
script, never hand-typed."**

This file exists because **I have no MCP tool that can write a knowledge article.** The worker
toolset exposes `get_knowledge_article`, `search_knowledge_articles`, `list_knowledge_articles` and
`link_ticket_to_knowledge` — all read-or-link. There is no create/update counterpart, and
`add_comment` does not accept a knowledge article as a commentable. So the edit is delivered here,
ready to apply verbatim, rather than skipped or described vaguely. Same constraint PS-177 hit.

---

## ⚠️ WHICH FIGURES ARE DERIVED AND WHICH ARE JUDGEMENT — read before applying

PS-177's patch was **blocked by review** for claiming every figure was script-printed when its
0–100 scores were not. That correction is inherited here rather than re-learned:

* **DERIVED** — printed by `derive.py` / `derive_ps186.py` from the committed records and
  reproducible by re-running them (`derived-output.txt` and `derived-output.ps186.txt` are those
  outputs, committed alongside): every row count, per-checker read/absent/unobtainable tally, the
  evidence verdict and fraction, fired-verdict counts, the hash/seed/exit/day table, the ASN
  counts, the pixelscan verdict states, the GPU agreement comparison, and the Level 2 results.
* **COUNTED** — read off the source, not measured: the GPU pool sizes and their uniform-draw
  collision figures (`MAC_GPUS` 2 → 50.0%, `LINUX_GPUS` 8 → 12.5%, `ANDROID_GPUS` 4 → 25.0%,
  `WIN_GPUS` 5 → 20.0%). **Unchanged by this sweep** — carried forward as-is, still labelled
  theoretical.
* **JUDGEMENT** — every 0–100 score below. `derive.py` emits no scores; the word `score` appears
  nowhere in its output. PS-16 itself says a score is *"my judgement from the row data, not a number
  any checker emits"*. Reasoning is stated inline for each so it can be argued with.

**Whoever applies this: apply it as written.** If you re-type a *derived* number, re-run the script
instead. If you disagree with a *judgement* score, change it — and say why.

---

## Edit 1 — the `Last re-derived` line at the top

Replace:

```
**Last re-derived: 2026-08-25, against `origin/main` @ `c04e15d`.**
```

with:

```
**Last re-derived: 2026-08-26, against `origin/main` @ `6dffce6`.**
```

---

## Edit 2 — the headline section, after "As of PS-177 there is ALSO one Firefox reading…"

The headline currently says every full reading sits in ONE cell and that Firefox has pixelscan and
creepjs missing. **Both statements are now out of date.** Append:

```
**PS-186 UPDATE — the sweep PS-177 planned has now been taken in full.** PS-177 obtained 1 of 8
configurations; PS-186 re-ran the lost 7 on a new credential and obtained **8 of 8**, every one
`sufficient` at **28/28 fingerprint rows (100%)**. Nothing was lost to the exit.

So the "every reading sits in one cell" headline is retired. The corpus now holds readings at
**seeds 5150 and 24601 on four arms** — chromium/{windows,macos,linux} and firefox/windows — and
the two arms that had NEVER been read by any checker (chromium/macos, chromium/linux) are read.

Three things that sweep found, each of which contradicts a statement elsewhere in this article and
is corrected in place below:

1. The firefox `webgl_pixel_hash` bound is no longer n=2. It is **n=4, and settled** (Edit 5).
2. The two-GPU defect class this article records as "fixed and verified" is **alive on macos and
   linux** — the fix was only ever verified on windows, the one arm with coverage (Edit 6).
3. Two of the eight "structurally unobtainable" checkers **are readable** on the new exit pool, and
   both fire adverse verdicts — so "only two adverse markers fire anywhere in the whole matrix" is
   no longer true (Edit 7).
```

---

## Edit 3 — Table 1, rows 2, 3 and 5

Currently:

```
| 2 | chromium / macos | — | — | — | — | — | — | — |
| 3 | chromium / linux | — | — | — | — | — | — | — |
| 5 | **firefox** (windows only, forced) | — ‡ | — ‡ | **90** † | **90** † | 90 ◇ | 85 ◇ | 85 ◇ |
```

Replace with:

```
| 2 | **chromium / macos** | **45** ★ | **60** ★ | **90** ★ | **90** ★ | 90 ◇ | 85 ◇ | 85 ◇ |
| 3 | **chromium / linux** | **— ¶** | **35** ★ | **90** ★ | **90** ★ | 90 ◇ | 85 ◇ | 85 ◇ |
| 5 | **firefox** (windows only, forced) | **90** ★ | **40** ★ | **90** † | **90** † | 90 ◇ | 85 ◇ | 85 ◇ |
```

And add these footnotes beneath the table:

```
★ = **PS-186, seeds 5150 and 24601** (`readings/ps186-2026-08-26/matrix/`). Both seeds read on every
arm; each cell is a JUDGEMENT score over the derived row data, per this article's own definition.

¶ = **NOT SCOREABLE — the pixelscan verdict block did not render on either linux record.** This is a
COVERAGE HOLE, not a pass. pixelscan states the same fact in two opposite-polarity rows
(`fingerprint_consistent` / `fingerprint_inconsistent`) and NEITHER was read on linux, so the run
carries no pixelscan verdict at all. Scoring it green because the adverse row is `absent` would be
exactly the false-green this article exists to prevent — an absent adverse row means the pattern did
not match, which is indistinguishable from "nothing rendered" unless you check the positive row.
Re-derive with `derive_ps186.py` §3b.
```

### The reasoning behind each judgement score — argue with these, don't re-type them

**firefox / pixelscan = 90** (was `— ‡`, never measured). 7 rows read, 0 adverse, and pixelscan does
not merely stay silent — it **affirms**, reading `fingerprint_consistent = True` with matched text
*"Your Browser Fingerprint is consistent"*. That is the strongest positive result anywhere in this
matrix. Not 100: five verdict rows are `absent` rather than read, and the renderer axis remains
unresolvable from a GPU-less host.

**firefox / creepjs = 40** (was `— ‡`). **Deliberately LOW despite ZERO adverse verdicts**, and this
is the cell most likely to be misread. creepjs read 9/9 rows and objected to nothing — but the row
it exposes, `webgl_pixel_hash`, is **identical across all four firefox seeds** and therefore ties two
profiles to each other (Edit 5). This article's own rule is *the whole verdict, not the headline*: a
checker that does not object while handing out a cross-profile linking identifier has not passed. A
score of 90 here would be a false green sitting directly on top of the project's only outright
Level 2 failure.

**chromium / macos / pixelscan = 45.** `fingerprint_inconsistent` and `masking_detected` both fired
on seed 24601. On seed 5150 the verdict block did not render, so that record contributes no verdict
either way. Slightly below chromium/windows' 55 because macos additionally carries the two-GPU
disagreement (Edit 6) and only one of its two records produced a verdict at all.

**chromium / macos / creepjs = 60.** 9 rows read, 0 adverse — but creepjs and pixelscan were handed
**different Apple GPUs on the same run** (M4 vs M2 Pro; M2 vs M1). Below chromium/windows' 75 for
exactly the reason windows scored 75 rather than 90 before its fix landed.

**chromium / linux / creepjs = 35 — the lowest cell in the table.** 9 rows read, 0 adverse, and yet
creepjs reads `ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader
driver)` — **the container's real software rasteriser**. That is not a plausible consumer GPU, it is
the host's true renderer reaching a checker, and it is on the arm where the engine is known to return
one identical SwiftShader string for every seed (100% collision) — the case `LINUX_GPUS` exists to
prevent. Zero adverse verdicts does not redeem a cell that leaks the real renderer.

**iphey / sannysoft = 90 on both new chromium arms.** Asked and silent across the same 3 / 2 rows
that earn 90 on chromium/windows, so scored identically for consistency. Note `sannysoft ::
webdriver_present` is `absent` on all 8 records while rebrowser reports `navigatorWebdriver` — an
**open discrepancy** (Edit 7), not yet a reason to lower these.

### Also update the coverage sentence beneath the table

Replace:

```
**So the honest read of this table is narrower than it looks.** Strip the ◇ columns and the real
coverage is: chromium/windows measured on four checkers, firefox measured on two, everything else
nothing. That is **6 engine-describing cells out of 20.**
```

with:

```
**So the honest read of this table is narrower than it looks.** Strip the ◇ columns and the real
coverage is: chromium/windows, chromium/macos and firefox measured on four checkers each,
chromium/linux on three (its pixelscan verdict never rendered), chromium/android nothing. That is
**15 engine-describing cells out of 20** — up from 6 before PS-186. The remaining blanks are the
whole chromium/android row, plus the linux pixelscan hole.
```

---

## Edit 4 — the `⚠` footnote about the owner's eyesight

The `~100` / `~75` figures are still NOT carried into any cell — they remain unreproducible. But the
sentence explaining why the cells are blank no longer applies, since they are no longer blank.
Replace the `‡` footnote entirely with:

```
‡ = **RETIRED by PS-186.** This footnote recorded that pixelscan and creepjs were lost on firefox to
an exit failure mid-record in PS-177. Both are now READ on both firefox seeds — creepjs 9/9,
pixelscan 7 read / 5 absent, 0 unobtainable — so the cells carry measured scores (★). The owner's
`~100` / `~75` eyesight figures are STILL not carried forward: they were never in `readings/` and
cannot be diffed. The cells above are what our harness measured, independently of them.
```

---

## Edit 5 — Table 2, the firefox readback cell, and the bound

Currently:

```
| **firefox / windows / desktop** | — | — | **🔴 FAILS — one value @ 2 seeds** | — | — | — | — | — |
```

Replace with:

```
| **firefox / windows / desktop** | **RISK** — see Edit 6 | — | **🔴 FAILS — one value @ 4 seeds** | OK | OK | OK | — | — |
```

Then replace the paragraph beginning *"`creepjs :: webgl_pixel_hash` reads `51df3565` on both firefox
seeds…"* and the `**Owned by PS-182.** Bound: n=2 firefox seeds…` line with:

```
`creepjs :: webgl_pixel_hash` reads **`51df3565` on ALL FOUR firefox seeds** — 1337, 4242, 5150 and
24601 — across **four different exits and three different days**. Two firefox profiles are therefore
linkable to each other on a vector the bar names explicitly.

**BOUND: n = 4. SETTLED.** PS-16 previously recorded n=2 with the note that "a third firefox seed
settles it". PS-186 delivered a third AND a fourth, and both landed on the same value. Critically,
5150 and 24601 were chosen by PS-177 precisely because they appear NOWHERE in the prior corpus, so
this is not an artefact of re-exercising a seed some earlier run had already touched. The "those two
collided by chance" explanation is dead.

**The chromium control, from the same script over the same corpus, moves the opposite way:** 18
readings across 5 distinct seeds produce **5 distinct hash values** — `f801a1b3` @1337, `a96eedf0`
@2024, `b8dba17f` @9001, `c893e6c9` @5150, `6ba330fa` @24601. Every chromium seed reads its own
value; every firefox seed reads the same one. So this is a masking gap on the firefox leg, not a
constant of the checker — measured on both engines, per the two-engine rule.

**Owned by PS-182.** No longer blocked on the proxy and no longer bounded at n=2: the measurement
PS-182 was waiting for exists. Re-derive with `readings/ps186-2026-08-26/derive_ps186.py` §1.
```

---

## Edit 6 — GPU identity: the "fixed and verified" claim must be scoped to windows

This is the correction with the most operational consequence. The section currently opens *"The
contradiction is **fixed and verified**"* and shows a before/after table. That is true — **on
chromium/windows, which was the only arm with any checker coverage when it was written.**

Add immediately after that before/after table:

```
### ⚠️ PS-186 — THE VERIFICATION WAS WINDOWS-ONLY, AND THE DEFECT IS ALIVE ON THE OTHER TWO ARMS

The fix above was verified on **chromium/windows**. macos and linux were `—` in every column of
Table 1, so it was never read there. PS-186 read them, and both fail:

| arm | creepjs `gpu_renderer` | pixelscan `webgl_renderer` | |
|---|---|---|---|
| chromium/windows/5150 | NVIDIA RTX 3060 Laptop `0x2560` | NVIDIA RTX 3060 Laptop `0x2560` | **AGREE** |
| chromium/windows/24601 | AMD Radeon `0x1638` | AMD Radeon `0x1638` | **AGREE** |
| firefox/windows (both seeds) | NVIDIA GTX 980 | NVIDIA GTX 980 | **AGREE** |
| **chromium/macos/5150** | Apple **M4** | Apple **M2 Pro** | **⚠ DISAGREE** |
| **chromium/macos/24601** | Apple **M2** | Apple **M1** | **⚠ DISAGREE** |
| **chromium/linux/5150** | **SwiftShader** | Intel **Iris Xe** | **⚠ DISAGREE** |
| **chromium/linux/24601** | **SwiftShader** | Intel **UHD 630** | **⚠ DISAGREE** |

**The windows rows are the internal control**: same instrument, same run, same comparison, and it
produces AGREE there. So DISAGREE elsewhere is a property of those arms, not of the measurement.
(pixelscan suffixes its string with ", or similar"; that suffix is normalised before comparing —
comparing raw manufactures a false disagreement on the firefox rows.)

**Both authors are identifiable in the source.** pixelscan's values are VERBATIM entries of our own
pools — the Intel UHD 630 and Iris Xe strings are `LINUX_GPUS` entries, and `Apple M1` / `Apple M2
Pro` are exactly the two `MAC_GPUS` entries. creepjs's values are what `gpu_ext.py`'s own header
measured the ENGINE producing (macos: "only TWO values across 30 seeds, Apple M2 87%, Apple M4 13%"
— precisely the M2 and M4 seen here).

So on macos and linux **our masking layer authors one card while the engine authors another, and
different checkers see different ones** — the exact defect class PS-170 closed on windows, where
`ENGINE_AUTHORED_IDENTITY_ARMS = frozenset({"windows"})` makes both authors the same one.

**Linux is the more serious half:** creepjs reads the container's REAL SwiftShader rasteriser. Not a
plausible consumer GPU — the host's true renderer reaching a checker, on the arm where the engine is
known to return one identical SwiftShader string for every seed.

A **loopback differential was run before the sweep and returned `moved`**, so the masking layer does
reach the page. This is two authors disagreeing, not the layer being absent.

**Not owned by PS-186** (measure-and-report only). Nearest owner is PS-183 (`MAC_GPUS`), which is
scoped to pool width and does NOT cover the linux SwiftShader leak or the two-author split. That gap
needs a ticket.
```

Also update the Table 2 GPU-identity cells for those arms:

```
| chromium / macos / desktop | **RISK — two cards on one run** | RISK 50% | — | — | — | — | — | — |
| chromium / linux / desktop | **🔴 FAILS — real SwiftShader visible to creepjs** | RISK 12.5% | — | — | — | — | — | — |
```

---

## Edit 7 — the unobtainable list is 6, not 8, and the "only two adverse markers" claim is dead

In *"Checkers our harness cannot read at all"*, **remove these two rows**:

```
| bot-detector.rebrowser.net | `ERR_SOCKS_CONNECTION_FAILED` through our exit. |
| deviceandbrowserinfo.com | `ERR_SOCKS_CONNECTION_FAILED` through our exit. |
```

and add beneath the table:

```
**PS-186 — this list is 6, not 8.** `bot-detector.rebrowser.net` (**14 rows read**, 2 absent) and
`deviceandbrowserinfo.com` (**5 rows read**, 11 absent) both answer on the new exit pool. They were
listed as refusing our SOCKS exit, and that was measured against the OLD pool — they refused those
exits, not our harness. The mobile→residential credential change is the most economical explanation.
The other six still hold (browserscan 401, amiunique/coveryourtracks click-gated, whoer Cloudflare —
out of scope by charter, fv.pro paywalled, scrapfly ruleset).
```

Then, in *"Basis for the one measured row"*, replace:

```
**Only two adverse markers fire anywhere in the whole matrix:**
```

with:

```
**PS-186: this is no longer true.** Two newly-readable checkers (Edit 7) fire on chromium:

- `deviceandbrowserinfo.com :: bot_verdict_positive` = true — 5 of 6 chromium records
- `bot-detector.rebrowser.net :: detected` = `navigatorWebdriver` — all 6 chromium records

**Neither fires on either firefox record** (rebrowser's row is `absent` there — the page was read and
the pattern did not match). Firefox fired **zero** adverse verdicts on both seeds.

⚠️ **`navigatorWebdriver` is an OPEN DISCREPANCY, not a confirmed webdriver leak.** On the SAME runs,
`sannysoft :: webdriver_present` is `absent` on all 8 records and both `webdriver_advanced_passed`
and `webdriver_missing_passed` read `true` on all 8. Two checkers disagree about the same property.
Settling it needs a layer-off control on this credential — a DIFFERENTIAL, which requires the
sticky-session token. Not run in PS-186; recorded as the highest-value follow-up.

On chromium/windows specifically, the pre-existing two markers still fire:
```

---

## Edit 8 — Level 2 status, in the headline section

Replace *"Level 2 is therefore **MEASURED-AND-FAILING on firefox/windows/desktop** (n=2, one linking
row) and **STRUCTURALLY UNMEASURED on every other arm**"* with:

```
Level 2 is now measured on FOUR arms (PS-186 read both seeds on each):

| arm | entropy rows read on both seeds | differ | identical | verdict |
|---|---|---|---|---|
| firefox / windows | 9 | 4 | **5** | **FAILS** — incl. `webgl_pixel_hash` |
| chromium / macos | 10 | 7 | **3** | **FAILS** — GPU vendor strings |
| chromium / linux | 10 | 6 | **4** | **FAILS** — GPU renderer + vendor strings |
| chromium / windows | 10 | 9 | 1 | **UNANSWERABLE** — see caveat |
| chromium / android | — | — | — | STRUCTURALLY UNMEASURED |

The identical rows on macos and linux are GPU vendor strings (`Google Inc. (Apple)`, `Google Inc.
(Intel)`), constant across seeds because the pool is one vendor deep in those slots.

⚠️ **CAVEAT — `chromium/windows` reads FAILS only because of a classifier bug, and should be read as
UNANSWERABLE.** `derive.py` classifies `bot-detector.rebrowser.net :: detected = "navigatorWebdriver"`
as entropy-bearing, because it is a non-numeric string of ≥8 characters with no `vector` tag. It is
in fact a DETECTOR'S REASON TOKEN — the name of the signal that tripped — not an attribute of the
profile. Two profiles both reading `navigatorWebdriver` are both detected for the same reason; they
are not thereby linkable. The macos, linux and firefox verdicts are UNAFFECTED (each fails on other,
genuinely entropy-bearing rows), but chromium/windows fails on that row ALONE. `derive.py` is
committed UNMODIFIED so PS-177's output stays reproducible; the correction lives in
`readings/ps186-2026-08-26/EVIDENCE.md` §6.
```

---

## Edit 9 — "What to measure first", items 1 and 2

Item 1 (a complete Firefox reading) and item 2 (a second profile through the same checker) are
**both delivered**. Replace them with:

```
1. ~~Firefox, a COMPLETE reading.~~ **DONE (PS-186.)** Both firefox seeds read all four
   engine-describing checkers at 28/28 fingerprint rows, including the pixelscan and creepjs the
   owner judges by. Zero adverse verdicts fired — but see the `webgl_pixel_hash` failure, which zero
   adverse verdicts does not redeem.
2. ~~A second profile through the same checker.~~ **DONE (PS-186)** on four arms — see Edit 8. Level 2
   is now measured rather than assumed on everything except chromium/android.
3. **A layer-off control on the current credential.** NOW THE TOP READING. It is the only thing that
   separates "the pool did it" from "the product did it" for both open questions: the
   `navigatorWebdriver` discrepancy and any pixelscan movement. It is a DIFFERENTIAL, so it needs
   the sticky-session token — the exit rotates per call, and PS-97's finding was only readable
   because both its arms carried one address.
4. **The two-author GPU split on macos and linux** (Edit 6). Measured, unowned, and the linux half
   leaks the host's real renderer.
5. **A mobile profile.** Still never read; still unreadable from this tier (no `device_type`
   selector). Unchanged by PS-186.
6. **chromium / android.** Now the only wholly unread row in Table 1.
7. **browserscan.net, or an honest note that we cannot.** Unchanged — still structurally unreadable,
   still a surface the owner judges by.
```
