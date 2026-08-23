# PS-128 — the live Chromium reading, taken 2026-08-23

One session, one provisioned environment, four questions. Read in order: each
answer below is read against the ones before it.

Everything here is a **measurement**. Where a question could not be answered,
this document says so and says why, rather than recording the absence as a
pass. Two cautions were carried in from the ticket and both turned out to be
load-bearing: a reading below the evidence floor is *inconclusive, never
clean*, and a unit test showing an internal buffer differs is *necessary and
not sufficient*.

---

## 0. The environment — and what it cost to make real

**The Chromium engine was genuinely absent, and provisioning it was the wall
PS-97 could not pass.** `~/.persona/engine/` held a stub — `builds.json` and
`.engine-complete` — and **no binary**. That stub is worth naming: the two
files it contained are exactly the completion markers, so the obvious reading
("the markers are there, the engine is installed") is wrong. The project's own
gate was not fooled — `updater.is_installed()` returned `False`, because it
checks the binary's size before it trusts a marker.

Provisioned through the project's **own** verified path, not by hand:

| | |
|---|---|
| route | `services/engine/updater.download_engine` (digest gate + `_install_linux`) |
| tag | `148.0.7778.215` |
| asset | `ungoogled-chromium-148.0.7778.215-1-x86_64.AppImage`, 188,811,768 B |
| sha256 | `a5fa5e6c05cb7fa3617ec2ca642ad3cc6e586ac5249cc29edb0a602d695685f0` (verified by the installer, not by me) |
| result | `is_installed() == True`, `--version` → `Chromium 148.0.7778.215` |

Release metadata had to come from `gh api` rather than the module's own
`fetch_latest_full()`: GitHub's unauthenticated API was **rate-limited on this
container's direct egress** (`159.195.144.196`, 60/60 used). Worth recording
because `fetch_latest_full()` returned `("", "", "")` — three empty strings, no
exception — so a caller that did not check would have installed nothing and
reported nothing. The bytes themselves were fetched and digest-verified by the
project's own downloader.

Two host facts, both already handled by code in the tree:

- **No FUSE** (`/dev/fuse` absent, no `fusermount`), so the AppImage cannot
  self-mount. `--appimage-extract-and-run` is required, and both
  `process.py:557` and `chromium_tier.py:307` already pass it. **Not a defect.**
- **The Chromium sandbox cannot start here.** `unshare(CLONE_NEWUSER)` returns
  `EPERM`; a bare launch dies `FATAL: No usable sandbox!` and core-dumps.

### ⚠️ The caveat that qualifies every Chromium row below

`--allow-unsandboxed-chromium` was **required** to take these readings. By that
flag's own documentation, a reading taken with it **is not the product's
default surface**, and the record tags it. It is stated here rather than left
in a JSON field because it bounds every Chromium answer in this document.

### The exits — proven, and recorded per reading

No reading was taken over an unproven exit and none fell back to a direct
connection. The exit **rotated between runs**, which is the design (a fixed
exit would permanently hide a coupling between a fingerprint and an address),
so it is recorded per run rather than once:

| run | exit | country |
|---|---|---|
| run 1 (matrix, both engines) | `95.49.113.111` | Warsaw / PL / Orange Polska |
| run 2 (chromium re-run) | `79.191.76.230` | Warsaw / PL / Orange Polska |
| run 3 (chromium, order reversed) | `79.191.76.230` | Warsaw / PL / Orange Polska |

---

## 0b. Two harness defects, found because the run refused itself

**The first attempt refused all four configurations, and the message blamed the
credential and the connection. Both were fine.** This is recorded in full
because the failure looked exactly like a dead proxy, and reporting it as one
would have stalled this ticket the way PS-97 was stalled.

Measured through the same credential, in the same minute:

| probe (through proxy, `socks5h`) | result |
|---|---|
| `ipwho.is` | `95.49.113.111` · **Poland** · Warsaw |
| `api.ipify.org` | `95.49.113.111` |
| `ipinfo.io` | **HTTP 429 Rate limit hit** |
| `ipinfo.io` **direct** (control) | 200 — returns our real netcup address |

`ipinfo.io` works direct and 429s *through the exit*, so the rate limit
attaches to the exit's **shared mobile address**. It is not ours to clear, not
retryable, and rotation is the operator's job from the host. Two places hung on
that single oracle:

1. **`exit_guard.observe_exit`** — the Python fetcher's proof. One dead oracle
   refused the whole run.
2. **`browser_tier._observe_engine_exit` / `ENGINE_EXIT_CHECKER`** — the
   engine-side proof, which is the browser tier's *precondition*. Its failure
   marked **all 37 browser rows unobtainable in all 4 configurations** (34 rows
   reading "the engine's exit observation carried no country").

Both now try providers in order until one **answers**. The property carefully
preserved, and asserted by a test:

> This is redundancy for **reachability only**. A provider that answers is
> **authoritative** — a wrong-country answer still ends the run and is *never*
> re-asked against a friendlier provider. Shopping for an agreeable oracle is
> how a fallback becomes a way to launder a bad exit.

`api.ipify.org` is deliberately **excluded**: it answers 200 with no country,
which this guard correctly treats as unproven, so listing it would refuse
healthy runs.

The second provider's dialect is normalised (`country: "Poland"` with the code
in `country_code`; `connection.isp`; `timezone.id`). This mattered more than it
looks: read with ipinfo's key layout, ipwho.is yields `"Poland" != "PL"`, so the
guard would refuse a **healthy Polish exit while reporting the wrong country** —
worse than the 429 it was added to survive, because the message would be
actively false.

**These were fixed because they are the instrument, not the product.** No
change was made to masking, to perturbation magnitude, or to anything a checker
reads.

---

## 1. Pixelscan on Chromium — the whole matrix, not the headline

**A real persona profile on Chromium does NOT read fully clean.**

Every row, as read. `absent` for an adverse row is the *good* outcome (the
negative verdict did not appear); `absent` for a benign row means the value was
not published.

### chromium / windows / seed 1337 — evidence SUFFICIENT (24/27 rows, 4 checkers)

| row | state | value |
|---|---|---|
| `masking_detected` | **read** | **True** ⚠ |
| `fingerprint_inconsistent` | **read** | **True** ⚠ |
| `timezone_spoofed` | **read** | **True** ⚠ |
| `timezone_from_js` | read | **`Africa/Abidjan`** ⚠ |
| `webgl_renderer` | read | `-` ⚠ |
| `webgl_vendor` | read | `-` ⚠ |
| `canvas_hash` | read | `f564d214e7f819f1ea5db5324aa5e5d0` |
| `fingerprint_consistent` | absent | — |
| `geo_country_city` | absent | — |
| `automation_detected` | absent | *(good)* |
| `proxy_detected` | absent | *(good)* |
| `webgl_hash` | absent | — |

### firefox / windows / seed 4242 — **fully clean**

| row | state | value |
|---|---|---|
| `fingerprint_consistent` | **read** | **True** ✅ |
| `masking_detected` | absent | *(good)* |
| `fingerprint_inconsistent` | absent | *(good)* |
| `timezone_spoofed` | absent | *(good)* |
| `proxy_detected` | absent | *(good)* |
| `automation_detected` | absent | *(good)* |
| `geo_country_city` | read | `Poland / Warsaw` |
| `timezone_from_js` | read | `Europe/Warsaw` |
| `webgl_renderer` | read | `ANGLE (Intel, Intel(R) HD Graphics 400 Direct3D11 vs_5_0 ps_5_0), or similar` |
| `webgl_vendor` | read | `Google Inc. (Intel)` |
| `webgl_hash` | read | `70454ee84609f3f5a47231aa48437133` |
| `canvas_hash` | read | `e74bab01ec3cfc301e5a3bee1080495f` |

### firefox / windows / seed 1337 — NOT clean

`masking_detected=True`, `fingerprint_inconsistent=True`. Geography and
timezone are **correct** (`Poland / Warsaw`, `Europe/Warsaw`) and the renderer
is plausible (`ANGLE (AMD, Radeon R9 200 Series …)`).

### What this says about PS-119 — **contradicted, not confirmed**

PS-119 concluded that the earlier `masking_detected` was an artifact of the
measuring harness: it passed no locale, so the request advertised the host's
language while `navigator.language` reported the profile's, and pixelscan
caught the contradiction. The ticket asked for that to be confirmed or
contradicted by a real reading.

**It is contradicted.** These readings were taken through the normal launch
path with the masking layer installed and complete (`route=extensions`, 10
extensions on Chromium; `route=init_scripts`, 3 on Firefox) — not through the
locale-less harness — and `masking_detected` **still reads True** on Chromium
seed1337 and on Firefox seed1337.

The honest boundary on that claim: `masking_detected` is **not** uniform across
seeds. Firefox seed4242 read fully clean while Firefox seed1337 did not, on the
same engine, same exit, same declared machine, minutes apart. So the missing
locale cannot be the whole explanation, but "Chromium's masking is detected"
is not established as a stable property either — the variable is the **seed**,
and identifying which seed-derived value trips it is a separate measurement
this ticket does not scope.

### Two Chromium rows worth separating (per PS-10's standing rule)

`webgl_renderer` and `webgl_vendor` both read `-` on Chromium, where Firefox
published a plausible ANGLE string. Per PS-10 a **string** red and a **render**
red have different fixes and must not be collapsed. This is the *string* side,
and the value is not an implausible renderer but an **absent** one.

`timezone_from_js` reading `Africa/Abidjan` against a `Europe/Warsaw` exit is
the sharper of the two: it is UTC+0, i.e. the container's own zone rather than
the exit's, and it sits directly beside `timezone_spoofed=True`. persona
derives a profile's timezone from proxy geography, so this is a coherence
failure on the Chromium path — reported here, not fixed, since it is well past
"cheap and obvious".

---

## 2. The two-seed WebGL reading — stated per engine, never blended

### Firefox — **STILL COLLIDES**

| seed | CreepJS `webgl_pixel_hash` | CreepJS `webgl_image_hash` |
|---|---|---|
| 4242 | **`51df3565`** | `911e9c23` |
| 1337 | **`51df3565`** | `5e9c98db` |

Byte-identical, and it is **the exact value PS-97 recorded** (`51df3565` under
both seeds). Meanwhile `image_hash` *does* move across the same two seeds — so
the profiles are genuinely different and this one vector is flat.

**This is expected and is not a failure of PS-97's fix.** `webgl_ext` is a
Chromium extension and does not run on the Firefox path at all, so the fix
shipped in PR #79 was never in force here. The reading confirms the vector is
still linkable **on Firefox**; it says nothing about whether the fix works.

### Chromium — **UNANSWERABLE this session**

| seed | result |
|---|---|
| 1337 | `f801a1b3` (3 runs, identical each time) |
| 4242 | **crashed before CreepJS was reached — 3 runs, every time** |

A pair cannot be compared when one arm never renders. **The Chromium half of
PS-97's verification remains open**, and it is deliberately not reported as
passing: `f801a1b3` differs from Firefox's `51df3565`, but that is a
cross-engine difference, not a two-seed one, and reading it as the answer would
be exactly the error this ticket exists to correct.

### The crash is itself a finding — reproducible and seed-correlated

The ticket said a renderer crash is a product problem to report with evidence,
not a nuisance to retry past. It reproduced **3 out of 3**:

| run | order | seed 4242 | seed 1337 |
|---|---|---|---|
| 1 | 4242 first | **crash** (7/27 rows) | ok (24/27) |
| 2 | 4242 first | **crash** (7/27 rows) | ok (24/27) |
| 3 | **1337 first** | **crash** (7/27 rows) | ok (24/27) |

Failure mode: `TargetClosedError: Page.inner_text: Target page, context or
browser has been closed`, the session ending mid-run with **9 CreepJS rows
recorded as NEVER ASKED** rather than as failed — the PS-110 distinction, doing
its job.

Run 3 varied **one axis** — the order — precisely to separate "first launch on
a cold container" from "this seed". Seed 4242 crashed when it ran *second*, and
seed 1337 survived when it ran *first*. **The crash follows the seed, not the
position.** Both runs 2 and 3 also sat on a different exit from run 1, so it is
not exit-coupled either.

This meets the owner's stated gate directly: a profile that cannot complete a
reading on a major checker is a product problem. Reported, not fixed — the
cause is not cheap or obvious, and guessing at it before the measurement exists
is what the boundaries forbid.

---

## 3. PS-90's internal probe, read against the live records

The ticket warned these two can disagree, and that an internal probe seeing two
different buffers while a checker reads one identical hash is *exactly* the
failure PS-97 existed to catch. Measured on the same two seeds, one axis varied
(the seed; every other input is the pinned baseline profile), 0 errors:

| side | seed 4242 | seed 1337 | agree? |
|---|---|---|---|
| **internal** `webgl.readback` digest | `1444116715` | `1471895271` | **differ** ✅ |
| **live** CreepJS `webgl_pixel_hash` (firefox) | `51df3565` | `51df3565` | **identical** ❌ |

**They disagree, and that disagreement is the finding.** The internal buffers
are genuinely different; the difference does not survive the trip to the page.
This is the precise shape PS-97 described, now confirmed **live on Firefox**
rather than argued — and it is the concrete demonstration of why a unit test
asserting "the internal buffer differs" would have reported success here.

It also re-proves the ticket's own caution: reading either side alone gives the
wrong answer. The internal probe alone says the vector is fine.

---

## 4. The baseline — prediction confirmed exactly, plus two movements nobody predicted

Re-recorded through the documented workflow
(`xvfb-run -a python -m src.services.verify.baseline_cli record`), **not
hand-edited** — editing a recorded digest fabricates the reading the machinery
exists to police.

### The predicted value: confirmed

| | |
|---|---|
| committed | `3733647300` |
| **recorded** | **`1121969883`** |
| predicted | `1121969883` |
| **match** | **exact** ✅ |

Recorded on the same `engine_build` (`firefox-20`), same `app_version`
(`2.9.18`), same pinned seed (`1042768975`), same `bytes` (4096) and `mid`
(3072). Only the digest moved, which is what a mechanism change should look
like.

### But the diff carries **three** changes, not one

The ticket said disagreement is the more interesting result and is to be
reported as a finding rather than quietly reconciled. The digest agreed — but
two other probes moved, and neither was predicted. Both are reported here
rather than absorbed into the commit. **Neither is declared env-sensitive**, so
neither can be waved off as a property of this host, and **I changed no masking
or audio code in this ticket.**

**(a) `audio.periodicWave`: `35.749972` → `35.749988`**

`35.749972` is a value this codebase documents by name: `audio_ext.py:263`
records it as what `audio.digest` read on **four profiles with four distinct
seeds** — identical to six decimal places, which on a continuous vector is not
coincidence. It is the *unseeded collision* value, from when the Firefox arm
returned before audio perturbation was applied.

So the committed baseline was recorded while that defect was live, and PS-73 /
PS-78 landed afterwards. This movement is a **fix becoming visible**, not a
regression — the baseline was pinning a broken value.

**(b) `masking.workerConstructor`: `function Worker() { [native code] }` → 2109
characters of raw patch source**

This is the one to look at. `worker_wrap.py:387` states the standard in its own
words: a `toString()` returning raw patch source is a detection marker, where
*"every real engine returns `[native code]`"*. The new recording is the raw
source, in the **window realm**. The worker realm still returns `[native
code]`, so the cloak is working on one realm and not the other.

Cause, as far as measurement supports: PS-78 was the first change to deliver
these spoofs to the Firefox **window** realm at all, and it landed after this
baseline was recorded — so this is very likely a real, newly-introduced
exposure rather than a recording artifact. It is **PS-48's exact territory**
(*"stop the spoof registry publishing its own source text"*), arriving on the
other engine.

I am reporting rather than fixing it: it is outside this ticket's boundary
("fixing whatever the reading turns up, beyond reporting it"), it is not cheap
or obvious, and it wants its own ticket with this reading attached. **The
committed baseline now records the observed value, so it is a truthful record
of what the code produces today — it is not an endorsement of that value**, and
the finding is stated here so re-recording does not silently bless it.

---

## What is answered, and what is not

| question | answer |
|---|---|
| Does a real Chromium profile read clean on pixelscan? | **No** — `masking_detected`, `fingerprint_inconsistent`, `timezone_spoofed`, `timezone_from_js=Africa/Abidjan`, empty renderer/vendor. Every row stated above. |
| Was PS-119's harness explanation confirmed? | **Contradicted** — detection persists on a real launch. Bounded: it is seed-dependent, not uniform. |
| Are two seeds still linkable on the WebGL readback? | **Firefox: yes, still `51df3565`** (expected — `webgl_ext` never ran there). **Chromium: unanswerable**, one arm crashed 3/3. |
| What is the baseline digest now? | **`1121969883`** — prediction confirmed exactly. |

**Left open, deliberately:**

- The Chromium two-seed answer — blocked on the seed-4242 crash.
- The seed-4242 renderer crash — reproducible, seed-correlated, order- and
  exit-independent. Needs its own ticket.
- `masking.workerConstructor` leaking patch source in the Firefox window realm.
- Chromium's `timezone_from_js` disagreeing with its own exit.
- Which seed-derived value trips `masking_detected` on some seeds and not
  others.

Nothing above was tuned to make a number come out right, no perturbation
magnitude was changed, and no reading was taken over an unproven exit.

---

## Reproducing this

```bash
# engine (once) — provisions ~/.persona/engine/fpchrome.AppImage, digest-verified
python -c "from src.services.engine import updater; ..."   # see §0

# the matrix, both engines, two seeds
python -m src.services.verify.checker_cli read \
    --engine both --declared-machine windows --seed 4242,1337 \
    --allow-unsandboxed-chromium -o readings/

# the baseline
xvfb-run -a python -m src.services.verify.baseline_cli record

# PS-90's internal probe on the same two seeds
xvfb-run -a python scripts/ps90_crossread.py
```

Records in this directory:

- `run1-matrix/` — 4 records, both engines, both seeds, exit `95.49.113.111`
- `run2-chromium-rerun/` — chromium, both seeds, exit `79.191.76.230`
- `run3-chromium-order-reversed/` — chromium, **1337 first**, the one-axis test
- `ps90-internal-readback-crossread.json` — the internal probe, both seeds
