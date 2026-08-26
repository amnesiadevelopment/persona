# PS-16 patch — the cells PS-185 fills, and the ones it explicitly does not

**DoD #3 of PS-185 is: "PS-16 updated in this ticket — its maintenance rule, and the reason this
ticket exists."** This file exists because **I have no MCP tool that can write a knowledge article.**
The worker toolset exposes `get_knowledge_article`, `search_knowledge_articles`,
`list_knowledge_articles` and `link_ticket_to_knowledge` — all read-or-link. There is no
create/update counterpart, `add_comment` does not accept a knowledge article as a commentable, and
the REST path (`/api/v1/knowledge_articles/PS-16`) answers 404. Verified, not assumed.

So the edit is delivered here, ready to apply verbatim, rather than skipped or described vaguely.
This follows the convention `readings/ps177-2026-08-25/PS-16-PATCH.md` set.

## Provenance of every figure below — read this before applying

PS-177's reviewer blocked that ticket for claiming "nothing was hand-typed" when four scores were in
fact judgement. That correction is honoured here by stating the split up front:

* **Derived** — printed by `readings/ps185-2026-08-26/derive.py` from the committed JSON records, and
  reproducible by re-running it. `derived-output.txt` is that output, committed alongside. This
  covers **every number in this patch**: all collision probabilities, distinct-identity counts, seed
  counts, the unbiased/expected/plug-in statistics, the Monte-Carlo p-values, and every readback hash.
* **Judgement** — the *prose reading* of those numbers: which verdicts are estimator artefacts, and
  what the firefox `webgl_pixel_hash` contrast means for PS-182. Each is argued inline from the
  derived figures so it can be disagreed with. **No 0–100 score is added or changed by this ticket**,
  so unlike PS-177 this patch introduces no judgement scores at all.

**Whoever applies this: apply it as written.** If you re-type a derived number, re-run `derive.py`
instead.

---

## Edit 1 — the `Last re-derived` line at the top

Replace:

```
**Last re-derived: 2026-08-25, against `origin/main` @ `c04e15d`.**
```

with:

```
**Last re-derived: 2026-08-26, against `origin/main` @ `6dffce6` (PS-185).**
```

---

## Edit 2 — Table 2's GPU unlinkability column

In **Table 2 — what leaks, per vector**, replace the `GPU unlinkability` cells. The old column read
`RISK 15.6%` / `RISK 50%` / `RISK 12.5%` / `RISK 25%`, of which only the first was a measurement.

| engine / OS / type | GPU unlinkability (was) | GPU unlinkability (now) |
|---|---|---|
| chromium / windows / desktop | **RISK 15.6%** | **RISK 13.9%** — measured (layer OFF), 24 seeds |
| chromium / macos / desktop | **RISK 50%** *(theoretical)* | **RISK 53.1%** — measured (layer ON), 24 seeds |
| chromium / linux / desktop | **RISK 12.5%** *(theoretical)* | **RISK 18.1%** — measured (layer ON), 24 seeds |
| chromium / android / mobile | **RISK 25%** *(theoretical)* | **RISK 27.4%** — measured (layer ON), 24 seeds |

**Every "theoretical" in Table 2 is now "measured". No cell in this column is `—`.**

⚠️ **The basis is per-cell and the two are not the same quantity.** `measured (layer OFF)` on windows
is the ENGINE's figure — windows is the one arm that defers to it
(`ENGINE_AUTHORED_IDENTITY_ARMS = frozenset({"windows"})`). `measured (layer ON)` on the other three
is persona's own `gpu_ext` pool drawing through `pick()`, which is what those profiles actually ship.
A bare `measured` in this column would put both under one label. See Edit 3 for why the distinction
is load-bearing and what the other authorship arm reads.

⚠️ **The `android` row is the android GPU POOL, not mobile device-type coverage.** The row label is
PS-16's existing one and this patch does not rename it, but the two are different axes: no mobile
*device type* is reachable on this tier at all (`browser_tier.DECLARED_MACHINES` has no mobile
member — see the coverage table in Edit 8). Read this cell as the GPU arm it measures, not as
evidence that a mobile profile was tested.

---

## Edit 3 — replace the whole "GPU unlinkability" subsection

Replace the existing subsection *"### GPU unlinkability — the real weak spot, and macOS is the worst
cell"* (including its four-row basis table and the "Why windows defers and the others do not"
paragraph) with the block below. It is the `derive.py` output verbatim.

> ### GPU unlinkability — MEASURED on all four arms, both authorship arms
>
> **Lower is better; it is the chance that two random profiles draw the same card.** Every figure below is a MEASUREMENT taken on 2026-08-26 over 24 seeds per arm, on loopback with no proxy and no exit. Engine: `148.0.7778.215` (sha256 verified against the install manifest).
>
> ⚠️ **There are TWO numbers per arm and they are not interchangeable.** `engine_gpu_variance` measures with persona's layer OFF, because it polices the arms where the ENGINE authors the identity. But `ENGINE_AUTHORED_IDENTITY_ARMS = frozenset({"windows"})` — only windows ships that way. On macos/linux/android persona's own pool authors the pair via `gpu_ext`'s `pick(POOL, 0x67900)`, so the LAYER-ON column is the one that describes what a profile actually ships, and it is the column that replaces the old "theoretical" figures.
>
> | arm | authors the identity | **layer ON (what ships)** | layer OFF (the engine alone) | distinct ON/OFF | basis |
> |---|---|---|---|---|---|
> | windows | **engine** | **13.9%** | 13.9% | 9 / 9 | **measured (layer OFF)**, 24 seeds |
> | macos | ours (`gpu_ext`) | **53.1%** | 58.7% | 2 / 2 | **measured (layer ON)**, 24 seeds |
> | linux | ours (`gpu_ext`) | **18.1%** | 100.0% | 7 / 1 | **measured (layer ON)**, 24 seeds |
> | android | ours (`gpu_ext`) | **27.4%** | 100.0% | 4 / 1 | **measured (layer ON)**, 24 seeds |
>
> Every cell above is `measured`. No arm is `theoretical` any more, and no arm was left `—`.
>
> **Read the basis column, not just the number.** `measured (layer OFF)` on windows is the ENGINE's figure, because windows is the one arm that defers to it; `measured (layer ON)` on the other three is persona's own pool drawing through `pick()`. Those are different quantities and the column is what tells them apart.
>
> **windows layer-ON is byte-identical to layer-OFF** — all 24 of 24 seeds returned the same identity in both modes, the same 9 distinct identities, the same 13.9%. That is what deferring is supposed to look like, and it is a positive control: on linux and android the two columns diverge sharply (linux 1 identity with the layer off against 7 pool entries with it on, android 1 identity with the layer off against 4 pool entries with it on), which is the layer proving it reached the page rather than an assertion that it was installed.
>
> #### ⚠️ The gate's own verdicts on three of those arms are an ESTIMATOR ARTEFACT, not a product finding
>
> `engine_gpu_variance` returns `TOO_NARROW` for macos, linux AND android on the layer-ON run. An identical adverse verdict across every non-windows cell is the shape this project has learned to distrust (PS-14), and it does not survive checking.
>
> `collision_probability` is the **plug-in** Simpson index `sum (n_i/N)^2`, which is a BIASED estimator; `bar_for(arm)` is `1/k`, the collision probability of a uniform draw **in the limit**. Those are not comparable at finite N, because under a genuinely uniform draw `E[S_hat] = 1/k + (1 - 1/k)/N`. So a perfectly uniform `pick()` is EXPECTED to score above the bar, and the gate flags it.
>
> | arm | plug-in (what the gate uses) | unbiased | E[plug-in] if uniform | bar `1/k` | Monte-Carlo p | reading |
> |---|---|---|---|---|---|---|
> | windows | 0.1389 | 0.1014 | 0.2333 | 0.2000 | 1.000 | OK → — |
> | macos | 0.5312 | 0.5109 | 0.5208 | 0.5000 | 0.308 | TOO_NARROW → artefact |
> | linux | 0.1806 | 0.1449 | 0.1615 | 0.1250 | 0.164 | TOO_NARROW → artefact |
> | android | 0.2743 | 0.2428 | 0.2812 | 0.2500 | 0.580 | TOO_NARROW → artefact |
>
> **The single line that settles it:** android scored 0.2743, which is BELOW the 0.2812 a uniform draw is expected to score at N=24 — and the gate still called it `TOO_NARROW`. An arm cannot be *worse than uniform* while scoring *better than uniform predicts*. The comparison failed, not the pool.
>
> **So the old "theoretical" figures are CONFIRMED rather than overturned:** the uniform-selection assumption behind them holds on the real draw (p = macos 0.31, linux 0.16, android 0.58, none anywhere near significance). What has changed is that they are now measurements instead of assumptions — which is the result PS-185 was written to get, and it is a result even though the numbers barely moved: it retires an assumption. **Whether `engine_gpu_variance` should adopt the unbiased estimator is a decision for that module's owner — PS-185 measured and reported it, and deliberately did not change the gate.**
>
> #### The GENUINE finding: linux AND android are CONSTANT with the layer off
>
> * **linux** — every one of 24 profiles was handed the SAME identity (`Google Inc. (Google) | ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)`). Monte-Carlo p = 0.000. This one IS a real finding, not an estimator artefact.
> * **android** — every one of 24 profiles was handed the SAME identity (`Google Inc. (Google) | ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)`). Monte-Carlo p = 0.000. This one IS a real finding, not an estimator artefact.
>
> Neither arm is engine-authored, so **this is not a live breach** — persona's own pool is what ships there, and the layer-ON column shows it working. It is the measurement that says those arms must NOT be moved into `ENGINE_AUTHORED_IDENTITY_ARMS`. linux confirms PS-161's existing SwiftShader reading; **android is new** — it had never been measured on either arm.
>
> **macos, engine side, has MOVED.** PS-161 recorded 76.9% over 30 seeds (Apple M2 87% / M4 13%). This run reads 58.7% over 24 seeds on the same two-value pool — same conclusion (the engine is worse than our own 50.0% pool, so macos stays ours), different number. Two different engine builds, so this is a re-measurement rather than a contradiction.
>
> Note also that the layer-ON macos pool draws **Apple M1 / Apple M2 Pro** while the engine draws **Apple M2 / Apple M4**. The two authors do not agree on which cards exist, which is worth knowing for PS-183 (`MAC_GPUS` widening) but is not this ticket's to fix.

---

## Edit 4 — Table 2's WebGL readback and canvas cells, firefox row

The firefox row currently reads **`🔴 FAILS — one value @ 2 seeds`** under WebGL readback, sourced
from checker data. **That cell is about the CHECKER and it stays as it is** — this ticket did not
take a checker read and must not overwrite a checker cell with a loopback one.

Instead, **add a loopback column** so the two venues sit side by side. That contrast is the finding.

| engine / OS / type | WebGL readback (checker) | **WebGL readback (loopback, new)** | canvas (checker) | **canvas (loopback, new)** |
|---|---|---|---|---|
| chromium / windows / desktop | **OK** — 3 values @ 3 seeds | **OK** — 3 values @ 3 seeds | OK | **OK** — 3 values @ 3 seeds |
| firefox / windows / desktop | **🔴 FAILS — one value @ 2 seeds** | **OK** — 3 values @ 3 seeds | — | **🔴 RISK — 2 of 3 seeds collide** |

---

## Edit 5 — replace the paragraph under the firefox readback cell

Replace the paragraph beginning *"`creepjs :: webgl_pixel_hash` reads `51df3565` on both firefox
seeds…"* with the block below (`derive.py` output verbatim, plus the two-engine note).

> ### WebGL / canvas readback — BOTH engines, on loopback
>
> Measured 2026-08-26 on loopback with the layer INSTALLED. Engines: chromium `148.0.7778.215`
> (digest verified) and `Mozilla Firefox 151.0` (invisible_core 20.14.0). Seeds 1337, 4242, 9001.
>
> | engine | vector | seed 1337 | seed 4242 | seed 9001 | verdict |
> |---|---|---|---|---|---|
> | firefox | `webgl_pixel_hash` | `dabeff0d` | `eb6c926b` | `4df84217` | **DIFFERS** |
> | firefox | `canvas_pixel_hash` | `2735004646:…` | `2735004646:…` | `994724504:…` | **DIFFERS** (2 of 3 collide) |
> | chromium | `webgl_pixel_hash` | `95ebd0dd` | `c85ceb58` | `d83cff45` | **DIFFERS** |
> | chromium | `canvas_pixel_hash` | `43978124:…` | `4044089155:…` | `1211239796:…` | **DIFFERS** |
>
> **Instrument check (PS-14):** every reading above was taken twice in independent runs — 12/12 came
> back byte-identical, so these are stable values and not one-off draws.
>
> #### ⭐ The firefox `webgl_pixel_hash` question — ANSWERED, and it is the harder answer
>
> PS-16 records the one outright FAILURE in this matrix: `creepjs :: webgl_pixel_hash` reads
> `51df3565` for BOTH firefox seeds 1337 and 4242, across two exits and two days. PS-185 asked
> whether the LOOPBACK probe sees that same collision. **It does not.**
>
> * checker (creepjs), firefox @1337 and @4242 → `51df3565` and `51df3565` — **identical**
> * loopback probe, firefox @1337 → `dabeff0d`, @4242 → `eb6c926b` — **different**
>
> So the seed DOES move this vector inside the browser, and the difference **does not survive the trip
> out to the checker**. That is the second of the two branches PS-185 named, and it is the expensive
> one: it is PS-97's exact lesson, one vector over.
>
> **Consequence for PS-182: it cannot be worked or verified on the loopback probe alone.** A green
> local reading is exactly what we already have while the checker still collides, so a loopback-only
> fix would produce precisely the false green this article exists to prevent. **PS-182 stays blocked
> on the proxy**, and that is now a measured statement rather than an assumption.
>
> ⚠️ **This is stated separately from the canvas result below, deliberately.** The two point in
> different directions and must not be averaged into one verdict.
>
> #### canvas readback — a SPLIT across the engines
>
> On firefox, seeds 1337 and 4242 produce the SAME canvas hash (`2735004646:bytes8192:mid6144`),
> while seed 9001 differs. On chromium all three differ.
>
> The mechanism is recorded in `local_probe`'s own docstring and is confirmed by the layer report in
> these records: canvas 2D is **delegated to `--fingerprint=`, which is chromium-only**, and the
> firefox arm returns before it. The layer report for the firefox readings lists
> `['audio', 'locale', 'webgl']` — **no canvas extension at all** — against ten on chromium. So
> firefox canvas entropy is whatever the engine happens to produce, and two of three seeds colliding
> there is expected rather than surprising.
>
> **This is a two-engine-rule cell, not a chromium cell.** A chromium canvas fix does not touch it.
> **Bound: n = 3 seeds.** One collision in three pairs is not a rate; it says the vector is not
> reliably seed-separated on firefox, not that it collides 33% of the time.

---

## Edit 6 — append to "What to measure first, and why that order"

Item 3 currently reads *"The macOS arm. Worst collision figure, and never read by a checker. Both
halves of the problem sit in one cell."* **Amend the first half only** — the collision figure is now
measured, so the reason to prioritise macOS has narrowed:

> 3. **The macOS arm, by a CHECKER.** Its collision figure is now measured (53.1% layer-on, PS-185)
>    and is no longer the open half of the question — but it remains the weakest arm and it has still
>    never been read by any checker. Only the checker half is left.

And **add** a new item, because PS-185 created it:

> 6. **A firefox `webgl_pixel_hash` reading through a healthy exit.** PS-185 established that the
>    loopback probe DIFFERS at the two seeds where the checker read identical, so the defect lives in
>    delivery rather than in generation. That makes the third-seed checker read the only thing that
>    can settle PS-182, and it is blocked on the proxy.

---

## Edit 7 — append to the "two-engine rule" section

Append after point 4:

> **PS-185 is the rule's first clean pass, and it is worth keeping as the worked example of the rule
> SUCCEEDING** (PS-97 is the example of it failing). Every instrument in PS-185 ran on both engines.
> That is what produced its sharpest result: the firefox `webgl_pixel_hash` contrast is only legible
> because chromium was read at the same seeds in the same session, and the canvas split is only
> visible because both engines' layer reports were recorded — firefox's `['audio', 'locale', 'webgl']`
> against chromium's ten extensions is what identifies the cause as a chromium-only delegation rather
> than a firefox bug.

---

## Edit 8 — append a coverage note to the headline section

Append to the end of *"The headline, before the tables"*:

> **PS-185 (2026-08-26) filled every matrix cell that runs on loopback**, which is a different axis
> from the checker coverage described above and does not widen it. It converted all three
> "theoretical" GPU unlinkability figures into measurements, measured the android arm for the first
> time on either authorship arm, and read the WebGL and canvas readback vectors on BOTH engines at
> three seeds. **It took no checker read** — the credential is rejected at account level — so Table 1
> is unchanged by it.
>
> Three things were attempted and NOT obtained, recorded here rather than left as blanks that read as
> untried:
>
> | wanted | status | why |
> |---|---|---|
> | any checker read | **not covered** | The proxy credential is rejected at account level (`User was rejected by the SOCKS5 server (1 3)`). Out of scope for PS-185, and a direct connection is never the fallback. |
> | firefox on macos / linux / android | **does not exist** | `InvisiblePlaywright` takes no OS/platform parameter, so Firefox presents Windows regardless (`declared_machine_honoured: false`, issue #211). Not a coverage gap — the configuration is unreachable. |
> | a mobile profile on the loopback path | **not reachable from this tier** | `browser_tier.DECLARED_MACHINES` is `("windows", "macos", "linux")` with no mobile member, and `masking_layer` hardcodes `device_type="desktop"` when it computes `engine_platform` (its own comment: *"a mobile declared machine is not a thing this tier can be asked for"*). The android GPU arm above is the android **GPU pool**, a different axis from a mobile **device type**. Reaching a real mobile profile needs the product's `build_mobile_extension` path, which this harness does not build. |
>
> No arm was recorded `INCONCLUSIVE`: all 4 GPU arms returned 24/24 readable seeds in layer-OFF and
> layer-ON, and all 70 readback cells that were read produced a usable value on both engines.
>
> ⚠️ **1 readback leg of 15 attempted produced no reading at all** —
> `readback-vectors.replicate.json` chromium@9001 (could not attach to persona's chromium over CDP
> on port 37053: TimeoutError: BrowserType.connect_over_cdp: Timeout 180000ms exceeded.). It is
> recorded here because a leg that returns NO value is invisible to a check for unusable values: the
> scan finds nothing wrong with the cells that survived. No published figure rests on it — it
> belongs to a repeatability re-run, and the chromium repeatability above is computed against
> `readback-vectors.replicate-chromium.json`, which is complete.
>
> Re-derive with `readings/ps185-2026-08-26/derive.py`; the records are committed beside it.

---

## One thing this patch does NOT change, and why

**Table 1 is untouched.** PS-185 took no checker read, and every Table 1 column is a checker verdict.
Filling any of it from loopback data would be exactly the borrowed-number false green this article
warns about two sections above its own maintenance rule.

**The firefox checker cell in Table 2 (`🔴 FAILS`) is also untouched**, for the same reason — Edit 4
adds a loopback column beside it rather than overwriting it. The whole value of PS-185's readback
result is the *contrast between the two venues*, which is destroyed if one is written over the other.
