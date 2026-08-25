# PS-170 — re-reading pixelscan once one profile really reports one GPU

**Date:** 2026-08-25 · **Ref under test:** `origin/main` @ `3989f97` · **Checkers:** `pixelscan.net`, `creepjs` (+ 9 more)
**Artifacts:** `arm-a-postfix-layer-on.json` (61 rows) · `take-reading.sh` (the instrument that produced it)
**Baseline compared against:** `readings/ps150-2026-08-24/arm-a-baseline-layer-on.json` — the committed file, row by row, not memory.

---

## 0. Headline

| question | answer |
|---|---|
| **GPU rows — do creepjs and pixelscan now agree?** | **YES.** Both read AMD `0x00001638`. The contradiction is **closed**. |
| **`fingerprint_inconsistent`** | **UNCHANGED** — still fires, still `adverse`, byte-identical matched text. |
| **`masking_detected`** | **UNCHANGED** — still fires, still `adverse`, byte-identical matched text. |

**53 of 61 rows are byte-identical to the pre-fix baseline.** Of the 8 that moved, **6 are exit
rotation** and **2 are the GPU rows** — and the 2 GPU rows moved in the direction PS-161 claimed.

**Neither verdict moved.** Read §5 and §6 before drawing anything from that: **two bounds run in
opposite directions and both are load-bearing.**

---

## 1. The precondition gate — PASSED, and re-checked for substance

The ticket's gate, run **ref-scoped against `origin/main`**, never the working tree:

```
$ git fetch origin && git rev-parse origin/main
3989f975b43a6ce3ed2e7d7ca0a33f9f45ace9bd

$ git grep -c "ENGINE_HONOURED_PLATFORMS" origin/main -- src/services/browser/gpu_ext.py
4                                                                    # clause 1 PASS
$ git grep -c "device_type" origin/main -- src/services/browser/gpu_ext.py
3                                                                    # clause 2 PASS  <- the real gate
```

**Both clauses pass.** The ticket warned that the *previous* gate could not fail, because its symbol
was present at the very commit that failed audit. So I did not stop at symbol presence — I checked
that round 4's **structure** is on `main`:

| round-4 claim | verified on `origin/main` @ `3989f97` |
|---|---|
| a new module OWNS the value | `src/services/browser/engine_platform.py` **exists** |
| computed ONCE, before extensions are built | `process.py:416` → `engine_platform_for(profile.os_type, profile.device_type)` |
| the SAME string handed to both consumers | `process.py:531` → `build_gpu_extension(engine_platform=…)`; same value to `--fingerprint-platform` |
| the old resolver DELETED, not deprecated | `git grep engine_authors_identity_for_os_type origin/main` → **no hits in `src/` or `tests/`** |

PR #133 is **merged**. Round 4 is on `main`. The ticket was correctly released.

> **A note on the container, because it bears on reproducibility.** This agent container had **no
> clone at all** — not a clone parked on the PS-161 branch, which is the trap the ticket described.
> I cloned fresh from `amnesiadevelopment/persona`, so every ref above is a freshly fetched one and
> no local fossil could have resolved silently.

---

## 2. The instrument — provisioned, then verified, then caught lying once

**PS-14 says a live finding is a claim about the reading before it is a claim about the product.**
That rule earned its place twice here.

**The engine was ABSENT from this container.** Only Debian stock `/usr/bin/chromium`
151.0.7922.169 was present, which is **not a substitute** — a reading taken with it measures the
instrument. I provisioned the real engine rather than substituting or declaring the live half
uncovered:

```
ENGINE_DIR = /home/yatfa/.persona/engine
sha256  a5fa5e6c05cb7fa3617ec2ca642ad3cc6e586ac5249cc29edb0a602d695685f0
        ^ MATCHES the digest PS-161 recorded (a5fa5e6c…) — same binary, independently obtained
$ fpchrome.AppImage --version  ->  Chromium 148.0.7778.215
```

**The first run returned INCONCLUSIVE and it was the instrument, not the product.** All **38**
browser-tier rows came back unobtainable carrying one *identical* reason:

```
could not attach to persona's chromium over CDP on port 42687: ModuleNotFoundError: No module named 'playwright'
```

An identical failure across every cell is exactly the shape PS-14 says to distrust, and PS-161's
worker hit the same class of trap (system python instead of the venv). **The harness wrote the
record and labelled it INCONCLUSIVE rather than passing it off as a reading — the tooling behaved
correctly.** I installed `playwright` into the venv and re-ran. **No result from that first run is
used anywhere in this record.**

---

## 3. Preconditions

| constraint | status |
|---|---|
| proxied exit, mandatory, **no fallback** | **PASS** — `checker_cli` proves the exit *before* reading anything and refuses (exit 2) otherwise. There is no flag that reads a checker over a direct connection. |
| exit is Polish | **PASS** — `79.184.244.189`, **Warsaw / PL**, `AS5617 Orange Polska`, `Europe/Warsaw` |
| exit vs baseline | **ROTATED — disclosed, not swept.** Baseline was `5.173.155.60` (Warsaw, `AS39603 P4`). A different Warsaw exit is **exit-driven variance per PS-10**, not a failed comparison. It accounts for 6 of the 8 moved rows (§4). |
| credential channel | `file` — `/workspace/_secrets/test-proxy.txt` (present, non-empty, as the confirm review recorded) |
| `--allow-unsandboxed-chromium` | **REQUIRED — the waiver, disclosed.** See below. |
| GPU present | **NO** — `/dev/dri` absent. Per PS-10 that is **not** an exemption: the engine is expected to present a plausible GPU wherever it runs. |
| condition matched to baseline | engine `chromium`, `declared_machine=windows`, `seed=9001`, layer **ON** (`route=extensions`, same 10 vectors), no `--match-product-geo` (baseline's `installed[]` has no `geo`) |

**The waiver, stated plainly:** this run passed `--no-sandbox`, because this host forbids the
unprivileged user namespace the sandbox requires. **persona's own launch path passes that flag
nowhere, so this reading is not the product's default surface.** The baseline carried the same
waiver, so it does not bias the comparison — but it is inherited by every conclusion here.

**Evidence floor:** `sufficient` — 24 of 28 fingerprint-bearing rows, from 4 checkers (floor: 2
checkers / 20%). This run is entitled to be read as a reading.

---

## 4. The row-by-row comparison — 53 of 61 identical

Compared against the committed baseline **file**, on `(checker, item)`, across every field
(`state`, `adverse`, `sort`, `value`, `matched_text`, `reason`, `pattern`, `vector`).
No row was added and none disappeared.

**All 8 movers, in full:**

| # | row | baseline → new | sort |
|---|---|---|---|
| 1 | `engine-exit::observed_ip` | `5.173.155.60` → `79.184.244.189` | exit |
| 2 | `engine-exit::org` | `AS39603 P4` → `AS5617 Orange Polska` | exit |
| 3 | `ipleak.net::as_number` | `39603` → `5617` | exit |
| 4 | `ipleak.net::isp_name` | `Play` → `Orange Polska` | exit |
| 5 | `ipleak.net::observed_ip` | `5.173.155.60` → `2a01:110f:…` (v6) | exit |
| 6 | `tls.peet.ws::observed_ip` | `5.173.155.60:43007` → `79.184.244.189:21510` | exit |
| **7** | **`pixelscan.net::webgl_renderer`** | **`ANGLE (NVIDIA, … RTX 3070 (0x00002484) …)` → `ANGLE (AMD, AMD Radeon(TM) Graphics (0x00001638) …)`** | **fingerprint** |
| **8** | **`pixelscan.net::webgl_vendor`** | **`Google Inc. (NVIDIA)` → `Google Inc. (AMD)`** | **fingerprint** |

**Sorted per PS-10:** rows 1–6 are **exit-driven** — all six are `sort: exit`, all six track the
rotation, and none is `adverse`. Rows 7–8 are **fingerprint-driven** and are the subject of this
ticket. **Every other fingerprint row — including both canvas/webgl hashes — is byte-identical**
(`canvas_hash 2bcfee12…`, `webgl_hash 036072f3…`), which is what makes the two GPU rows readable
as a change in the product rather than as run-to-run noise.

---

## 5. The GPU rows — the contradiction is CLOSED

This is the question the ticket said **outranks every verdict question**, because a persisting
disagreement would mean PS-161 did not land what it claims.

| checker | row | baseline | **new** |
|---|---|---|---|
| creepjs | `gpu_vendor` | `Google Inc. (AMD)` | `Google Inc. (AMD)` — unchanged |
| creepjs | `gpu_renderer` | `…AMD Radeon(TM) Graphics (0x00001638)…` | identical — unchanged |
| pixelscan | `webgl_vendor` | `Google Inc. (NVIDIA)` | **`Google Inc. (AMD)`** |
| pixelscan | `webgl_renderer` | `…RTX 3070 (0x00002484)…` | **`…AMD Radeon(TM) Graphics (0x00001638)…`** |

**One profile, one run, one GPU.** pixelscan moved onto creepjs's value; creepjs did not move. The
second spoofer stood down, exactly as PS-161's fix describes: on `windows`/`desktop`,
`engine_platform_for('windows','desktop') = 'windows'`, which **is** in `ENGINE_HONOURED_PLATFORMS`,
so `engine_authors_identity_for_engine_platform('windows') = True` and our layer defers to the
engine. Verified directly against the module on `main`.

**The merged consistency gate agrees, and it is proven able to fail** — I ran all three arms rather
than only the one that flatters the result:

```
consistency  ps170 post-fix record   -> exit 0   FINDINGS — none. gpu_claimed: all 4 rows name the same hardware (amd)
consistency  ps150 pre-fix baseline  -> exit 1   gpu_claimed: 2 different hardware identities in one record:
                                                   amd (creepjs) ; nvidia (pixelscan)
consistency  ps143 layer-off control -> exit 0   (control arm still clean)
```

A gate that goes red on the pre-fix record and green on the post-fix one, in the same invocation
style, is a differential — not an assertion.

---

## 6. Per verdict — what moved

### `fingerprint_inconsistent` — **UNCHANGED**

`state: read`, `adverse: true`, matched text `"Your Browser Fingerprint is inconsistent"` —
byte-identical to baseline. The complementary row `fingerprint_consistent` remains `absent` in both.

**Closing the GPU contradiction did not clear this verdict.**

### `masking_detected` — **UNCHANGED**

`state: read`, `adverse: true`, matched text `"Masking detected"` — byte-identical to baseline.
**Expected**, and recorded as the ticket instructed: nothing links this verdict to the GPU vector.
No movement is read into it, and **no cause is attributed to it. It stays open.**

### Every other adverse row — **UNCHANGED**

All 10 adverse rows across the matrix hold their baseline state:
`pixelscan::{automation_detected, proxy_detected, timezone_spoofed}` absent,
`bot.sannysoft::{webdriver_present, phantom_js}` absent, `iphey::not_trustworthy` absent,
`rebrowser::detected` and `deviceandbrowserinfo::bot_verdict_positive` unobtainable in both.

---

## 7. ⚠️ Two bounds, in opposite directions. Both belong in the record.

### Bound 1 — an unchanged verdict is **NOT** evidence against PS-161

PS-159 measured a **stock** Chromium — carrying none of persona's code — getting **both** verdicts
on this same exit, and removing automation, fixing the timezone and hiding the renderer moved
neither. The renderer axis could not be removed at all: **this container has no real GPU, and
hiding the renderer is itself a tell.**

So an unchanged `fingerprint_inconsistent` here is **fully consistent with the checker flagging any
browser in this environment**, and this reading cannot separate those. Settling it needs a machine
with a real GPU, which nobody has. **Do not read §6 as "the fix didn't work."** The fix's own
claim — one profile, one GPU — is **measured as delivered** in §5.

### Bound 2 — a clean windows/desktop reading is **NOT** evidence the `device_type` axis is closed

**This reading is the `windows` / `desktop` arm.** The whole ps143/ps150 corpus is
windows/desktop, so it is the only arm the baseline permits comparing against — and it is
**exactly the path round 4 does *not* touch.**

Measured directly on `main`, the two arms resolve differently:

```
windows/desktop -> engine_platform=windows  engine_authors=True    <- THE ARM READ HERE
windows/mobile  -> engine_platform=linux    engine_authors=False   <- the arm round 4 fixed. NOT READ.
```

The `windows`+`mobile` pair — the one whose host leak failed round 3's audit — **was not read by
this run and could not be**: this checker tier takes `--declared-machine`, and exposes no
`device_type` selector. A clean reading here **can coexist with a mobile-arm defect**, and it must
not be reported as "PS-161 landed, contradiction closed" without this sentence attached.

**A third bound, mine, which the ticket did not ask for.** The desktop arm's deferral follows from
`engine_platform='windows'` being engine-honoured — a property established by PS-161 **round 2**,
not round 4. This reading is taken against `main` @ `3989f97`, which carries *all four rounds*, so
it **cannot attribute the movement to round 4 specifically.** What it establishes is that the
merged PS-161 work closes the contradiction **on the arm read**. Attribution to a single round is
not in this measurement, and I am not claiming it.

---

## 8. Reproducibility — regenerated with the committed instrument

The ticket requires the record be reproducible **from the script committed beside it**, because a
reading produced by an earlier version of its own instrument has already cost an audit round here.

`take-reading.sh` is that script, committed in this directory. I re-ran the **committed file
itself** (not the shell history that produced the record) and compared all 61 rows:

```
$ ./readings/ps170-2026-08-25/take-reading.sh /tmp/ps170-repro.json
REPRO vs COMMITTED RECORD: 60 identical / 1 differ (of 61)

>> tls.peet.ws :: observed_ip
     RECORD  "79.184.244.189:21510"
     REPRO   "79.184.244.189:21635"        <- ephemeral TCP source port. Same exit IP.
```

**60 of 61 byte-identical**, six minutes apart, through the same exit. The single difference is the
kernel-assigned source port of the TLS connection — not a property of the profile.

**Every row this ticket turns on re-derived exactly:**

| row | reproduced |
|---|---|
| `pixelscan::webgl_renderer` | ✅ `ANGLE (AMD, … (0x00001638) …)` |
| `pixelscan::webgl_vendor` | ✅ `Google Inc. (AMD)` |
| `creepjs::gpu_renderer` | ✅ `ANGLE (AMD, … (0x00001638) …)` |
| `creepjs::gpu_vendor` | ✅ `Google Inc. (AMD)` |
| `pixelscan::fingerprint_inconsistent` | ✅ `true` |
| `pixelscan::masking_detected` | ✅ `true` |

---

## 9. What this settles, and what it does not

**Settles:**
- One profile now reports **one** GPU to both checkers that read it, on the windows/desktop arm.
  PS-161's central claim is **measured as delivered on that arm**, live, through a proxied exit.
- Closing that contradiction **does not clear** `fingerprint_inconsistent`.
- `masking_detected` **did not move** — as expected, and still unattributed.

**Does not settle:**
- **Why** `fingerprint_inconsistent` fires. Bound 1: this environment flags a stock browser too, and
  the GPU axis cannot be de-confounded without real hardware.
- **Anything about the `windows`+`mobile` arm.** Bound 2: not read, not readable from this tier.
- **Which PS-161 round** produced the movement. Bound 3.
- `masking_detected`'s cause. Explicitly out of scope; it stays open.

**Nothing in this ticket changed any product code.** This is a measurement; the only files added are
this record, the reading, and the instrument that produced it.
