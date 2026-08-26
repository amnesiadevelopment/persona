# PS-182 — Does the Firefox WebGL readback carry per-profile entropy?

**Taken 2026-08-26, branch `fix/ps182-firefox-webgl-readback-measurement`, from `origin/main` @ `6dffce6`.**

Everything below is a **measurement or a commit timestamp**. Where a question
could not be answered from this seat, this document says so rather than
recording the absence as a pass.

---

## 0. The answer, before the working

The ticket asks one thing first: *"Whether Firefox has any per-profile readback
perturbation at all, or whether the packaged engine simply returns a constant."*

**Firefox HAS per-profile readback perturbation. It is delivered, it runs, and it
is ours.** This is established three independent ways below, one of which is a
bit-identical reproduction of a real engine's recorded output.

**The ticket's central premise — that nothing was ever delivered for the Firefox
path — is refuted.** It was delivered by PS-78, ~6 hours *before* PS-97 even
landed, and PS-97's fix reached Firefox too because it edited **shared** code.

That does **not** make the finding a false alarm. `creepjs :: webgl_pixel_hash`
really does read `51df3565` for two different Firefox profiles, and those two
profiles really are linkable on that row. **The collision is real and the
diagnosis in the ticket is wrong**, which means the fix the ticket anticipated
would not have fixed it.

---

## 1. Chronology — the premise, against the commit log

| what | commit | timestamp (UTC) |
|---|---|---|
| PS-78 delivers `firefox_webgl_init_script` | `01b10cd` | **2026-08-23 01:47:32** |
| PS-97 "Chromium" fix — edits the **shared** `_CONTENT_SCRIPT` | `09e34b5` | **2026-08-23 08:01:31** |
| PS-103 installs the masking layer into the checker harness, **including the Firefox route** | `aae090a` | **2026-08-23 08:49:31** |
| collision reading `ps128`, seeds 1337 + 4242 | — | **2026-08-23 21:46:11** |
| collision reading `ps137`, seeds 1337 + 4242 | — | **2026-08-24 09:51:20** |

Two things follow directly, and both contradict the ticket:

1. **The Firefox vector was NOT missing.** `webgl_ext.firefox_webgl_init_script`
   exists and `invisible_launch.py:3345` installs it **unconditionally**
   (`_install_spoof("webgl", firefox_webgl_init_script(seed))`).
2. **PS-97's fix DID reach Firefox.** PS-97 replaced the `_STRIDE = 17` byte-comb
   with the content-ordinal reservoir *inside `_CONTENT_SCRIPT`* — the body
   `firefox_webgl_init_script` splices via `_webgl_patch_js`. The two engines
   differ **only** in the `nativeWrap` / worker-cloak seam. So "PS-97 fixed
   Chromium and the Firefox path was never delivered" is false on both halves.

Both collision readings **postdate all three commits**, and both record:

```
masking_layer: installed=['audio','locale','webgl']  route=init_scripts  complete=true  failed={}
```

So `51df3565` appearing on 23 Aug *and* 25 Aug is **not** evidence of an
undelivered fix. It is evidence that this particular row does not move **even
with the fixed code demonstrably installed** — a different and sharper problem.

---

## 2. The instrument, before the product (PS-14)

This seat **cannot** take a live Firefox reading, and saying so plainly matters
more than producing a number:

| | |
|---|---|
| engine binary | **absent** — `~/.persona/engine/` holds only `builds.json` |
| `Xvfb` / `xvfb-run` | **not installed** |
| `DISPLAY` | **empty** |
| proxied exit | **dead** — `User was rejected by the SOCKS5 server (1 3)`, account-level |
| `node` | v20.20.2 — **available** |

The display gap is disqualifying, not merely inconvenient. A prior reviewer-seat
finding records that on this project **Firefox WebGL is display-dependent: with
no X display `getContext('webgl')` and `getParameter(VERSION)` both return
`null`** — which is *byte-identical to the signature of a spoof that never
loaded*. A live Firefox run from this seat would produce a confident-looking
null that cannot be told apart from the very defect under investigation.

So the measurement was taken the one way that is sound from here: **execute the
shipped script itself**, with `node`, in a real JS realm.

### 2.1 An instrument defect I found in my own harness — recorded because it faked the result

The first version of the harness ran all four seeds **in one node process**,
sharing one `Object`. The shipped per-realm idempotency guard
(`worker_wrap.realm_guard_js`) stores its flag at `Object.__pnaRealm.webgl`, so:

- seed 1337 patched normally and moved 384 bytes;
- seeds 4242, 111 and 9001 hit `if (__pnaReg["webgl"] === true) return;` and
  **patched nothing at all**, returning the unperturbed digest.

That produced a clean, plausible, **entirely fake collision** — the product
looked broken because the instrument was. It is fixed with
`vm.createContext` per seed (one fresh realm per profile, which is what a real
page load is), and the harness carries a comment forbidding the collapse.

This is exactly PS-14's rule — *an identical result across every cell is the
shape to distrust* — and it is why the harness is validated in §3.3 rather than
merely trusted.

---

## 3. The measurement

Re-derive with:

```bash
python3 readings/ps182-2026-08-26/emit_scripts.py   # emit the SHIPPED script per seed
node    readings/ps182-2026-08-26/harness.js        # execute it in fresh realms
```

No browser, no display, no network, no proxy, no credential. `emit_scripts.py`
writes **the product's own text** (`firefox_webgl_init_script(seed)`); the
harness never re-implements the perturbation, because a re-implementation would
only prove the harness is self-consistent — the PS-11 failure class, arriving
inside the instrument built to avoid it.

### 3.1 Results

| geometry | guard-eligible bytes | bytes moved | distinct digests @ 4 seeds |
|---|---|---|---|
| **A** — the loopback probe's own draw (`probes.py webgl.readback`), 32x32 mid-range bands | 3072 of 4096 | 384 | **4 of 4** |
| **B** — CreepJS corner 17x42, 16 eligible of 2856 (the census `webgl_ext.py:30-45` measured) | 16 of 2856 | 16 | **4 of 4** |
| **C** — CreepJS corner 17x42, **zero** eligible | **0** of 2856 | **0** | **1 — COLLIDES** |
| **D** — geometry A in a **WebGL2-only** realm (the Firefox worker shape) | 3072 of 4096 | 384 | **4 of 4** |

Seeds: 111, 1337, 4242, 9001.

### 3.2 What each row rules in or out

- **A answers the ticket's first question outright.** Four seeds, four distinct
  readback digests, 384 bytes moved each time. The Firefox path is **not** a
  constant and the packaged engine is **not** simply returning one.
- **B is the decisive one.** It is the *starved* CreepJS geometry PS-97 fixed
  Chromium for — only 16 usable bytes in 2856 — and the shipped Firefox script
  still yields **four distinct digests**. So "the perturbation is too sparse to
  survive CreepJS's sampling" is **refuted** as the cause.
- **C is the only geometry that reproduces the observed collision.** With zero
  guard-eligible bytes the patch is a no-op by construction (`v > 1 && v < 254`
  admits nothing in a fully-cleared region) and all four seeds return the
  identical unperturbed digest — the shape of `51df3565`.
- **D closes the one remaining in-scope Firefox delivery gap.** `probes.py`
  declares `webgl.readback` WINDOW_ONLY because in a Firefox worker only
  `'webgl2'` yields a context; had the patch missed `WebGL2RenderingContext`,
  that would have been a genuine Firefox-specific hole. It does not.

### 3.3 The harness is validated against real engine output — not merely trusted

`readings/ps135-2026-08-24/` holds loopback probe readings taken on a **real
packaged `firefox-20` engine under `xvfb`**. Geometry A reproduces them
**bit-identically**:

| seed | this harness | recorded real engine (`ps135`) | |
|---|---|---|---|
| 111 | `2372980207` | `2372980207` | MATCH |
| 1337 | `1471895271` | `1471895271` | MATCH |
| 4242 | `1444116715` | `1444116715` | MATCH |

and the **unperturbed** value, `2952899525`, is exactly what the layer-off
Chromium counterfactual recorded
(`counterfactual.chromium.no-fingerprint-flag.seedarg*.json`).

**This is the strongest single result in this document, and it is worth stating
as a syllogism.** The real Firefox engine's recorded readback equals
*(the ideal band buffer) + (our perturbation at that seed)*, byte for byte:

1. The packaged engine's raw render of the probe draw is the mathematically
   exact band buffer (SwiftShader is deterministic and GPU-less here).
2. Our patch, applied to that buffer at seed *s*, produces digest *D(s)*.
3. The real engine, at seed *s*, recorded **exactly** *D(s)*.

Therefore **persona's WebGL readback perturbation demonstrably executed on a
real Firefox engine**, and every bit of per-profile entropy in that reading is
**ours** — the engine contributed none. A three-seed bit-identical agreement is
not something a harness reproducing the wrong arithmetic can achieve by luck.

---

## 4. The corpus control — the checker's own rows disagree with each other

Within the **same** Firefox records, at the same seeds, on two different exits
and two different days:

| row | seed 1337 | seed 4242 | |
|---|---|---|---|
| `creepjs :: canvas_data_hash` | `8169d87a` | `b1b3f0d8` | **differs** |
| `creepjs :: webgl_image_hash` | `5e9c98db` | `911e9c23` | **differs** |
| `pixelscan :: webgl_hash` | `f8819d18…` | `70454ee8…` | **differs** |
| **`creepjs :: webgl_pixel_hash`** | **`51df3565`** | **`51df3565`** | **IDENTICAL** |

Reproduced identically in `ps128` (2026-08-23, exit `95.49.113.111`) and `ps137`
(2026-08-24, exit `83.175.185.209`).

This is the control that makes §3 a statement about **this one row** rather than
about the harness or the exit. The profiles differ everywhere else CreepJS
looks — including on `webgl_image_hash`, which is the *same checker* reading the
*same WebGL surface*. A dead masking layer, a dead exit or a broken record would
not move three rows and freeze the fourth.

**`webgl_image_hash` moving while `webgl_pixel_hash` does not is the sharpest
clue in the corpus**, and it points the same way geometry C does: CreepJS's
`images:` hash covers rendered imagery that genuinely varies per profile, while
its `pixels:` hash reads a **corner region** (`drawingBufferWidth/15 x
drawingBufferHeight/6`) that on the Firefox render appears to contain **no
guard-eligible content in any profile**.

---

## 5. Conclusion, and the bound on it

**Settled by measurement (DoD 1 & 2):** the Firefox WebGL readback **does** carry
per-profile entropy today. It is delivered (PS-78), it survives PS-97's rewrite,
it covers WebGL1, WebGL2 and the worker realm, it produces four distinct digests
at four seeds even under CreepJS's starved geometry, and it is **proven to have
executed on a real Firefox engine** by bit-identical reproduction. **No code fix
is warranted on the delivery path, because there is no defect on it.**

**Leading hypothesis for the live collision, explicitly NOT settled:** the region
CreepJS samples for `pixels:` contains **zero bytes passing the mid-range guard**
on the Firefox render, making the perturbation a no-op *there specifically* while
working everywhere else. Geometry C reproduces the collision exactly; nothing
else tested does.

**Why this is a hypothesis and not a finding.** Confirming it requires reading
CreepJS's actual readback region on a live Firefox — which needs the proxied
exit, and **the proxy credential is rejected at account level**
(`User was rejected by the SOCKS5 server (1 3)`). I did attempt a cheaper
confirmation: a brute-force search over ~27,200 plausible cleared/uniform buffer
geometries for one hashing to `51df3565` under CreepJS's `hashMini`. **No match**
— and that is recorded as **inconclusive**, not as counter-evidence, because
`hashMini`'s exact form is not pinned in this repo and a miss is consistent with
both a wrong hash function and a wrong buffer.

**Why no fix is attempted here.** The only lever that would perturb a fully
cleared region is the mid-range guard `v > 1 && v < 254` — which lives in the
**shared** `_CONTENT_SCRIPT`, is byte-pinned by `tests/test_webgl_ext.py`, and is
placed **out of scope by this ticket** ("do not touch `webgl_ext`'s selection
logic to chase this"). Widening it would also move every Chromium readback and
would nudge near-black pixels, which is a visible tell rather than sub-pixel
dither. **Changing it blind, without the live read that would confirm the region
is empty, is precisely the PS-97 mistake this ticket exists to correct** —
shipping a perturbation that nothing observes.

### What would settle it

One live CreepJS read on Firefox through a healthy exit, capturing the readback
region's byte census (`bytes`, and how many pass `v > 1 && v < 254`). If that
census is zero, the hypothesis is confirmed and the follow-up is a *deliberate,
Chromium-affecting* decision about the guard — a scoped ticket, not a blind edit.

---

## 6. Coverage this reading does and does not add

| | |
|---|---|
| **Live checker confirmation** | **NOT TAKEN.** Proxy credential rejected at account level (`User was rejected by the SOCKS5 server (1 3)`), re-verified by the planner 2026-08-26T07:2xZ; it refused 7 of PS-177's 8 arms. Operator fault, tracked separately. |
| **Loopback probe reading** | Taken, at four seeds, against the shipped script — and validated bit-identically against real `firefox-20` engine output in `ps135`. |
| **Engine build / exit IP** | Not applicable: no live arm was taken. The reproduced corpus rows carry `invisible_playwright/firefox-20` and exits `95.49.113.111` (`ps128`) / `83.175.185.209` (`ps137`). |
| **`declared_machine_honoured`** | `false` on the Firefox path by engine limitation (issue #211) — expected, not a finding. |

**The loopback result is deliberately NOT substituted for the live one.** PS-97's
whole lesson is that an internal buffer differing did not survive the trip to the
checker. A probe reading four different buffers while a checker reads one
identical hash is **the disagreement to preserve**, and §3 and §4 are recorded
side by side for exactly that reason.
