# PS-16 patch — the Firefox WebGL readback cell, and the framing PS-182 corrects

**DoD #5 of PS-182 is: "Knowledge article PS-16 is updated — its Table 2 currently has no
Firefox row for this vector."** That statement is now itself out of date: PS-177 added the row
(article v9) while PS-182 was being written, and the row it added carries a **diagnosis that this
ticket's measurements refute**. So the work here is not to fill a blank cell — it is to correct a
filled one.

This file exists because **I have no MCP tool that can write a knowledge article.** The worker
toolset exposes `get_knowledge_article`, `search_knowledge_articles`, `list_knowledge_articles`
and `link_ticket_to_knowledge` — all read-or-link. There is no create/update counterpart, and
`add_comment` does not accept a knowledge article as a commentable. Same constraint PS-177 hit and
recorded; same remedy — the edit is delivered here, ready to apply verbatim.

**Which figures are derived, and which are judgement.**

* **Derived** — printed by `node readings/ps182-2026-08-26/harness.js` from the shipped script and
  committed as `result.json`: every digest, every eligible-byte count, every "distinct across N
  seeds" figure, and the bit-identical corpus validation against `readings/ps135-2026-08-24/`.
  Re-run the harness rather than re-typing any of them.
* **Derived from the commit log** — the four timestamps in Edit 3, via
  `git log --format='%h %ad' --date=iso <path>`.
* **Judgement** — the words "leading hypothesis", and the decision to leave the Table 2 cell
  reading **FAILS**. Both are argued inline so they can be disagreed with.

**Verified against the LIVE article at version 10** (fetched 2026-08-26, `updated_at`
2026-08-26T07:43:33Z). Every block quoted below as "currently reads" was matched verbatim against
that version, not against a local copy. Note the article moved 9 -> 10 *while this ticket was being
worked* — the change restructured **Table 1** (the per-checker score rows) and touched none of the
six passages patched here, so these edits still apply cleanly. If the version has moved again,
re-check the "currently reads" blocks before applying: an anchor that no longer matches means
someone else edited the same passage, and that is a conversation, not a merge.

**Applying this does NOT clear the failure.** The live collision is real and unfixed. What changes
is the *stated cause*, which currently points at a delivery gap that measurement shows does not
exist — and which would send the next worker to fix an engine path that is already working.

---

## Edit 1 — `Last re-derived` line at the top

Replace:

```
**Last re-derived: 2026-08-25, against `origin/main` @ `c04e15d`.**
```

with:

```
**Last re-derived: 2026-08-26, against `origin/main` @ `6dffce6`.**
```

---

## Edit 2 — Table 2, the Firefox WebGL readback cell

The cell currently reads:

```
| **firefox / windows / desktop** | — | — | **🔴 FAILS — one value @ 2 seeds** | — | — | — | — | — |
```

Replace with:

```
| **firefox / windows / desktop** | — | — | **🔴 FAILS at the checker — one value @ 2 seeds; but the layer is MEASURED WORKING (4 distinct @ 4 seeds on the loopback probe)** | — | — | — | — | — |
```

**Why the cell still says FAILS.** Two Firefox profiles remain linkable on a row a checker really
reads. Nothing about PS-182's measurements makes that untrue, and downgrading the cell because the
internal buffer differs would be exactly the substitution the ticket forbids: *"a probe reading two
different buffers while a checker reads one identical hash is exactly the disagreement to preserve
rather than average away."*

---

## Edit 3 — replace the diagnosis paragraph under Table 2

The paragraph currently reads:

```
`creepjs :: webgl_pixel_hash` reads **`51df3565` on both** firefox seeds (1337 and 4242), across
two different exits and two different days — so two firefox profiles are **linkable to each other**
on a vector the bar names explicitly. The same row on chromium takes three distinct values at three
seeds, which is what makes this a masking gap rather than a checker constant.

**Owned by PS-182.** Bound: n=2 firefox seeds. A third firefox seed settles whether it is invariant
across all seeds or those two collided; that is the cheapest high-value reading available and it is
blocked on the proxy.
```

Replace with:

```
`creepjs :: webgl_pixel_hash` reads **`51df3565` on both** firefox seeds (1337 and 4242), across
two different exits and two different days — so two firefox profiles are **linkable to each other**
on a vector the bar names explicitly. The same row on chromium takes three distinct values at three
seeds.

**PS-182 MEASURED THE CAUSE AND IT IS NOT A MISSING FIREFOX SPOOF.** The obvious reading of this
row — "our perturbation never runs on firefox" — is REFUTED, and by more than an argument:

* `firefox_webgl_init_script` exists and `invisible_launch.py:3345` installs it UNCONDITIONALLY.
  PS-78 (`01b10cd`) delivered it **2026-08-23 01:47**, about six hours BEFORE PS-97 landed.
* PS-97 (`09e34b5`, **08-23 08:01**) edited the **shared** `_CONTENT_SCRIPT`/`perturbBytes`, which
  the firefox script splices via `_webgl_patch_js`. The engines differ only in the
  nativeWrap/worker-cloak seam, so **PS-97's fix reached firefox too.**
* PS-103 (`aae090a`, **08-23 08:49**) put the layer into the checker harness, firefox route
  included.
* **Both collision readings postdate all three** (`ps128` 08-23T21:46, `ps137` 08-24T09:51) and
  both record `masking_layer installed=[audio,locale,webgl] route=init_scripts complete=true`.

Executing the SHIPPED script in fresh realms (`readings/ps182-2026-08-26/`, 4 seeds) gives
**4 distinct readback digests** on the probe geometry, **4 distinct** under CreepJS's starved 17x42
corner (16 eligible bytes of 2856 — the geometry PS-97 fixed chromium for), and **4 distinct** in a
WebGL2-only realm (the firefox worker shape). The harness reproduces the digests a real
`firefox-20` engine recorded in `readings/ps135-2026-08-24/` **bit-identically**
(`2372980207`@111, `1471895271`@1337, `1444116715`@4242), so the engine's readback equals
*(ideal buffer) + (our perturbation)* byte for byte — **persona's perturbation provably executes on
real firefox, and the per-profile entropy in that reading is ours.**

**Leading hypothesis (NOT settled):** the only geometry that reproduces the collision is one with
**zero bytes passing the mid-range guard** `v > 1 && v < 254`. If CreepJS's sampled corner is fully
cleared on the firefox render, the perturbation is a no-op *there specifically* while working
everywhere else. Consistent with the sharpest clue in the corpus: within the same records
`webgl_image_hash` DIFFERS per seed (`5e9c98db` vs `911e9c23`) while `webgl_pixel_hash` does not —
the same checker, the same WebGL surface, one row moving and one frozen.

**Owned by PS-182; the delivery path is closed, the checker row is not.** Confirming the hypothesis
needs a live CreepJS read on firefox capturing the readback region's byte census — blocked on the
proxy (rejected at account level). The only lever is the shared mid-range guard, which is
out of scope for PS-182 and byte-pinned by `tests/test_webgl_ext.py`; widening it blind would move
every chromium readback and ship a perturbation nothing observes, which is the PS-97 mistake
itself. **Do not "fix" the firefox delivery path — it is not broken.**
```

---

## Edit 4 — the two-engine rule's worked example (§"The two-engine rule")

The section uses PS-97/PS-182 as its worked example and states the Firefox half "was never
delivered … It stayed broken and invisible for three days". **That is now known to be wrong**, and
it is the single most load-bearing sentence in the article for anyone closing a masking ticket.

Replace:

```
**What happened, as the worked example:** PS-97 measured a WebGL readback collision **on Firefox**.
It correctly established that persona's perturbation code (`webgl_ext`) is a **Chromium extension**
that does not run on the Firefox path, fixed Chromium, and closed. The Firefox half — the path the
finding was actually measured on — was never delivered. It stayed broken and invisible for three
days, and became visible only when PS-177 produced **chromium counter-evidence** to contrast it
against. The tell was in plain sight the whole time: the hash `51df3565` appears in PS-97's own
table on 23 Aug and again in PS-177's sweep on 25 Aug, byte-identical, with a merged "fix" between
them.
```

with:

```
**What happened, as the worked example — and the correction PS-182 forced.** PS-97 measured a WebGL
readback collision **on Firefox**, reasoned that `webgl_ext` is a Chromium extension that does not
run on the Firefox path, fixed Chromium, and closed. The recurring hash `51df3565` — in PS-97's own
table on 23 Aug and again in PS-177's sweep on 25 Aug, byte-identical with a merged "fix" between
them — was read as proof that the Firefox half was never delivered.

**PS-182 measured that story and it does not hold.** PS-78 had ALREADY delivered
`firefox_webgl_init_script` at 01:47 on 23 Aug, six hours before PS-97 landed; PS-97's change was to
the SHARED patch body, so it reached Firefox as well; and the Firefox perturbation demonstrably runs
on a real engine (§"Table 2"). The Firefox path was not undelivered. The row simply does not move
at the checker, for a reason that is still open.

**So the rule survives, but its lesson changes — and the new one is harder.** The original moral was
"deliver on the measured path". The measured moral is:

**A recurring identical hash across a fix is a QUESTION, not a verdict.** It is equally consistent
with (a) the fix never ran, and (b) the fix ran and this particular reading cannot see it. Those
demand opposite responses — ship the missing delivery, versus stop shipping and go measure the
instrument — and choosing between them by inspection is how PS-182 came to be filed against a path
that was already working. **Distinguish them by EXECUTING the shipped artefact and reading the bytes
back**, which needs no browser and no exit
(`readings/ps182-2026-08-26/harness.js` is the worked example), before writing a line of fix.

The engine-axis discipline below is unchanged and is still right: name both engines or say why one
is out of scope. Just do not infer WHICH engine is broken from a hash that did not move.
```

---

## Edit 5 — "What to measure first", item 2

Item 2 currently reads *"A second profile through the same checker"* and describes Level 2 as
structurally unmeasured. Append to that item:

```
   **PS-182 sharpens what this reading must capture.** For the firefox WebGL readback specifically,
   a second profile at a third seed is no longer the highest-value ask: the loopback probe already
   shows 4 distinct values at 4 seeds, so another seed will very likely differ internally and change
   nothing about the checker row. What is needed instead is **the byte census of the region CreepJS
   samples** on a live firefox render — `bytes`, and how many pass `v > 1 && v < 254`. That single
   number decides between "the perturbation is invisible there" and "something else is wrong", and
   no amount of extra seeds substitutes for it.
```

---

## Edit 6 — the Level 2 bound in §"The headline, before the tables"

The Level-2 paragraph states the firefox arm is *"MEASURED-AND-FAILING on firefox/windows/desktop
(n=2, one linking row)"*. That remains true at the checker. Append:

```
  **PS-182 (2026-08-26) adds the counterpart reading and it points the other way.** On the LOOPBACK
  probe, the same engine at four seeds produces four distinct WebGL readbacks — so the linking row
  is a property of what the CHECKER can see, not of what the profile renders. Level 2 on this arm is
  therefore failing **at the checker** while the underlying vector is measured healthy, and the two
  statements are recorded side by side deliberately: an internal buffer differing did not survive
  the trip to the checker, which is the PS-97 lesson restated as a bound rather than averaged away.
```
