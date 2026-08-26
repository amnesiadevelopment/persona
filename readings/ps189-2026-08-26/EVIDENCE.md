# PS-189 — the ServiceWorker realm has no author

**Date:** 2026-08-26
**Ticket:** PS-189 (Theme 1, masking invisibility; roadmap `undetectable-masking`)
**Branch basis:** `origin/main` @ `fa0a5e9`
**Instruments (committed beside these records):**
`scripts/ps189_realm_gpu.py` (loopback realm sweep),
`scripts/ps189_live_creepjs.py` (live checker read through the proxied exit),
`readings/ps189-2026-08-26/derive.py` (realm sweep -> checker-matrix records).

---

## The question, and the answer

PS-186's 8-arm sweep left two symptoms on the arms PS-161 never touched:

1. **`chromium / linux`** — creepjs read the container's **real SwiftShader**
   while `pixelscan.net` read a plausible Mesa card. Scored the lowest cell in
   the matrix (35/100) **with zero adverse verdicts fired**.
2. **`chromium / macos`** — creepjs and `pixelscan.net` read **different Apple
   cards** from one profile in one run.

The ticket required settling **by measurement** whether these are one defect or
two, before fixing either.

> **They are ONE defect.** The `ServiceWorkerGlobalScope` is authored by
> **neither** of the two identity authors, so it falls through to whoever is
> left: the **ENGINE** on an arm the engine spoofs (macos), and the **HOST** on
> an arm it does not (linux). Same hole, two faces.

---

## §1 The measurement that settles it

`scripts/ps189_realm_gpu.py` reads the WebGL identity pair from **twelve
realms** in **one launch, at one instant**, with the masking layer **ON**.
Full records: `realm-gpu.json`; summary: `realm-gpu-summary.txt`.

| arm / seed | the 11 page-reachable realms | **`service_worker`** |
|---|---|---|
| linux / 24601 | `ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)` | **`ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)`** |
| linux / 5150 | `ANGLE (Intel, Mesa Intel(R) Iris(R) Xe Graphics (ADL GT2), OpenGL 4.6)` | **the same SwiftShader string** |
| macos / 24601 | `ANGLE (Apple, ANGLE Metal Renderer: Apple M1, ...)` | **`... Apple M2 ...`** |
| macos / 5150 | `ANGLE (Apple, ANGLE Metal Renderer: Apple M2 Pro, ...)` | **`... Apple M4 ...`** |
| windows / 24601 | `ANGLE (AMD, AMD Radeon(TM) Graphics (0x00001638) ..., D3D11)` | *agrees* |
| windows / 5150 | `ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Laptop GPU ..., D3D11)` | *agrees* |

The eleven that agree are: `page`, `page_webgl2`, `iframe_same_origin`,
`iframe_about_blank`, `iframe_srcdoc`, `worker`, `worker_nested`,
`worker_in_iframe`, `worker_http`, `worker_module`, `shared_worker`.

**The values reproduce PS-186's live readings exactly** — `M2`/`M4` on macos and
SwiftShader on linux are what creepjs reported through the exit — which is what
ties this loopback mechanism to the live defect rather than to a local artefact.

### Why this is one defect and not two

The macos service-worker value is **the engine's own**, established by a
**control** rather than by inspection: with the layer **OFF**
(`realm-gpu-layer-off.json`) the engine produces exactly `Apple M2` (seed 24601)
and `Apple M4` (seed 5150), in **all twelve** realms. So on macos the unauthored
realm falls through to the engine; on linux the engine authors nothing, so it
falls through to the host. One mechanism, two outcomes, decided only by whether
the engine happens to spoof that arm.

### ⚠️ Windows is NOT the control for realm coverage

Windows is clean **because** `gpu_ext.ENGINE_AUTHORED_IDENTITY_ARMS ==
frozenset({"windows"})` stands our layer down there, leaving the **engine** as
the single author of every realm — including the service worker. A green windows
reading is evidence about **authorship count**, never about whether our layer
reaches a service worker. **Reading it as the latter is precisely what let this
defect survive PS-161**, whose fix was verified on the one arm structurally
incapable of showing the bug.

---

## §2 The mechanism

`worker_wrap` chains `Worker` and `SharedWorker` (`worker_wrap.py:331-332`) —
both **constructors the page calls**. A service worker is reached by neither: it
is **registered** with the browser (`navigator.serviceWorker.register`) and
**started by the browser**, often for a later navigation. There is no
construction to intercept and no realm handle to chain onto, and an MV3 content
script does not run there. Nothing in `src/services/browser/` handles
`serviceWorker` at all.

So this is the **same failure class as PS-155/PS-161** — two authors, and a gap
where **neither** writes the identity pair — recurring on a **third axis**. The
earlier rounds closed the axes of *our-fold-vs-engine* and
*os_type-vs-engine_platform*; this is the **realm** axis, which no amount of
agreeing on the platform string can cover.

---

## §3 The gate sees it, both red and green

The merged consistency gate is the ticket's designated instrument.
`derive.py` maps each realm onto a `checker` row named `realm:<name>` (vector
`gpu_claimed`), producing the records in `derived-matrix/`. **No value is
invented — every one is copied verbatim from the sweep.**

```
$ python -m src.services.verify.checker_cli consistency <record>

realm-matrix.chromium.linux.seed24601.json    exit=1   HOST_LEAK
realm-matrix.chromium.linux.seed5150.json     exit=1   HOST_LEAK
realm-matrix.chromium.macos.seed24601.json    exit=1   CONTRADICTION
realm-matrix.chromium.macos.seed5150.json     exit=1   CONTRADICTION
realm-matrix.chromium.windows.seed24601.json  exit=0   CONSISTENT
realm-matrix.chromium.windows.seed5150.json   exit=0   CONSISTENT
```

`HOST_LEAK` outranks `CONTRADICTION`, and is satisfied by a **single row** —
which matters here, because the leak needs no disagreement to be a finding.

**The gate ALSO fires on PS-186's own committed records** (linux `HOST_LEAK`,
macos `CONTRADICTION`, windows and firefox clean), so this is not an artefact of
the derived shape. The ticket's warning is worth restating: a `CONSISTENT`
verdict would **not** have cleared problem 1, because a value leaked
*consistently* by every reader is not a self-contradiction. The value was
checked, not only the verdict.

---

## §4 Live verification through the proxied exit

Loopback alone does not close this (PS-97, PS-182: an internal buffer differing
is not evidence the difference survives to a page). `scripts/ps189_live_creepjs.py`
read the live site through the proxied exit. Record: `live/live-creepjs.json`.

The live page **names the realm itself**, in this order:

```
extension: 41c71bad
1741.10ms
ServiceWorkerGlobalScope        <- creepjs labels the scope it read
Worker2871944a
...
gpu:
Google Inc. (Google)
ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)
```

`ServiceWorkerGlobalScope` is the **only** worker-scope label on the page, and
**our card appears nowhere on it**. Only two `ANGLE (` occurrences exist on the
whole page and both are the SwiftShader string — so this is a genuine leak and
**not** a first-match artefact of the `(angle \([^\n]+\))` pattern, a
possibility that was tested and ruled out rather than assumed.

**Exits, recorded per record and never as a constant** (PS-186 measured 5
distinct ASNs across 8 records):

| observation | IP | city | ASN |
|---|---|---|---|
| pre-run guard | `109.243.71.202` | Dębowiec | `AS39603 P4 Sp. z o.o.` |
| linux / 24601 | `31.186.220.36` | Mokotów | `AS9141 P4 Sp. z o.o.` |

The exit is re-observed **per record**, because this is a rotating mobile exit
and a single observation carried across records would write an ASN that was
never true of the later ones. The run **reports and does not fall back**: it
aborts rather than continuing on a direct connection.

**Credential provenance:** the file and the environment **disagree** on this
host (`diverged: true`), exactly as PS-186 recorded. The **file** won, per
`resolve_credential`'s precedence (the file is bind-mounted and rotates; the
environment variable goes stale silently).

---

## §5 Instrument checked before the product (PS-14)

This container has **no GPU**, and **both** the product (`process.py:630`) and
the verify tier (`chromium_tier.py:486`) pass `--use-angle=swiftshader` on
Linux. So SwiftShader here is the host's **real** renderer under our own flags —
not an artefact of the harness. The argv actually used is captured into **every**
record so this stays checkable rather than assumed.

Per the owner's standing ruling (**PS-10**, 2026-08-22) there will be no GPU
machine in the loop and the engine is expected to present a plausible GPU
wherever it runs. **So SwiftShader reaching a page here is a product finding,
not an environment excuse.**

---

## §6 Why this is not fixed in the extension layer

Both techniques this codebase already relies on were **tried against the service
worker and refused by the browser** — measured by the same script, arms
`fix_blob_registration` / `fix_cross_origin_registration` /
`fix_register_patchable`:

| technique | result |
|---|---|
| register a SW from a `blob:` URL (the re-blob trick the whole worker path is built on) | **refused** — `TypeError: The URL protocol of the script ('blob:...') is not supported` |
| register a SW from a cross-origin URL (e.g. an extension origin) | **refused** — `SecurityError: The origin of the provided scriptURL does not match the current origin` |
| patch `ServiceWorkerContainer.prototype.register` | **possible** — `writable: true, configurable: true` |

So a **hook exists and no delivery technique does**: there is nowhere to put our
leaf. This is PS-189's definition-of-done branch (b) — *"an honest 'we cannot
author this arm and here is why' closes this; a spoof that no checker observes
does not."*

**Two candidate remedies exist and both are product decisions, not a worker's:**

1. **Defer macos to the engine** (as windows already does). Measured: it *would*
   close macos, because the engine authors all twelve realms there. But
   `gpu_ext.py:761` records the engine's macos pool as **two values across 30
   seeds — a 76.9% chance two profiles share a card**, against 50.0% for our
   own two-entry `MAC_GPUS`. That trades a contradiction for a **Level 2
   (mutual unlinkability)** regression. **It does not help linux at all**: with
   the layer off, the engine reports SwiftShader in *every* realm, so deferring
   linux would spread the leak from one realm to twelve.
2. **Suppress service-worker registration.** Closes the leak completely and
   breaks every site that needs a service worker — and a browser that refuses
   `register()` is itself a novel tell.

Escalated on the ticket rather than chosen here.

---

## §7 The firefox leg (the two-engine rule, PS-16)

**A `firefox / linux` arm does not exist**, and that is a product fact rather
than an untried cell. `InvisiblePlaywright` has **no OS/platform parameter**
(product issue **#211**): Firefox presents **Windows regardless** of the declared
machine, which is why PS-186's firefox records carry
`declared_machine_honoured: false` while the chromium records carry `true`.

So the firefox leg of this defect is **not measurable on linux or macos**, and
its two `windows` records are **clean** under the same gate (exit 0). Stated
explicitly so the blank does not read as untried.

Note the consequence: whatever is decided for chromium, **the firefox arm's
service worker has not been characterised on any non-windows declared machine**,
because no such arm can be produced today.

---

## §8 What is NOT covered

* **`MAC_GPUS` pool width** — PS-183. Related, separate, deliberately not folded
  in. This is an **authorship** finding (does our card reach the page at all,
  and every reader consistently); that is a **pool width** finding.
* **The `navigatorWebdriver` discrepancy** PS-186 also surfaced. Not
  investigated here and **not** speculatively attributed to this cause.
* **`device_type=mobile`** — not selectable from this tier. Every record here is
  the **desktop** arm. A clean reading here is not evidence about any mobile arm.
* **WebGPU** — `navigator.gpu` reported **no adapter** in this container, so it
  could not be a leak vector here. On a host with a real GPU it is a **second,
  independent** graphics-identity surface that `gpu_ext` does not patch at all.
  Untested there; flagged, not claimed.
* **Firefox on linux/macos** — see §7; cannot exist today.

---

## §9 Reproducing

```bash
# The realm sweep (loopback; no exit, no credential, no third party).
.venv/bin/python -m scripts.ps189_realm_gpu -o <dir> \
    --arms linux,macos,windows --seeds 24601,5150 --layer on
.venv/bin/python -m scripts.ps189_realm_gpu -o <dir> \
    --arms linux,macos --seeds 24601,5150 --layer off

# The derived checker-matrix records, and the gate's verdict on them.
.venv/bin/python readings/ps189-2026-08-26/derive.py
.venv/bin/python -m src.services.verify.checker_cli consistency \
    readings/ps189-2026-08-26/derived-matrix/realm-matrix.chromium.linux.seed24601.json

# The live read (requires the exit; aborts rather than falling back).
.venv/bin/python -m scripts.ps189_live_creepjs -o <dir> --arms linux --seeds 24601
```

**Expected to match:** every renderer string in §1, and every gate verdict in §3.
**Expected to rotate:** the exit IP, city and ASN — this is a rotating mobile
exit, and rotation *within Poland* is the design rather than a fault.
