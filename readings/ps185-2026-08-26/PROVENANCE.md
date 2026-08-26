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
