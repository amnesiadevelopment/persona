# PS-301 — the self-built engine, LAUNCHED

**Date:** 2026-09-05 · **Tree:** `feature/ps301-launch-self-built-engine` off `main` `493fed2`
**Author:** worker seat · **Method:** the binary *executed*, every reading taken twice
(once through the sanctioned CDP harness, once directly from the command line),
every value anchored against a stock control run on the same host in the same hour.

**Engine measured — say this before any number:**

| | |
|---|---|
| **binary** | self-built **patched** `chrome`, **Chromium 144.0.7559.132** |
| **provenance** | CI artifact `ps218-patched-binary-144.0.7559.132-1` (id `9895111731`, run 2026-09-03T13:17Z) |
| **sha256** | `3b403afbdd6e6847d394a630a27e097831701123fad738de25e8e0d71c3c0fe4` |
| **size** | 456,688,328 bytes |
| **control** | **stock** Google Chrome for Testing **144.0.7559.133**, sha256 `b582e32f…c353` |
| **host** | `Linux beceaf359992 6.8.0-138-generic x86_64`, container, **no GPU** |

**Reproducing:** `artifacts/ps301_engine_launch.py` (the harness), `artifacts/ps301_compare.py`
(every verdict below is *computed* by it, not typed), `artifacts/ps301_repro.sh` +
`repro-transcript.txt` (the direct, non-harness reproductions), and the raw
`readings-self-built-144.json` / `readings-stock-cft-144.json`.
**`artifacts/verify_claims.py` re-derives all 76 load-bearing figures below from
those committed records and exits non-zero on a miss** — so a later reader can
tell "the report still describes these artifacts" from "the artifacts moved
underneath it". It was itself checked against a sabotaged copy (one realm's GPU
string rewritten to SwiftShader) and correctly failed, so a pass is a real
signal rather than a vacuous one.

---

## 0. Summary — five findings, in order of how much they change the answer

1. **⭐ IT RUNS, AND THE MASKING WORKS AT THE ENGINE LEVEL. The GPU question —
   the ticket's sharpest — answers YES.** With persona's JS layer **OFF**, the
   self-built engine reports `NVIDIA GeForce RTX 4070` (seed 24601) /
   `RTX 3080 Laptop GPU` (seed 5150) to **all seven realms** — page, three
   iframe shapes, blob worker, worker-in-iframe, and a depth-2 nested worker —
   while stock reports the host's real SwiftShader to all seven. The strings are
   in patch 011's own table and in our binary, and **zero** of them appear in
   `LINUX_GPUS`, so this is the ENGINE's spoof and cannot be our JS. (§3)
2. **🐞 A REAL DEFECT, FOUND ONLY BY RUNNING IT: patch 015 makes `measureText`
   return a NEGATIVE width.** `11.629` becomes `-6.698e-06`. The observed/stock
   ratio is *identical across four different strings*, which identifies the
   mechanism exactly: `TextMetrics::Shuffle` **multiplies** by the noise factor
   instead of perturbing by it. A negative width is impossible per spec, so this
   is trivially detectable — the opposite of what the patch is for. No compile,
   no hash and no static review could have found this. (§4)
3. **🐞 THREE SWITCHES ARE DEAD — declared, forwarded to the renderer, and read
   by nothing.** `--fingerprint-screen-width`, `--fingerprint-screen-height` and
   `--fingerprint-device-scale-factor` are accepted in silence and change no
   observable. Measured at runtime *and* confirmed statically: patch 000 defines
   and forwards all twelve switches, and no other patch in the set ever reads
   those three. (§5)
4. **THE 152 BINARY THIS TICKET WAS SENT TO MEASURE DOES NOT EXIST.** The
   planner's steering names `/home/builder/personium-152-patched-local`; that
   path is absent from this host, and no `ps218-patched-binary-152…` artifact
   was ever produced. The 152 patched build's own manifest records
   **`Tree COMPILED: NO — FAILED`, `chrome binary on disk: ABSENT`**. This is
   stated first among the caveats because it changes what the whole report is
   about: this is a **144** reading and must never be quoted as a 152 one. (§1)
5. **The masking layer still installs and still works on this build** — all ten
   modules — and with the layer ON the identity is realm-coherent across all
   seven realms. But layer-ON is *not* how the engine was judged here: every
   attribution below is taken layer-**OFF**, because that is the only state in
   which a difference from stock is attributable to the engine. (§6)

**Against the ticket's stated trap** — *"size and sha256 attest to PRESENCE, not
to FUNCTION"* — the answer is now measured in both directions: the patch set
demonstrably works on the vectors in §3, and it demonstrably ships a
page-detectable defect in §4. Both facts required executing the binary.

---

## 1. Which binary — and why it is not the one the steering named

The ticket's body says the 144 build is *"the only self-built engine in
existence"*. A planner comment on 2026-09-05T19:30Z overtook that, stating all
three 152 engines were built and preserved, and instructing: **"Measure the 152
Linux binary."** I could not, and the reason is not a limitation of this seat.

**What I checked, and what it said:**

| check | result |
|---|---|
| `/home/builder/personium-152-patched-local` | **does not exist** on this host |
| user `builder` | does not exist |
| any file >200 MB on this host | 3 hits: playwright's chromium, a firefox `libxul.so`, `/usr/lib/chromium/chromium` — no personium build |
| CI artifacts matching `ps218-patched-binary-152*` | **none have ever existed** |
| CI artifact `ps218-patched-152.0.7977.75-1` (id `9935550319`) | manifest: **`Patches APPLIED: YES`**, **`Tree COMPILED: NO — FAILED`**, **`chrome binary on disk: ABSENT`** |
| the 21:22Z re-dispatch (id `9954911879`) | **`NOT ATTEMPTED`** — its borrowed-control verification **REFUSED** the build (`nproc` 32 vs 8, hostname differs, kernel `WSL2` vs `25.5.0`: it landed on the **macOS** runner) |

So on the evidence reachable from here, the 152 Linux compile **failed and left
no binary**, and the run that would have retried it was refused before it
started. The owner may well hold a 152 binary built outside CI on his own
machine — the 11:54Z attestation says so — but **that artifact is not reachable
from this container**, and I will not report a reading I did not take.

**What I measured instead: the 144 patched binary**, which does exist, is
retained until 2026-09-10, and is the only self-built engine reachable in any
form. Per the ticket's own instruction — *"if that rebase is slow, measure the
144 binary we ALREADY HAVE first and on its own"* — that is the sanctioned
fallback, and a 144 reading is the baseline a future 152 reading is compared
against.

> ⚠️ **Every number in this report is a `144.0.7559.132` number.** None of it may
> be quoted as a claim about 152, about Windows, or about macOS. §8 lists what
> stays unmeasured.

---

## 2. Getting it to launch — two mechanical facts worth recording

### 2.1 Pointing the harness at a bare `chrome`, without defeating its guard

`chromium_tier._engine_binary()` resolves only `ENGINE_DIR/fingerprint_chromium_filename()`
and refuses a PATH fallback, because *"a stock browser would launch happily and
produce a complete-looking record of something that is not the product."* Our
artifact is a bare `chrome`.

The route taken is the one the planner specified and it worked first time:
stage a directory holding a **symlink named `fpchrome.AppImage`** pointing at the
real binary, and set `PERSONA_ENGINE_DIR` to it. The built artifact is untouched,
`fingerprint_chromium_filename()` is unedited, no binary was placed on PATH, and
the resolver's refusal is intact. `--appimage-extract-and-run` is passed by
`_launch_args` on Linux and a real ELF ignores it harmlessly — verified.

### 2.2 ⚠️ The artifact is NOT self-contained, and its failure does not say so

`ps218-patched-binary-*` uploads exactly two paths — `out/Default/chrome` and
`out/Default/chromedriver`. It ships **none** of the runtime resources chromium
needs. Launched as-is the binary dies with:

```
ERROR:base/i18n/icu_util.cc:232] Invalid file descriptor to ICU data received.
```

and then `rc=133` — which through the harness surfaces as *"persona's chromium
exited before opening a debug port"*, a message that reads like a broken build.
**It is not a broken build; it is an incomplete artifact.** I resolved it by
staging the binary into a **version-matched** Chrome-for-Testing
`144.0.7559.133` resource tree (`icudtl.dat`, the `.pak` files, the V8 snapshots,
`locales/`, the ANGLE/SwiftShader libraries).

> **The honest caveat that comes with that, stated rather than buried:** the
> resources are from `.133` and the binary is `.132` — adjacent patch releases in
> the same milestone. Nothing observed here plausibly turns on that skew (the
> spoofs measured live in the binary's own Blink/GPU code, and the *stock control
> was run from that same `.133` tree*, so any resource-level effect is present in
> **both** arms and cancels out of every difference below). But it is a skew, it is not
> zero, and a future run with a self-contained artifact would remove it.
>
> ⭐ **The actionable half:** if the trial-build workflow is meant to produce an
> artifact anyone can execute, its upload step needs the rest of `out/Default`.
> That is a change to `engine-trial-build.yml`, and it is **out of scope here** —
> recorded as a finding, not fixed.

---

## 3. ⭐ Q2 — the GPU. The sharpest question, and it answers YES.

**Layer OFF. Stock control, same flags, same host, same page.**

| seed | self-built `UNMASKED_RENDERER` | stock control |
|---|---|---|
| 24601 | `ANGLE (NVIDIA Corporation, NVIDIA GeForce RTX 4070/PCIe/SSE2, OpenGL 4.5.0)` | `ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)` |
| 5150 | `ANGLE (NVIDIA Corporation, NVIDIA GeForce RTX 3080 Laptop GPU/PCIe/SSE2, OpenGL 4.5.0)` | *(identical SwiftShader string)* |

`UNMASKED_VENDOR` moves with it: `Google Inc. (NVIDIA Corporation)` against the
control's `Google Inc. (Google)`.

**Realm coverage — the part that matters, because the known chromium/linux leak
is a realm the JS layer never reached.** With the layer **OFF**, each seed
produced **exactly one distinct identity across all seven realms**:

```
seed 24601 layer OFF:
   ANGLE (NVIDIA Corporation, NVIDIA GeForce RTX 4070/PCIe/SSE2, OpenGL 4.5.0)
      <- 7 realms: iframe_about_blank, iframe_same_origin, iframe_srcdoc,
                   page, worker_blob, worker_in_iframe, worker_nested
seed 5150 layer OFF:
   ANGLE (NVIDIA Corporation, NVIDIA GeForce RTX 3080 Laptop GPU/PCIe/SSE2, OpenGL 4.5.0)
      <- 7 realms: (the same seven)
```

**Three independent checks that this is the ENGINE and not our JS**, because
"it looks spoofed" is not evidence:

1. **The layer was OFF.** `install_layer=False`; the record carries the argv.
2. **The strings are not in our pool.** `LINUX_GPUS` holds 8 entries and
   **zero** mention NVIDIA at all, let alone an RTX 4070 — verified by importing
   the module. Our JS could not have produced these values.
3. **The strings are in the binary and in the patch.** `GeForce RTX 3080 Laptop GPU`
   appears in patch `011-gpu-info.patch`'s device table and in the self-built
   binary (37 `GeForce RTX` strings); the same grep against the stock control
   binary returns **zero**.

**Reading:** this is what owning the engine bought. A JS layer has to *reach*
each realm and the linux arm is where it historically failed to; an engine-level
spoof is present in every realm by construction, including a depth-2 nested
worker inside an `about:blank` iframe. On this vector, on this build, the
engine-level approach does what it was supposed to do.

**Bound:** this measures WebGL *identity strings*. It does **not** measure
rendered pixels agreeing with the claimed card, and it is one host with no GPU.
See §8.

---

## 4. 🐞 Q3 — the defect. `measureText` returns a negative width.

**This is the finding that justifies the ticket existing.** It is invisible to a
compile, to a hash, and to a patch-applies-as-text check.

Direct reproduction, outside the CDP harness (`repro-transcript.txt` §2):

```
--- self-built, --fingerprint=24601
A                          | w=-0.000006698308010171917 | abbL=0
hello                      | w=-0.000023661617660485952 | abbL=5.759808038305448e-7
persona-PS301              | w=-0.00007390609937276053  | abbL=5.759808038305448e-7
The quick brown fox jumps  | w=-0.00013180032613590952  | abbL=-5.759808038305448e-7

--- self-built, NO --fingerprint (the patch stands down — correct)
A                          | w=11.62939453125
hello                      | w=41.08056640625
persona-PS301              | w=128.3134765625
The quick brown fox jumps  | w=228.82763671875

--- STOCK control, --fingerprint=24601 (the switch is inert in stock)
   ... byte-identical to the no-seed self-built row above.
```

**The mechanism, identified exactly rather than guessed.** Divide each spoofed
width by its stock width:

| string | observed / stock (seed 24601) | (seed 5150) |
|---|---|---|
| `A` | `-5.759808e-07` | `-3.873258e-06` |
| `hello` | `-5.759808e-07` | `-3.873258e-06` |
| `persona-PS301` | `-5.759808e-07` | `-3.873258e-06` |
| `The quick brown fox jumps` | `-5.759808e-07` | `-3.873258e-06` |
| **spread (max−min)** | **`0.000e+00`** | **`0.000e+00`** |

A **constant** ratio across four different strings is multiplication, not
perturbation — and the constant is *numerically equal to the `noise_x` the patch
computes*, which is also what it reports as `actualBoundingBoxLeft`. Patch 015
computes `noise_x = norm_x * 0.00001` with `norm_x ∈ [-0.5, 0.5]`, i.e.
`|noise_x| ≤ 5e-6`, and hands it to `text_metrics->Shuffle(noise_x)`. The
upstream call site passed `GetNoiseFactorX()`, which is a value **around 1**
(`1 + (rand−0.5)*0.000003`) precisely because `Shuffle` **scales**. The rebase
substituted an *offset-shaped* value into a *scale-shaped* parameter.

**Why it matters more than a rounding bug:** a negative `TextMetrics.width` is
impossible for any real browser — the spec has it non-negative — so a detector
does not need a baseline or a comparison to flag it. One `if (m.width < 0)` is a
perfect, zero-false-positive tell for this engine. The patch intended to make
text measurement *unremarkable* and instead made it *unique*.

**Scope, measured rather than assumed:**
- **DOM realms (page, all three iframes): affected.** Layer OFF.
- **Worker realms (blob, in-iframe, nested): NOT affected** — they read
  `172.1083984375`, identical to stock. The patch's `HostAsOffscreenCanvas()`
  branch requires a `LocalDOMWindow`, which a worker has not, so the spoof
  never fires there. That is a *second*, separate observation: `measureText` in
  a worker is unspoofed on this build.
- **Our JS layer masks it in the DOM realms** (`172.109375` vs stock
  `172.1083984375`) — the `measuretext` module is one of the ten that install.
  So an operator running the full product today is not exposed by this. **That
  does not make it harmless**: it means the engine-level patch is contributing
  nothing here and would expose anyone who ran the engine without the layer,
  which is exactly the configuration this engine exists to make possible.

> **Out of scope, per the ticket:** fixing it. Recorded for the masking direction.

---

## 5. 🐞 Q1 — the switches. Nine live, three dead.

The ticket puts this first because *"patch 000 defines the switches every later
patch reads. If those are not honoured, nothing else in the set can be."* They
are honoured — **most of them**.

**Live, and attributable (layer OFF, against the stock control):**

| switch / surface | self-built | stock, same flags |
|---|---|---|
| `--timezone` → `Intl…timeZone` | `America/Chicago` **in all 7 realms** | `UTC` in all 7 |
| … → `Date` offset | `300` | `0` |
| `--fingerprint-hardware-concurrency` / seed | `12`, `18` (**moves with seed**) | `8`, `8` (constant) |
| `--fingerprint-platform=windows` | `Win32`, `Windows NT 10.0` UA, uaD `Windows` | `Linux x86_64` (switch inert) |
| `--fingerprint-platform=macos` | `MacIntel`, `Intel Mac OS X 10_15_7` UA | — |
| `navigator.webdriver` (patch 009) | `false` | **`true`** |
| `--fingerprint` → canvas/WebGL digests | all move with seed (§7) | constant |

The `webdriver` row deserves a note: stock reports `true` under CDP automation
and the self-built engine reports `false`. Patch 009 is doing its job — and it is
the clearest single "this is not stock" signal in the whole record.

**Dead — accepted in silence, observable unchanged:**

| switch | passed | observed |
|---|---|---|
| `--fingerprint-screen-width=2560` | yes | `screen.width` = `800` (headless) / `1920` (the harness's Xvfb) |
| `--fingerprint-screen-height=1440` | yes | `screen.height` = `600` / `1080` |
| `--fingerprint-device-scale-factor=2` | yes | `devicePixelRatio` = `1` |

Contrast in the same run: `--fingerprint-hardware-concurrency=6` → `hc=6`. So the
mechanism works; these three specific switches are not wired to anything.

**Confirmed statically, so the runtime result is not a fluke of this host.**
Grepping each switch constant across the patch set, excluding patch 000 itself:

```
kFingerprintScreenWidth          -> <NOBODY — declared, forwarded, never read>
kFingerprintScreenHeight         -> <NOBODY — declared, forwarded, never read>
kFingerprintDeviceScaleFactor    -> <NOBODY — declared, forwarded, never read>
kFingerprintLocation             -> <NOBODY — declared, forwarded, never read>
kFingerprintHardwareConcurrency  -> 005-hardware-concurrency-fingerprint.patch
kFingerprintPlatform             -> 002, 006, 011
kFingerprintTimezone             -> 018-timezone.patch
```

Patch 000 declares twelve switches and forwards them to the renderer; **four have
no consumer**. Three are proven dead at runtime above. The fourth,
`--fingerprint-location`, is *not* claimed as dead here — I did not measure it
(no geolocation probe in this run), and an unmeasured vector is reported as
unmeasured. Its static reading is suggestive and is a lead, not a verdict.

**The screen values are the DISPLAY's, not a spoof.** Layer OFF read
`1920x1080` — exactly the Xvfb the harness starts (`Xvfb :N -screen 0 1920x1080x24`).
Headless with no display reads `800x600`. Both are the environment showing
through, which is what "not spoofed" looks like.

---

## 6. Q4 — the masking layer still works on this engine

The layer installed **all ten** modules on the self-built binary:
`audio, canvas_ctx, device, gpu, locale, measuretext, native, stealth, voice, webgl`.
No module reported absent, and no cell errored — 7/7 realms read in every one of
the four self-built cells (2 seeds × layer on/off).

With the layer **ON**, the WebGL identity is realm-coherent across all seven
realms and reads from our own pool (`Mesa Intel(R) Iris(R) Xe Graphics (ADL GT2)`
/ `UHD Graphics 630`) — and, notably, **identically in the stock arm**, which is
the expected result: with the layer on, our JS authors the identity in both
engines, so the engine's own spoof is overwritten and the two arms agree.

> **That agreement is exactly why every attribution in this report is taken
> layer-OFF.** A layer-ON reading cannot distinguish "the engine masked this"
> from "our JS masked this" — the two produce the same string. Reading only the
> layer-ON column would have shown the GPU spoof as *"no difference from stock"*
> and missed §3 entirely.

---

## 7. Q3 — canvas and WebGL readback, the rest of it

All layer-OFF, against the stock control. `✓` = differs from stock **and** moves
with the seed (the two claims the product's contract actually needs).

| vector | patch | DOM realms | worker realms |
|---|---|---|---|
| `getImageData` digest | 012 | ✓ | ✓ (all 3) |
| `toDataURL` digest | 013 | ✓ | n/a — `OffscreenCanvas` has no `toDataURL` |
| WebGL `readPixels` digest | 016 | ✓ | ✓ (all 3) |
| `measureText` width | 015 | 🐞 §4 | **unspoofed** (reads stock) |
| `getBoundingClientRect` x / y | 014 | ✓ | n/a — no DOM |
| `getBoundingClientRect` **width** | 014 | **unchanged, correctly** | n/a |
| WebGL `VENDOR` / `RENDERER` (masked) | — | `WebKit WebGL` both arms — **not spoofed, and correct**: that is the constant every browser returns | same |

Two of those rows are "no difference" that would be **wrong to read as a gap**,
and both cost me a false negative before I checked:

- **`getBoundingClientRect` width does not move — by design.** Patch 014 calls
  `rect.Offset(...)`, not `Scale(...)`. It *moves* the rect and never resizes it,
  so a width-only probe cannot see it whatever element it uses.
- **My first probe read a `position:absolute` element and saw nothing.** Patch 014
  adds `Element::ShouldSkipClientRectsOffset()`, which deliberately **exempts**
  an absolutely-positioned element with a deterministic top *and* left —
  precisely so a page cannot detect the noise by placing an element at
  coordinates it already knows. I corrected the harness to read a
  statically-flowed element **and** to keep reading the exempt shape beside it,
  so the record shows the exemption being honoured rather than merely assuming
  it. Both matrices were then re-run from scratch; §7's numbers are from the
  corrected probe.

> ⚠️ **Recorded because it nearly became a false finding:** "vector X shows no
> difference" is only a finding once you have checked that your probe *could*
> have seen a difference. Two of the four canvas/rect vectors here needed a
> specific element shape or a specific property to be observable at all.

---

## 8. What was NOT measured — stated, not inferred

An unmeasured vector is reported as unmeasured. None of the following may be
inferred from a neighbour that passed.

- **The 152 engines — Linux, Windows, macOS.** Not measured; §1. The Linux 152
  patched compile failed in CI and left no binary; the owner's local artifacts
  are not reachable from this container. **Nothing here is a 152 reading.**
- **Windows and macOS at all.** One Linux binary on one Linux host.
- **Rendered GPU pixels.** §3 measures WebGL *identity strings*. Whether the
  pixels a checker renders are consistent with the claimed RTX 4070 is a
  different question and was not asked. This host has no GPU.
- **`--fingerprint-location`** (patch 000 declares it; no patch consumes it).
  No geolocation probe was run — the static reading is a lead, not a verdict.
- **Patches 001, 007, 010** (`Runtime.enable`, shadow root, headless) — no probe
  targets them. Not measured, either way.
- **Patch 006 (fonts)** — no font-enumeration probe. Not measured.
- **Patch 003 (audio)** — the probe is present but the page-realm audio read is
  not analysed in this report; a value was captured and is in the raw JSON,
  unverdicted.
- **Any live third-party checker.** The venue was loopback (`127.0.0.1`) with no
  exit and no remote observer. This is a mechanism measurement, not a
  creepjs/pixelscan run, and it does not discharge one.
- **Anything under the sandbox.** This host forbids the unprivileged user
  namespace, so every cell ran with `--no-sandbox` (`sandbox_waived: true` in
  every record). persona's own launch path does not pass that flag.
- **A second host, a second profile, a second build.** One binary, one host, one
  probe page. **This is not a characterisation of the engine.** It is the first
  time anyone has looked at all.

---

## 9. Leads for other tickets — recorded, not acted on

Fixing what the measurement found is explicitly out of scope. Four things
another ticket may want:

1. **Patch 015's `Shuffle` argument is offset-shaped where a scale is
   wanted** (§4). Highest value: it is a page-detectable tell.
2. **Four switches have no consumer** (§5) — three proven dead at runtime.
   Either wire them or delete them from patch 000; a switch that is accepted and
   ignored is worse than one that is absent, because callers believe it works.
3. **`engine-trial-build.yml` uploads an unrunnable artifact** (§2.2) — two
   files out of a runtime tree. Anyone who tries to execute a build artifact
   hits an ICU error that reads like a broken compile.
4. **`measureText` and `toDataURL` are unspoofed in worker realms** (§7) —
   for `measureText` because the patch's branch needs a `LocalDOMWindow`. Whether
   that matters depends on whether a detector reads text metrics from a worker;
   this report does not answer that.

---

## 10. The one-line answer to the ticket

**The self-built engine was launched. It runs, and a page sees a masked machine
on the vectors that matter most — the GPU identity is spoofed at engine level in
all seven realms, which is the thing owning the engine was supposed to buy.
Running it also found a defect no compile could: `measureText` returns a negative
width, which is a perfect detection tell. Both halves required executing the
binary, and neither was visible in a green build.**
