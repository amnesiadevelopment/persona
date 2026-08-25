# PS-161 — whose GPU is it, and does the engine's own one vary per profile?

Measured 2026-08-25 at the worker seat, against `origin/main` @ `8b29b65`.
Record: `engine-gpu-identity.json` (M1/M2 grid, layer-off, 3 arms x 3 seeds)
plus `layer-on-differential.json` (both layer states, seed 9001, 3 arms).
Script: `scripts/ps161_engine_gpu_identity.py`.

**Nothing here implements PS-161's fix.** The (a)/(b) choice is the owner's and
this is the investigation it should be made *from*. Two of the four findings
below correct the ticket, and one corrects this record's own instrument.

---

## 0. Scoping obligation: THE ENGINE IS PRESENT — the ticket is stale on this

PS-161 says the engine is "verified absent from the agent container", and the
confirm-review says `~/.persona/engine/` "holds only `builds.json` — no
AppImage". **Both are now false**, and this was checked before anything was
attributed to it:

| | |
|---|---|
| binary | `~/.persona/engine/fpchrome.AppImage`, 188,811,768 bytes |
| magic | `7f 45 4c 46 02 01 01 00 41 49 02` — ELF64 + `AI\2` (AppImage type 2) |
| `version.txt` | `148.0.7778.215` |
| `.engine-complete` | `ok` |
| sha256 | `a5fa5e6c05cb7fa3617ec2ca642ad3cc6e586ac5249cc29edb0a602d695685f0` |

So `fingerprint-chromium/148` was available and **every reading below was taken
under the product's own engine.** No governed install was needed; system
chromium 151 was never launched (PS-14).

⇒ **PS-161's "record the live half as not covered, with the engine-availability
reason" escape is NOT available**, and this record does not use it. What remains
uncovered is stated in §6 — for a different reason, stated plainly.

Instrument pre-checks: Xvfb present; `/dev/shm` = 1 GiB, above
`MIN_DEV_SHM_BYTES` (256 MiB), so PS-133's `--disable-dev-shm-usage` workaround
was **not** engaged and no cell ran on a waived surface.

---

## 1. ⚠️ CORRECTION — the corpus is the WINDOWS arm, not the linux arm

PS-161 states, and the confirm-review repeats and builds on, that *"every
`0x1638` reading in this ticket is from the LINUX arm"*. The confirm-review
reasons from it: *"a linux-only result would answer your question for one arm
and be silently wrong for the others."*

**Every record in the corpus is `declared_machine: "windows"`, `seed: 9001`.**
Audited all four:

| record | declared_machine | seed | layer |
|---|---|---|---|
| `ps143/arm-a-layer-on.json` | **windows** | 9001 | on |
| `ps143/arm-b-layer-off.json` | **windows** | 9001 | off |
| `ps150/arm-a-baseline-layer-on.json` | **windows** | 9001 | on |
| `ps150/arm-b-geo-gap-closed.json` | **windows** | 9001 | on |

**There is no linux reading in the corpus at all.** The instruction to measure
per arm was right; its factual premise was inverted. This matters concretely:
the first cell run here was `linux/4242`, chosen *because* the ticket said the
corpus was linux — it was never like-for-like with `ps143`, and reporting it as
a re-measurement of the header rationale would have compared two different arms
and called the difference a finding. The baseline was re-run at the corpus's
**actual** condition before any cross-arm claim was made (§3).

## 2. ⚠️ CORRECTION — an instrument bug in THIS script, caught by recorded argv

The first run passed `chromium_tier.NO_PROXY` as the *credential*. That sentinel
is what `_proxy_server_and_bridge` **returns** for a no-exit venue, not what it
**takes**: it went down the parse path and emitted a literal
`--proxy-server=socks5://__no_proxy__:1080` instead of `--no-proxy-server`.

Chromium bypasses a proxy for loopback, so **the cell would have produced a
perfectly clean-looking reading through a launch surface nobody chose** — the
PS-14 shape exactly. Fixed to `""` + `allow_no_proxy=True`, which is the form
the helper's own docstring specifies, and verified in the recorded argv.

It was caught only because this script records the argv it actually launched
(the PS-103 discipline). That is why every cell in the record carries its full
command line rather than an echo of what was requested.

---

## 3. Baseline reproduces, byte-for-byte

Before measuring anything new, `ps143` arm-b's condition was re-run:

```
windows / seed 9001 / layer OFF
  -> Google Inc. (AMD) | ANGLE (AMD, AMD Radeon(TM) Graphics (0x00001638) Direct3D11 vs_5_0 ps_5_0, D3D11)
```

Identical to the committed `ps143/arm-b-layer-off.json`. The control arm
reproduces under a fresh run 2026-08-25, so the corpus is sound and the
de-confounding below is meaningful rather than moot.

## 4. ⭐ The swiftshader confound is DISSOLVED, not merely set aside

Ticket comment 3 pre-registers a lead: the linux harness forces
`--use-angle=swiftshader`, so a SwiftShader row could be the *instrument*.

The recorded argv settles it. `_launch_args` gates that block on
`_platform.IS_LINUX`, which is `sys.platform` — **the HOST, not the declared
machine.** Confirmed in the record: the `windows`-arm launch carries
`['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader']`
alongside `--fingerprint-platform=windows`.

⇒ **All nine cells ran with identical GL flags.** The windows arm read a
plausible AMD card under the *same* forced-swiftshader flags that the linux arm
read SwiftShader under. The flags are held constant across the contrast, so they
cannot explain the difference between arms. The lead is closed, on evidence.

---

## 5. THE GRID — layer OFF, the engine's own identity

`UNMASKED_VENDOR_WEBGL | UNMASKED_RENDERER_WEBGL`, WebGL1 (WebGL2 agreed in
every cell):

| arm | seed 9001 | seed 4242 | seed 1337 |
|---|---|---|---|
| **linux** | `(Google)` SwiftShader `0x0000C0DE` | `(Google)` SwiftShader `0x0000C0DE` | `(Google)` SwiftShader `0x0000C0DE` |
| **windows** | `(AMD)` Radeon `0x00001638` | `(Intel)` Iris Xe `0x0000A7A0` | `(NVIDIA)` RTX 4060 Laptop `0x000028E0` |
| **macos** | `(Apple)` Metal: Apple M2 | `(Apple)` Metal: Apple M4 | `(Apple)` Metal: Apple M2 |

**These are the engine's values, not ours leaking in.** Exact-string search
across `src/`: `0x0000A7A0` **0 hits**, `0x000028E0` **0 hits**, `Apple M4`
**0 hits**, `RTX 4060` **0 hits**.

> ⚠️ One precision the confirm-review got slightly wrong, in our favour: it
> reported `Iris Xe` as **0 hits**. It is **2 hits** — but at
> `0x0000A7A1`/`ADL GT2` (`gpu_ext.py:91,179`), while the engine produced
> `0x0000A7A0`. **One hex digit apart, and a different device.** The conclusion
> stands and is in fact sharper: near-identical model names from two independent
> authors is precisely how a same-vector contradiction hides from a name-only
> comparison.

### M1 — the `gpu_ext.py` header rationale: **STALE on 2 of 3 arms**

The header asserts that without the extension the engine reads as a generic
`Google Inc. (Google)` / SwiftShader pair, an instant headless tell.

* **linux — HOLDS.** Exactly the pair the header names, on all three seeds.
* **windows — STALE.** A plausible, IHV-varied real desktop GPU.
* **macos — STALE.** A plausible Apple Metal renderer.

So the header is not simply wrong; it is **arm-specific and unqualified**. It
describes the linux arm and is presented as describing the engine.

### M2 — does the engine's identity vary per profile? **A SPLIT**

* **linux — `CONSTANT_ACROSS_SEEDS`.** Every seed, one identical SwiftShader
  string. Deferring here gives every linux profile the same "GPU" — a shared
  cross-profile identifier, which **Level 2 (mutual unlinkability) forbids.**
  **(b) is not viable on linux.**
* **windows — `VARIES_BY_SEED`.** Three seeds, three different IHVs.
  **(b) is viable on windows.**
* **macos — `VARIES_BY_SEED`, with a caveat that must not be dropped.** Seeds
  9001 and 1337 **both** returned `Apple M2`; only 4242 differed. It varies, but
  2-of-3 collided on a three-seed sample. Apple's real product line is small, so
  a small pool is not per se a defect (`probes.py` grades `webgl.unmasked` as
  `POOLED` for this exact reason) — but "varies" is a weaker claim on macos than
  on windows and is recorded as such rather than rounded up.

This **independently reproduces PS-69** (`Iris Xe`/`RTX 4060` on windows,
`M4`/`M2` on macos, same two seeds) from a separate seat and instrument.

### The two-spoofer contradiction, shown directly

`layer-on-differential.json`, seed 9001, both layer states:

| arm | layer OFF (engine's) | layer ON (what ships) | ours? |
|---|---|---|---|
| linux | `(Google)` SwiftShader | `(Intel)` Mesa Intel UHD 630 (CFL GT2) | `gpu_ext.py:177` ✅ |
| windows | `(AMD)` Radeon `0x00001638` | `(NVIDIA)` RTX 3070 `0x00002484` | `gpu_ext.py:89` ✅ |
| macos | `(Apple)` Metal: Apple M2 | `(Apple)` Metal: Apple M1 | `gpu_ext.py:99` ✅ |

Both authors are live and they disagree **on every arm** — the ticket's root
cause, reproduced first-hand rather than cited, and now shown to be general
rather than a windows-only artefact.

---

## 6. What is NOT covered here, and why

**The live checker-matrix half.** PS-161's definition of done asks for a fresh
run through the **proxied exit**, verified by the merged consistency check.
This record does not contain one.

The reason is **not** engine availability (§0 settles that) and **not** a
missing credential (`/workspace/_secrets/test-proxy.txt` is present). It is
that **the fix has not been written**, because writing it requires the owner's
(a)/(b) choice, which per PS-161 must be recorded before implementation begins.
Verifying an unchanged product against the gate would measure today's known
contradiction a second time.

Everything above is a **loopback** reading: the page is served from `127.0.0.1`
and no third party is contacted, so there is no address to leak and no exit to
prove (`local_probe.py`'s established venue; PS-10 forbids re-introducing the
exit dependency for reads that do not need it). **This is not a waiver of the
proxied-exit rule** — that rule governs checker reads, and the checker half
still owes a proven exit and a recorded exit IP.

---

## 7. What this does and does not settle for (a)/(b)

**It does not dissolve the decision, and it does not leave it a binary.**

The planner's dissolving condition — *every profile gets the same card, so (b)
is off the table and (a) is forced* — **is met on linux and fails on
windows/macos.** The confirm-review anticipated exactly this: *"he should not be
handed a binary if the measurement returns a split."* It returned a split.

Stated neutrally, without taking the choice:

* **(a) is forced on linux** on Level 2 grounds, independent of preference.
* **(b) is available on windows and macos**, where the engine already authors a
  seed-varied identity — weaker on macos (§5).
* A uniform **(b)** across all arms is **eliminated by measurement.**
* **(a)'s premise is partly stale**: the header rationale that justifies
  `gpu_ext`'s existence holds only on linux — the one arm where (a) is forced
  anyway. On windows/macos the extension is overriding an already-plausible,
  already-varied engine value.

The live possibility the confirm-review raised — a per-arm answer — is what the
measurement returned, so the real question is narrower than (a)-or-(b):
**whether persona wants one policy across arms, or the per-arm split the
evidence supports.** That is a decision about what persona *is*, and it is not
taken here.
