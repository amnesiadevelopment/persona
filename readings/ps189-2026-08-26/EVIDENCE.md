# PS-189 — the ServiceWorker realm has no author

**Date:** 2026-08-26
**Ticket:** PS-189 (Theme 1, masking invisibility; roadmap `undetectable-masking`)
**Branch basis:** `origin/main` @ `880fd14`
**Instruments (committed beside these records):**
`scripts/ps189_realm_gpu.py` (loopback realm sweep),
`scripts/ps189_live_creepjs.py` (live checker read through the proxied exit,
extended in round 2 with the page/worker/webgl2/iframe **controls**),
`readings/ps189-2026-08-26/derive.py` (realm sweep -> checker-matrix records).

**Live records, in the order they were taken:**

| directory | what it is |
|---|---|
| `live/` | **round 1 — superseded.** Scrape only, no control. Hand-trimmed after the run; see §4.3. Kept as the evidence of that defect, not relied on. |
| `live-round2-a-controls/` | round 2 A — scrape **plus** page and worker controls, full `page_text`. |
| `live-round2-b-realms/` | round 2 B — as A, plus `page_webgl2` and two iframe realms, through a different exit. |

Round 2 exists because the review found round 1's single live record
**contradicting** the finding it was filed as supporting. §4 is the
reconciliation, and it is measured rather than argued.

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
read the live site through the proxied exit.

> **⚠️ ROUND 1 GOT §4 WRONG, AND THE CORRECTION IS THE POINT OF THIS SECTION.**
> Round 1 captured only creepjs's *rendered text*, saw the SwiftShader string in
> a **main-thread** section, and cited that as *corroboration* of a finding that
> says the main thread carries **our** card. That is the opposite of what it
> shows. Round 2 answers it with a **control** instead of an inference, and the
> answer changes the finding's *scope* while leaving its *mechanism* intact.

### §4.1 The page-realm control — the measurement round 1 lacked

Round 1's record could not answer *"did our card reach the live main thread?"*
at all: it held no `getParameter` probe, only a scrape. So the divergence was
unresolvable from the record, exactly as the review said.

Round 2 takes the controls **in the same run, on the same origin, against the
same page that produced the scrape**, immediately after it. The probe is
**imported from `ps189_realm_gpu.py`, not re-typed**, so a divergence cannot be
an artefact of two different probes. Two independent runs, two different exits:

| realm, on the LIVE creepjs origin | run A | run B |
|---|---|---|
| `page` (WebGL1 `getParameter`) | **our card** | **our card** |
| `page_webgl2` (separate prototype) | *not probed* | **our card** |
| `iframe_about_blank` | *not probed* | **our card** |
| `iframe_srcdoc` | *not probed* | **our card** |
| blob `Worker` (OffscreenCanvas) | **our card** | **our card** |

"our card" = `ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)`,
verbatim, in every cell above.

**So hypothesis (a) is falsified by measurement**: there is no second surface
our layer fails to reach. Our card reaches the live main thread, the WebGL2
prototype, and child frames — *on the origin where the defect was found*, not on
loopback. The `page_webgl2` and `iframe_*` arms are in the table because they
are the two classic routes around a MAIN-world patch; both were checked rather
than assumed.

### §4.2 What the live page nevertheless renders — the honest widening

In those same two runs, creepjs rendered the **SwiftShader** string in **both**
its `ANGLE (` rows, and our card appeared **nowhere** in the page text (`0`
occurrences of `Mesa`/`UHD Graphics 630` across the full 4,107 chars — full text
now committed, so this is re-checkable rather than asserted).

**This is a real widening of the finding and it is stated as one.** The review
is right that the leak is not confined to one *rendered section*: creepjs's
headline main-thread `WebGL` row displays the host value too. What round 2 adds
is *where that value comes from* — **not** from a main-thread read, because the
main thread demonstrably returns our card at that same instant.

So the finding is now stated on two axes, which round 1 conflated:

* **Origin (unchanged, and still exactly one):** the `service_worker` realm is
  the only realm that reads the host value. Eleven page-reachable realms carry
  our card, on loopback *and* — for the five re-checked above — live.
* **Blast radius (widened):** the value leaked by that one realm is displayed by
  creepjs in **more than one section**, including the main-thread `WebGL` row a
  reader would take for a main-thread reading.

**A weaker corroboration, kept with its hedge attached.** The live page also
renders `ServiceWorkerGlobalScope` as a scope label directly above the first
leaked `gpu:` row, and it is the only worker-scope label on the page. That is
consistent with the origin axis — but it is read by the script's
`nearest_heading` heuristic, which simply takes the nearest preceding short
line, and the script says of itself that *"a heading guessed this way is a lead,
not a proof."* Round 1 leaned on this harder than it can bear. It is retained
here as a **lead only**; the origin axis rests on the loopback realm sweep (§1)
and the layer-off control, not on this label. Note also that both occurrences
report the same `nearest_heading` (`Google Inc. (Google)`), which is itself a
demonstration of the heuristic's limits rather than a section identification.

Both leaked rows carry creepjs's own `confidence: moderate` marker, its
aggregation annotation. **The exact internal route inside creepjs is not claimed
as proven** — it is a third party's code and this evidence does not run it under
instrumentation. What *is* proven is the load-bearing half: the main-thread
value creepjs displays is **not** what a main-thread `getParameter` returns on
that page, so the row is derived rather than directly read.

**Why this does not change the remedy.** The origin axis is what a fix acts on,
and it is unmoved: one unauthored realm. The blast-radius axis raises the
*severity* — the host card reaches the row a reader trusts most — which is an
argument for the escalated decision in §6 being taken, not deferred.

### §4.3 Provenance defect in the round-1 record — disclosed

The round-1 record `live/live-creepjs.json` carries a key,
`page_text_excerpt_around_scope_label`, that **no instrument in this repository
emits** — `grep` finds it in no script and in `derive.py` neither. The committed
script writes `page_text` (the full text); the round-1 record has **no**
`page_text`, only a 1,000-character excerpt under that hand-made key, while
declaring `page_text_chars: 4106`.

That record was therefore **edited after the run**, and ~76% of the page text it
measured is not in it. The `angle_occurrences` list *was* computed over the full
text before the trim, so round 1's "only two occurrences" claim is not
contradicted — but it was **not re-checkable from the artefact**, and a review
grep over that record necessarily covered 24% of the page.

It is left committed and labelled rather than quietly replaced, because deleting
it would erase the evidence of the defect. **Round 2's records supersede it**
and carry the full `page_text` verbatim.

**Exits, recorded per record and never as a constant** (PS-186 measured 5
distinct ASNs across 8 records):

| observation | IP | city | ASN |
|---|---|---|---|
| round 1, pre-run guard | `109.243.71.202` | Dębowiec | `AS39603 P4 Sp. z o.o.` |
| round 1, linux / 24601 | `31.186.220.36` | Mokotów | `AS9141 P4 Sp. z o.o.` |
| round 2 A, pre-run guard | `5.173.151.74` | Warsaw | `AS39603 P4 Sp. z o.o.` |
| **round 2 A, linux / 24601** | `89.151.42.41` | Gdynia | `AS29314 VECTRA S.A.` |
| **round 2 B, linux / 24601** | `46.205.197.221` | Warsaw | `AS12912 T-Mobile Polska S.A.` |

Round 2 alone added **two ASNs that appear nowhere in round 1**, which is why an
ASN is never written as a constant. That the two runs reached the same verdict
through *different networks* is also what makes §4.1 a reproduction rather than
a single observation.

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

* **The macos half has NO live confirmation.** Every live record here is
  `chromium / linux / seed24601`. The macos face of this finding — the engine
  authoring the service-worker realm — rests on **loopback only**, plus the
  layer-off control in §1. PS-186's live macos record is consistent with it, but
  that record predates this instrument and carries no realm control. So the
  macos claim is one measurement short of the linux claim, and is stated as
  such rather than inheriting the linux arm's live confirmation.
* **One arm, one seed, live.** The two round-2 runs are both `linux/24601`,
  differing in exit and in which realms they probed. Seed 5150 was read on
  loopback only. Two runs through two different ASNs is a reproduction of the
  *linux/24601* cell, not of the matrix.
* **creepjs's internal derivation is not instrumented.** §4.2 proves the row it
  renders for the main thread is *not* what a main-thread `getParameter`
  returns; it does **not** prove which of its internal paths produced it. That
  would need instrumenting a third party's code, which this evidence does not do.
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

> **⚠️ USE `.venv/bin/python` FOR THE LIVE COMMANDS — `python3` WILL NOT WORK.**
> `.venv` is the repository's own virtualenv, per README lines 124-126. It is
> **gitignored and NOT provisioned by `install.sh`** — it is created by the
> reader, so whether it exists is a fact about your container and not about this
> repository. Round 2 asserted it "does exist"; that was true of the seat that
> wrote it and false in the reviewer's container and in the round-3 container,
> where `ls .venv/bin/python` returns *No such file or directory*. Both readings
> were half right, so **create it rather than assume it**:
>
> ```bash
> python3 -m venv .venv
> .venv/bin/pip install --prefer-binary -r requirements.txt
> ```
>
> What is NOT container-dependent is why the live commands need it: **PySocks**
> (`requirements.txt:26`) is what `exit_guard` uses to reach the proxy, and the
> bare `python3` on these containers does not carry it. Under it the live read
> does not merely warn, it **aborts**:
>
> ```
> ExitNotProven: could not observe the exit through the proxy — 2 provider(s)
> tried (https://ipinfo.io/json: ModuleNotFoundError: No module named 'socks';
> https://ipwho.is/: ...), none answered. Refusing to fall back to a direct
> connection.
> ```
>
> That abort is the exit guard working correctly — it will not silently take the
> reading over a direct connection — but it means a reader who substitutes
> `python3` gets **no live record at all**. The pure-loopback and offline
> commands below run fine under either interpreter.

```bash
# The realm sweep (loopback; no exit, no credential, no third party).
.venv/bin/python -m scripts.ps189_realm_gpu -o <dir> \
    --arms linux,macos,windows --seeds 24601,5150 --layer on
.venv/bin/python -m scripts.ps189_realm_gpu -o <dir> \
    --arms linux,macos --seeds 24601,5150 --layer off

# The derived checker-matrix records, and the gate's verdict on them.
# OFFLINE — no proxy, no PySocks, so plain `python3` is what is written here.
# Both were re-run under bare `python3` in round 3; `derive.py` is idempotent
# (it rewrites the six derived records byte-identically, so re-running is safe).
python3 readings/ps189-2026-08-26/derive.py
python3 -m src.services.verify.checker_cli consistency \
    readings/ps189-2026-08-26/derived-matrix/realm-matrix.chromium.linux.seed24601.json

# The live read (requires the exit; aborts rather than falling back).
# .venv is MANDATORY here — see the warning above.
.venv/bin/python -m scripts.ps189_live_creepjs -o <dir> --arms linux --seeds 24601

# The test suite (offline).
python3 -m pytest tests/test_ps189_service_worker_realm.py \
                  tests/test_verify_matrix_consistency.py -q
```

**Expected to match:** every renderer string in §1, every gate verdict in §3, and
the **page-realm control returning our card** in §4.1 — that last one is the
assertion the whole reconciliation rests on, so it is the one to re-run first.
**Expected to rotate:** the exit IP, city and ASN — this is a rotating mobile
exit, and rotation *within Poland* is the design rather than a fault. Round 2
alone saw two ASNs absent from round 1.
