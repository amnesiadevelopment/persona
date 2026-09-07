# PS-365 — which spoofs can move onto the engine's STEALTHY flag mechanism

**Date:** 2026-09-07 · **Tree:** `feature/PS-365-flag-mechanism-audit` off `main` `0be4644`, clean
**Seat:** worker · **Deliverable:** a per-spoof verdict with a reason, plus an ordered A/B list.

> ⛔ **This is an AUDIT. Nothing here is implemented and no engine was built or
> launched.** Two things WERE measured in-container, and they are labelled as such
> throughout: a stock-Chromium **control** run this session, and a **re-scoring** of
> PS-344's committed readings. Every other statement is patch-source reading, which
> — per PS-363's lesson — **generates candidates and does not rank them.**

---

## The answer in one paragraph

The audit's own framing question was *"which noise spoofs can become flags?"*. The
honest answer is that **the question is aimed at the wrong property**, and the
correction is the finding.

The ticket predicted the noise family is unmovable because *"canvas/WebGL noise is
per-read and per-pixel"* and a flag is constant. **The first half of that is already
false in our tree**: all eight `kDisableSpoofing` patches were rewritten by upstream to
be **fully seed-deterministic** — zero non-deterministic sources remain, and the
`base::RandDouble()` calls the prediction describes are visible in the patches only as
**deleted** lines. So "it varies per read" cannot be why anything is unmovable.

What actually decides it is **ARITY** — how many arguments the spoofed value is a
function of. And measuring that splits the family the ticket treated as one:

- **`014-client-rects` and `015-canvas-measure-text` compute ONE constant per launch
  from the seed alone.** They are *already flag-shaped* and the structural objection
  does not apply to them at all.
- **`012` / `013` / `016` are functions of `(x, y, pixel)` and `003` / `006` of a
  per-call argument.** Those are genuinely unmovable, and the property is arity, not
  variance.

⭐ **And a second, larger result fell out of measuring it.** Moving these spoofs to a
flag would not make them stealthy anyway, because **the perturbation leaves a
detectable numeric signature that has nothing to do with the mechanism carrying it.**
Two invariants of stock Blink are violated by the noise family in **every DOM realm, on
every seed, with persona's JS layer ON** — measured, with a clean control. That is a
live detection this audit found and did not go looking for.

---

## Measured this session — the two DOMAIN invariants

**Instrument:** container Chromium **152.0.7977.82** (`/usr/bin/chromium`), which is the
same 152 line as the published engine and is being used **only as a stock control**.
Harness: `artifacts/ps365_domain_probe.html`.

| # | invariant | why stock Blink obeys it |
|---|---|---|
| **1** | every client-rect coordinate is an exact multiple of **1/64** | Blink stores layout in `LayoutUnit`, a 1/64-px fixed-point type |
| **2** | every canvas `TextMetrics` field is a **float32-exact** double | Blink computes text metrics in `float` and widens at the bindings layer |

**Control result — 0 violations on both, on a wide adversarial probe:**

```
rect values probed  6008   off-lattice   0
TextMetrics probed  1008   off-float32   0   (12 fonts x 12 strings x 7 fields)
```

⚠️ **The control corrected me once, and the correction is why the probe is shaped as it
is.** My first pass scored *transformed* and *percentage-width* elements and found 12
off-lattice values **on stock** — the lattice is a property of the layout box, and a
`scale()` transform legitimately leaves it. That would have been a false positive. The
committed probe uses only detector-authorable simple geometry, which is exactly what a
fingerprinter can guarantee for its own probe element. Likewise my first `measureText`
lattice idea died on its control (`1/64` is simply the wrong lattice for text) and was
replaced by the float32 test, which holds at 1008/1008.

### Scoring PS-344's committed readings against them

`artifacts/ps365_score_domain.py` — reads the published-152 and version-matched-stock
artifacts already in the tree. **No engine launched.**

```
ENGINE  rect off-lattice  64/64      TextMetrics off-float32  44/84
STOCK   rect off-lattice   0/64      TextMetrics off-float32   0/84
```

A total separation, and the per-row table shows three things the aggregate hides:

1. **It fires with the masking layer OFF**, so it is attributable to the **engine**, not
   to our extensions.
2. **It fires with the layer ON too**, and the mechanism is worth naming precisely.
   `measuretext_ext` wraps the result in a `Proxy` that divides **every** numeric field
   by the learned factor — so it does not skip the bounding-box fields. But a double
   divided by a non-float32 factor is a double: the quotient lands back on the float32
   grid only by coincidence. Measured, that coincidence happens for `width` at seed
   24601 and for nothing else — **2 of 3 fields off-domain at seed 24601, 3 of 3 at seed
   5150**. (Note the repaired `width` `172.109375` is also not the stock
   `172.1083984375`, so this is a plausible value rather than a restored one.) **The
   repair narrows the tell; it cannot close it, because dividing is the wrong inverse
   for a quantised domain.**
3. **Worker realms are clean** — 0/3 — because patch 015 never reaches them. That is the
   PS-344 asymmetry restated in domain terms: `measureText` is *broken* in the DOM and
   *unspoofed* in a worker, and **a detector can read both in one page**.

**Falsification (non-waivable, and run):** both predicates are driven to **both**
verdicts on values this project measured — 5 stock values that must pass, 6 engine
values that must fail. `11/11`. A scorer only ever seen to pass is not known to work;
this one has been seen to say the opposite on real inputs.

> ⛔ **Bound, stated plainly:** the container control is Chromium **152.0.7977.82** and the
> PS-344 readings are **152.0.7977.75** — near, not identical. The invariants are
> structural properties of Blink's layout and text pipelines rather than version
> constants, but *"I re-ran the probe on the published engine"* is a claim **I have not
> earned** and it is A/B #1 below.

---

## Per-spoof verdict table

Three questions per the ticket: **(1)** is the value constant for a launch? **(2)** does a
flag-authored version reach every realm? **(3)** what does moving it cost?

Arity census: `artifacts/ps365_arity_census.py`. It **derives** the per-call inputs from
the patch text rather than restating a hand reading, asserts zero non-deterministic
sources in all eight patches (assertion holds), and **self-tests that it can see each
hash-input shape before it reports** — because it could not, at first. Its initial
version read only `std::string x = …;` bindings and therefore scored `006-font` as
seed-only, contradicting a hand reading of the same patch. `006` passes its per-call
argument **inline** — `std::hash<std::string>{}(fingerprint + requested_family)` — and a
probe that cannot see that shape reports a movable spoof that is not. **The disagreement
between the tool and the hand reading is what found it**; the self-test now pins both
shapes so it cannot regress silently.

| spoof | value is a function of | constant per launch? | verdict |
|---|---|---|---|
| **`011-gpu-info`** | seed + `--fingerprint-platform` | **YES** | ⭐ **ALREADY FLAG-AUTHORED.** Nothing to move. It is the existing proof that the pattern works. |
| **`005` deviceMemory** | *nothing* — hardcoded `8` | YES | ⭐ **ALREADY NATIVE, needs a SWITCH.** Confirms the planner's correction. |
| **`002` UA/platform/brand** | seed + brand/platform/version flags | YES | ⭐ **ALREADY FLAG-AUTHORED** — and undetected, per the owner's A/B. |
| **`018-timezone`** | `--timezone` | YES | ⭐ **ALREADY FLAG-AUTHORED.** |
| **`014-client-rects`** | **seed ALONE** (`hash(seed+"offset_x")`, once per Document) | **YES** | ⚠️ **ARITY PERMITS A FLAG — but moving it buys NOTHING.** See below. |
| **`015-canvas-measure-text`** | **seed ALONE** (`hash(seed+"clientrects_noise_x")`) | **YES** | ⚠️ **ARITY PERMITS A FLAG — same caveat, plus PS-345.** |
| **`012-canvas-get-image-data`** | seed **+ x + y + pixel colour + edge test** | **NO** | ⛔ **UNMOVABLE.** Per-pixel arity — a flag carries one value; this needs one per pixel. |
| **`013-canvas-toDataURL`** | delegates to `012` | NO | ⛔ **UNMOVABLE** — inherits 012's arity. |
| **`016-webgl-readPixels`** | delegates to `012` | NO | ⛔ **UNMOVABLE** — inherits 012's arity. |
| **`003-audio-fingerprint`** | seed **+ `number_of_frames` / `sample_rate`** | **NO** | ⛔ **UNMOVABLE.** The page chooses the argument. |
| **`006-font-fingerprint`** | seed **+ `requested_family`** | **NO** | ⛔ **UNMOVABLE.** It is a per-font *decision* (hide / substitute), not a value. |

### ⭐ The two rows that change the picture — and why "movable" ≠ "worth moving"

`014` and `015` compute **one number per launch from the seed and nothing else**:

```cpp
// 014-client-rects, in Document's constructor — ONCE per document
std::string combined_x = seed_str + "offset_x";
noise_factor_x_ = norm_x * 0.002;          // then Offset() applies it everywhere
```

By the ticket's stated test they are movable. **They should nonetheless NOT be moved,
and this is the audit's most useful negative result.** The stealth of a flag comes from
the engine authoring a *plausible* value before page code runs. These patches do not
author a value — they **perturb one Blink already produced**, and the arithmetic is done
in `double` on a quantity Blink keeps in `LayoutUnit`. **Carrying that same offset in a
flag would produce byte-identical off-lattice output.** The tell is in the *result*, not
in the *transport*, so the mechanism swap is invisible to the detector.

⭐ **The seed is already a flag** (`--fingerprint`) **and travels stealthily; the
perturbation it drives is what gets read. That is the boundary the ticket asked to
locate, and this is it, stated precisely:** a flag can carry a *value*; it cannot make a
*perturbation of a quantised value* land back on the quantisation.

**The route that would actually work for these two is to make the perturbation
domain-preserving** — snap the offset to a whole `1/64` unit, and the float32 grid for
metrics — which is a **patch fix, not a mechanism move**, and is adjacent to PS-345's
territory rather than this ticket's.

### PS-345 read before auditing the canvas patches, as instructed

PS-345's defect is confirmed and **the audit does not rest on the broken value**. The
arity finding for `015` is read from the *inputs* to its hash, which the offset-vs-
multiplier bug does not touch. Separately, this scoring **adds** to PS-345: the defect
is usually described as *"negative widths"*, but the negative sign is only the coarsest
symptom. **Persona's JS repair divides every metric by the learned factor, and those
quotients do not land back on the float32 grid** — so a fix validated on `width` alone
would still ship a detectable `TextMetrics`.

### Coordination with PS-362 — not duplicated

PS-362 (PR #300, `pending_audit`) owns the switch-surface census and has since moved the
split to **6 wired / 2 consumed-not-passed / 4 declared-never-consumed**. This audit
**consumes** that column and does not recount it. One note for it, offered rather than
acted on: the three screen switches are recorded `COVERED_ELSEWHERE` because
`device_ext` authors them in JS — and `device_ext`'s screen authorship is exactly the
`def()`-on-the-instance shape the researcher flagged. **That row is the strongest
candidate for a real flag move in the tree**, and unlike the noise family it has no
domain problem: `screen.width` is an integer.

---

## Cost of each move (question 3)

| route | cost | who can ship it |
|---|---|---|
| add a switch to an **existing** native read-site (`deviceMemory`) | smallest — no new patch | needs a rebuild (PS-7, manual, Linux-only) |
| wire a **dead** read-site (screen trio) | medium — `018-timezone` is the in-tree reference | rebuild |
| **new** native patch (`maxTouchPoints`, `languages`, `mediaDevices`) | large | rebuild |
| JS override | smallest to ship — rides a persona release | no rebuild |

⚠️ **The trade the ticket asked to be stated:** a stealthier spoof that can only be
updated by rebuilding an engine is **not automatically better**. For values that change
with the world — brand version, GPU pools, font lists — the rebuild latency is a real
cost and a JS override that can ship in an afternoon may be correct. For values that are
**structural and rarely change** — `platform`, `maxTouchPoints`, `deviceMemory`,
`screen` — the flag route wins outright, because those are exactly the values a JS
override pays a permanent detectability cost to author.

---

## ⛔ Traps respected

- **"No masking detected" is not the goal.** Nothing here proposes removing a spoof.
  Both concrete suggestions (snap the perturbation to the lattice; move screen geometry
  to a flag) **preserve the masked value** and change only its numeric domain or its
  transport.
- **pixelscan is not the scorer.** iphey 100/Trustworthy governs. Note the two
  invariants above are **not** pixelscan findings — no checker is known to test them.
  They are a detection that *is available*, which is a different and weaker claim than
  *"we are being detected by this today"*, and it is deliberately not overstated.
- **`--disable-spoofing` is a diagnostic.** Nothing here proposes shipping it.
- **No pixelscan reproduction was attempted in-container.**

---

## The A/B list for the owner — ordered, each with a prediction that can come back wrong

**(1) is worth more than the rest combined and costs one paste.**

### 1. Do the two domain invariants fire on the REAL published engine?

In a persona-Chromium page console (normal launch, layer ON):

```js
(() => {
  const f32=new Float32Array(1), isF32=v=>{f32[0]=v;return f32[0]===v;};
  const d=document.createElement('div');
  d.style.cssText='position:relative;width:100px;height:20px;margin-left:8px';
  d.textContent='Wg'; document.body.appendChild(d);
  const r=d.getBoundingClientRect();
  const ctx=document.createElement('canvas').getContext('2d');
  ctx.font='16px sans-serif'; const m=ctx.measureText('Hello measured text');
  return {rect_x:r.x, rect_on_1_64: Math.abs(r.x*64-Math.round(r.x*64))<1e-9,
          width:m.width, aL:m.actualBoundingBoxLeft, aR:m.actualBoundingBoxRight,
          metrics_all_f32:[m.width,m.actualBoundingBoxLeft,m.actualBoundingBoxRight].every(isF32)};
})()
```

**Prediction: `rect_on_1_64: false` and `metrics_all_f32: false`.** On stock Chrome both
are `true`.
*If both come back `true` on the engine, my model is wrong and the entire domain finding
must be withdrawn before anything is filed on it.*

### 2. The same read in a Web Worker, same page

```js
new Worker(URL.createObjectURL(new Blob([`
  const c=new OffscreenCanvas(300,80).getContext('2d'); c.font='16px sans-serif';
  const m=c.measureText('Hello measured text');
  postMessage([m.width,m.actualBoundingBoxRight]);`],{type:'text/javascript'})))
  .onmessage = e => console.log('WORKER', e.data);
```

**Prediction: the worker returns clean stock values while the page does not** — the
page/worker split PS-344 measured, now readable by a detector in one page as an
*internal contradiction*. A contradiction is a stronger tell than either value alone.
*If the worker matches the page, patch 015 reaches workers after all and PS-344's
asymmetry has changed.*

### 3. Does the pixelscan verdict move at all with the noise off?

`--disable-spoofing=canvas` (diagnostic only), then read pixelscan **and iphey**.

**Prediction I am least confident in, and I flag that:** pixelscan's noise arm quiets and
**iphey stays 100**. ⛔ **This measures a diagnostic configuration with reduced
protection and is NOT a shipping proposal** — its only purpose is to size how much of the
verdict the noise family owns before anyone spends a rebuild on it.

### 4. The screen-geometry flag question — the one worth a rebuild

Before writing any patch, confirm the three switches are still inert on the **152**
engine (PS-301's deadness reading is a *144* number):

```
--fingerprint-screen-width=2560 --fingerprint-screen-height=1440 \
--fingerprint-device-scale-factor=2
```
then read `screen.width`, `screen.height`, `devicePixelRatio` with **the persona
extensions disabled**.

**Prediction: still ignored — `800`/`600`/`1` or the host's real geometry.** If so, the
screen trio is the highest-value flag move available: an integer value, no domain
problem, and it retires the largest `def()`-on-the-instance surface in `device_ext`.
*If any of the three is honoured on 152, that is a change since PS-301 and it should be
reported first — it would make this a wiring task rather than a patch task.*

---

## What this audit did NOT establish

- **That pixelscan reads either invariant.** It is an *available* detection, measured
  against a control; which checker exercises it is unknown and is question 1's job.
- **That the published engine reproduces the container control.** Near-version, not
  identical — stated above, and it is A/B #1.
- **Anything about macOS or Windows.** Every measurement here is Linux x86_64.
- **A ranking of the candidates.** Per PS-363: patch-source reading generates
  candidates; only the host ranks them.
