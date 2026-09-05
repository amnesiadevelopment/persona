# PS-301 — persona's self-built engine, LAUNCHED and READ

**Observed:** 2026-09-05, worker container (Debian 13, no GPU)
**Engine measured:** Chromium **144.0.7559.132** / ungoogled revision **1**, with persona's 16 fingerprint patches
**Control:** the **same** ungoogled release tag, **unpatched**
**Venue:** loopback `127.0.0.1` — no third party contacted, no exit in the picture
**Instrument:** `scripts/ps301_engine_launch_read.py`, driving `verify/chromium_tier.ChromiumSession`
**Raw record:** `engine-launch.json` · **Rendered:** `engine-launch-summary.txt`

---

## The one-sentence answer

**The engine we built runs, and it masks — the fingerprint switches are honoured, the GPU identity is spoofed in all six realms read (including the realm the known chromium/linux leak lives in), and canvas/WebGL readback varies with the seed — but one patch is broken in a way that is worse than not masking at all: `measureText` returns metrics ~2.2e-06× their true size, and persona's JS layer is what hides it.**

---

## ⚠️ WHICH BINARY — read this before quoting any number

This measures **144.0.7559.132**. It is **not** a reading of the 152 engines, and no figure here may be presented as one.

The ticket's own guidance sanctions this explicitly (*"if that rebase is slow, measure the 144 binary we ALREADY HAVE first and on its own"*), and here it was not a preference but the only option:

- The owner's three 152 artifacts live on the **owner's own hosts** (`/home/builder/personium-152-patched-local`, `C:\personium-152-win`, `~/personium-152-mac-Chromium.app`). They are **not reachable** from a worker container.
- No CI artifact carries a **patched 152 Linux** binary. The full artifact history of the repository contains exactly **one** `ps218-patched-binary-*` artifact, and it is the **144** one (run `33748889046`, 456 MB, retained to 2026-09-10). The only 152 binary artifacts are **unmodified** (control) trees.

So: **144 is what could be executed, so 144 is what was executed, and every reading is tagged 144.** Extending this to 152 needs a run on a host that has that binary.

**One finding does carry across, and it is the important one** — see §Q3: the broken patch file is **byte-identical at HEAD** after the PS-299 rebase, so those two lines are in the 152 engines too. That is a claim about the *patch text*, verified by `git`, and it is stated separately from the *measurements*, which are 144-only.

---

## How the engine was reached, and why the reading is attributable

**The trap this ticket exists for.** A stored QA memory records it exactly: *"SIZE + SHA256 ON A BUILT ARTIFACT ATTEST TO PRESENCE, NOT TO FUNCTION. Three seats verified a Chromium binary by hash and none of them ever executed it."* Every figure below is read out of a **live page in a launched engine**. No `strings` count is reported here as a behaviour.

**⭐ The control is the whole design.** A reading from our engine alone cannot separate *"our patch did this"* from *"upstream already did this"* from *"the harness did this"* — three causes, one identical-looking green. So every cell was read **twice**, in two engines differing in exactly one way:

| | binary | sha256 (first 16) | `fingerprint-*` switch strings |
|---|---|---|---|
| **patched** | persona's self-built `chrome` | `3b403afbdd6e6847` | 10 |
| **control** | upstream's own `chrome`, same tag | `ac98a0973048d1f0` | 1 |

Same version, same ungoogled revision, same host, same flags, same probe, same seeds. **A difference between the columns is therefore attributable to our 16 patches and to nothing else.**

**The resource pairing, stated because it is load-bearing.** The CI artifact uploads two executables and **not** the resource sidecars Chromium needs to boot. Launched bare, our binary dies before opening a debug port: `Invalid file descriptor to ICU data received`. So the patched executable was paired with resources from the **exact same upstream release tag**. That pairing is sound *and checkable*: all 16 patches touch **compiled C++ only** (Blink, `ui/gfx`, `v8/src/inspector`, `components/ungoogled`) — not one touches a `.pak`, `icudtl.dat` or a snapshot. It also makes the control strictly honest: the control engine is that same upstream tree's own `chrome`, on the very same resources.

**The mechanical step, done the sanctioned way.** `_engine_binary()` resolves only `ENGINE_DIR/fpchrome.AppImage` and refuses a PATH fallback by design. A directory of that shape was staged and `PERSONA_ENGINE_DIR` pointed at it. **Nothing was renamed, `fingerprint_chromium_filename()` was not edited, and no PATH fallback was added** — stock Chromium 152 is present at `/usr/bin/chromium` in this container and was never launched.

**CDP.** `ChromiumSession` launches its own throwaway engine with `--remote-debugging-port=0` (ephemeral, unguessable, read back from `DevToolsActivePort`) into a temp user-data-dir removed at teardown. No operator profile was created, read or mutated. No fixed port, no `ai_control`, nothing persisting past the run.

**8 cells: 2 engines × 2 seeds (4242, 1337) × layer {off, on}. All 8 read; 0 failed.**

---

## Q1 — Are our fingerprint switches honoured? **YES**

Every switch-driven surface differs from the control in the direction the switch asks for. **The table is the layer-OFF reading** (the raw engine); every row below except `hardwareConcurrency` reads identically with the layer ON, so these are the engine's own answers and not the extension layer's:

| surface | patched | control | |
|---|---|---|---|
| `navigator.platform` | `Win32` | `Linux x86_64` | **spoofed** |
| `navigator.userAgent` | `Windows NT 10.0; Win64; x64` | `X11; Linux x86_64` | **spoofed** |
| `userAgentData.platform` | `Windows` | `Linux` | **spoofed** |
| UA-CH brands | `…Chromium/144 Google Chrome/144` | `…Chromium/144` | **differs** |
| `navigator.webdriver` | `false` | `true` | **suppressed** |
| `hardwareConcurrency` | `16` (seed 4242) / `30` (seed 1337) | `8` (host's real) | **spoofed, seed-varying** |
| `deviceMemory` | `8` | `8` | same |
| `Intl…timeZone` | `UTC` | `UTC` | same — **no `--timezone` was passed** (correct: loopback venue has no exit) |

**Patch 000 is doing its job** — it defines the switches every later patch reads, and the ones that were passed are honoured. `deviceMemory` matching is the honest reading of *"no switch for it was passed"*, not evidence of a defect.

**Two rows are the LAYER's, not the engine's, and are excluded from the claim above** — stated because reading them as engine behaviour is the easy mistake here:

- **`screen`** reads `1920x1080` layer-OFF and `2560x1440` layer-ON, **identically in patched and control**. `chromium_tier` never passes `--fingerprint-screen-width/height`, so this is persona's `device` extension, and the engine's screen switch was **not exercised** (see §UNMEASURED).
- **`hardwareConcurrency`** is spoofed by **both** layers, and they disagree: layer-OFF the engine reports `16`/`30` (seed-varying, control `8`); layer-ON **both** engines report `6`/`8`, i.e. the JS layer overwrites the engine's value with its own. Only the layer-OFF column is evidence about our patch.

---

## Q2 — Is the GPU spoofed? **YES, in every realm read — including the leak realm**

This is the sharpest question, and the reason it is read **per realm** is that the known chromium/linux defect is a *realm* leak: the top document can read spoofed while a worker inside an `about:blank` iframe reads the host's real GPU. **A single-realm read would report that defect as clean.**

Six realms, seed 4242, layer OFF:

| realm | patched | control |
|---|---|---|
| `page` | `Google Inc. (NVIDIA)` / `…RTX 3080 Ti Laptop GPU…D3D11` | `Google Inc. (Google)` / `…SwiftShader…` |
| `iframe_about_blank` | ↑ same spoof | ↑ SwiftShader |
| `iframe_srcdoc` | ↑ same spoof | ↑ SwiftShader |
| `worker` | ↑ same spoof | ↑ SwiftShader |
| `worker_nested` (depth 2) | ↑ same spoof | ↑ SwiftShader |
| **`worker_in_iframe`** | ↑ **same spoof** | ↑ SwiftShader |

**All six realms report the identical spoofed identity, with the masking layer OFF.** The engine authors this natively and consistently — including `worker_in_iframe`, the realm whose leak motivated this question. The identity is also **seed-varying** (`RTX 3080 Ti Laptop` at 4242, `RTX 3090 Ti` at 1337), which is what a real spoof does and a hardcoded constant does not.

The control column is what makes this evidence rather than a nice-looking string: SwiftShader is this GPU-less container's **real** renderer under our own `--use-angle=swiftshader` flag, and the control reports it in all six realms. So the patched column is our patch, measurably.

⚠️ **Bound:** this measures the **native engine's** realm coverage on **loopback**, not a live detector's verdict, and it does not retire the historical chromium/linux realm finding — that was measured on a **different engine** (the shipped fingerprint-chromium AppImage) through **JS-layer** spoofing. What this says is narrower and still valuable: **our own build does not have that gap natively at 144.**

---

## Q3 — Canvas and WebGL readback: three patches work, **one is broken**

Working, layer OFF, patched **varies with seed** where control is **constant across seeds**:

| vector | patch | patched | control |
|---|---|---|---|
| canvas `toDataURL` | 013 | **varies** (`3345026328` / `2433234074`) | constant |
| canvas `getImageData` | 012 | **varies** (`1514692039` / `2627843718`) | constant |
| WebGL `readPixels` | 016 | **varies** (`3056878596` / `2393998117`) | constant |
| client rects | 014 | **varies** (`7.999180…` / `8.000398…`) | constant `8.000000` |

That is the correct shape: seed-dependent perturbation, present in ours and absent upstream.

### 🔴 THE FINDING — `measureText` (patch 015) returns metrics ~2.2e-06× true size

| string | control width | patched width | ratio |
|---|---|---|---|
| `"a"` | `8.5791015625` | `-1.9223143168e-05` | `-2.2406942064968623e-06` |
| `"ab"` | `17.4658203125` | `-3.9135562386e-05` | `-2.2406942064968623e-06` |
| `"hello world"` | `76.8291015625` | `-1.7215052276e-04` | `-2.2406942064968623e-06` |
| `"persona PS-301 — canvas"` | `182.328125` | `-4.0854157337e-04` | `-2.2406942064968623e-06` |

Every metric on the object is destroyed the same way — width, all four `actualBoundingBox*`.

**Why this is worse than not masking.** No real browser reports a **negative, sub-micron** text width. A detector does not need to compare against anything to know it is looking at an instrumented engine: the value is self-evidently impossible. It also breaks any page that lays out text by measuring it.

**The cause — a two-patch interaction, not one bad line.** `TextMetrics::Shuffle(double)` is a **MULTIPLIER**: upstream's caller passed `Document::GetNoiseFactorX()`, which upstream initialises to `1 + (RandDouble() - 0.5) * 0.000003` — *centred on 1*. Scaling by ~1.0 perturbs; scaling by ~0 annihilates. Then two of our patches moved in opposite directions:

- **`014-client-rects`** redefined `noise_factor_x_` from that centred-on-1 multiplier to `norm_x * 0.002` — an **offset** centred on 0 — and correspondingly changed *its own* call sites from `Scale()` to `Offset()`. **Self-consistent.**
- **`015-canvas-measure-text`** stopped reading `GetNoiseFactorX()` and computes its own `noise_x = norm_x * 0.00001` — also offset-shaped, centred on 0 — but **still passes it to `Shuffle()`, which still multiplies.**

So 015 hands a value in `[-5e-06, +5e-06]` to a function that multiplies by it. Each patch is defensible read alone; the pair is not.

**The arithmetic is the proof.** The patched/control ratio is **identical to 17 significant figures across all four strings**. A constant ratio is what a multiply produces and what an add cannot — that is what rules out "some additive noise" and pins it to `Shuffle`. The observed `-2.24e-06` also sits inside 015's own stated `norm_x * 0.00001` range, tying it to that specific line.

**⭐ persona's JS layer HIDES it.** With the masking layer ON (on an http origin), the same probe reads `ratio = 1.0017` — plausible, perturbed-around-1 metrics — because the `measuretext` extension replaces the value before a page sees it. **This is invisible to any test that exercises the product end-to-end**, which is exactly why it took a **layer-OFF** read of a **raw engine** to surface. It matters anyway: the engine is shipped as the thing that masks *natively*, and a native layer being rescued by the JS layer is not doing its job — anything that reaches the engine without our extensions (a realm the content script does not enter) sees the broken value.

**Scope beyond 144 — verified in `git`, not inferred.** `015-canvas-measure-text.patch` is **byte-identical at HEAD** after the PS-299 rebase onto 152.0.7977.75 (one of the six patches the rebase left untouched). **The same two lines are in the 152 engines.** Confirming it *by measurement* on a 152 binary needs a host that has one.

Reproducer, committed so this is re-checkable in a minute and re-checkable again after a fix: **`scripts/ps301_measuretext_repro.py`**.

### Audio (patch 003) — layer-off reading is **inconclusive**

Layer OFF: patched `1079.89159002` vs control `1079.88729525` — differs from control, but **constant across both seeds**. A seed-independent constant is not the shape of a working per-profile spoof. It is *not* reported as a defect here: one probe, two seeds, on a container with no audio hardware is too thin. **Recorded as: differs from upstream, does not vary with seed, needs a wider seed sweep to rule on.**

---

## Q4 — Does persona's masking layer still work on this engine? **YES**

The layer installs cleanly on our own build and all ten extensions load:

```
LayerReport(route='extensions',
            installed=('native','locale','voice','stealth','measuretext',
                       'audio','device','webgl','gpu','canvas_ctx'),
            failed={}, expected=(… same ten …))
```

`failed={}` on every patched cell, both seeds. The GPU identity is **unchanged** between layer-off and layer-on in all six realms — the engine already authored it, and the JS layer agrees rather than fighting it, which is the correct interaction. And as §Q3 records, the layer **does** actively change `measureText`, where it is currently compensating for a broken native layer.

---

## ⛔ UNMEASURED — reported as unmeasured, never inferred from a neighbour

| vector | why |
|---|---|
| **The 152 engines (all 3 OSes)** | binaries live on the owner's hosts; unreachable from a worker container. The `015` *patch-text* finding carries; **no measurement here does.** |
| `--fingerprint-screen-width/height` | `chromium_tier` never passes them. Screen values seen (`1920x1080`, `2560x1440`) come from the **layer**, not the engine's switch, and were identical patched-vs-control. |
| `--fingerprint-location` / geolocation | no switch passed; not read. |
| Font enumeration (patch 006) | not probed. |
| Shadow root (007), headless (010) | not probed. |
| Timezone (patch 018) | `--timezone` correctly not passed on a loopback venue with no exit, so the patch was **not exercised**. Both engines read the host's UTC. |
| ServiceWorker realm | not probed here (the `ps189` harness covers it on the shipped engine). |
| Live detector verdicts | loopback venue by design; contacts no third party. |
| Windows / macOS behaviour | Linux container only. |
| GPU-present host behaviour | this host has no GPU. |

## Honest bound

**One binary, one host, one container, one declared arm (`windows`), two seeds, six realms, loopback.** That is **not** a characterisation of the engine. It is the first execution of it, and it answers the four questions asked with a control that makes each answer attributable. It does not license any statement about the 152 engines' *behaviour*, about other platforms, or about what a live detector would conclude.

SwiftShader appears here because this container has no GPU and **both** the product and this tier pass `--use-angle=swiftshader` on Linux — it is the host's real renderer under our own flags, and the argv for every cell is captured in `engine-launch.json` so a reader can check the flags before attributing anything.

## What this is NOT

Per the ticket's scope: **measure, do not fix.** Nothing here fixes the `measureText` defect, and no patch was edited. The fix belongs to the masking direction, and the reproducer is committed so whoever takes it has a RED to start from and a way to prove it went green.
