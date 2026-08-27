# PS-16 PATCH — from PS-193 (byte census of the region CreepJS samples)

**Why a patch file and not an edit:** the worker toolset cannot write a knowledge
article. PS-182 hit the same constraint and shipped a `PS-16-PATCH.md` alongside its
reading; this follows that precedent. **Apply these edits to PS-16 verbatim.**

Every figure below was **re-derived**, not edited to match: run
`python3 readings/ps193-2026-08-26/derive.py` and it prints them from the committed
JSON. That is PS-16's own maintenance rule.

---

## EDIT 1 — Table 2, the `firefox / windows / desktop` **WebGL readback** cell

The cell text stays **`🔴 FAILS`** — the defect is real and is not fixed by this
reading. Only the annotation changes, because "one value @ 2 seeds" is now stale twice
over (PS-186 raised it to n=4; this reading explains *why*).

**Replace:**

```
| **firefox / windows / desktop** | — | — | **🔴 FAILS — one value @ 2 seeds** | — | — | — | — | — |
```

**With:**

```
| **firefox / windows / desktop** | — | — | **🔴 FAILS — one value @ 4 seeds; CAUSE MEASURED (PS-193)** | — | — | — | — | — |
```

---

## EDIT 2 — replace the "Owned by PS-182." line (DoD 5, the orphaned owner)

This is the line the ticket exists to un-orphan. It currently points at a **closed**
ticket. Replace **`**Owned by PS-182.**`** — the line that follows the
"✅ The discriminating reading is the BYTE CENSUS…" paragraph — with the block below.

Note the paragraph immediately above it says the census is *"the one measurement that
genuinely needs a live exit"* and calls the starvation account *"the leading hypothesis,
and the only geometry that reproduces the collision."* **The census has now been taken
and it refutes that hypothesis**, so that paragraph must not be left standing as an open
question. This block replaces the ownership line and settles it.

```markdown
### ✅ PS-193 — THE CENSUS IS TAKEN. THE STARVATION HYPOTHESIS IS REFUTED, AND THE CAUSE IS THE REALM.

**The census, measured at CreepJS's own `readPixels` call on a real Firefox engine
through the proxied exit** — four live arms, four different exits, four different ASNs,
both the `webgl` and `webgl2` pass in each (8 readings, all identical):

| | |
|---|---|
| **total bytes in the region CreepJS samples** | **2912** |
| **bytes passing the guard `v > 1 && v < 254`** | **32** (1.10%) |

**❌ Candidate 1 — "the sampled region is starved" — REFUTED.** The region does **not**
hold ~zero guard-eligible bytes. Three independent reasons this kills the account:
**32 ≠ 0**, so PS-182's geometry **C** (the only geometry that reproduced the collision)
is not the live geometry; **32 > 16**, and 16 is geometry **B**, the starved-but-*working*
case that yielded four distinct digests; and `_BUDGET = 512` is far above 32, so the
shipped selector already spends its budget on **every one of the 32**.

**✅ Candidate 2 — "the delta never reaches the realm the checker reads" — CONFIRMED BY
MEASUREMENT, not by elimination.** Two live arms with the shipped
`firefox_webgl_init_script(seed)` installed, at two seeds, reducing CreepJS's received
bytes with FNV-1a at its own callsite:

| | seed 1337 | seed 4242 | |
|---|---|---|---|
| digest CreepJS received | `1379655975` | `1379655975` | **IDENTICAL — collides** |
| digest in the **page realm**, same run, same instant | `855826239` | `1729355265` | **differs — the spoof RAN** |

The page-realm column is the **positive control**, and it is what makes this a finding
rather than the PS-14 trap: an identical result across cells otherwise cannot distinguish
*the spoof never executed* from *the spoof executed and is not observed*. It executed.

**THE MECHANISM — it is the REALM, and it is renderer-independent.** Isolated on
loopback (no exit needed, since "which realms does our own code reach" is a property of
our code):

| realm | unspoofed | seed 1337 | seed 4242 | |
|---|---|---|---|---|
| `top_canvas` | 660023932 | 855826239 | 1729355265 | **REACHED** |
| `top_offscreen` | 660023932 | 855826239 | 1729355265 | **REACHED** |
| `phantom_canvas` | 660023932 | 660023932 | 660023932 | **NOT REACHED** |
| `phantom_offscreen` | 660023932 | 660023932 | 660023932 | **NOT REACHED** |

⚠️ **It is NOT "we miss OffscreenCanvas."** `top_offscreen` **is** an OffscreenCanvas and
it is reached; `phantom_canvas` is an ordinary canvas and it is not. Reading
`canvas_class: OffscreenCanvas` off the record and fixing that axis would have been the
wrong fix.

**Why that realm is missed**, in both codebases' own words. CreepJS builds a phantom
iframe and takes it by **indexed window access**, then constructs the canvas *from that
realm*:

```js
const iframeWindow = self[numberOfIframes];   // creep.js getPhantomIframe — INDEXED
let win = window; if (!LIKE_BRAVE && PHANTOM_DARKNESS) { win = PHANTOM_DARKNESS; }
canvas = new win.OffscreenCanvas(256, 256);
```

Our chain hooks the **accessors** (`worker_wrap.py:387-403`):

```js
["contentWindow", "contentDocument"].forEach(function (prop) { ... });
```

`self[N]` **never invokes `HTMLIFrameElement.prototype.contentWindow`**, so the chain's
only door into a child frame never opens and the leaf is never installed there.

**This also corrects a docstring.** `firefox_webgl_init_script` states that
`realm_bootstrap_js` "carries the leaf onward into workers and child frames, so the worker
realm is covered too" (`webgl_ext.py:288-290`). True for a frame reached through the
accessors; **false for one reached by index** — which is the one CreepJS uses.

**This is the PS-155 / PS-161 / PS-189 failure class on the SAME axis PS-189 found — the
REALM.** PS-189's service worker is unreachable because there is no constructor to
intercept; this frame is unreached because the interception point is an accessor the
consumer never touches. Same shape, different door.

⚠️ **DO NOT WIDEN THE SHARED MID-RANGE GUARD.** PS-193's ticket made that arguable *only
if the census reported starvation*. **It does not.** Widening it would move every Chromium
readback to chase a hypothesis this reading has refuted — the exact PS-97-shaped mistake
PS-182 was created to correct.

⚠️ **Renderer caveat, stated in both directions.** The census engine is Firefox 151.0 on
Mesa **llvmpipe** under Xvfb, not the packaged `firefox-20` on SwiftShader, so its
published hash is **`a8ee71dc`, not the corpus's `51df3565`**. This reading reproduces the
**shape** (one value, invariant across seeds, while the page realm varies), **not the
value**, and `32` is this engine+renderer's count for this scene. **The realm finding is
renderer-independent** — it is about which realms a script is installed into, and it is
reproduced on loopback with no exit in the picture.

**Basis:** `readings/ps193-2026-08-26/EVIDENCE.md`; re-derive with
`python3 readings/ps193-2026-08-26/derive.py`.

**Owned by PS-193.** The remedy — delivering the leaf into a realm reached by indexed
access — is a change to the shared `realm_bootstrap_js` chain that every module rides.
That is a design decision with real blast radius, not a worker call, so PS-193 measures
and hands off. **It is unowned until someone takes it.**
```

---

## EDIT 3 — "What to measure first", item 2

Item 2 currently reads *"The byte census of the region CreepJS samples, on Firefox"* and
describes it as the outstanding discriminating reading. **It has been taken.** Replace the
item with:

```markdown
2. ~~**The byte census of the region CreepJS samples, on Firefox.**~~ **TAKEN — PS-193.**
   The region holds **2912 bytes, 32 of them guard-eligible** (not ~zero), so the
   starvation hypothesis is **refuted** and the cause is measured instead: the shipped
   perturbation does not reach the **phantom-iframe realm** CreepJS reads, because that
   realm is taken by indexed access (`self[N]`) and our chain hooks the `contentWindow`
   accessor. See the firefox entry in Table 2. **What is now outstanding is a FIX, not a
   reading** — and it is a change to the shared realm chain, so it is a design decision
   rather than a measurement.

   Level 2 remains **structurally unmeasured on every arm except firefox/windows**, where
   it is **measured and failing**. On the other arms a second profile at a different seed,
   diffed against an existing reading, is still what converts a bar level from "assumed"
   to "known".
```

---

## EDIT 4 — the re-derivation date at the top

Per the maintenance rule ("Update the re-derivation date at the top"), the firefox
readback cell and the two blocks above were re-derived **2026-08-26 against
`readings/ps193-2026-08-26/`**. The rest of the article is unchanged by this patch — in
particular **no chromium figure, no GPU figure and no score in Table 1 was touched**, and
none should be inferred from this reading.
