# PS-345 — patch 015 `measureText`: the defect, measured on the PUBLISHED 152 engine

**Date:** 2026-09-07 · **Branch:** `feature/PS-345-fix-015-measuretext-multiplier` off `main` `db6d528`
**Author:** worker seat · **Layer:** persona's JS masking layer **OFF** throughout.

> **Read this first — what is proven and what is not.**
> The **before-arm is MEASURED** on the shipped 152 Linux engine, executed, against
> an exact-version stock control. The **after-arm is NOT**: no engine carrying the
> corrected patch was built, because the build runner is unreachable. This report
> therefore delivers the ticket's stated fallback — *"produce the measurement of
> the current defect and STOP"* — plus a patch edit whose runtime proof is
> **pending**. The fix is not claimed to work. It is claimed to apply cleanly and
> to be the right *shape*; those are different claims and are kept separate below.

---

## 1. Which binaries — say this before any number

The ticket warned against quoting PS-301's figures, which were taken on a **144**
build. This is not that reading. The engine measured here is the **published
artifact this ticket is about**.

| | observed arm | control arm |
|---|---|---|
| binary | `personium-152.0.7977.75-linux-x86_64.AppImage` | stock **Chrome for Testing 152.0.7977.75** |
| provenance | GitHub release `personium-152.0.7977.75` (prerelease, published 2026-09-06) | `chrome-for-testing-public`, `linux64` |
| sha256 (asset) | `6ddb7bbea0a2063b7a3618e6b5d4ebc96301cd80f8b0d6eae486af46a30bb4c3` | — |
| sha256 (`chrome`) | `79c3906194a621ae6cb39a6f1bdec8751782f2b871b9cd016d9a5de5b95c4494` | `3c84cfdbadd0b5b9d1943568b67faea54cf818d20ca32a337fd8a68a0d61ac76` |
| `--version` | `Chromium 152.0.7977.75` | `Google Chrome for Testing 152.0.7977.75` |

**The control is an exact version match**, so no part of the difference below is
attributable to a version delta. Both were run on the same host in the same hour,
headless, masking layer OFF — the only state in which a difference from stock is
attributable to the **engine** rather than to our JS.

---

## 2. The measurement

Four strings at `16px sans-serif`, all twelve `TextMetrics` fields, per seed.

| seed | ratio patched/stock | spread across 4 strings | negative widths |
|---|---|---|---|
| `24601` | **−5.759808038305448e−07** | **0.0** | 4/4 |
| `5150` | **−3.873257860279004e−06** | **0.0** | 4/4 |
| `777` | **+1.0110584148231562e−06** | **0.0** | **0/4** |
| *(no seed)* | `1.0` | `0.0` | 0/4 — patch correctly stands down |

Concretely, seed 24601: `"The quick brown fox jumps"` measures **215.3671875 px**
in stock and **−0.00012404736577497366 px** in the shipped engine.

### 2.1 The ratio is bit-identical across four different strings

Spread is **exactly `0.0`** — not "small", zero, in float64, across strings of
1, 5, 13 and 25 characters. A perturbation would leave a different ratio per
string. One constant factor applied to everything is a **multiply**, and this is
that signature measured rather than argued.

### 2.2 All twelve fields collapse, not just `width`

Seed 24601, `"The quick brown fox jumps"`:

| field | stock | shipped engine |
|---|---|---|
| `width` | 215.3671875 | −0.00012404736577497366 |
| `actualBoundingBoxRight` | 215.03125 | −0.00012385387222368685 |
| `fontBoundingBoxAscent` | 15 | −8.639712057458172e−06 |
| `fontBoundingBoxDescent` | 4 | −2.3039232153221793e−06 |
| `actualBoundingBoxAscent` | 12 | −6.911769645966538e−06 |
| `hangingBaseline` | 12 | −6.911769645966538e−06 |
| `ideographicBaseline` | −4 | +2.3039232153221793e−06 |

This confirms **at runtime** the researcher's source reading that `Shuffle()`
carries thirteen `*=` assignments and scales every metric `measureText` returns.

### 2.3 ⚠️ The headline finding: "negative width" UNDERSTATES the bug

**Seed 777 returns POSITIVE widths and is just as broken.** `"A"` measures
`10.945 px` in stock and `+1.1066e−05 px` in the shipped engine — positive,
spec-legal, and off by seven orders of magnitude.

Roughly **half of all seeds** land here, because `norm_x` is centred on 0 and its
sign is a coin flip. Both prior readings drew two seeds (24601, 5150) and both
happened to be negative, so this class had not been seen before. The consequence
for anyone writing a detector or a guard:

> **A rule keyed on `width < 0` alone passes seed 777 and reports the engine
> healthy.**

The real invariant is that the factor is centred on **0** where the consumer needs
one centred on **1**; the sign is incidental.

**⚠️ That is a statement about the RULE, not about our guard — and the difference
matters, so it is stated plainly rather than left to be inferred.**
`scripts/ps301_measuretext_repro.py` **already catches seed 777** and always did.
It does not stop at the negative-width test: its second branch condemns a ratio
that is *constant across strings AND implausible*, which is exactly the
positive-but-collapsed shape, and its own comment says so ("one constant factor
applied to every string is a MULTIPLY"). Run against this reading's own
seed-777 file it exits **1, DEFECT**:

```
$ python3 scripts/ps301_measuretext_repro.py \
    --observed readings/ps345-2026-09-07/artifacts/patched-777.json \
    --stock    readings/ps345-2026-09-07/artifacts/stock.json
DEFECT: observed/stock ratio is CONSTANT across 4 strings (spread 0.000e+00)
        at 1.011058e-06 — a multiply by an offset-shaped value, not a perturbation
exit 1  (DEFECT)
```

So this reading found **no blind spot in a committed instrument**. An earlier
draft of this report claimed it had; that claim was false and is retracted here
rather than softened. What seed 777 actually contributes is a sharper statement
of *why* the guard is right — it condemns on the factor's centre, and the
negative-width branch it happens to hit first on other seeds is the weaker of its
two reasons. That is worth having, and it is a smaller claim than the one it
replaces.

What was therefore added to the guard is the **opposite of a fix**: seed 777 is
pinned as a named `--self-test` case, recording behaviour already present so the
strong branch cannot be dropped as redundant by a later reader who sees only the
negative-width rule. See §4.

### 2.4 This is louder than a leak

A `measureText().width` of ~0 — or negative — on a non-empty string is not a
plausible machine. No real browser produces it. The patch exists to make the
engine *less* distinguishable and instead installs a **blaring, trivially
detectable automation tell** in every published 152 engine on all three platforms.

---

## 3. The fix

One line, in `engine/patches/fingerprint/015-canvas-measure-text.patch`:

```diff
-    // 使用 0.00001 作为因子 (相当于原来的 1/100000.0)
-    double noise_x = norm_x * 0.00001;
+    // TextMetrics::Shuffle() MULTIPLIES every metric by this value, so it must
+    // be a factor centred on 1 (like the upstream GetNoiseFactorX() it replaces),
+    // NOT the offset-shaped value patch 014 computes for rect.Offset(). ...
+    double noise_x = 1.0 + norm_x * 0.00001;
```

**Why not restore `GetNoiseFactorX()`** — the ticket's stated trap, and it is a
real one. Patch `014` redefines that field in the `Document::Document` constructor
from the multiplier form `1 + (RandDouble() − 0.5) * 0.000003` to the **offset**
form `norm_x * 0.002`, and moves its *own* consumers to `rect.Offset()` /
`quad.Offset()`. After `014` the field is an offset and is **not a legal argument
to a multiplier**. Restoring it would reproduce this very bug with a different
constant. The fix keeps 015's own seeded derivation — which is what makes the
noise deterministic per profile rather than per process — and corrects only its
*centre*.

Resulting factor: **1 ± 5e−6** (±0.0005%), seed-varying, matching the magnitude
of the upstream noise factor it replaces.

### 3.1 What is verified about the fix

- **`ps299_rebase_probe.py --tag 152.0.7977.75-1` → 81/81 hunks, 0 rejects, fuzz=0.**
  The hunk header line count was corrected (`+21` → `+25`) to match the new body.
- **The reconstructed source was read back** out of the probe tree, not merely
  reported as applied — the hunk lands inside `measureText()` with both `Shuffle()`
  call sites receiving the corrected `noise_x`. *"A patch that applies cleanly is
  not a patch that is correct"* is the ticket's warning and this is the minimum
  answer to it.
- **Projected post-fix values are exactly derivable from the measurement**, because
  the fix is `1 + buggy` and the measured ratio *is* `buggy`:

  | seed | corrected factor | `"hello"` 38.6640625 → | delta |
  |---|---|---|---|
  | 24601 | 0.9999994240191962 | 38.6640402302422 | −2.23e−05 px |
  | 5150 | 0.9999961267421397 | 38.66391274411601 | −1.50e−04 px |
  | 777 | 1.0000010110584148 | 38.66410159162574 | +3.91e−05 px |

  Plausible, seed-varying, non-degenerate — the ticket's territory. **⚠️ This is
  arithmetic on a measured input, NOT the runtime after-arm.** It assumes the
  edited patch compiles and that the emitted code does what the source says.
  Neither has been executed. It is offered as a projection and must not be quoted
  as a measurement.

### 3.2 What is NOT verified — the honest gap

**No engine carrying the corrected patch was built or run.** The falsification the
ticket declares non-waivable is therefore **incomplete**: the before-arm is
delivered, the after-arm is not.

The build was attempted, not assumed away. `engine-trial-build.yml` was dispatched
on this branch with the cheapest possible input (`trees=unmodified` — the
instrument check alone, not a multi-hour compile), run `34107160642`. It sat
**queued for 8+ minutes with `runner_name: null`** and never picked up — the
"offline runner queues forever" state the workflow's own header documents. The
three most recent dispatches before it all **failed** on `persona-wsl-builder`
answering the `persona-build` label as **Windows/WSL**, which the workflow's own
instrument check correctly refuses (`this runner is $(uname -s), not Linux`).
There is no docker in this container either, so `scripts/docker-build.sh` cannot
run locally.

Per the ticket's explicit instruction — *"If a build cannot be arranged, produce
the measurement of the current defect and STOP"* — I stopped here rather than
shipping an unverified patch edit dressed as a completed fix.

---

## 4. Reproducing

The verdict runs through **`scripts/ps301_measuretext_repro.py`** — the project's
one authoritative measureText guard. This reading adds an input reader for its
JSON shape (`--observed`/`--stock`, four strings per file) and a self-test case;
it does **not** add a second guard. A `ps345_verdict.py` was written during this
work and **deliberately dropped**: it restated the same predicates and was
behaviourally identical (0 disagreements across 15 factor scenarios, non-constant
and mixed-sign inputs included, plus all four readings below), so committing it
would have left two unreferenced, equivalent instruments and no way for a reader
to tell which was authoritative.

```bash
# the guard, and proof it can go red — no browser needed
python3 scripts/ps301_measuretext_repro.py --self-test

# the verdict on each committed reading (run from the repo root)
R=readings/ps345-2026-09-07/artifacts
python3 scripts/ps301_measuretext_repro.py --observed $R/patched-24601.json \
                                           --stock $R/stock.json   # exit 1 (DEFECT, negative)
python3 scripts/ps301_measuretext_repro.py --observed $R/patched-777.json \
                                           --stock $R/stock.json   # exit 1 (DEFECT, POSITIVE widths)
python3 scripts/ps301_measuretext_repro.py --observed $R/patched-noseed.json \
                                           --stock $R/stock.json   # exit 0 (patch stands down)

# re-take the readings (downloads the published AppImage + stock CFT 152)
python3 $R/measure.py <chrome> <label> [--fingerprint=SEED]
```

The guard **exits non-zero when the defect is present**, so it is RED today on
the seeded arms and turns GREEN the day an engine carrying the corrected patch is
measured. **Its redness is the finding, not a broken script.** Its `--self-test`
reaches all three verdicts on real measured inputs — including a
*healthy-but-constant* arm, which is the case that proves the constant-ratio
signature alone does not condemn (a correct `Shuffle()` factor is constant across
strings too), and the seed-777 arm described in §2.3.

`tests/test_ps345_measuretext_guard.py` pins all of the above against the
committed readings, so the behaviour cannot regress silently.

---

## 5. What remains

1. **Build an engine carrying the corrected patch and re-run §4.** The guard is
   already written and already red; the after-arm is one build away. This needs a
   working Linux x86_64 + docker runner answering `persona-build`.
2. **The runner registration is itself a defect** — a Windows/WSL machine and
   previously a macOS one have both answered a label whose workflow can only run
   on Linux x86_64. That is out of scope here and is not fixed by this ticket.
3. **The shipped 152 engines remain defective on all three platforms** until a
   rebuilt engine ships. This reading does not change what users have.
4. **The release manifest now disagrees with the tree, deliberately.**
   `engine/releases/personium-152.0.7977.75.json` records
   `015-canvas-measure-text.patch` at sha256 `773a27ce…`; on this branch the file
   hashes to `10e59276…`. **That is correct and must NOT be "fixed".** The
   manifest records what was *shipped*, and the shipped engine genuinely carries
   the pre-fix patch — editing it to match the tree would make the record claim a
   build that does not exist.

   It is noted here because the manifest's stated purpose is to let a future
   reader tell an unchanged patch set from a drifted one, and it is now drifted
   with the reason living only in this paragraph. Nothing checks these per-file
   digests against the tree (`ps343_verify_release_provenance.py` does not), so
   no test catches it either way. **The digest is expected to stay stale until a
   rebuilt engine ships**, at which point that release's manifest records the
   corrected patch and the divergence closes on its own.
