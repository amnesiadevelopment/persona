# PS-185 — instrument provenance for this reading set

Everything in this directory was measured on **2026-08-26**, on loopback
(`127.0.0.1`), with **no proxy, no exit and no third party contacted**. This
file records the things about the *instrument* that a reader needs in order to
judge the *readings* — including one modified binary.

PS-14's rule is the reason this file exists: **check the instrument before
attributing anything to the product.** A modified instrument is declared, not
hidden, even when the modification cannot reach the measurement.

---

## 1. The engine is the product's own, and that is proven rather than asserted

| fact | value |
|---|---|
| engine | fingerprint-chromium |
| binary | `/home/yatfa/.persona/engine/fpchrome.AppImage` |
| build | **148.0.7778.215** |
| sha256 | `a5fa5e6c05cb7fa3617ec2ca642ad3cc6e586ac5249cc29edb0a602d695685f0` |
| declared digest (`builds.json`) | identical |
| `digest_matches_manifest` | **true** |

`ENGINE_DIR` was **missing `fpchrome.AppImage`** when this work started — it held
only `builds.json` and `.engine-complete`. The tier **refused to run** rather
than falling back to `/usr/bin/chromium`, which is the guard in `_engine_binary()`
working exactly as designed: a reading taken against a stock browser would not be
a reading of this product. The binary was restored from a
`/tmp/persona-ui-driver-*` copy and then **verified against the manifest digest**
before any measurement was taken. The digest match above is what turns "a binary
was put there" into "the product's own engine ran".

This block is emitted live into every record by `sweep.py:engine_provenance()`.

## 2. `/dev/shm` — 1024 MiB, and no `--disable-dev-shm-usage` anywhere

| fact | value |
|---|---|
| `/dev/shm` total | **1024.0 MiB** (1 073 741 824 B) |
| above the tier's 256 MiB floor | **true** |
| `disable_dev_shm_usage_used` | **false** |

The tier refuses to launch below `MIN_DEV_SHM_BYTES = 256 MiB`
(`chromium_tier.py:286`) rather than produce a corrupt reading. Under that
ceiling chromium does not degrade, it **dies mid-page** with a contentless
`TargetClosedError` — the failure **PS-133** was filed as, after a renderer
crash was recorded as a property of fingerprint seed 4242.

The worker container satisfies the floor with room to spare, so the escape
hatch was **not used**: no reading here trades shm for disk.

## 3. ⚠️ The X server was a PATCHED PRIVATE COPY of Xvfb

**This is the one modified instrument in this ticket.** It is stated here
because a reader who meets a patched binary in the provenance needs the
argument beside it, not a ticket comment they may never find.

### What was patched

The tier **refuses `--headless` deliberately** — a headless engine presents a
different surface to a fingerprinting checker, so a record taken under one
would not be the shipped configuration. These readings therefore needed a real
display, and the container had **no Xvfb and no root**. One was unpacked into
`$HOME` with `apt-get download` + `dpkg-deb -x`.

Xvfb resolves `xkbcomp` through a path prefix **compiled into the binary**. It
is not read from the environment and no flag overrides it, so an unpacked copy
cannot find the compiler and refuses to start. A **private copy** had that
prefix rewritten in place:

```
/usr/bin   ->   /tmp/xkb
```

Both strings are **exactly 8 bytes**, which is the entire reason the patch works
without relinking — the string is *overwritten*, never moved, so no offset in
the image shifts. It is consumed by the format string
`"%s%sxkbcomp" -w %d %s -xkm ...`, which is still present in the stock binary
and can be confirmed with `strings -a $(command -v Xvfb) | grep xkbcomp`.

The GPU sweeps ran headed on **`DISPLAY=:77`**; both
`engine-gpu-variance.*.json` records carry that value.

### Why it cannot affect any reading in this directory

The patched bytes are on the **keyboard-initialisation path only**. The vectors
these records carry —

* `UNMASKED_RENDERER_WEBGL` (the GPU identity pair),
* `webgl.readback` (a hash over real `gl.readPixels` bytes),
* `canvas.readback`,

— are produced by the GPU and canvas stacks and **never consult XKB**. There is
no code path by which a keyboard-layout compiler returns a different pixel.

The failure mode is also **loud, not silent**, which is the stronger half of the
argument: if the XKB path breaks, **Xvfb refuses to start** — there is then no
display, no browser, and *no record at all*. It cannot produce a plausible wrong
reading, which is the failure shape this project actually keeps hitting (see
PS-11, and the `/dev/shm` misdiagnosis in PS-133 above).

### ⚠️ The patched copy no longer exists on this container

It lived in `$HOME` and `/tmp` and did not survive the container restart between
the first submission and this rework. `/usr/bin/Xvfb` now exists and is
**root-owned and unpatched**, so:

* `sweep.py:xserver_provenance()` probes the Xvfb it finds **live** and will
  today report `xkbcomp_path_patched: false` — that is an honest statement about
  **today's** server, and it is **not** a retroactive claim about the server the
  committed records were taken under;
* the records in this directory were taken under the **patched** copy described
  above, on `DISPLAY=:77`;
* a future re-run on a container that already has a working Xvfb needs **no
  patch at all**, and its records will say so by themselves.

The probe reads the prefix out of the binary rather than trusting a note about
it, so **whatever a future run actually used is what its record will state.**

## 4. What is script-derived and what is judgement

Every number in `derived-output.txt` and in `PS-16-PATCH.md` is produced by
`derive.py` from the committed JSON records. Re-run it and diff:

```
python3 readings/ps185-2026-08-26/derive.py --output /tmp/derived.txt
diff /tmp/derived.txt readings/ps185-2026-08-26/derived-output.txt
```

Use `--output` rather than a stdout redirect. `print()` appends one trailing
newline that `--output` does not write, so the redirect form reports a benign
one-byte difference and the next maintainer has to work out that it is harmless.
The `--output` path is byte-identical.

The judgement (`PASS`/`TOO_NARROW`/`INCONCLUSIVE`) is
`engine_gpu_variance.classify()` — **imported, never re-implemented** — so the
bar, the skew sensitivity and the `MIN_SEEDS = 8` floor are the module's own and
cannot drift from it. The collision bar is likewise read out of `gpu_ext`'s own
pool.

Where `PS-16-PATCH.md` adds prose that `derive.py` does not print, the patch
says so **at that spot**, per fragment. That correction is inherited from
PS-177, whose reviewer blocked it for claiming nothing was hand-typed when four
scores were in fact judgement.

### 4a. The sample-completeness claim is derived, and recounted from the readings

The sentence reporting that the sweep was **not truncated** is generated by
`derive.py`'s `completeness_statement()`. It was previously a string literal, on
the one question — *"did this run get truncated?"* — that must be answered from
the data rather than asserted about it.

Two properties of that derivation are deliberate:

1. **The seed counts are recounted from the raw `readings`, not read out of
   `result.per_arm['seeds_readable']`.** That stored field is a summary the
   sweep wrote **about itself**; a truncated or stale run can carry a
   full-looking summary over reduced readings, so using it to answer "was this
   truncated?" asks the run to grade its own homework. The recount uses
   `classify`'s own rule (`[v for v in by_seed.values() if v]`). If the two ever
   disagree, the statement says so and follows the readings.
2. **Readback legs are counted by what was ATTEMPTED, not by what came back.** A
   vector reading `unavailable:` or `error:` is the page reporting that it could
   not read that vector; a launch that never attached produces **no vectors to
   inspect at all**. A scan over values cannot see the second kind — it finds
   nothing wrong with whatever survived and reports a clean sweep.

Both are pinned by `tests/test_ps185_patch_provenance.py`, which nulls half an
arm's readings and requires the statement to change. `PS-16-PATCH.md`'s Edit 8
carries this claim into PS-16, so it is **spliced** from the same function by
`splice_patch.py` (run it with `--check` to verify the patch is in sync) rather
than re-typed.

### 4b. One readback leg was attempted and produced nothing

`readback-vectors.replicate.json` records **chromium@9001 with zero vectors** —
the launch could not attach over CDP (`TimeoutError` after 180 s), which is the
process-exhaustion shape described in §5.2. It is disclosed in the derived
output because DoD #5 requires anything attempted and not obtained to be
recorded with its reason.

**No published figure rests on it.** That record is a repeatability re-run, and
the chromium repeatability figure is computed against
`readback-vectors.replicate-chromium.json`, which is complete; the firefox
comparison uses the firefox legs of the same file, all of which read. The
primary `readback-vectors.three-seeds.json` sweep is complete on both engines.

### 4c. `readback-vectors.two-seeds.json` is a SUPERSEDED pilot and is deliberately not counted

The directory holds **four** readback records but the attempted-legs denominator
in the derived output is **15**, not 19. The missing four are
`readback-vectors.two-seeds.json`, which `derive.py` names as `READBACK2` but
never loads. That is stated here because a future reader counting legs in the
directory gets a different number than the published one, and an undocumented
gap between those two is indistinguishable from a leg being quietly dropped.

It is a **pilot run at two seeds (1337, 4242), superseded 69 seconds later** by
the three-seed sweep that is actually published (`09:08:49` vs `09:09:58`).
Nothing is hidden by the omission, and that is checked rather than assumed: all
**4 of its legs are complete** — both engines at both seeds, every one carrying
real vectors with an empty `error` — so there is no unobtained reading inside it
for the DoD #5 disclosure to owe an account of. Its vectors agree with the
published sweep on the two seeds they share, including the firefox
`canvas_pixel_hash` collision reported in the derived output.

The constant is left defined rather than deleted so the file on disk stays
traceable to the script that produced it; **it must not be added to the
denominator**, which would double-count two engine/seed legs that the published
three-seed sweep already reports.

## 5. Two defects found by this instrument, reported and NOT fixed

Fixing found defects is out of PS-185's scope; both are recorded so they are not
rediscovered:

1. **The estimator is compared against the wrong bar.** The plug-in Simpson
   index is tested against the limit bar `1/k`, which a finite sample is
   *expected* to exceed — so uniform draws are flagged `TOO_NARROW`. Android
   scored **0.2743 against a 0.2812 uniform expectation at N=24 and was still
   flagged**: an arm cannot be worse than uniform while scoring better than
   uniform predicts. Filed as **PS-191**. Now load-bearing, because PS-176
   wired this gate onto the chromium install path.
2. **The verify tier leaks ~35 engine processes per launch**, trending to OOM
   and to exactly the contentless `TargetClosedError` of PS-133. `sweep.py`
   works around it with per-chunk process groups (and a name-based sweep gated
   behind `--jobs 1`, because a global `pkill` would shoot a sibling job's
   browser and manufacture false "unreadable" cells). **Workaround, not a fix.**

## 6. How the "renders a stored summary" class was ENUMERATED (round 5)

Rounds 2, 3 and 4 each fixed the one instance they were handed, and each time a
new member of the same class was found in the next review. The class is:

> **any figure rendered from a summary the sweep wrote about ITSELF, rather
> than recounted from the raw readings.**

`result.per_arm.seeds_readable` is such a summary — it records what the run
*believed* it read, so a truncated sweep carries a full-looking count and every
consumer of that field inherits the blindness. So does
`result.per_arm.collision_probability`, `distinct_identities` and `verdict`, and
so does `readback-vectors.*.json`'s `verdicts` block one file over.

### The search was by BEHAVIOUR, not by grep

A grep is shaped by the instances you already know about, and it returns those
instances again. The enumeration was done by **mutating the records and diffing
the rendered output**:

1. Load the committed records.
2. **Destroy the raw readings** — null an arm's seeds, or empty a readback
   leg's vectors — while leaving **every stored summary block untouched**.
3. Re-render each section and diff it line by line against the unmutated
   render.
4. **Any number that does not move is not being computed from those readings.**

That search finds sites a grep cannot. Two of the four it added were invisible
to any search for a field name:

* the positive control counted `None == None` as agreement — it reads **no
  summary field at all**, and got *stronger* the more the sweep failed (a total
  launch failure in both modes scored a perfect 24 of 24);
* the firefox narrative asserted its **conclusion** as a literal, so with the
  leg lost it printed `@1337 → None, @4242 → None — **different**`.

**The search is committed, not just described.** Narrating a method leaves the
next person to rebuild it, so it ships as
`readings/ps185-2026-08-26/enumerate_summary_sites.py`. Run it after any change
to `derive.py`:

```
python3 readings/ps185-2026-08-26/enumerate_summary_sites.py
```

Every scenario should report changed lines. A scenario that reports **no
change** in a section whose readings it destroyed is a live defect of this
class, and the script exits non-zero saying so. It only reads the committed
records; every mutation is applied to an in-memory copy.

### What it found, beyond the four sites the review named

| site | what it rendered from a self-summary |
|---|---|
| the per-arm table's **percentage and distinct counts** | `result.per_arm` — the headline figure that replaces "theoretical" in PS-16 |
| the **positive control** | `None == None` counted as agreement |
| the **estimator table** | `seeds_readable` from the uniformity record |
| the **macos "has MOVED"** paragraph | `collision_probability` + `seeds_readable` |
| the **readback verdict table** | `rb["verdicts"]` — the readback run's account of itself |
| the **`CONSTANT` arm list** | the stored `verdict`, which decides which arms the section names at all |
| the **firefox webgl narrative** | `**It does not.**` / `— **different**` written in as prose |
| the **canvas split** | which seeds collide, asserted rather than derived |

The last four are **not** on the review's list; they were found by the
enumeration above. The final two matter beyond the count, because the ticket
explicitly forbids averaging the firefox and canvas results into one verdict —
and a hardcoded branch can only ever report one of them. Both now derive which
branch the readings actually support, and both report `INCONCLUSIVE` as **not a
pass** when the probe read nothing.

### The estimator recount came out broader than specified

The review asked for `N` to be recounted. Every other column on that row turned
out to be a closed-form function of the readings and the pool size, so all of
them are recomputed: `plugin_estimate`, `unbiased_estimate`,
`expected_plugin_under_uniform` and `bar_collision_probability`.
**`monte_carlo_p_value` is the one figure that genuinely cannot be** — it is a
seeded simulation, not a function of the readings — so it is read from the
record and left labelled as stored rather than silently re-derived.

The uniformity records carry **no raw readings at all**, only a `per_arm`
summary, so the recount comes from the sweep each one names in `source_record`.

### Both recounts use the PRODUCT'S OWN rules, imported

`derive.py` imports `engine_gpu_variance.collision_probability` and
`readback_vectors.verdict_for` rather than reimplementing either, for the same
reason `sweep.py` imports the module it drives: a second copy of the rule could
drift from the one that took the readings, and the article would then be derived
from a rule the product does not use. Verified against the committed records —
**all eight GPU figures and all four readback verdicts (with their per-seed
values and detail strings) reproduce exactly**, which is why replacing the
source moved no published number.

### Why no measured number moved

Every recount returns the stored value on the committed records, so
`derived-output.txt` is **byte-identical except for one intended word** in the
`:61` sentence (`24 seeds` → `24 seeds requested`, which distinguishes seeds
*requested* from seeds *obtained*). Lines 6–89 are unchanged. That is the
property that says a stale source was replaced rather than a result changed.

### The guard tests are revert-proven

Ten guard tests were added, one per site. Each was verified by **reverting its
fix in place and confirming the test FAILS**, then restoring the file
byte-identically. A guard test that still passes when you revert what it guards
is decoration, and this project's PS-11 article exists because that has happened
here before. Every mutation is driven through **in-memory records**; no
committed evidence file is ever written to.

### 6a. A gap in the splicer, found by re-splicing

`splice_patch.py` synchronised **Edit 8 only**. Edit 3 — the block labelled
*"the `derive.py` output verbatim"* — had **no mechanical re-splice path at
all**, so a change to the generator's prose broke its verbatim claim and the
only way to restore it was for a human to re-type the block. That is precisely
the re-typing the script exists to remove, and it is how a "verbatim" label
rots. The splicer now splices **both** blocks, and `--check` reports on both.

## 7. Round 6 — the class had a SECOND AXIS, and a false exemption hiding in it

Round 5 enumerated the "renders a stored summary" class **by behaviour** and
closed every member it found. It still returned a clean sweep over four live
members, because the harness mutated only one of the two things a stored
summary can be.

A stored summary block can fail in **two** ways:

| axis | mutation | catches |
|---|---|---|
| 1 | destroy the readings, keep the summary | a figure that claims to be recounted but is echoed |
| 2 | **poison the summary, keep the readings** | a figure that never consulted the readings **at all** |

Destroying readings **cannot** detect a figure that never read them, so axis 1
is structurally blind to axis 2's members and reported exit 0 over them. The
enumerator now runs both, and `--axis` selects one.

### 7a. The exemption that justified the last member was FALSE

`_uniformity_stats` documented `monte_carlo_p_value` as *"the ONE figure that
genuinely cannot be recomputed — it is a seeded simulation, not a function of
the readings"*, and said recomputing it would require a re-measurement this
round was forbidden from doing.

**That sentence was untrue**, and it is corrected here rather than quietly
deleted, because a confident false provenance claim inside a provenance
artifact is the same defect as round 1's untrue *"verbatim"* label. Both
uniformity records store the `monte_carlo_seed` (`20260826`) and
`monte_carlo_trials` (`200000`) they were run with, which makes the simulation a
**deterministic function of the readings plus two recorded parameters**.
Feeding them back through the instrument's own `analyse()` reproduces every
stored p-value exactly, on all four arms in both modes — no browser, no sweep,
no re-measurement:

```
windows 1.000000   macos 0.308370   linux 0.163825   android 0.579675
```

It is the column that decides **artefact vs genuine**, so echoing it was not
cosmetic: under an android truncation it moves `0.579675 → 0.977530`, and left
frozen it rendered three recounted columns beside a stale p-value **inside one
table row** — the self-contradicting row this whole class exists to prevent,
one table below where round 4 blocked on exactly that shape.

⚠️ **Iterate the arms in the instrument's own `sorted()` order — call
`analyse()`, do not reimplement its loop.** All four arms are drawn from ONE
shared `random.Random(seed)`, so each arm's p-value depends on how much of the
stream the previous arms consumed. Drawing them in this file's `ARMS` order
lands macos at `0.308535` instead of `0.308370`.

### 7b. `pool_size` — a site on nobody's list

The axis-2 walk is **generic**: it poisons every scalar field of every stored
summary block rather than a list of known field names, because a list can only
re-find what someone already named. That returned a site no review had cited
and no grep for `monte_carlo` could reach.

Round 5 recomputed the estimator **formulae** but fed them a `k` read out of the
stored block, leaving `E[plug-in | uniform]` and the `1/k` bar half-derived.
Poisoning the stored `pool_size` moved **the single sentence the artefact
finding rests on** — *"android scored 0.2743, BELOW the 0.2812 a uniform draw is
expected to score"* — while every other column sat still. `k` now comes from
`engine_gpu_variance.fallback_pool_size`, the pool itself.

### 7c. `module_verdict` — recounted, and why that is not a contradiction

The review left this to the worker's judgement, noting the column exists to
report *what the gate said* so the estimator can be contrasted against it.

**Chosen: recount it via `engine_gpu_variance.classify`.** That keeps the
column's meaning exactly — asking the gate afresh still reports the gate's
verdict — while removing a transcription of a transcription (the uniformity
record's copy of the sweep's copy). Verified to reproduce all eight stored
verdicts exactly.

One subtlety worth recording: `analyse()` carries a `module_verdict` of its own,
but reads it from `record["result"]["per_arm"][arm]["verdict"]` — the sweep's
stored summary. Taking it from there would have left the column echoed *by a
different route* while looking recounted, so the gate is asked directly and the
override is applied **after** the `analyse()` result is merged.

### 7d. The exemption that remains is ASSERTED, not waived

Axis 2's rule is **not** a blanket *"no rendered line may depend on a stored
field"*, because one site depends on one **on purpose**: `gpu_completeness`
cross-checks its own recount against the sweep's stored `seeds_readable` and
**discloses** any disagreement, so a record whose summary and readings tell
different stories says so out loud. A blanket rule would have deleted that
disclosure.

So that field is exempt — and the exemption is **tested**. For it the harness
asserts the disclosure actually **fires**, and that what moves is prose only:
**no `|` table row may move**, since a moving row is a published figure taken
from a summary. An exemption nobody checks is how a defect hides behind the word
"intentional".

The exemption is scoped by **(record, field)**, not by bare field name. The
first cut keyed on the name `seeds_readable` alone and over-matched the
uniformity records' own field of that name — a different quantity that nothing
cross-checks and that is now fully recounted — which would have waived any
future defect in it for no better reason than a shared spelling.

### 7e. Verification

* `derive.py --output` reproduces `derived-output.txt` **byte-identical** — no
  published figure moved, the same property every round before this one held.
* Both axes exit 0: axis 1's six scenarios all move the render; axis 2 walks
  **215 stored fields** and none moves it, bar the asserted disclosure.
* **31 targeted tests pass.** Six new guards, each **revert-proven**: the fix was
  reverted in place, the guard confirmed to FAIL, then the file restored
  byte-identically. Every mutation is driven through in-memory records; no
  committed evidence file is ever written to.

⚠️ **Reverting an INPUT field needs a faithful revert.** `pool_size` is not
rendered directly — it *feeds* two columns — so overwriting the output field
alone left the render unchanged and made a genuine guard look like decoration.
Restoring `k` as the **input** to the expectation column and the bar, which is
what the pre-round-6 code actually did, fails the guard correctly. A naive
revert can slander a real guard.

### 7f. The recount is not free

Recomputing rather than echoing costs a 200k-trial simulation per record, taking
a `derive.py` run from under a second to ~17 s. `_analysed` memoises it **on the
readings themselves**, not on the record's identity, so a caller that truncates
an arm and re-renders correctly MISSES the cache and gets the recomputed value —
a memo keyed on identity would have reintroduced exactly the staleness the
recount removes. The test module memoises the subprocess run and the module
import for the same reason, which is what keeps the suite at ~3 minutes instead
of ~7.
