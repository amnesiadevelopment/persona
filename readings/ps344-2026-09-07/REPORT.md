# PS-344 — the PUBLISHED engine, LAUNCHED

**Date:** 2026-09-07 · **Tree:** `feature/ps344-verify-published-engine` off `main` `b2ab64a`
**Author:** worker seat · **Platform measured: LINUX x86_64 ONLY.** macOS and Windows are
**UNMEASURED** here and nothing below may be read as a statement about them.

**The engine measured — say this before any number:**

| | |
|---|---|
| **binary** | the **PUBLISHED** `personium-152.0.7977.75-linux-x86_64.AppImage`, **downloaded from the release** |
| **provenance** | `gh release download personium-152.0.7977.75` — the asset `_asset_matches()` selects on Linux |
| **sha256** | `6ddb7bbea0a2063b7a3618e6b5d4ebc96301cd80f8b0d6eae486af46a30bb4c3` (= the release API `digest` the updater verifies against) |
| **size** | 202,193,400 bytes |
| **`--version`** | **`Chromium 152.0.7977.75`** |
| **control** | **stock** Google Chrome for Testing **152.0.7977.75** — *version-matched*, sha256 `3c84cfdb…ac76` |
| **host** | `Linux bbd58a129361 6.8.0-138-generic x86_64`, container, **no GPU** (host GL is SwiftShader) |
| **seeds** | `24601`, `5150` — two, because one cannot tell "spoofed" from "constant" |

> **⛔ NOT ONE NUMBER IS CARRIED ACROSS FROM PS-301.** Those are readings of a
> *different binary* (self-built 144.0.7559.132). Every figure here was measured
> on 2026-09-07 on the downloaded 152 artifact. A ratio quoted from another
> ticket's JSON is not a measurement of this one.

---

## The answer

**The fingerprint patches are PRESENT AND FUNCTIONING in the binary an operator
downloads.** `scripts/ps344_verdict.py` exits **0** on the product arm: all 10
required layer-OFF signals observed, both negative controls held still.

**No STOP condition fired.** The ticket's ⛔ instruction was to halt and comment
if the published engine read like stock on any vector. It does not read like
stock on any vector this contract covers — the two arms disagree on GPU identity
in all seven realms, on `hardwareConcurrency`, on timezone, on `webdriver`, on
canvas and WebGL readback, and on client rects.

**And the instrument is known to be able to say the opposite.** The
falsification arm — the *stock control run a second time and labelled as the
product* — exits **1** and reports **10 of 10** required signals absent. A
verification that has only ever been seen to pass is not known to work; this one
has been seen to fail, on a real binary, through the same code path.

---

## What this ticket closed, precisely

Three checks existed and none of them read this object:

| check | reads | proves |
|---|---|---|
| `ps307_verify_patches_in_tree.sh` | the **source tree** | the 16 patches are in the tree about to compile |
| PS-301 | a **locally-built** 144 binary | *that* build masks |
| `engine-gpu-variance.yml` | the **published** release | GPU varies by seed — **one vector** |
| PS-343 | the **published** asset | digest + switch **strings** in machine code, and two spot executions |
| **PS-344 (this)** | **the published binary, EXECUTED** | **what a page observes, every vector, against a control** |

PS-343's contribution is real and is not restated as this one: a switch *string*
present in a binary is not a patch that *runs*, and two spot reads are not a
per-vector table across seven realms and two seeds.

---

## The per-vector table

Full table: `artifacts/verdicts.txt` (44 rows, computed by `ps301_compare.py` —
not typed). The layer-**OFF** rows are the engine-attributable ones; with
persona's JS layer ON, a difference could be the engine *or* the extension.

### Q2 — GPU identity, the headline

| seed | published engine, all 7 realms | stock control, all 7 realms |
|---|---|---|
| 24601 | `ANGLE (NVIDIA Corporation, NVIDIA GeForce RTX 4070/PCIe/SSE2, OpenGL 4.5.0)` | `ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)` |
| 5150 | `ANGLE (NVIDIA Corporation, NVIDIA GeForce RTX 3080 Laptop GPU/PCIe/SSE2, OpenGL 4.5.0)` | *(identical to above)* |

Vendor reads `Google Inc. (NVIDIA Corporation)` where stock reads
`Google Inc. (Google)`. **One value per seed across all seven realms** — page,
same-origin iframe, about:blank iframe, srcdoc iframe, blob worker, worker in an
iframe, and the depth-2 nested worker — on a container with **no GPU at all**.
The renderer *changes between the two seeds*, so it is derived, not a constant
fake.

### Q1 — switches

| surface | published | stock, same flags |
|---|---|---|
| `hardwareConcurrency` | **18** (seed 24601) / **12** (seed 5150), all 7 realms | **8** (the host's real count) |
| `Intl` timeZone | `America/Chicago`, all 7 realms | `UTC` (the host clock) |
| `Date` offset | moves with it | host offset |
| `navigator.webdriver` | `false` | **`true`** (stock reports it under CDP) |
| `navigator.userAgent` | *identical to stock* | — |

The user-agent row is stated deliberately rather than omitted: the harness
declares `--fingerprint-platform=linux` on a Linux host, so there is nothing for
patch 002 to change. The direct reproduction (§ below) drives the switch across
all three arms and shows it *does* work.

### Q3 — canvas, WebGL readback, client rects

| vector | patch | published (seed 24601 → 5150) | stock |
|---|---|---|---|
| `getImageData` digest | 012 | `234950365` → `3740682202` | `507247862` |
| WebGL `readPixels` digest | 016 | `3803232679` → `1153635684` | `1412431872` |
| `toDataURL` digest (DOM realms) | 013 | `3391877372` (moves per seed) | `294442885` |
| eligible rect `x` | 014 | `7.999754…` → `7.999578…` | `8` |

### The negative controls, which are not decoration

| vector | published | stock | why it matters |
|---|---|---|---|
| rect `width` | `135.375` | `135.375` | patch 014 calls `Offset()`, **not** `Scale()` — it moves a rect and never resizes it |
| `client_rects_exempt.x` | `13.296875` | `13.296875` | patch 014 deliberately **exempts** `position:absolute` with deterministic top+left |

A run where *everything* differs is a broken instrument, not a well-patched
engine. These two rows are what tells those apart, and both held still.

### Q4 — the masking layer still installs on this build

All ten modules load on the published engine:
`audio, canvas_ctx, device, gpu, locale, measuretext, native, stealth, voice, webgl`.
The layer-OFF cells recorded `layer_installed: []`, which is what makes every
attribution above an attribution to the **engine**.

---

## Two findings that are NOT clean, reported rather than buried

Both are **PS-301 defects reproduced in the SHIPPED binary** — which is itself
the point of the ticket, since until now they were only known to exist in a
trial build. Neither is a new defect and neither is fixed here (this is a
measure-not-fix ticket).

### 1. patch 015 `measureText` is defective in the shipped engine

Measured through the harness (page realm, layer OFF):

| seed | published width | stock width |
|---|---|---|
| 24601 | **`-9.913e-05`** | `172.1083984375` |
| 5150 | **`-6.666e-04`** | `172.1083984375` |

A **negative** text width is impossible per spec. The direct, non-CDP
reproduction (`artifacts/repro-transcript.txt`) shows the signature exactly:
every observed value is the stock value multiplied by one constant factor
`k = -5.7598e-07`, spread across strings `0.0`, 4/4 strings, both seeds. With no
`--fingerprint` the patch stands down and widths are the sane stock values, so
the defect is the patch's, not the browser's. `ps301_measuretext_repro.py` exits
**1 (DEFECT PRESENT)** against the published binary.

It is **DOM-only**: the worker realms read `172.1083984375`, identical to stock —
so `measureText` is *unspoofed* in a worker and *broken* in the DOM. Neither
state is the product's contract.

### 2. Three declared switches are accepted and ignored

| switch | asked for | observed |
|---|---|---|
| `--fingerprint-screen-width=2560` | 2560 | **800** |
| `--fingerprint-screen-height=1440` | 1440 | **600** |
| `--fingerprint-device-scale-factor=2` | 2 | **1** |
| `--fingerprint-hardware-concurrency=6` *(live, for contrast)* | 6 | **6** |

The first three are declared by patch 000, forwarded on the command line, and
never read. PS-343 recorded all eleven switch *strings* as present in the
shipped machine code — this is the measurement showing that presence in the
binary and effect on the page are different claims, for three of them.

### 3. patch 003 audio is partial, and the asymmetry is stated

At seed **5150** the product's audio digest differs from stock
(`sum 124.03659725…` vs `124.04347776…`). At seed **24601** it is
**bit-identical** to stock. Both are asserted in `verify_claims.py` so neither
can quietly change — a report mentioning only the differing seed would be
selecting its evidence. Audio is therefore *not* claimed as a working vector and
is deliberately absent from the verdict script's required set.

---

## Falsification — the non-waivable clause

> *"The harness must be shown able to report a failure: run it against the stock
> control as if it were the product and confirm it reports the patches absent."*

Done, as a **third measured arm**, not a thought experiment:
`readings-stock-as-product.json` is the stock control launched a second time and
labelled as the product. Its verdict (`artifacts/verdict-falsification.txt`):

```
VERDICT: PATCHES ABSENT — 10 of 10 required signals were not observed
exit 1
```

Its comparison table (`artifacts/verdicts-falsification.txt`) reads `no` on every
differ column — that is what "the patches are not in this binary" looks like
through this exact instrument.

### And the guard was found to have a real blind spot, which was fixed

The falsification above passes trivially for a *wholly* stock binary. The harder
question is a **partial** leak — one realm, one seed — which is the shape of the
known chromium/linux realm leak this project has chased. So the verifier was
sabotaged: one realm's GPU string on one seed, rewritten to the stock SwiftShader
value.

**It passed.** `ps301_compare` scores a realm's `differs_from_control` as
`any(...)` across seeds, so a realm that leaks on seed A and spoofs on seed B is
counted as differing and `all_realms_differ` stays true. The judge was leaning on
an aggregate that could not see the defect it most needs to see.

`_every_realm_and_seed_differs()` in `ps344_verdict.py` now recomputes the
stricter reading from the per-seed lists the comparison already carries. On the
same sabotage it goes red and names the leak:

```
!! webgl.unmasked_renderer  differs=True  all_realms=False  seed_derived=True
VERDICT: PATCHES ABSENT — 1 of 10 required signals were not observed:
  · webgl.unmasked_renderer: does not differ in every realm on every seed —
    worker_nested[seed#1] reads stock: "ANGLE (Google, Vulkan 1.3.0 (SwiftShader…"
```

`ps301_compare.py` was deliberately **not** edited: it is the shared instrument,
PS-301's committed table was rendered by it, and redefining its columns would
silently restate an older ticket's published findings. The stricter reading
belongs to the judge, not to the renderer.

So the guard is demonstrated in **three** states, on real measured inputs:

| input | verdict | exit |
|---|---|---|
| the published engine | PATCHES LIVE | **0** |
| stock, labelled as the product | PATCHES ABSENT (10/10) | **1** |
| the published engine with ONE realm on ONE seed sabotaged | PATCHES ABSENT (1/10) | **1** |

`verify_claims.py` was falsified the same way and correctly failed (`74 passed,
1 FAIL`), so its 75-check pass is a real signal rather than a vacuous one.

---

## Reproducing

```bash
# 1. the artifact a user downloads
gh release download personium-152.0.7977.75 --repo amnesiadevelopment/persona \
   --pattern '*linux-x86_64.AppImage' --dir /tmp/ps344
sha256sum /tmp/ps344/personium-152.0.7977.75-linux-x86_64.AppImage
#   → 6ddb7bbea0a2063b7a3618e6b5d4ebc96301cd80f8b0d6eae486af46a30bb4c3

# 2. the VERSION-MATCHED stock control
curl -sSL -o /tmp/ps344/cft.zip \
  https://storage.googleapis.com/chrome-for-testing-public/152.0.7977.75/linux64/chrome-linux64.zip
unzip -q -d /tmp/ps344 /tmp/ps344/cft.zip

# 3. stage BOTH under the name the resolver expects — a SYMLINK, so no artifact
#    is renamed, no filename function is edited, nothing lands on PATH
mkdir -p /tmp/ps344/engine-published /tmp/ps344/engine-stock
ln -sf /tmp/ps344/personium-152.0.7977.75-linux-x86_64.AppImage \
       /tmp/ps344/engine-published/fpchrome.AppImage
ln -sf /tmp/ps344/chrome-linux64/chrome /tmp/ps344/engine-stock/fpchrome.AppImage

# 4. three arms, one server, same host, same hour
APPIMAGE_EXTRACT_AND_RUN=1 python3 -m scripts.ps344_launch_published \
  --published-dir /tmp/ps344/engine-published \
  --stock-dir     /tmp/ps344/engine-stock \
  --published-label 'PUBLISHED personium-152.0.7977.75 … (THE PRODUCT)' \
  --stock-label     'STOCK Chrome for Testing 152.0.7977.75 (CONTROL)' \
  -o readings/ps344-2026-09-07/artifacts

# 5. the table, then the VERDICT (which has a direction and can go red)
python3 -m scripts.ps301_compare readings/ps344-2026-09-07/artifacts \
  --product readings-published-152.json --control readings-stock-cft-152.json
python3 scripts/ps344_verdict.py --dir readings/ps344-2026-09-07/artifacts
#   → PATCHES LIVE, exit 0
python3 scripts/ps344_verdict.py --dir readings/ps344-2026-09-07/artifacts \
  --product readings-stock-as-product.json --expect absent
#   → PATCHES ABSENT, --expect met, exit 0

# 6. the direct, non-CDP reproductions
bash readings/ps301-2026-09-05/artifacts/ps301_repro.sh \
  artifacts/pub-wrapper.sh /tmp/ps344/chrome-linux64/chrome
#   → exit 1: the patch-015 defect, in the SHIPPED binary

# 7. the report still describes these artifacts
python3 readings/ps344-2026-09-07/artifacts/verify_claims.py   # 75 checks
```

### The guard the scope guidance names was respected

`src/services/verify/chromium_tier.py:_engine_binary()` refuses a PATH fallback
by design. It was **not edited**; `fingerprint_chromium_filename()` was **not
edited**; no artifact was renamed; nothing was dropped on PATH. Both arms are
staged as a directory holding a **symlink** under the expected name, with
`PERSONA_ENGINE_DIR` pointed at it — the documented override, and the same
mechanism PS-301 used. The recorded `argv` confirms what was launched:
`/tmp/ps344/engine-published/fpchrome.AppImage --appimage-extract-and-run …`.

`artifacts/pub-wrapper.sh` exists for step 6 only and adds exactly one runtime
flag (`--appimage-extract-and-run`, which the AppImage runtime consumes and which
must lead — this host has no FUSE). The **harness** runs, which produce every
verdict, went through the unmodified resolver, which emits that flag itself.

---

## Artifacts

| file | what it is |
|---|---|
| `readings-published-152.json` | the product arm — 4 cells x 7 realms, raw |
| `readings-stock-cft-152.json` | the version-matched stock control |
| `readings-stock-as-product.json` | the falsification arm |
| `verdicts.txt` / `verdicts.json` / `side-by-side.json` | the 44-row per-vector table, computed |
| `verdicts-falsification.txt` | the same table for the falsification arm |
| `verdict-product.txt` / `verdict-falsification.txt` | the deciding verdict, both directions |
| `repro-transcript.txt` | the direct, non-CDP reproductions (exit 1 = patch 015) |
| `verify_claims.py` | 75 checks re-deriving every figure above; falsified |
| `ps344_launch_published.py`, `ps344_verdict.py`, `pub-wrapper.sh` | committed copies of the tooling |

---

## What this does NOT establish

Stated plainly, because a bounded claim is the only kind worth making:

- **macOS and Windows are UNMEASURED.** No macOS or Windows host was available;
  an arm64 dmg cannot run on this x86_64 Linux box. PS-343 additionally recorded
  that the macOS asset tagged `.75` **contains `152.0.7977.64`** — that
  discrepancy is untouched here and remains open.
- **This is not a build attestation.** It shows the shipped binary *behaves* as a
  patched engine. It does not show it was built from this exact tree; nothing in
  this repository builds these assets (see PS-343's `no-build-attestation`).
- **This is not a live-checker run.** The page is served from `127.0.0.1`; no
  third party was contacted and no operator address is in the picture. It
  measures the *engine*, not a real detector's verdict.
- **Headless was used only in §6**, for single-vector arithmetic reproductions.
  Every verdict comes from the headed harness.
- **The masking layer is not evaluated here.** Layer-ON rows exist in the data
  but every attribution above is layer-OFF, on purpose.
