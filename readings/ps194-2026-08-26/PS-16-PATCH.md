# PS-16 patch — retire the "needs a machine with a real GPU" conclusion

**Produced by PS-194, 2026-08-26.** PS-16's own maintenance rule requires the article to be
updated in the same ticket as the reading. This file is the exact replacement text, with the
evidence behind it, so the edit can be verified against the records rather than transcribed on
trust.

Every figure here is printed by `derive.py` in this directory.

---

## What is being retired

PS-16 §"The pixelscan question — why it is not closed and may not be closable here" currently
ends:

> What we could not remove is the renderer axis — this machine has no GPU, and a software
> rasteriser can be hidden but not replaced. **Settling whether the pixelscan gate is
> achievable needs a machine with a real GPU.** Until then, a red pixelscan here is not
> evidence against our masking, and a green one cannot be produced.

That conclusion is repeated in PS-150, PS-159 and PS-170. **It is refuted from two independent
directions.**

1. **The owner, from real hardware.** pixelscan reports `masking detected` on a real Windows
   host with a real GPU, in a VM, and everywhere else he has run it — *"дело не в видеокарте а
   в самом движке хромиума это прям точно"*.
2. **Our own cross-engine control**, taken before he said it and now reproduced live under
   PS-194, where the firefox and chromium legs ran through **different exits on different
   ASNs** (AS9141 P4 vs AS29167 Netia) — so the PS-186 exit-address-block confound the confirm
   review flagged is not reproduced. pixelscan additionally renders **`No proxy detected` on
   all five PS-194 legs**, flagged and affirmed alike, so it is not objecting to the exit.

**Neither was derived from the other.** The GPU explanation is dead: the renderer axis was a
confound, not a cause.

### The line that kills it

**Firefox and Chromium were read on the SAME GPU-less host, in the same campaign.** If the
absent GPU were the cause, both would fail. Instead:

| leg | engine | verdict |
|---|---|---|
| A | `invisible_playwright/firefox-20` | **`fingerprint_consistent = True` — AFFIRMED** |
| B | `fingerprint-chromium/148.0.7778.215` | `fingerprint_inconsistent` + `masking_detected` |

pixelscan does not merely stay silent on Firefox — **it positively affirms the fingerprint is
consistent.** A checker that could not evaluate a fingerprint on a GPU-less host would go
`absent`, not affirm. **The host is identical. Only the engine differs.**

Both records are `evidence: sufficient` at 28/28 fingerprint rows from 6 checkers
(`matrix/reading.{firefox,chromium}.windows.seed5150.json`).

---

## Replacement text for that section

> ### The pixelscan question — it is the ENGINE, not the GPU
>
> `fingerprint_inconsistent` and `masking_detected` fire on every chromium arm and on
> **neither** firefox arm.
>
> **They are not caused by our masking, and they are not caused by the absent GPU.**
>
> - layer **off** (`ps143`, and re-confirmed live in `ps194` leg C1): both still fire, with the
>   layer's rows byte-identical to layer-on.
> - stock chromium with three confounds removed (`ps159`): both still fire.
> - post-GPU-fix (`ps170`): both still fire, unchanged.
> - **cross-engine on one host (`ps186`, reproduced live in `ps194`): firefox is AFFIRMED
>   `fingerprint_consistent = True` while chromium is flagged.**
>
> **The GPU explanation is RETIRED (PS-194).** It was refuted from two independent directions —
> the owner's observation that pixelscan flags Chromium on real hardware with a real GPU, and
> our own cross-engine control on one GPU-less host. The renderer axis was a confound. A red
> pixelscan on a chromium arm **is** a finding about the chromium path and must not be written
> off as "no GPU here" again.
>
> **What PS-194 named**, at row level, from retained page text (neither row has a pattern in
> `BROWSER_CHECKERS`, so neither appears in ANY committed matrix record — they are invisible to
> every reading this project has ever taken):
>
> | row | firefox (affirmed) | chromium (flagged) | owner |
> |---|---|---|---|
> | **`Browser`** (feature-derived candidate set) | `Firefox-11-22,Firefox-114-0` | `Chrome-29-0,Opera-16-0,Edge-79-0,MIUI Browser-0-0,Yandex-0-0,Safari-7-0,Mobile Safari-7-0,Facebook-0-0,m-firefox-30-0,m-Edge-45-0,m-Opera Touch-0-0` | **Chromium FAMILY** — byte-identical on stock Chromium 151 |
> | **`Fonts` / `Font hash`** | 51 fonts, plausible Windows set | **2–3 fonts** (`Arial`, `MS Gothic`, `Microsoft JhengHei`) on a declared Windows 10 host | **the packaged engine's patches** — seed-derived, identical layer-on/layer-off |
>
> **Attribution is settled by measurement** across stock Chromium 151, packaged
> `fingerprint-chromium` 148 and firefox on one host (`ps194`):
>
> - **our extension layer is EXCLUDED** — layer-on and layer-off are byte-identical on both rows
>   and both verdicts still fire;
> - **the font row is the packaged engine's** — it changes with the seed, and stock chromium
>   shows the host's real fontconfig instead;
> - **the `Browser` row is neither ours nor the packaging's** — stock Chromium produces it
>   byte-identically, so it is a property of the Chromium engine family.
>
> ⚠️ **This is a correlation over five legs, not a demonstrated cause.** Engine family is
> perfectly confounded with every Chromium-only value in this design, so the `Browser` row
> cannot be separated from any other value constant across Chromium and absent on Firefox.
> Saying otherwise would repeat the exact error being retired here.
>
> **The next measurement is the font spoof** (closable from our own extension layer — we already
> ship `measuretext_ext`), because it settles causation either way: if the verdict flips, the
> font row was the cause; if it does not, something Chromium-family is load-bearing and the row
> may not be closable at all without not being Chromium. **It costs one extension rather than a
> fork.** Routes are costed in `readings/ps194-2026-08-26/EVIDENCE.md`.

---

## Also correct, in Table 1

The pixelscan cell for **row 5 (firefox)** is currently `— ‡` ("not measured", because PS-177
lost pixelscan to an exit failure). **It is now measured, twice** — PS-186 at seeds 5150/24601
and PS-194 live — and it **passes with a positive affirmation**, which is the strongest result
any cell in that table holds.

That cell should read a score with this basis:

> **firefox / pixelscan — AFFIRMED.** `fingerprint_consistent = True` at seeds 5150 and 24601
> (`ps186`) and again live under `ps194`. Zero adverse verdicts on any firefox record. This is
> the only cell in the matrix where a checker positively affirms rather than merely staying
> silent.

**Do not** carry the chromium pixelscan score across to it, and **do not** mark the three
PS-186 chromium records that rendered **no pixelscan verdict at all** (macos/5150, linux/5150,
linux/24601) as passes — they are coverage holes. `absent` on an adverse row means only "the
adverse pattern did not match"; the discriminator is whether **either** polarity reached
`state: read`.

---

## Where the two-engine rule earned its keep

PS-16's own two-engine rule says a defect found on one engine is not fixed by repairing the
other, and that filling a cell for one engine leaves the sibling blank as a prompt.

**PS-159 removed automation, timezone and the SwiftShader renderer one axis at a time and
concluded "not our masking". The comparison that actually discriminated was the one axis nobody
varied — the engine.** The cross-engine control had been sitting in our own corpus since PS-186,
taken before the owner said anything, and was not read as a control because the sweep was
designed to measure arms rather than to compare engines.

> **A controlled experiment you already own is invisible until someone asks the question it
> answers.**

And the PS-194 corollary, which is why this directory retains page text:

> **A corpus that stores only the rows its catalogue can already match cannot surface a signal
> nobody has written a pattern for yet.** Both rows named above were discarded at parse time by
> every reading this project has taken, including all eight of PS-186's.
