# PS-373 — feder-cr / Camoufox native anti-detect, mapped onto our patch series

**Date:** 2026-09-07 · **Branch:** `feature/PS-373-canvas-probe-guards` off `main` `649f6ae`
**Seat:** worker · **Layer:** the C++ patch series. No JS masking layer involved.

> ## Read this first — what is MEASURED and what is not
>
> This ticket's three prior rounds were **source reading**, and the ticket says
> plainly why that is a limit: *"Source reading generates candidates; only the host
> ranks them"* (the PS-363 lesson, which has now displaced five leads including two
> of the planner's).
>
> **This round adds the one thing a container CAN settle: arithmetic.** The central
> claim — *our canvas patch modifies pixelscan's reference probe* — is a claim about
> what our own C++ does to a buffer, and that is executable here even though
> pixelscan is not. So the patch's **own loop was compiled and run**.
>
> ⛔ **Still NOT measured, and no engine was built or launched:** whether pixelscan's
> probe is really 70×5, whether 14 colours is the right count, whether `isCanvas`
> gates the font probe, and whether any of this clears the badge. All of that is
> **second-hand from feder's RE as recorded on this ticket**. The geometry is a
> *parameter* of the harness, not a constant, and results are reported across a
> sweep so no finding rests on one guessed size.

---

## The answer in one paragraph

Round 3's **finding** is confirmed by execution: our canvas patch modifies a
14-colour 70×5 reference render at every seed tested, so it fails a byte-exact
equality probe by construction, and no tuning of the noise budget can fix that.
But **three of round 3's four supporting claims are wrong**, and the fix it
proposed would not have worked. The `< 2` clamp it blames is *innocent* on that
geometry; we *already* have a uniform-render skip and *already* survive the
CreepJS `clearRect` trap; and feder's small-canvas size floor — the round-3
recommendation, and the whole content of proposed A/B 6 — **would exempt a real
fingerprinting canvas** while being the wrong shape for the problem. The correct
guard is not a size floor but a **distinct-colour ceiling**, which is what this
branch implements, and which is measured to exempt reference renders while
leaving every realistic fingerprinting canvas noised.

---

## 1. Method — why the numbers are about our patch and not about my typing

Re-typing the patch's arithmetic into a test would measure my transcription. So
`extract_shuffle.py` **mechanically extracts the loop from the patch file**: every
line it compiles is a `+` line of `012-canvas-get-image-data.patch` with the `+`
stripped and nothing else changed. Edit the patch, and the harness recompiles
against the edit. `test_extractor_is_reading_the_shipped_patch` asserts exactly
that relationship, so the link cannot rot silently.

**What is NOT verbatim, stated rather than glossed.** Chromium's tree is not in an
agent container, so the Skia surface the loop calls into is a shim
(`skia_shim.h`) — the pack/unpack macros, the colour-type enum, the addressing
macro. A mis-ordered channel would make every number below meaningless, so the
harness **round-trips known ARGB values through every colour type before it
measures anything** and aborts if the self-test fails. It reports `shim
self-test: PASS` on every run in `artifacts/`.

---

## 2. CONFIRMED — we fail a reference-equality probe, at every seed

`artifacts/probe-harness-before.txt`, section A, against the shipped patch:

```
== A. pixelscan reference probe, as REed by feder (70x5, 14 bands) ==
   budget arithmetic: (70*5)/128 = 2  -> after clamp = 2
   seed=0         modified reference pixels = 2   FAILS probe
   seed=12345     modified reference pixels = 2   FAILS probe
   seed=deadbeef  modified reference pixels = 2   FAILS probe
```

⭐ **This is the ticket's central question, settled on our side by execution.** The
probe is a *binary equality check*; one modified pixel fails it exactly as hard as
a thousand. So every "tune the density" lever on this ticket is dead — not
deprioritised, dead — and round 3 was right about that.

---

## 3. ⛔ CORRECTION A — the `< 2` clamp is INNOCENT, and A/B 6 as written tests nothing

Round 3's headline mechanism is a specific, quotable attribution:

> *"On pixelscan's 70×5 probe: `(70*5)/128 = 2`. The `< 2` branch **clamps up to 2**.
> ⭐⭐ The clamp that exists to guarantee a minimum amount of noise is precisely what
> guarantees we modify the detector's reference probe."*

**`2 < 2` is false. The clamp does not fire.** `artifacts/clamp-attribution.txt`
runs the loop twice on identical input — once with the shipped clamp, once with
the clause deleted — and the budget is **2 either way**:

```
   size       area     shipped    no-clamp   mod(ship) mod(no)   clamp fires?
   70x5       350      2          2          1         1         no    (pixelscan probe)
   15x15      225      2          1          2         1         YES   (area 225)
   8x8        64       2          0          1         0         YES   (area 64)
```

The clamp only fires below `w*h = 256`. The probe's area is 350.

⚠️ **Why this matters beyond pedantry.** The finding survives (we do modify the
probe) but the **cause** is different: a 350-pixel banded canvas earns a budget of
2 from the plain floor-divided area, with no help from any clamp. The real defect
is that the budget has **no notion of whether the render is a reference pattern**.
A fix aimed at the clamp would ship, look principled, and change nothing.

---

## 4. ⛔ CORRECTION B — we ALREADY have a uniform-render skip

Round 3's table records `uniform-render skip | feder: ≤16 colours → byte-exact |
ours: ⛔ none`. **The behaviour says otherwise.** `probe-harness-before.txt`
section C, on the *unmodified* patch, every size byte-exact:

```
== C. uniform (single-colour) render — the degenerate reference ==
   70x5         modified = 0  (byte-exact)
   280x60       modified = 0  (byte-exact)
   256x256      modified = 0  (byte-exact)
```

The reason is structural: our loop only writes where `isEdge` holds
(`*current != *right || *current != *bottom`), and a single-colour buffer has no
such pixel. ⭐ **So we have the guard implicitly — and `distinct-sweep-before.txt`
measures its width at exactly ONE distinct colour.** Two colours is already enough
to be modified.

⭐⭐ **That reframes the whole gap.** It is not "he has a guard and we don't". It is
**a threshold difference: our tolerance is 1, feder's `kMaxRefColors` is 16, and
the probe reportedly sits at 14 — inside the gap.** That is a far more precise
account of why the same probe passes his engine and fails ours, and it points
directly at the fix.

---

## 5. ⛔ CORRECTION C — we already survive the CreepJS `clearRect` trap

Round 3 lists `clearRect trap | ours: ⛔ skips pure-black PIXELS, not zero CHANNELS`
as a guard we lack. The code statement is true; the **behavioural** conclusion is
not. `artifacts/clearrect-probe.txt` draws a busy canvas, clears the left half to
`(0,0,0,0)`, and re-reads:

```
   pixels modified inside the CLEARED region : 0
   cleared pixels that read back NON-ZERO    : 0
   verdict: cleared pixels read back as zero -> CreepJS lied=true is NOT tripped
```

A cleared pixel is pure black, and pure black is already excluded by
`isValidColor`. The per-channel difference is **real but is a different case**: a
pixel with one zero channel that is not pure black *is* writable by our loop
(measured: 8 of 10). Whether any detector reads that case is a host question. It
is **not** the CreepJS trap, and porting feder's guard for that reason would be
porting it for a reason that does not apply.

---

## 6. ⛔ CORRECTION D — feder's size floor is the WRONG guard for us, and it costs protection

Round 3's recommendation, and the entire content of proposed **A/B 6**, is *"add a
size floor to `ShuffleSubchannelColorData` (skip `< 64*64*4`) and rebuild"*.

`artifacts/distinct-sweep-before.txt` measures what that floor would exempt:

```
   size         bytes      modified   under floor?
   70x5         1400       1          YES     (pixelscan probe)
   32x32        4096       7          YES     (favicon)
   100x30       12000      9          YES     (small fp canvas)
   64x64        16384      7          no      (exactly at floor)
```

⛔ **`100x30` is under the floor.** That is a small but entirely real fingerprinting
canvas, and a size floor would switch its masking off. The ticket's standing trap
is explicit — *"any recommendation must preserve or improve what is masked"* — so
the size floor must be **refused as written**.

⭐ **And it is the wrong SHAPE regardless of the threshold.** Size is a proxy for
"is this a reference render", and a poor one in both directions: it exempts small
real canvases, and it still noises a *large* solid-colour reference render. The
property the detector actually reads is **"few distinct colours"**, so that is what
the guard should test.

---

## 7. The fix — a distinct-colour ceiling, and what it costs

`012-canvas-get-image-data.patch` now carries a reference-render guard before the
noise loop: count distinct pixel values, **early-exit the moment the count exceeds
`kMaxRefColors = 16`**, and return byte-exact if it never does. This makes our
existing implicit 1-colour tolerance explicit and widens it to cover real
reference patterns.

**The early exit is what makes it cheap.** A genuine fingerprinting canvas passes
16 distinct colours within its first few pixels and leaves the block immediately;
only already-reference-like renders are scanned in full.

### Does it preserve protection? Measured, because that is the half that constrains

`artifacts/protection-regression-after.txt`:

```
   realistic fp canvas 220x30  :   6224 distinct colours
   realistic fp canvas 280x60  :  13924 distinct colours
   realistic fp canvas 300x150 :  34438 distinct colours
   realistic fp canvas 500x200 :  45551 distinct colours
   pixelscan reference probe   :     14
   guard ceiling kMaxRefColors :     16

   220x30  modified = 8    OK (protection preserved)
   280x60  modified = 8    OK (protection preserved)
   300x150 modified = 8    OK (protection preserved)
   500x200 modified = 9    OK (protection preserved)
   two different seeds differ : yes OK
   same seed reproduces       : yes OK
   70x5 / 14 colours modified = 0   OK (passes equality probe)
```

⭐ **Three orders of magnitude of separation** between the two populations
(6,224–45,551 vs a ceiling of 16). No plausible modelling error closes that gap.

⚠️ **The fingerprint canvas is MODELLED, not captured** — there is no browser here
to rasterise text, so antialiased glyph coverage is synthesised as a blend ramp.
That is what antialiasing does, but it is a model, and the claim is kept coarse
enough to survive it: *thousands vs tens*, not an exact count.

⚠️ **One honest edge, from `artifacts/threshold-cost.txt`.** The populations touch
at the extreme weak end: a canvas drawn with a **single** glyph in **one** colour
pair at **8-step** antialiasing models to 9 distinct colours, below the ceiling of
16. Whether such a canvas is a real fingerprinting surface is a host question. A
lower ceiling would shrink that exposure; 16 is chosen to match feder's measured
`kMaxRefColors` rather than to be independently optimal, and **it is a one-constant
change if the host says otherwise**.

### The test discriminates in BOTH directions

A green test that cannot fail pins nothing (PS-11). `tests/test_ps373_canvas_reference_guard.py`
proves it can, by mutation:

| mutation | expected | result |
|---|---|---|
| `kMaxRefColors = 100000` (guard too wide) | real canvases lose noise | **rejected**, `NOISE LOST` |
| guard disabled (`if (false)`) | probe gets modified | **rejected**, probe assertion fails |
| shipped | both hold | **passes** |

---

## 8. The three-column table, for the rows this round touched

| | **feder** | **Camoufox** | **ours (after this branch)** | ⭐ VERDICT | PORTABILITY |
|---|---|---|---|---|---|
| **reference-render skip** | explicit, `kMaxRefColors=16` | n/a — no canvas noise at all | ⭐ explicit, ceiling 16 (was implicit, 1) | **AGREE** (feder + the detector's own probe shape) ⇒ property of the DETECTOR | ✅ **DONE** — pure arithmetic, no RFP |
| **small-canvas size floor** | `< 64*64*4` skip | n/a | ⛔ **deliberately NOT ported** | **DISAGREE, and we refuse it** — measured to exempt a real 100×30 canvas | ✅ portable but ⛔ **costs protection** |
| **clearRect trap** | `if (*p == 0) continue;` | n/a | already survives it (measured) | **AGREE on the OUTCOME** by a different mechanism | n/a — nothing to port |
| **canvas 2D noise at all** | yes, (b) ~6.25% | ⛔ **none** | yes, ≤10 px | **DISAGREE + both pass** ⇒ we are **FREE** on quantity | — |
| **WebGL readback noise** | gamma LUT | ⛔ **none** | LSB set, same shared fn | **DISAGREE + both pass** ⇒ pixel noise is NOT what is read | ⛔ do not port the LUT on this evidence |

⭐ **The shared-function coupling is an advantage here.** `ShuffleSubchannelColorData`
is called by **012, 013 and 016**, so this single guard moves `getImageData`,
`toDataURL` **and** WebGL `readPixels` together — one edit, three surfaces, and
feder's WebGL uniform-render skip is satisfied by the same code.

---

## 9. What the owner should run — and what would refute this

⛔ **A/B 6 as round 3 wrote it should NOT be run.** It tests a size floor that is
measured here to be both the wrong shape and a protection loss.

**A/B 6′ (replacement).** Build this branch, change nothing else, load pixelscan.
> **Prediction: "masking detected" clears, and `osFontsStatus` flips to the 49-font
> Windows list.**
> ⛔ **If it does not clear**, the `canvasNoiseOn2d` → `isCanvas` → font-list chain
> is not what our engine trips, feder's RE does not transfer to Chromium's probe
> path, and the canvas family is **exonerated** — which points hard at a
> non-graphics surface.

**A/B 7 (unchanged, still valuable).** Read `isCanvas` / `osFontsStatus` /
`jsFontsKey` directly rather than the headline badge.
> **Prediction: they move together.** If `isCanvas` flips true while the badge
> stays, a second independent surface is also failing.

**A/B 8 (new, cheap, and it falsifies the ceiling specifically).** On the built
branch, render a canvas with a **known** number of distinct colours either side of
16 and read `getImageData` back.
> **Prediction: ≤16 colours byte-exact, ≥17 modified.** This tests the *threshold*
> rather than the badge, so it can localise a failure that A/B 6′ can only report.

⚠️ **The standing warning applies to my own most attractive finding.** feder's own
source records that he *previously* believed a canvas rationale here and later
called it *"a red-herring"* — the real driver was `OfflineAudioContext` noise. **He
was wrong about his own canvas once, in this exact area, and corrected it by
measurement.** Nothing above is a claim that this clears the badge.

---

## 10. Deliberately not done

- ⛔ **PS-367 port sub-tickets are NOT filed.** Round 3's reasoning holds and is
  strengthened: A/B 6′ can retire the canvas hypothesis outright, and filing the
  port now would bake in the guess this ticket exists to replace.
- **Rows 4–6 (WebGL params / fonts-navigator-screen-audio-clientRects-timezone /
  seed propagation) remain unswept.** Row 4 overlaps PS-365 and stays flagged.
- **`native-antidetect-techniques.md`** — the per-surface table above is the portion
  this round's evidence supports; publishing the full page before the host ranks
  A/B 6′ would publish the unranked half as fleet knowledge.

## Housekeeping

No engine built or launched; no secrets, proxies or tokens touched or read. No
network clones — this round worked entirely from our own tree plus the C++
evidence already recorded on the ticket by rounds 1–3. Compiled binaries were
removed; only sources and captured outputs are committed.
