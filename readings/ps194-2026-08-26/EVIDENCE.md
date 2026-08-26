# PS-194 — what pixelscan reads on the Chromium path

**Taken 2026-08-26, one host, one campaign, through the mandatory proxied exit.**
Every figure below is printed by `derive.py` in this directory, out of the committed
JSON. Nothing is transcribed from memory. Re-run it rather than trusting this prose:

```
cd readings/ps194-2026-08-26 && python3 derive.py
```

---

## ⚠️ The instrument was broken first, and that is part of the record

The first run of legs A and B returned **`browser tier: 0 read, 38 unobtainable`** on
*both* engines, with the reason recorded per row:

> `no DISPLAY and no Xvfb binary: persona's chromium is a HEADED browser and cannot render a checker page here.`

An identical failure in every cell is the PS-14 shape — **distrust the instrument.** It was
the instrument: this container had no `Xvfb`. Installed it, re-took everything. Both
re-taken records are `evidence: sufficient` at **28/28 fingerprint rows from 6 checkers**.

Had that first run been read as data it would have produced a fourth wrong ticket, which is
the exact failure mode PS-194 exists to end.

---

## The five legs

| leg | engine | layer | seed | declared | verdict |
|---|---|---|---|---|---|
| **A** | `invisible_playwright/firefox-20` | ON | 5150 | Firefox 151.0 on Windows | **CONSISTENT — affirmed** |
| **B** | `fingerprint-chromium/148.0.7778.215` | ON | 5150 | Chrome 148.0.0.0 on Windows | INCONSISTENT + masking |
| **B′** | `fingerprint-chromium/148.0.7778.215` | ON | 24601 | Chrome 148.0.0.0 on Windows | INCONSISTENT + masking |
| **C1** | `fingerprint-chromium/148.0.7778.215` | **OFF** | 5150 | Chrome 148.0.0.0 on Windows | INCONSISTENT + masking |
| **C2** | **stock** Chromium 151.0.7922.169 (Debian) | **OFF** | 5150 | Chrome 151.0.0.0 on **Linux** | INCONSISTENT + masking |

Legs A and B are full 61-row matrix records (`matrix/`). C1/C2 are the pixelscan-only
binary-attribution arm (`leg-c-stock-vs-packaged.json`). B/B′/A page text is retained in
`pixelscan-page-text.json` — see "why the text is kept" below.

**The cross-engine control reproduced live, and the PS-186 exit confound is not reproduced
here.** The researcher's confirm review flagged that in PS-186 the two firefox records sat on
`109.243.x` while every chromium record sat elsewhere, so engine and exit-address-block were
confounded. That does not hold in this reading: the firefox and chromium *matrix* legs — the
two records the cross-engine claim actually rests on — ran through **different exits on
different ASNs** (firefox `89.68.178.81` / AS9141 P4, chromium `37.209.131.15` / AS29167
Netia), and the split is unchanged: **firefox affirmed, chromium flagged.**

Across the four exit blocks this reading retained, `derive.py` §7 prints:

```
  distinct ASNs across all legs : 3
    AS29167 Netia SA
    AS5617 Orange Polska Spolka Akcyjna
    AS9141 P4 Sp. z o.o.
```

**Three, not four** — and the count is now *printed* by `derive.py` rather than written here by
hand, because round 1 of this ticket claimed four and named an `AS12912 T-Mobile Polska` that
belongs to `readings/ps143-2026-08-24/` and `readings/ps186-2026-08-26/` and appears in **no
PS-194 record**. It was the one figure in this file that had not come out of the script, and it
was wrong. See "not covered" for the exits this reading failed to retain.

**The exit is not what pixelscan is objecting to**, and that rests on a stronger row than exit
diversity anyway. pixelscan renders **`No proxy detected` in the verdict panel of all five
legs**, and `pixelscan :: proxy_detected` is `state=absent` on both matrix records:

| leg | A ff | B ch/5150 | B′ ch/24601 | C1 pkg-off | C2 stock-off |
|---|---|---|---|---|---|
| panel says | `No proxy detected` | `No proxy detected` | `No proxy detected` | `No proxy detected` | `No proxy detected` |

**pixelscan is not reporting an objection to the exit at all** — on the flagged legs as much as
the affirmed one. This is what carries the point; exit diversity is corroboration, not the
argument. (`derive.py` §9 prints the row.)

---

## DoD #1 — the signal, named at row level

Only **verdict** rows differ between the engines. Every fingerprint *input* row in the
catalogue **reads on both**. So pixelscan is not objecting to a missing row — it is objecting
to a **value**. Two rows carry a qualitative engine split, and neither has ever appeared in a
committed record in this project.

### Row 1 — `Browser` (pixelscan's feature-derived candidate set)

This is the row that tracks the verdict most tightly.

| leg | `Browser` |
|---|---|
| **A firefox** | `Firefox-11-22,Firefox-114-0` |
| **B / B′ / C1 / C2 (all chromium)** | `Chrome-29-0,Opera-16-0,Edge-79-0,MIUI Browser-0-0,Yandex-0-0,Safari-7-0,Mobile Safari-7-0,Facebook-0-0,m-firefox-30-0,m-Edge-45-0,m-Opera Touch-0-0` |

pixelscan derives, from observed *features*, which browsers the page could be. `derive.py` §3
counts the row rather than describing it from memory:

```
  firefox  candidate set:  2 entries, 0 of them MOBILE
  chromium candidate set: 11 entries, 5 of them MOBILE
    MOBILE  MIUI Browser-0-0
    MOBILE  Mobile Safari-7-0
    MOBILE  m-firefox-30-0
    MOBILE  m-Edge-45-0
    MOBILE  m-Opera Touch-0-0
```

On Firefox it resolves to **two entries, both Firefox and neither mobile** — a tight,
self-consistent answer beside a declared `Firefox 151.0 on Windows`. On Chromium it resolves to
**eleven entries, five of them MOBILE browsers**, beside a declared `Chrome 148 on Windows`
with `Platform: Win32`.

(No vendor count is given. Grouping these entries by vendor needs a hand-built
name→vendor mapping that is not derivable from the row, so any such figure would be
transcribed rather than printed — the exact defect this file's opening invariant forbids.
The mobile count carries the argument on its own.)

**A desktop Win32 Chrome whose feature probe also matches MIUI Browser and Mobile Safari is
internally contradictory, and "inconsistent" is a fair description of it.**

**All four chromium legs are byte-identical on this row** — stock and packaged, layer on and
layer off, both seeds. It is a **Chromium-family constant**, not a persona artefact.

### Row 2 — `Fonts` / `Font hash`

| leg | font hash | n | listed |
|---|---|---|---|
| **A firefox** ON 5150 | `3238fd528b20aee841821f0f549167d5` | **51** | Arial, Calibri, Cambria Math, Cambria, Candara, Comic Sans MS, Consolas, +44 more |
| **B packaged** ON 5150 | `866c250f7a410b2065a36c89e1d69a27` | **3** | Arial, MS Gothic, Microsoft JhengHei |
| **B′ packaged** ON 24601 | `0e9f39106c66437a10c1bbadc591d697` | **2** | Arial, Microsoft JhengHei |
| **C1 packaged** OFF 5150 | `866c250f7a410b2065a36c89e1d69a27` | **3** | Arial, MS Gothic, Microsoft JhengHei |
| **C2 stock** OFF 5150 | `aecd897e2e539610bfba63cb202d6bd4` | **4** | Abyssinica SIL, DejaVu Sans, DejaVu Serif, DejaVu Sans Mono |

**On the like-for-like comparison — the three legs that declare Windows — this is a stark
split: 51 fonts on the affirmed leg, 2–3 on every flagged one.**

A real Windows 10 machine ships Calibri, Cambria, Consolas, Segoe UI, Tahoma, Verdana and
dozens more. The packaged engine presents a declared Windows 10 / Chrome 148 host that has
**Arial, MS Gothic and Microsoft JhengHei and nothing else** — two of those three being CJK
fonts. Firefox, on the same host, presents a plausible 51-font Windows set.

⚠️ **The stock leg does NOT extend this split, and I am not claiming it does.** C2 declares
*Linux* and presents a coherent 4-font DejaVu set — coherent *with its own declared platform*.
It asks a different question rather than answering this one.

### Why the page text is kept in this directory

Neither row has a pattern in `BROWSER_CHECKERS`, so **neither appears in any committed matrix
record anywhere in this project** — including all eight of PS-186's. They are readable only
from retained page text. That is why `pixelscan-page-text.json` and the `page_text` field on
each leg-C arm exist: a record that stores only the rows the catalogue already knows how to
match cannot surface a signal nobody has written a pattern for yet.

**This is the reusable finding.** The corpus could not have answered PS-194's question no
matter how many arms were run, because the differing row was being discarded at parse time.

---

## DoD #2 — attribution, settled by measurement

Three comparisons, each isolating one axis:

| comparison | result | conclusion |
|---|---|---|
| packaged **layer ON** vs **layer OFF** (B vs C1) | font hash **byte-identical** `866c250f…`; `Browser` row identical; **both flagged** | **NOT our extension layer** |
| packaged **seed 5150** vs **24601** (B vs B′) | font hash **and count both change** (3 → 2) | the font row is **seed-derived** — authored by the packaged engine's own `--fingerprint` patches |
| **stock** vs **packaged**, both layer OFF (C2 vs C1) | stock shows the host's **real** fontconfig (DejaVu); packaged shows a synthetic Windows set | stock does **no** font masking; the packaged build does |

**Verdict on the ticket's two candidates:**

- **Candidate 2 — our extension layer: EXCLUDED.** Confirmed live rather than inherited from
  `ps143`, exactly as the ticket asked. The layer-off arm is byte-identical to the layer-on
  arm on both named rows *and still fires both verdicts*.
- **Candidate 1 — the packaged engine's patches: CONFIRMED as the author of the font row**,
  and it is a **defect in that patch** — a 2–3 font Windows machine is not plausible.
- **The `Browser` row belongs to NEITHER.** It is identical on stock Chromium 151, which
  carries none of persona's patches and none of persona's layer. It is a property of the
  **Chromium engine family**.

### ⚠️ What this does *not* establish, stated plainly

**This is a correlation across five legs, not a demonstrated cause.** Two limits:

1. **Engine family is perfectly confounded with every Chromium-only value in this design.**
   Only one engine family is affirmed, so the `Browser` row cannot be separated from *any*
   other value that is constant across Chromium and absent on Firefox. Naming it as *the*
   cause would repeat precisely the error PS-194 was raised to correct — a confound read as a
   cause.
2. **The stock leg was confounded in this run and cannot disconfirm anything on its own.** It
   rendered `Africa/Abidjan` behind a Warsaw exit and fired `Timezone spoofed` **and**
   `Automated behavior detected`, each independently sufficient for the verdict. What
   establishes "stock is also flagged" is **PS-159's de-confounded arm on this same stock
   binary** (`removable_only`: automation and timezone both removed, both verdicts still
   fired) — not leg C2.

**What would settle causation:** a leg that changes **one** named row and holds the rest
fixed. For the font row that is achievable without a fork (see route F1). For the `Browser`
row it is not reachable from any seat we have — see "not covered".

---

## DoD #3 — the routes, costed honestly

### The font row

| route | feasible? | cost |
|---|---|---|
| **F1 — extension layer: spoof the font-enumeration surface** | **Yes** | We already ship `measuretext_ext.py`, which hooks `measureText` and repairs metrics through a `Proxy` over the native object, and `native_ext` keeps such overrides stringifying as native. The font *list* surface is adjacent to machinery we own and understand. **This is the cheapest real route and it is ours.** Cost: one extension, a plausible per-declared-OS font pool with per-entry provenance, and seed-stable selection. The hard part is not the hook — it is the pool, exactly as with `MAC_GPUS` (PS-183). |
| **F2 — engine fork: fix the patch that emits 2–3 fonts** | Yes, but | Fixes it at the source and for every consumer, but takes on the full maintenance burden the owner is explicitly weighing: rebasing a patched Chromium on every upstream bump, and this project already runs an engine auto-updater (`scripts/engine_autobump.py`) that a fork would have to be reconciled with. **Not justified for this row alone when F1 exists.** |
| **F3 — report upstream to fingerprint-chromium** | Yes | Zero maintenance, no control over timing, and does not help the currently shipped build. Worth doing **in addition to** F1, never instead. |

**Recommendation on the font row: F1, with F3 alongside.** It is closable from our own layer.

### The `Browser` candidate-set row

| route | feasible? | cost |
|---|---|---|
| **B1 — extension layer** | **Unknown, and not costable yet** | We do not know which feature probes feed this row. It is derived by pixelscan from observed features, so closing it means finding and correcting *every* feature that puts `MIUI Browser` and `Mobile Safari` in a desktop Chrome's candidate set. That set is unenumerated. **Costing this before identifying the probes would be a guess.** |
| **B2 — engine fork** | Same problem | A fork does not help until the inputs are known either. |
| **B3 — out of reach** | **Plausibly** | If the row is derived from the Blink feature surface *as such*, then any Chromium-family engine produces it and it is not closable at any price short of not being Chromium. **Stock Chromium 151 producing the byte-identical row is consistent with this.** |

**Recommendation: do not pick a route for this row yet.** The next measurement is to identify
which features drive it — that is a separate, cheap, offline experiment (vary one feature at a
time against pixelscan's own scoring), and it should be its own ticket.

### Sequencing

**F1 is also the experiment that settles causation.** Ship the font spoof, re-read pixelscan on
one host: if the verdict flips, the font row was the cause and the `Browser` row is a
bystander. If it does not, the `Browser` row (or something else Chromium-family) is load-bearing
and B3 becomes the likely answer. **Either outcome is decisive, and it costs one extension
rather than a fork.** That ordering is the substantive recommendation of this ticket.

---

## DoD #4 — PS-16 correction: STAGED HERE, NOT YET LANDED

⚠️ **This DoD is not met by this PR, and I am not claiming it is.** `PS-16-PATCH.md` in this
directory is the exact replacement text, following the precedent of `ps177`, `ps182` and
`ps186`, which each shipped a patch file that the researcher/planner seat then applied to the
article. **The worker seat produces the patch; it does not edit the knowledge article.**

**At the time of writing, PS-16 is at v18 and still contains the retired sentence verbatim:**

> *"Settling whether the pixelscan gate is achievable needs a machine with a real GPU."*

**The handoff is therefore explicit, not assumed.** What remains, for whoever applies it:

1. Replace the retired conclusion in PS-16 §"The pixelscan question…" with the replacement
   text in `PS-16-PATCH.md`.
2. Correct the **Table 1 firefox / pixelscan cell** from `— ‡` ("not measured") to the affirmed
   score, per that file.
3. Do **not** score the three PS-186 chromium records that rendered no pixelscan verdict
   (macos/5150, linux/5150, linux/24601) as passes — they are coverage holes.

Until those land, PS-150, PS-159 and PS-170 also still repeat the retired conclusion. **The
ticket's DoD #4 should be read as open until the article edit is applied.**

---

## DoD #5 — not covered, with reasons

- **Causation is not proven for either row.** Correlational over five legs; engine family is
  perfectly confounded with every Chromium-only value. Stated above with what would settle it.
- **Two legs' exits were not retained, and are unrecoverable.** This reading holds **five legs
  but only four exit blocks**: legs A (firefox), B (chromium/5150) and B′ (chromium/24601) share
  the *file-level* `exit` in `pixelscan-page-text.json`, and that block is **the last
  invocation's only**. B′ was captured in a separate invocation (see Reproducibility), and the
  capture script rebuilt `exit` from the current run while merging only `engines` — so
  `83.24.254.80 / AS5617` is B′'s exit, and **the exits legs A and B actually ran through were
  overwritten.** `derive.py` §7 prints the gap (`page-text legs: 3 … NOT retained: 3`) rather
  than letting it pass silently. Round 1 of this ticket claimed "every leg drew a *different*
  rotating exit"; **that claim is withdrawn — it cannot be checked from this record.** What
  *is* evidenced is three distinct ASNs across the four surviving blocks, and — the part that
  matters — a firefox and a chromium **matrix** leg on different ASNs, which is the pair the
  cross-engine claim rests on. `scripts/ps194_pixelscan_text.py` now writes the exit **per
  leg** and preserves prior legs' exits on merge, so a re-run records what this one lost. This
  is the same defect the confirm review flagged in PS-186 (`asn` null in every record), which
  makes repeating it here the more embarrassing; the fix is in the script, not just the prose.
- **The inputs to the `Browser` row are unidentified**, so its routes are uncosted. Naming them
  needs its own experiment.
- **No `--help` surface was obtained from the packaged engine.** `--appimage-extract-and-run
  --help` dies `rc=133 FATAL execlp failed` on this host, so the engine's font-related flags
  (if any) were not enumerated. **If such a flag exists, F1 may be unnecessary** — worth 10
  minutes on a host where the AppImage runs.
- **`--no-sandbox` was waived on every chromium leg** (this host forbids the unprivileged user
  namespace). persona's own launch path passes it nowhere, so these are not the product's
  default surface. It applied equally to PS-186 and PS-170.
- **Only the `windows` declared machine was read**, and only seeds 5150/24601. macos/linux are
  PS-189's, and the three PS-186 chromium records with **no pixelscan verdict at all**
  (macos/5150, linux/5150, linux/24601) remain coverage holes — **not passes**.
- **`bot-detector :: detected` and `deviceandbrowserinfo :: bot_verdict_positive`** fire on the
  chromium leg and not on firefox, per the ticket's instruction to note overlap without folding
  it in. **They are a different root cause:** `bot-detector` reports `navigatorWebdriver`, an
  automation tell, not a fingerprint-coherence one. Not pursued here.
- **Firefox was not touched.** It passes.
- **Level 2 / mobile / android** — out of scope, unchanged.

---

## Reproducibility

```
python3 -m src.services.verify.checker_cli read --engine both \
  --declared-machine windows --seed 5150 --allow-unsandboxed-chromium \
  --credential /workspace/_secrets/test-proxy.txt -o readings/ps194-2026-08-26/matrix
python3 -m scripts.ps194_three_engine_pixelscan -o readings/ps194-2026-08-26/
python3 -m scripts.ps194_pixelscan_text -o readings/ps194-2026-08-26/
python3 -m scripts.ps194_pixelscan_text -o readings/ps194-2026-08-26/ \
  --engines chromium --seed 24601 --label chromium_seed24601
```

**Expected to match:** the verdict per leg, the `Browser` row, the font hashes per
(engine, seed, layer). **Expected to rotate:** every exit address, city, ASN, and the
`Time from IP` rows. `Xvfb` must be installed or the browser tier records 0 rows.
