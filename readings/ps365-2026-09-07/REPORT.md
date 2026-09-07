# PS-365 — which spoofs can move onto the engine's STEALTHY flag mechanism

**Date:** 2026-09-07 · **Tree:** `feature/PS-365-flag-mechanism-audit` off `main` `0be4644`, clean
**Seat:** worker · **Deliverable:** a per-spoof verdict with a reason, plus an ordered A/B list.
**Revision 2** — reworked after code review on PR #301. See *Correction 3*.

> ⛔ **This is an AUDIT. Nothing here is implemented and no engine was built or
> launched.** Three things WERE measured in-container, and they are labelled as such
> throughout: a stock-Chromium **control** run this session, a **device-scale boundary
> sweep** added in revision 2, and a **re-scoring** of PS-344's committed readings.
> Every other statement is patch-source reading, which — per PS-363's lesson —
> **generates candidates and does not rank them.**

> ⭐ **What changed in revision 2, and why it is worth reading even if you read
> revision 1.** Code review established that **invariant 1 was over-generalised**: it
> is a property of stock Blink at a *power-of-two* `devicePixelRatio`, not of stock
> Blink, and persona itself passes `--force-device-scale-factor` on scaled Windows and
> macOS hosts. The consequence was concrete — **A/B #1 as originally written could only
> ever be confirmed, never refuted.** Correction 3 measures the boundary across 15
> scales, A/B #1 is rebuilt to test the lattice in *device* space and validated against
> stock at 7 scales, and the recommended fix for `014` is now stricter than it was.
> **The arity census, Correction 1 and Correction 2 are unchanged and their tools
> re-run byte-identically.**

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

⚠️ **One of the two is CONDITIONAL, and code review is what established the
condition.** Invariant 1 (the client-rect lattice) is a property of stock Blink
only at a **power-of-two `devicePixelRatio`** — at Windows 125% / 150%, a scale
**persona itself passes**, stock Chromium violates it in the thousands. It is
still a real tell, but a *conditional* one that must divide out `dpr` first.
Invariant 2 (float32-exact `TextMetrics`) carries no such condition — measured
clean at every scale. **Correction 3 below is that boundary, measured.**

---

## Measured this session — the two DOMAIN invariants

**Instrument:** container Chromium **152.0.7977.82** (`/usr/bin/chromium`), which is the
same 152 line as the published engine and is being used **only as a stock control**.
Harness: `artifacts/ps365_domain_probe.html`.

| # | invariant | why stock Blink obeys it | scale-independent? |
|---|---|---|---|
| **1** | every client-rect coordinate is an exact multiple of **1/64** | Blink stores layout in `LayoutUnit`, a 1/64-px fixed-point type | ⚠️ **NO — conditional on `devicePixelRatio`.** See the boundary control below. |
| **2** | every canvas `TextMetrics` field is a **float32-exact** double | Blink computes text metrics in `float` and widens at the bindings layer | ✅ **YES — measured 0/1008 at every scale from 0.5 to 4.** |

**Control result — 0 violations on both, on a wide adversarial probe:**

```
rect values probed  6008   off-lattice   0     <- AT devicePixelRatio 1 ONLY
TextMetrics probed  1008   off-float32   0     (12 fonts x 12 strings x 7 fields)
```

> ⛔ **The `0 / 6008` above is a dpr-1 reading and nothing more.** An earlier
> version of this report generalised it to *"a property of stock Chromium"* that
> *"a detector can check with no knowledge of persona at all"*. **That
> generalisation was wrong**, it was caught in code review, and the correction
> is the next section. Invariant 2 is unaffected.

### ⭐ Correction 3 — invariant 1 is a property of stock Blink **at dpr 1**, not of stock Blink

The committed control varied 500 geometries, 12 fonts and 12 strings. **It never
varied device scale factor.** That omission matters here rather than
academically, because **persona itself passes the flag**:

```python
# src/services/browser/process.py:1253-1255
scale = _host_display_scale()
if scale != 1.0:
    args.append(f"--force-device-scale-factor={scale:g}")
```

and `_host_display_scale()` (`launch_policy.py:534`) returns the **Windows system
DPI** or the macOS Retina backing scale, clamped to `[1.0, 3.0]`. **Windows at
125% or 150% is one of the most common desktop configurations there is.** So a
scaled display is an ordinary persona launch, not a contrived parameter.

**Boundary control, run this session** — `artifacts/ps365_dpr_sweep.sh` driving
`artifacts/ps365_dpr_lattice_probe.html`, same geometry as the committed control
so the dpr-1 row is directly comparable. Three formulations, scored per scale:

| scale | dpr pow2? | `css_1_64` off | `dev_1_64` off (ε) | `dev_exact` off | non-f32 |
|---|---|---|---|---|---|
| 0.5 | ✅ | **0** | **0** | **0** | 0 |
| 0.75 | ❌ | 2914 | 2914 | 1643 | 345 |
| **1** | ✅ | **0** | **0** | **0** | 0 |
| 1.1 | ❌ | 5833 | 6008 | 1709 | 903 |
| **1.25** | ❌ | **3591** | 3591 | 1229 | 477 |
| 1.3333 | ❌ | 6005 | 6008 | 2957 | 884 |
| **1.5** | ❌ | **2925** | 2925 | 1552 | 344 |
| 1.75 | ❌ | 4877 | 4877 | 3503 | 715 |
| **2** | ✅ | **1810** | **0** | **0** | 0 |
| 2.5 | ❌ | 3156 | 3156 | 1135 | 482 |
| 3 | ❌ | 2188 | 2188 | 1250 | 342 |
| 4 | ✅ | 3306 | **0** | **0** | 0 |
| 5 | ❌ | 3801 | 3801 | 1222 | 570 |
| 6 | ❌ | 3793 | 3037 | 1661 | 389 |
| 8 | ✅ | 3441 | **0** | **0** | 0 |

**This is stock Chromium failing the committed invariant on thousands of values.**
`artifacts/ps365_dpr_boundary.py` scores the formulations and self-tests first
(every predicate driven to **both** verdicts, plus a tolerance arm — exit 1 if any
arm misbehaves; it exits 0).

**The mechanism, and it *confirms* the `LayoutUnit` model rather than undermining it:**
the lattice is in **device** pixels. Blink divides by the scale factor and stores
the CSS-pixel result through a `float`, so at a scale whose reciprocal is not
exactly representable the CSS value lands off the 1/64 grid **by construction**.

```
dpr 1.5   x = 5.364583492279053    x*64       = 343.333…   (off)
                                   x*1.5*64   = 515.000…   (on)
dpr 2     x = 5.3671875            x*64       = 343.5      (off)
                                   x*2*64     = 687.0      (exactly on)
```

⭐ **And the boundary is NOT "integral scale", which is the obvious wrong answer
and the one I would have written without measuring.** `dev_exact` is clean at
`0.5, 1, 2, 4, 8` — **exactly the powers of two.** dpr **3 and 5 are integers and
both fail**; **0.5 is fractional and passes.** The property is *exact
representability of the division*: dividing by a power of two only shifts the
exponent, so the float32 store is lossless; any other divisor discards mantissa
bits. The tool asserts `clean set == power-of-two set` and it holds on all 15 rows.

⚠️ **A note on the third column, because it is a trap I fell into first.** The
naive device-space test `|v*dpr*64 - round()| < ε` is **not** the right
instrument: the float32 store already discarded bits, so multiplying back
recovers an integer only to within a float32 ulp — which at a coordinate near
4000 is far above any fixed `1e-9`. An ε-based device test measures its own
tolerance. `dev_exact` carries **no epsilon at all**: it derives the candidate
integer and *replays the forward pipeline* (`float32(n/64/dpr) == v`).

**⛔ What this does NOT establish:** that a scale-independent formulation of
invariant 1 exists. `dev_exact` was the best candidate and **it is not one**.
Saying so is the result — inventing a fourth predicate that happens to fit these
15 rows would be curve-fitting, not measurement.

**Invariant 2 is untouched by all of this** — `artifacts/inv2-scale-sweep.txt`,
**0/1008 at every scale from 0.5 to 4**, including the scales where invariant 1
collapses. The asymmetry is structural: invariant 1 is a *lattice* claim about a
value Blink **divided** by the scale factor, while invariant 2 is a
*representation* claim about a value that never passes through the scale factor
at all. "Is this double a float32" is closed under nothing the scale factor does.

⚠️ **The control corrected me once before submission, and code review corrected it a
second time.** My first pass scored *transformed* and *percentage-width* elements and
found 12 off-lattice values **on stock** — the lattice is a property of the layout box,
and a `scale()` transform legitimately leaves it. That would have been a false positive.
The committed probe uses only detector-authorable simple geometry, which is exactly what
a fingerprinter can guarantee for its own probe element. Likewise my first `measureText`
lattice idea died on its control (`1/64` is simply the wrong lattice for text) and was
replaced by the float32 test, which holds at 1008/1008.

⭐ **The third correction is the one I did not find myself, and it is the same variable
one layer down.** The `transform: scale()` false positive above was *device scale
showing up in CSS*; the defect review caught was device scale showing up in the
**launch flag**. Same axis, same failure mode — a probe that passed because it was
never driven over the axis that breaks it. Correction 3 is that axis, now swept.

### Scoring PS-344's committed readings against them

`artifacts/ps365_score_domain.py` — reads the published-152 and version-matched-stock
artifacts already in the tree. **No engine launched.**

```
ENGINE  rect off-lattice  64/64      TextMetrics off-float32  44/84
STOCK   rect off-lattice   0/64      TextMetrics off-float32   0/84
```

> ⭐ **The separation stands, and its precondition is satisfied.** I checked the
> `argv` recorded in both PS-344 artifacts: **no `--force-device-scale-factor` on
> any arm**, engine or stock, and no `devicePixelRatio` key in either file. Both
> sides were measured at **dpr 1**, which is exactly where invariant 1 holds. So
> the `64/64` vs `0/64` result is a valid comparison — what Correction 3 forbids
> is *generalising* it to scaled displays, not the measurement itself.

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

> ⛔ **Bounds, stated plainly.**
> - **Version.** The container control is Chromium **152.0.7977.82** and the
>   PS-344 readings are **152.0.7977.75** — near, not identical. *"I re-ran the
>   probe on the published engine"* is a claim **I have not earned** and it is
>   A/B #1 below.
> - ⚠️ **Device scale (Correction 3).** Invariant 1 holds only at a
>   power-of-two `devicePixelRatio`. Every claim about it in this report carries
>   that precondition, and A/B #1 tests the lattice in **device** space and reads
>   `devicePixelRatio` explicitly so it can come back wrong.
> - **Invariant 2 carries no scale precondition** — measured across the sweep.
> - **Linux x86_64 only.** Nothing here says anything about macOS or Windows,
>   which is where the non-power-of-two scales actually occur.

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
domain-preserving** — which is a **patch fix, not a mechanism move**, and is adjacent to
PS-345's territory rather than this ticket's. ⚠️ **Correction 3 sharpens what
"domain-preserving" has to mean, and it is not the obvious thing:**

- For `014-client-rects`, snapping the offset to a whole `1/64` **CSS** unit is
  **the wrong fix** — that is precisely the quantity stock Blink itself does not
  preserve off a power-of-two scale. The offset must be a whole `1/64` unit in
  **device** space, i.e. applied before the divide-and-store-as-float32, so the
  perturbed value is the float32 image of *some* integer `LayoutUnit` count.
  Anything else re-creates the tell on exactly the scaled displays persona's own
  `--force-device-scale-factor` produces. ⭐ **This is a stronger constraint than
  the pre-review version of this report stated, and it makes the fix harder, not
  easier** — the patch would have to know the scale factor.
- For `015-canvas-measure-text` the constraint is simpler, because invariant 2
  carries no scale precondition: the perturbed metric need only be **float32-exact**.

⛔ Neither of these is proposed as work here — they are the shape a fix would have
to take, recorded so a later ticket does not implement the CSS-space version and
believe it closed the tell.

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
  ⚠️ **And invariant 1's availability is narrower still**: a detector shipping it
  as stated would flag **stock Chrome** on any non-power-of-two display scale, so
  it is only usable once `devicePixelRatio` is read and divided out — and even
  then only where the division is exact. Stated at its real strength, it is *"a
  structural tell on an unscaled (or power-of-two-scaled) display"*, not *"a tell
  a detector can check with no knowledge of persona at all"*, which is what an
  earlier version of this report claimed.
- **`--disable-spoofing` is a diagnostic.** Nothing here proposes shipping it.
- **No pixelscan reproduction was attempted in-container.**

---

## The A/B list for the owner — ordered, each with a prediction that can come back wrong

**(1) is worth more than the rest combined and costs one paste.**

### 1. Do the two domain invariants fire on the REAL published engine?

⚠️ **This snippet is the code-review rewrite.** The first version tested the
lattice in **CSS** space and predicted `rect_on_1_64: false` — but stock Chrome
*also* returns `false` at dpr 1.25 / 1.5 / 1.75, so that prediction could only
ever be confirmed and never refuted. **On a scaled Windows display it would have
read as confirmation of a model it did not test.** This version reads
`devicePixelRatio`, tests the lattice in **device** space, and reports the
precondition alongside the answer.

In a persona-Chromium page console (normal launch, layer ON):

```js
(() => {
  const f32=new Float32Array(1);
  const toF32=v=>{f32[0]=v;return f32[0];}, isF32=v=>toF32(v)===v;
  const dpr=window.devicePixelRatio;
  // exact: is v the float32 image of an integer count of 1/64 DEVICE px?
  const onDev=v=>{const n=Math.round(v*dpr*64);
    return [n-1,n,n+1].some(c=>toF32(c/64/dpr)===v);};
  const onCss=v=>Math.abs(v*64-Math.round(v*64))<1e-9;
  // is the division itself lossless? (dpr an exact power of two)
  const pow2=Number.isFinite(dpr)&&dpr>0&&Math.log2(dpr)===Math.round(Math.log2(dpr));
  const mk=css=>{const d=document.createElement('div');
    d.style.cssText='position:relative;'+css; d.textContent='Wg';
    document.body.appendChild(d); const r=d.getBoundingClientRect();
    return [r.x,r.y,r.width,r.height,r.top,r.left,r.right,r.bottom];};
  // BOTH geometries on purpose: integral sits on the lattice at any scale and
  // therefore cannot discriminate; fractional is the one that exercises it.
  const gi=mk('width:100px;height:20px;margin-left:8px');
  const gf=mk('width:100.37px;height:20.13px;margin-left:7.3px');
  const ctx=document.createElement('canvas').getContext('2d');
  ctx.font='16px sans-serif'; const m=ctx.measureText('Hello measured text');
  return {dpr, dpr_is_pow2:pow2,
          INVARIANT_1_VALID_HERE: pow2,             // <- read this FIRST
          integral_on_device_lattice: gi.every(onDev),
          fractional_on_device_lattice: gf.every(onDev),   // <- the real reading
          fractional_on_css_1_64: gf.every(onCss),
          fractional_vals: gf,
          width:m.width, aL:m.actualBoundingBoxLeft, aR:m.actualBoundingBoxRight,
          metrics_all_f32:[m.width,m.actualBoundingBoxLeft,m.actualBoundingBoxRight].every(isF32)};
})()
```

**How to read it, in order:**

1. **`INVARIANT_1_VALID_HERE`** — if this is `false`, the host is on a
   non-power-of-two scale and **the rect half of this A/B tells us nothing**.
   Report the `dpr` and either re-run with `--force-device-scale-factor=1` or
   read only the metrics half. ⭐ *Reporting `false` here is a useful outcome, not
   a failed run* — it tells us the invariant is unusable on the owner's actual
   hardware, which is itself the answer to "can a detector ship this?".
2. **`metrics_all_f32`** — unconditional, valid at any scale.
3. **`fractional_on_device_lattice`** — the rect reading, valid only when (1) is
   `true`. ⚠️ **Read the `fractional_` row, not the `integral_` one.** I measured
   both on stock: integral geometry sits on the lattice at *every* scale and so
   **cannot discriminate at all** — it is included only as a negative control,
   and my first draft of this A/B used integral geometry alone, which is why it
   would have returned a confirming `true` on hardware that proves nothing.

**Prediction (given `INVARIANT_1_VALID_HERE: true`):**
`fractional_on_device_lattice: false` and `metrics_all_f32: false`. On stock
Chrome at a power-of-two scale, **both are `true`** — I measured that across
`dpr ∈ {0.5, 1, 2, 4, 8}`, 6008 values each, zero violations, and re-ran this
exact snippet on stock at dpr 1 / 1.5 / 2 to confirm it returns `true` there.

*This can now come back wrong in four distinct ways, which is the point:*
- *both `true` at a pow2 dpr → my model is wrong and the entire domain finding
  must be withdrawn before anything is filed on it;*
- *`fractional_on_device_lattice: false` but `fractional_on_css_1_64: true` → the
  engine perturbs in CSS space rather than device space, and the mechanism
  differs from what I described;*
- *`integral_` and `fractional_` disagree in the other direction → the
  perturbation is geometry-dependent, which no patch reading predicts;*
- *`INVARIANT_1_VALID_HERE: false` → the invariant is not checkable on this host
  at all, and its practical reach is narrower than even Correction 3 says.*

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
- ⚠️ **That invariant 1 holds off a power-of-two device scale. It does NOT** — measured,
  Correction 3. Every use of it carries that precondition.
- ⚠️ **That a scale-independent formulation of invariant 1 exists.** `dev_exact` was the
  best candidate and it is not one. I am reporting the negative rather than fitting a
  fourth predicate to 15 rows.
- **That the engine perturbs in device space rather than CSS space.** The scoring
  separates engine from stock at dpr 1, where the two formulations agree, so the PS-344
  data cannot tell them apart. A/B #1 reports both columns for exactly this reason.
- **Anything about macOS or Windows.** Every measurement here is Linux x86_64 — which is
  precisely *not* where the non-power-of-two scales occur, so Correction 3's practical
  reach on real hosts is inferred from the flag's source, not measured on those hosts.
- **A ranking of the candidates.** Per PS-363: patch-source reading generates
  candidates; only the host ranks them.

---

## Artifacts

| file | what it is | re-run |
|---|---|---|
| `ps365_arity_census.py` | derives per-call hash inputs from the 8 patches; self-tests both binding shapes | `python3 ps365_arity_census.py` → `arity-output.txt` |
| `ps365_score_domain.py` | scores both invariants over PS-344's committed readings; 11/11 falsification | `python3 ps365_score_domain.py` → `score-output.txt` |
| `ps365_domain_probe.html` | the dpr-1 stock control (6008 rect + 1008 metric values) | → `control-stock-152.txt` |
| **`ps365_dpr_lattice_probe.html`** | **NEW** — same geometry, three lattice formulations, emits raw values | driven by the sweep |
| **`ps365_dpr_sweep.sh`** | **NEW** — the device-scale boundary control, 15 scales | `./ps365_dpr_sweep.sh` → `dpr-sweep-output.txt` |
| **`ps365_dpr_boundary.py`** | **NEW** — scores the formulations, self-tests every predicate to both verdicts | `python3 ps365_dpr_boundary.py` (exit 1 if broken) |
| **`inv2-scale-sweep.txt`** | **NEW** — invariant 2 across the sweep: 0/1008 everywhere | — |
| **`ab1-stock-validation.txt`** | **NEW** — A/B #1's snippet run verbatim on stock at 7 scales, the negative control | — |

Both previously-accepted tools were re-run after this rework and produce
**byte-identical output** to their committed transcripts.
